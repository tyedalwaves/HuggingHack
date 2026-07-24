from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from huggingface_hub import HfApi, hf_hub_download, hf_hub_url

from .config import Settings, validate_repo_id


GGUF_RANGE_PATTERN = re.compile(r"^bytes=(\d+)-(\d+)$")
GGUF_MAX_HEADER_BYTES = 50_000_000
GGUF_MAX_RANGE_BYTES = 2_100_000


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _file_size(sibling: Any) -> int:
    direct = getattr(sibling, "size", None)
    if isinstance(direct, int):
        return direct
    lfs = getattr(sibling, "lfs", None)
    if isinstance(lfs, dict):
        return int(lfs.get("size") or 0)
    nested = getattr(lfs, "size", None)
    return int(nested or 0)


def _license_from_tags(tags: list[str]) -> str | None:
    return next((tag.split(":", 1)[1] for tag in tags if tag.startswith("license:")), None)


def _parameter_count(info: Any) -> int | None:
    safetensors = getattr(info, "safetensors", None)
    parameters = getattr(safetensors, "parameters", None)
    if isinstance(parameters, dict):
        values = [value for value in parameters.values() if isinstance(value, int)]
        return sum(values) if values else None
    if isinstance(safetensors, dict):
        raw = safetensors.get("parameters")
        if isinstance(raw, dict):
            values = [value for value in raw.values() if isinstance(value, int)]
            return sum(values) if values else None
    return None


def validate_gguf_filename(filename: str) -> str:
    value = filename.strip().replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 500
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() != ".gguf"
    ):
        raise ValueError("GGUF filename must be a safe repository-relative .gguf path.")
    return path.as_posix()


def parse_gguf_range(value: str | None) -> tuple[int, int]:
    match = GGUF_RANGE_PATTERN.fullmatch((value or "").strip())
    if not match:
        raise ValueError("A single bounded byte range is required.")
    start, end = (int(part) for part in match.groups())
    if end < start:
        raise ValueError("The GGUF byte range is invalid.")
    if end - start + 1 > GGUF_MAX_RANGE_BYTES:
        raise ValueError("GGUF range requests are limited to 2.1 MB.")
    if end >= GGUF_MAX_HEADER_BYTES:
        raise ValueError("GGUF inspection is limited to the first 50 MB of a file.")
    return start, end


class HubService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.api = HfApi(
            endpoint=settings.hf_endpoint,
            token=settings.hf_token or False,
            library_name="hugginghack",
            library_version=settings.app_version,
        )
        self.range_client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    def close(self) -> None:
        self.range_client.close()

    @staticmethod
    def serialize_model(info: Any) -> dict[str, Any]:
        tags = list(getattr(info, "tags", None) or [])
        model_id = getattr(info, "id", None) or getattr(info, "modelId", None)
        return {
            "id": model_id,
            "author": getattr(info, "author", None)
            or (model_id.split("/", 1)[0] if model_id and "/" in model_id else None),
            "pipeline_tag": getattr(info, "pipeline_tag", None),
            "library_name": getattr(info, "library_name", None),
            "tags": tags,
            "downloads": int(getattr(info, "downloads", None) or 0),
            "downloads_all_time": int(getattr(info, "downloads_all_time", None) or 0),
            "likes": int(getattr(info, "likes", None) or 0),
            "trending_score": float(getattr(info, "trending_score", None) or 0),
            "last_modified": _iso(getattr(info, "last_modified", None)),
            "created_at": _iso(getattr(info, "created_at", None)),
            "private": bool(getattr(info, "private", False)),
            "gated": getattr(info, "gated", False),
            "sha": getattr(info, "sha", None),
            "license": _license_from_tags(tags),
            "parameter_count": _parameter_count(info),
        }

    def search_models(
        self,
        search: str = "",
        sort: str = "trending",
        task: str = "",
        library: str = "",
        app: str = "",
        parameters: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        sort_map = {
            "trending": "trending_score",
            "downloads": "downloads",
            "updated": "last_modified",
            "likes": "likes",
        }
        filters = [value for value in (library,) if value]
        models = self.api.list_models(
            search=search or None,
            pipeline_tag=task or None,
            filter=filters or None,
            apps=app or None,
            num_parameters=parameters or None,
            sort=sort_map.get(sort, "trending_score"),
            limit=max(1, min(limit, 50)),
            full=True,
        )
        return [self.serialize_model(info) for info in models]

    def model_details(self, repo_id: str, revision: str = "main") -> dict[str, Any]:
        validated = validate_repo_id(repo_id)
        info = self.api.model_info(
            validated,
            revision=revision,
            files_metadata=True,
            securityStatus=True,
        )
        result = self.serialize_model(info)
        files = []
        for sibling in getattr(info, "siblings", None) or []:
            files.append(
                {
                    "path": getattr(sibling, "rfilename", ""),
                    "size": _file_size(sibling),
                    "blob_id": getattr(sibling, "blob_id", None),
                }
            )
        result.update(
            {
                "revision": revision,
                "files": files,
                "total_bytes": sum(item["size"] for item in files),
                "security_status": self._security_status(info),
                "source_url": f"{self.settings.hf_endpoint}/{validated}",
            }
        )
        return result

    @staticmethod
    def _security_status(info: Any) -> Any:
        value = getattr(info, "security_repo_status", None)
        if value is None:
            return None
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, (dict, list, str, int, float, bool)):
            return value
        return str(value)

    def read_model_card(self, repo_id: str, revision: str = "main") -> str | None:
        validated = validate_repo_id(repo_id)
        try:
            path = hf_hub_download(
                repo_id=validated,
                filename="README.md",
                revision=revision,
                cache_dir=self.settings.hub_cache_path,
                token=self.settings.hf_token or False,
                endpoint=self.settings.hf_endpoint,
            )
        except Exception:
            return None
        return Path(path).read_text(encoding="utf-8", errors="replace")[:120_000]

    def read_gguf_range(
        self,
        repo_id: str,
        filename: str,
        revision: str,
        range_header: str | None,
    ) -> dict[str, Any]:
        validated_repo = validate_repo_id(repo_id)
        validated_filename = validate_gguf_filename(filename)
        start, end = parse_gguf_range(range_header)
        url = hf_hub_url(
            repo_id=validated_repo,
            filename=validated_filename,
            revision=revision,
            endpoint=self.settings.hf_endpoint,
        )
        headers = {
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "Range": f"bytes={start}-{end}",
            "User-Agent": f"hugginghack/{self.settings.app_version}",
        }
        if self.settings.hf_token:
            headers["Authorization"] = f"Bearer {self.settings.hf_token}"

        with self.range_client.stream("GET", url, headers=headers) as response:
            if response.status_code in {401, 403}:
                raise PermissionError(
                    "Hugging Face denied access to this GGUF file. Check HF_TOKEN and repository access."
                )
            if response.status_code == 404:
                raise FileNotFoundError("The selected GGUF file was not found.")
            if response.status_code == 416:
                raise ValueError("The requested GGUF header range is unavailable.")
            if response.status_code not in {200, 206}:
                raise RuntimeError(
                    f"Hugging Face returned status {response.status_code} for the GGUF header."
                )
            if response.status_code == 200 and start != 0:
                raise RuntimeError("The remote file server does not support byte ranges.")

            expected = end - start + 1
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > expected:
                    raise RuntimeError(
                        "The remote file server ignored the bounded GGUF range request."
                    )

            result_headers = {
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=3600",
                "Vary": "Range",
            }
            content_range = response.headers.get("Content-Range")
            if content_range:
                result_headers["Content-Range"] = content_range
            return {
                "content": bytes(content),
                "headers": result_headers,
                "status_code": response.status_code,
            }

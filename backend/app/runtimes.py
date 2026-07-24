from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

import httpx

from .auth import utc_iso
from .config import Settings, repository_path, validate_repo_id
from .database import INTEGRITY_ERRORS, Database


TARGET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
OLLAMA_METADATA_SUFFIXES = {".json", ".model", ".tiktoken"}
TRANSFER_CHUNK_BYTES = 8 * 1024**2


@dataclass(frozen=True)
class RuntimeTarget:
    id: str
    name: str
    kind: Literal["ollama", "vllm"]
    base_url: str
    token_env: str | None = None
    remote_model_root: str | None = None
    keep_alive: str | int = "5m"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "base_url": self.base_url,
            "remote_model_root": self.remote_model_root,
            "authenticated": bool(self.token_env and os.getenv(self.token_env)),
            "transfer_mode": "blob-upload" if self.kind == "ollama" else "shared-path",
            "keep_alive": self.keep_alive if self.kind == "ollama" else None,
        }


def _clean_base_url(value: Any) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Runtime target base_url must be an http(s) URL without credentials, "
            "a query, or a fragment."
        )
    return base_url


def parse_runtime_targets(raw: str) -> list[RuntimeTarget]:
    if len(raw) > 65_536:
        raise ValueError("RUNTIME_TARGETS_JSON is too large.")
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError as error:
        raise ValueError(f"RUNTIME_TARGETS_JSON is invalid JSON: {error.msg}.") from error
    if not isinstance(payload, list):
        raise ValueError("RUNTIME_TARGETS_JSON must contain a JSON array.")
    if len(payload) > 32:
        raise ValueError("At most 32 runtime targets may be configured.")

    targets: list[RuntimeTarget] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Runtime target {index + 1} must be an object.")
        target_id = str(item.get("id") or "").strip().lower()
        if not TARGET_ID_PATTERN.fullmatch(target_id):
            raise ValueError(
                f"Runtime target {index + 1} has an invalid id. "
                "Use lowercase letters, numbers, underscores, or hyphens."
            )
        if target_id in seen:
            raise ValueError(f"Runtime target id '{target_id}' is duplicated.")
        seen.add(target_id)

        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"ollama", "vllm"}:
            raise ValueError(
                f"Runtime target '{target_id}' kind must be ollama or vllm."
            )
        name = str(item.get("name") or target_id).strip()
        if not name or len(name) > 80:
            raise ValueError(
                f"Runtime target '{target_id}' name must be 1 to 80 characters."
            )
        token_env_value = str(item.get("token_env") or "").strip()
        token_env = token_env_value or None
        if token_env and not ENV_NAME_PATTERN.fullmatch(token_env):
            raise ValueError(
                f"Runtime target '{target_id}' token_env is not a valid environment name."
            )
        remote_root_value = str(item.get("remote_model_root") or "").strip()
        remote_model_root = remote_root_value or None
        if kind == "vllm" and not remote_model_root:
            raise ValueError(
                f"vLLM target '{target_id}' requires remote_model_root."
            )
        keep_alive = item.get("keep_alive", "5m")
        if not isinstance(keep_alive, (str, int)) or (
            isinstance(keep_alive, str) and len(keep_alive) > 32
        ):
            raise ValueError(
                f"Runtime target '{target_id}' keep_alive must be a short string or integer."
            )
        targets.append(
            RuntimeTarget(
                id=target_id,
                name=name,
                kind=kind,  # type: ignore[arg-type]
                base_url=_clean_base_url(item.get("base_url")),
                token_env=token_env,
                remote_model_root=remote_model_root,
                keep_alive=keep_alive,
            )
        )
    return targets


def validate_runtime_model_name(value: str) -> str:
    name = value.strip()
    if not MODEL_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Runtime model names must start with a letter or number and use only "
            "letters, numbers, dots, underscores, colons, slashes, or hyphens."
        )
    return name


def validate_source_file(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip().replace("\\", "/")
    relative = PurePosixPath(cleaned)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ValueError("The selected runtime source file is invalid.")
    if len(cleaned) > 500:
        raise ValueError("The selected runtime source file path is too long.")
    return str(relative)


def remote_model_path(remote_root: str, relative_path: str) -> str:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("The indexed model path is invalid.")
    if re.match(r"^[A-Za-z]:[\\/]", remote_root) or "\\" in remote_root:
        root = PureWindowsPath(remote_root)
        if not root.is_absolute():
            raise ValueError("vLLM remote_model_root must be an absolute path.")
        return str(root.joinpath(*relative.parts))
    root = PurePosixPath(remote_root)
    if not root.is_absolute():
        raise ValueError("vLLM remote_model_root must be an absolute path.")
    return str(root.joinpath(*relative.parts))


def ollama_files(root: Path, source_file: str | None = None) -> list[Path]:
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ValueError("The model is not present in the local cache.")

    if source_file:
        relative = validate_source_file(source_file)
        assert relative is not None
        candidate = resolved_root / relative
        selected = candidate.resolve()
        if (
            resolved_root != selected
            and resolved_root not in selected.parents
        ) or not selected.is_file() or candidate.is_symlink():
            raise ValueError("The selected GGUF file was not found in this model.")
        if selected.suffix.lower() != ".gguf":
            raise ValueError("Ollama source_file must select a GGUF file.")
        return [selected]

    gguf_files = sorted(
        (
            path
            for path in resolved_root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() == ".gguf"
        ),
        key=lambda path: str(path.relative_to(resolved_root)).lower(),
    )
    if len(gguf_files) == 1:
        return gguf_files
    if len(gguf_files) > 1:
        raise ValueError(
            "This repository contains multiple GGUF files. Select source_file explicitly."
        )

    root_files = [
        path
        for path in resolved_root.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and (
            path.suffix.lower() in OLLAMA_METADATA_SUFFIXES
            or path.suffix.lower() == ".safetensors"
        )
    ]
    if not any(path.suffix.lower() == ".safetensors" for path in root_files):
        raise ValueError(
            "Ollama import requires one GGUF file or a root-level SafeTensors model."
        )
    return sorted(root_files, key=lambda path: path.name.lower())


class RuntimeManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        client_factory: Callable[[RuntimeTarget], httpx.Client] | None = None,
    ):
        self.settings = settings
        self.database = database
        self.targets = {
            target.id: target
            for target in parse_runtime_targets(settings.runtime_targets_json)
        }
        self._executor = ThreadPoolExecutor(
            max_workers=settings.runtime_workers,
            thread_name_prefix="runtime",
        )
        self._client_factory = client_factory or self._default_client

    @staticmethod
    def _default_client(target: RuntimeTarget) -> httpx.Client:
        headers = {"User-Agent": "HuggingHack/runtime"}
        if target.token_env:
            token = os.getenv(target.token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        timeout = httpx.Timeout(None, connect=10.0)
        return httpx.Client(headers=headers, timeout=timeout, follow_redirects=False)

    def public_targets(self) -> list[dict[str, Any]]:
        return [target.public() for target in self.targets.values()]

    def queue(
        self,
        target_id: str,
        model: dict[str, Any],
        runtime_model_name: str | None,
        source_file: str | None,
        user_id: str | None,
    ) -> dict[str, Any]:
        target = self.targets.get(target_id)
        if not target:
            raise ValueError("Runtime target not found.")
        repo_id = validate_repo_id(model["repo_id"])
        if not model.get("cached"):
            raise ValueError("Restore this model to the local cache before loading it.")
        root = repository_path(repo_id, self.settings.model_storage)
        if not root.is_dir():
            raise ValueError("The model is not present in the local cache.")
        if self.database.find_active_runtime_job(target.id, repo_id):
            raise ValueError(
                f"{repo_id} already has an active job for {target.name}."
            )
        if target.kind == "vllm" and self.database.find_active_runtime_target(target.id):
            raise ValueError(
                f"{target.name} is already switching to another model."
            )

        name = validate_runtime_model_name(
            runtime_model_name or repo_id.replace("/", "-").lower()
        )
        selected_source = validate_source_file(source_file)
        total_bytes = int(model.get("size_bytes") or 0)
        if target.kind == "ollama":
            selected_files = ollama_files(root, selected_source)
            total_bytes = sum(path.stat().st_size for path in selected_files)
        timestamp = utc_iso()
        try:
            job = self.database.create_runtime_job(
                {
                    "id": uuid.uuid4().hex,
                    "target_id": target.id,
                    "target_name": target.name,
                    "target_kind": target.kind,
                    "repo_id": repo_id,
                    "runtime_model_name": name,
                    "source_file": selected_source,
                    "status": "queued",
                    "total_bytes": total_bytes,
                    "processed_bytes": 0,
                    "progress": 0,
                    "message": "Waiting for a runtime worker",
                    "error": None,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "completed_at": None,
                    "user_id": user_id,
                }
            )
        except INTEGRITY_ERRORS as error:
            raise ValueError(
                f"{target.name} already has a conflicting active runtime job."
            ) from error
        self._executor.submit(self._run, job["id"])
        return job

    def _update(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        changes["updated_at"] = utc_iso()
        return self.database.update_runtime_job(job_id, **changes)

    @staticmethod
    def _response_error(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("detail")
            if detail:
                return str(detail)[:500]
        return (response.text.strip() or response.reason_phrase or "Remote request failed")[
            :500
        ]

    def _require_response(
        self, response: httpx.Response, expected: set[int] | None = None
    ) -> httpx.Response:
        accepted = expected or set(range(200, 300))
        if response.status_code not in accepted:
            raise RuntimeError(
                f"Remote runtime returned {response.status_code}: "
                f"{self._response_error(response)}"
            )
        return response

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(TRANSFER_CHUNK_BYTES):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def _upload_blob(
        self,
        client: httpx.Client,
        target: RuntimeTarget,
        job: dict[str, Any],
        path: Path,
        digest: str,
        processed: int,
    ) -> int:
        size = path.stat().st_size
        endpoint = f"{target.base_url}/api/blobs/{digest}"
        existing = client.head(endpoint)
        if existing.status_code == 200:
            processed += size
            self._update(
                job["id"],
                processed_bytes=processed,
                progress=min(90.0, processed / max(job["total_bytes"], 1) * 90),
                message=f"Reusing {path.name} already on {target.name}",
            )
            return processed
        if existing.status_code != 404:
            self._require_response(existing)

        sent = 0

        def chunks():
            nonlocal sent
            with path.open("rb") as source:
                while chunk := source.read(TRANSFER_CHUNK_BYTES):
                    sent += len(chunk)
                    current = processed + sent
                    self._update(
                        job["id"],
                        processed_bytes=current,
                        progress=min(
                            90.0, current / max(job["total_bytes"], 1) * 90
                        ),
                        message=f"Sending {path.name} to {target.name}",
                    )
                    yield chunk

        response = client.post(
            endpoint,
            headers={"Content-Length": str(size)},
            content=chunks(),
        )
        self._require_response(response, {200, 201})
        return processed + size

    def _run_ollama(
        self, client: httpx.Client, target: RuntimeTarget, job: dict[str, Any]
    ) -> None:
        root = repository_path(job["repo_id"], self.settings.model_storage)
        files = ollama_files(root, job.get("source_file"))
        self._update(
            job["id"],
            status="preparing",
            message=f"Hashing {len(files)} model file{'s' if len(files) != 1 else ''}",
        )
        manifest: dict[str, str] = {}
        digests: list[tuple[Path, str]] = []
        for path in files:
            digest = self._digest(path)
            digests.append((path, digest))
            name = (
                path.name
                if len(files) == 1 and path.suffix.lower() == ".gguf"
                else str(path.relative_to(root)).replace("\\", "/")
            )
            if name in manifest:
                raise ValueError(f"Ollama import has a duplicate file name: {name}")
            manifest[name] = digest

        self._update(
            job["id"],
            status="transferring",
            message=f"Checking blobs on {target.name}",
        )
        processed = 0
        for path, digest in digests:
            processed = self._upload_blob(
                client, target, job, path, digest, processed
            )

        self._update(
            job["id"],
            status="loading",
            progress=92,
            message=f"Creating {job['runtime_model_name']} in Ollama",
        )
        create = client.post(
            f"{target.base_url}/api/create",
            json={
                "model": job["runtime_model_name"],
                "files": manifest,
                "stream": False,
            },
        )
        self._require_response(create)
        self._update(
            job["id"],
            progress=96,
            message=f"Loading {job['runtime_model_name']} into memory",
        )
        preload = client.post(
            f"{target.base_url}/api/generate",
            json={
                "model": job["runtime_model_name"],
                "prompt": "",
                "stream": False,
                "keep_alive": target.keep_alive,
            },
        )
        self._require_response(preload)

    def _run_vllm(
        self, client: httpx.Client, target: RuntimeTarget, job: dict[str, Any]
    ) -> None:
        assert target.remote_model_root is not None
        model = self.database.get_local_model(job["repo_id"])
        if not model or not model.get("cached"):
            raise ValueError("The model is no longer present in the local cache.")
        mapped_path = remote_model_path(
            target.remote_model_root, model["relative_path"]
        )
        self._update(
            job["id"],
            status="loading",
            progress=10,
            message=f"Starting {job['runtime_model_name']} on {target.name}",
        )
        response = client.post(
            f"{target.base_url}/v1/models/load",
            json={
                "repo_id": job["repo_id"],
                "model_path": mapped_path,
                "served_model_name": job["runtime_model_name"],
            },
        )
        self._require_response(response)

    def _run(self, job_id: str) -> None:
        job = self.database.get_runtime_job(job_id)
        if not job:
            return
        target = self.targets.get(job["target_id"])
        if not target:
            self._update(
                job_id,
                status="failed",
                error="Runtime target is no longer configured.",
                message="Target unavailable",
                completed_at=utc_iso(),
            )
            return
        try:
            with self._client_factory(target) as client:
                if target.kind == "ollama":
                    self._run_ollama(client, target, job)
                else:
                    self._run_vllm(client, target, job)
            completed = utc_iso()
            self._update(
                job_id,
                status="ready",
                processed_bytes=job["total_bytes"],
                progress=100,
                message=f"{job['runtime_model_name']} is ready on {target.name}",
                error=None,
                completed_at=completed,
            )
        except Exception as error:
            completed = utc_iso()
            detail = (str(error).strip() or error.__class__.__name__)[:1000]
            self._update(
                job_id,
                status="failed",
                message="Runtime load failed",
                error=detail,
                completed_at=completed,
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .config import Settings, repository_path, validate_repo_id
from .indexer import UNSAFE_EXTENSIONS


MANIFEST_NAME = ".hugginghack.json"
PART_SUFFIXES = (".hugginghack-part", ".hugginghack-s3-part")


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _safe_relative_key(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("S3 object key escapes the repository cache.")
    return path


def _public_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))
    except ValueError:
        return "[invalid endpoint]"


class FilesystemModelStorage:
    backend = "filesystem"
    remote = False

    def __init__(self, settings: Settings):
        self.settings = settings

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "enabled": False,
            "connected": True,
            "bucket": None,
            "prefix": None,
            "endpoint": None,
            "error": None,
        }

    def sync_repository(self, repo_id: str, root: Path) -> str | None:
        return None

    def delete_repository(self, repo_id: str) -> None:
        return None

    def discover_repositories(self) -> list[dict[str, Any]]:
        return []

    def repository_manifest(self, repo_id: str) -> dict[str, Any] | None:
        return None

    def list_repository_files(
        self, repo_id: str, limit: int = 500
    ) -> dict[str, Any] | None:
        return None

    def restore_repository(self, repo_id: str) -> Path:
        raise ValueError("This model is not stored in S3.")

    def evict_repository_cache(self, repo_id: str) -> None:
        raise ValueError("Filesystem models do not have a separate durable copy.")


class S3ModelStorage(FilesystemModelStorage):
    backend = "s3"
    remote = True

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        transfer_config: Any | None = None,
    ):
        super().__init__(settings)
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required when MODEL_STORAGE_BACKEND=s3.")
        if settings.s3_addressing_style not in {"auto", "path", "virtual"}:
            raise ValueError("S3_ADDRESSING_STYLE must be auto, path, or virtual.")
        if bool(settings.s3_access_key_id) != bool(settings.s3_secret_access_key):
            raise ValueError(
                "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together."
            )
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix
        self.endpoint = settings.s3_endpoint_url
        self._lock = threading.RLock()
        if client is None:
            try:
                import boto3
                from boto3.s3.transfer import TransferConfig
                from botocore.config import Config
            except ImportError as error:
                raise RuntimeError(
                    "S3 storage requires boto3. Install backend requirements first."
                ) from error
            client_options: dict[str, Any] = {
                "service_name": "s3",
                "use_ssl": settings.s3_use_ssl,
                "verify": settings.s3_verify_ssl,
                "config": Config(
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"max_attempts": 5, "mode": "standard"},
                    s3={"addressing_style": settings.s3_addressing_style},
                ),
            }
            if settings.s3_endpoint_url:
                client_options["endpoint_url"] = settings.s3_endpoint_url
            if settings.s3_region:
                client_options["region_name"] = settings.s3_region
            if settings.s3_access_key_id:
                client_options["aws_access_key_id"] = settings.s3_access_key_id
            if settings.s3_secret_access_key:
                client_options["aws_secret_access_key"] = settings.s3_secret_access_key
            if settings.s3_session_token:
                client_options["aws_session_token"] = settings.s3_session_token
            client = boto3.client(**client_options)

            def _inject_md5(request, **kwargs):
                if request.body and "Content-MD5" not in request.headers:
                    md5_hash = hashlib.md5(request.body).digest()
                    request.headers["Content-MD5"] = base64.b64encode(md5_hash).decode("utf-8")

            client.meta.events.register("request-created.s3.DeleteObjects", _inject_md5)

            chunk_bytes = settings.s3_multipart_chunk_mb * 1024**2
            transfer_config = TransferConfig(
                multipart_threshold=chunk_bytes,
                multipart_chunksize=chunk_bytes,
                max_concurrency=settings.s3_max_concurrency,
                use_threads=True,
            )
        self.client = client
        self.transfer_config = transfer_config

    def _prefix(self, value: str = "") -> str:
        parts = [part for part in (self.prefix, value.strip("/")) if part]
        return "/".join(parts)

    def _repo_prefix(self, repo_id: str) -> str:
        return f"{self._prefix(validate_repo_id(repo_id))}/"

    def _manifest_key(self, repo_id: str) -> str:
        return f"{self._repo_prefix(repo_id)}{MANIFEST_NAME}"

    def remote_uri(self, repo_id: str) -> str:
        return f"s3://{self.bucket}/{self._prefix(validate_repo_id(repo_id))}"

    def _objects(self, prefix: str) -> Iterable[dict[str, Any]]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            yield from page.get("Contents") or []

    def _delete_keys(self, keys: Iterable[str]) -> None:
        batch: list[dict[str, str]] = []
        for key in keys:
            batch.append({"Key": key})
            if len(batch) == 1000:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                batch = []
        if batch:
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": batch, "Quiet": True},
            )

    def _transfer_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self.transfer_config is not None:
            options["Config"] = self.transfer_config
        return options

    def _upload_options(self) -> dict[str, Any]:
        options = self._transfer_options()
        if self.settings.s3_storage_class:
            options["ExtraArgs"] = {"StorageClass": self.settings.s3_storage_class}
        return options

    def health(self) -> dict[str, Any]:
        error = None
        connected = False
        try:
            self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=self._prefix(),
                MaxKeys=1,
            )
            connected = True
        except Exception as exception:
            error = str(exception).strip() or exception.__class__.__name__
            for secret in (
                self.settings.s3_access_key_id,
                self.settings.s3_secret_access_key,
                self.settings.s3_session_token,
            ):
                if secret:
                    error = error.replace(secret, "[redacted]")
            error = error[:300]
        return {
            "backend": self.backend,
            "enabled": True,
            "connected": connected,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "endpoint": _public_endpoint(self.endpoint),
            "error": error,
        }

    def _local_files(self, root: Path) -> list[tuple[Path, str]]:
        files: list[tuple[Path, str]] = []
        for current, directories, names in os.walk(root):
            directories[:] = [
                name
                for name in directories
                if name not in {".cache", "__pycache__"} and not name.startswith(".")
            ]
            for name in names:
                path = Path(current) / name
                if (
                    path.is_symlink()
                    or any(name.endswith(suffix) for suffix in PART_SUFFIXES)
                ):
                    continue
                files.append((path, path.relative_to(root).as_posix()))
        return files

    def sync_repository(self, repo_id: str, root: Path) -> str:
        validated = validate_repo_id(repo_id)
        expected_root = repository_path(validated, self.settings.model_storage)
        if root.resolve() != expected_root:
            raise ValueError("Repository cache path does not match its repository ID.")
        manifest_path = root / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("A complete repository manifest is required for S3 sync.") from error
        if manifest.get("status") != "complete" or manifest.get("repo_id") != validated:
            raise ValueError("Only complete repositories can be synced to S3.")

        config_path = root / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                config = {}
        except (OSError, json.JSONDecodeError):
            config = {}
        manifest["storage_backend"] = "s3"
        manifest["remote_uri"] = self.remote_uri(validated)
        manifest["config"] = {
            "architectures": config.get("architectures"),
            "model_type": config.get("model_type"),
            "torch_dtype": config.get("torch_dtype"),
            "vocab_size": config.get("vocab_size"),
        }
        if not manifest.get("pipeline_tag") and config.get("model_type"):
            manifest["pipeline_tag"] = config["model_type"]
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with self._lock:
            repo_prefix = self._repo_prefix(validated)
            manifest_key = self._manifest_key(validated)
            self._delete_keys([manifest_key])
            local_files = self._local_files(root)
            intended_keys = {f"{repo_prefix}{relative}" for _, relative in local_files}
            for path, relative in local_files:
                if relative == MANIFEST_NAME:
                    continue
                self.client.upload_file(
                    str(path),
                    self.bucket,
                    f"{repo_prefix}{relative}",
                    **self._upload_options(),
                )
            existing_keys = {item["Key"] for item in self._objects(repo_prefix)}
            self._delete_keys(existing_keys - intended_keys)
            self.client.upload_file(
                str(manifest_path),
                self.bucket,
                manifest_key,
                **self._upload_options(),
            )
        return self.remote_uri(validated)

    def delete_repository(self, repo_id: str) -> None:
        with self._lock:
            keys = [item["Key"] for item in self._objects(self._repo_prefix(repo_id))]
            self._delete_keys(keys)

    def repository_manifest(self, repo_id: str) -> dict[str, Any] | None:
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=self._manifest_key(repo_id),
            )
            body = response["Body"]
            payload = body.read()
            close = getattr(body, "close", None)
            if close:
                close()
            manifest = json.loads(payload.decode("utf-8"))
            return manifest if isinstance(manifest, dict) else None
        except Exception:
            return None

    def discover_repositories(self) -> list[dict[str, Any]]:
        root_prefix = f"{self._prefix()}/" if self._prefix() else ""
        records: list[dict[str, Any]] = []
        for item in self._objects(root_prefix):
            key = item.get("Key") or ""
            if not key.endswith(f"/{MANIFEST_NAME}"):
                continue
            relative_key = key[len(root_prefix) :] if root_prefix else key
            parts = PurePosixPath(relative_key).parts
            if len(parts) != 3 or parts[-1] != MANIFEST_NAME:
                continue
            repo_id = f"{parts[0]}/{parts[1]}"
            try:
                validate_repo_id(repo_id)
            except ValueError:
                continue
            manifest = self.repository_manifest(repo_id)
            if (
                not manifest
                or manifest.get("status") != "complete"
                or manifest.get("repo_id") != repo_id
            ):
                continue
            cache_root = repository_path(repo_id, self.settings.model_storage)
            cached_manifest = cache_root / MANIFEST_NAME
            cached = False
            if cached_manifest.is_file():
                try:
                    cached = (
                        json.loads(cached_manifest.read_text(encoding="utf-8")).get("status")
                        == "complete"
                    )
                except (OSError, json.JSONDecodeError):
                    cached = False
            records.append(
                {
                    "repo_id": repo_id,
                    "relative_path": repo_id,
                    "size_bytes": int(manifest.get("total_bytes") or 0),
                    "file_count": int(manifest.get("file_count") or 0),
                    "modified_at": (
                        manifest.get("uploaded_at")
                        or manifest.get("downloaded_at")
                        or _iso(item.get("LastModified"))
                    ),
                    "downloaded_at": manifest.get("downloaded_at"),
                    "revision": manifest.get("revision"),
                    "sha": manifest.get("sha"),
                    "pipeline_tag": manifest.get("pipeline_tag"),
                    "library_name": manifest.get("library_name"),
                    "license": manifest.get("license"),
                    "tags": manifest.get("tags") or [],
                    "config": manifest.get("config") or {},
                    "source_url": manifest.get("source_url"),
                    "source": manifest.get("source"),
                    "owner_id": manifest.get("owner_id"),
                    "managed": True,
                    "storage_backend": "s3",
                    "cached": cached,
                    "remote_uri": self.remote_uri(repo_id),
                }
            )
        return records

    def list_repository_files(
        self, repo_id: str, limit: int = 500
    ) -> dict[str, Any] | None:
        prefix = self._repo_prefix(repo_id)
        files: list[dict[str, Any]] = []
        unsafe_count = 0
        for item in self._objects(prefix):
            key = item.get("Key") or ""
            relative = key[len(prefix) :]
            if not relative:
                continue
            _safe_relative_key(relative)
            unsafe = PurePosixPath(relative).suffix.lower() in UNSAFE_EXTENSIONS
            unsafe_count += int(unsafe)
            files.append(
                {
                    "path": relative,
                    "size": int(item.get("Size") or 0),
                    "modified_at": _iso(item.get("LastModified")),
                    "unsafe_serialization": unsafe,
                }
            )
            if len(files) >= limit:
                break
        if not files:
            return None
        files.sort(key=lambda file: (-file["size"], file["path"]))
        return {
            "files": files,
            "unsafe_file_count": unsafe_count,
            "truncated": len(files) >= limit,
        }

    def restore_repository(self, repo_id: str) -> Path:
        validated = validate_repo_id(repo_id)
        manifest = self.repository_manifest(validated)
        if not manifest or manifest.get("status") != "complete":
            raise FileNotFoundError("A complete S3 copy of this repository was not found.")
        prefix = self._repo_prefix(validated)
        objects = list(self._objects(prefix))
        if not objects:
            raise FileNotFoundError("The S3 repository is empty.")
        root = repository_path(validated, self.settings.model_storage)
        with self._lock:
            root.mkdir(parents=True, exist_ok=True)
            ordered = sorted(
                objects,
                key=lambda item: (item.get("Key", "").endswith(f"/{MANIFEST_NAME}"), item.get("Key", "")),
            )
            for item in ordered:
                key = item.get("Key") or ""
                relative_value = key[len(prefix) :]
                if not relative_value:
                    continue
                relative = _safe_relative_key(relative_value)
                target = root.joinpath(*relative.parts)
                resolved_parent = target.parent.resolve()
                if root != resolved_parent and root not in resolved_parent.parents:
                    raise ValueError("S3 object key escapes the repository cache.")
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(f".{target.name}.hugginghack-s3-part")
                if target.is_symlink() or partial.is_symlink():
                    raise ValueError("S3 restore cannot overwrite a symbolic link.")
                self.client.download_file(
                    self.bucket,
                    key,
                    str(partial),
                    **self._transfer_options(),
                )
                partial.replace(target)
        return root

    def evict_repository_cache(self, repo_id: str) -> None:
        with self._lock:
            manifest = self.repository_manifest(repo_id)
            if not manifest or manifest.get("status") != "complete":
                raise ValueError("The local cache cannot be removed until S3 has a complete copy.")
            root = repository_path(repo_id, self.settings.model_storage)
            if root.exists():
                shutil.rmtree(root)


def create_model_storage(settings: Settings) -> FilesystemModelStorage:
    if settings.model_storage_backend == "filesystem":
        return FilesystemModelStorage(settings)
    if settings.model_storage_backend == "s3":
        return S3ModelStorage(settings)
    raise ValueError("MODEL_STORAGE_BACKEND must be filesystem or s3.")

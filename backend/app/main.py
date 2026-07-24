from __future__ import annotations

import hmac
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .auth import AuthService, utc_iso
from .config import settings, validate_repo_id
from .database import INTEGRITY_ERRORS, Database
from .downloads import DownloadManager
from .hub_service import HubService
from .indexer import LocalModelIndexer
from .runtimes import RuntimeManager
from .storage import create_model_storage
from .uploads import UploadManager


database = Database(settings.database_target)
hub = HubService(settings)
indexer = LocalModelIndexer(settings, database)
model_storage = create_model_storage(settings)
downloads = DownloadManager(settings, database, hub, indexer, model_storage)
auth = AuthService(settings, database)
uploads = UploadManager(settings, database, indexer, model_storage)
runtimes = RuntimeManager(settings, database)


def refresh_model_index() -> dict[str, Any]:
    result = indexer.scan()
    remote_models: list[dict[str, Any]] = []
    remote_error = None
    if model_storage.remote:
        try:
            remote_models = model_storage.discover_repositories()
            for model in remote_models:
                if model.get("source") == "user-upload":
                    owner_id = model.get("owner_id")
                    if (
                        not owner_id
                        or not database.get_owned_repository(model["repo_id"], owner_id)
                    ):
                        continue
                indexer.index_remote(model)
        except Exception as error:
            remote_error = (str(error).strip() or error.__class__.__name__)[:500]
    models = database.list_local_models()
    return {
        "count": len(models),
        "models": models,
        "local_count": result["count"],
        "remote_count": len(remote_models),
        "remote_error": remote_error,
        "scanned_at": result["scanned_at"],
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    database.initialize()
    database.fail_unfinished_runtime_jobs(utc_iso())
    auth.ensure_local_user()
    await run_in_threadpool(refresh_model_index)
    downloads.resume_unfinished()
    yield
    downloads.shutdown()
    runtimes.shutdown()
    hub.close()


app = FastAPI(
    title="HuggingHack API",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "Upload-Offset",
        "Upload-Length",
    ],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class SetupRequest(CredentialsRequest):
    display_name: str = Field(default="", max_length=80)


class CreateUserRequest(SetupRequest):
    pass


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class DownloadRequest(BaseModel):
    repo_id: str
    revision: str = Field(default="main", max_length=200)
    allow_patterns: list[str] = Field(default_factory=list, max_length=50)
    ignore_patterns: list[str] = Field(default_factory=list, max_length=50)
    mode: Literal["full", "safetensors", "gguf", "metadata", "custom"] = "full"

    @field_validator("repo_id")
    @classmethod
    def repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)

    @field_validator("allow_patterns", "ignore_patterns")
    @classmethod
    def patterns_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            item = value.strip()
            if len(item) > 200:
                raise ValueError("File patterns must be 200 characters or fewer.")
            if item:
                cleaned.append(item)
        return cleaned


class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)


class SavedModelRequest(BaseModel):
    repo_id: str
    note: str = Field(default="", max_length=1000)
    collection_ids: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("repo_id")
    @classmethod
    def saved_repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)


class RepositoryRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=96)
    description: str = Field(default="", max_length=500)
    visibility: Literal["private", "shared"] = "private"


class RepositoryUpdateRequest(BaseModel):
    description: str = Field(default="", max_length=500)
    visibility: Literal["private", "shared"] = "private"


class DeleteRepositoryRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)


class RuntimeLoadRequest(BaseModel):
    repo_id: str
    runtime_model_name: str | None = Field(default=None, max_length=128)
    source_file: str | None = Field(default=None, max_length=500)

    @field_validator("repo_id")
    @classmethod
    def runtime_repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)


def session_for_request(request: Request) -> dict[str, Any] | None:
    return auth.session(request.cookies.get(auth.cookie_name))


def require_user(request: Request) -> dict[str, Any]:
    if auth.setup_required():
        raise HTTPException(status_code=428, detail="Create the owner account first.")
    session = session_for_request(request)
    if not session or not session.get("user"):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    request.state.auth_session = session
    return session["user"]


def require_write_user(
    request: Request, user: Annotated[dict[str, Any], Depends(require_user)]
) -> dict[str, Any]:
    session = request.state.auth_session
    if not auth.verify_csrf(session, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=403, detail="Security token is missing or expired.")
    return user


def require_admin(
    user: Annotated[dict[str, Any], Depends(require_write_user)]
) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(require_user)]
WriteUser = Annotated[dict[str, Any], Depends(require_write_user)]
AdminUser = Annotated[dict[str, Any], Depends(require_admin)]


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        auth.cookie_name,
        raw_token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def auth_payload(session: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "accounts_enabled": settings.accounts_enabled,
        "setup_required": auth.setup_required(),
        "user": session.get("user") if session else None,
        "csrf_token": session.get("csrf_token") if session else None,
    }


@app.get("/api/health")
def health() -> dict:
    settings.ensure_directories()
    usage = shutil.disk_usage(settings.model_storage)
    object_storage = model_storage.health()
    return {
        "status": "ok" if object_storage["connected"] else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "database_backend": database.backend,
        "storage": {
            "path": str(settings.model_storage),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "writable": os_access_writable(settings.model_storage),
        },
        "object_storage": object_storage,
        "hf_token_configured": bool(settings.hf_token),
        "hf_endpoint": settings.hf_endpoint,
        "accounts_enabled": settings.accounts_enabled,
        "upload_chunk_bytes": settings.upload_chunk_mb * 1024**2,
        "max_upload_size_bytes": settings.max_upload_size_gb * 1024**3,
        "runtime_target_count": len(runtimes.targets),
        "runtime_api_token_configured": bool(settings.runtime_api_token),
    }


def os_access_writable(path: Path) -> bool:
    try:
        probe = path / ".hugginghack-write-test"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    return auth_payload(session_for_request(request))


@app.post("/api/auth/setup", status_code=201)
def setup_account(payload: SetupRequest, response: Response) -> dict:
    if not settings.accounts_enabled:
        raise HTTPException(status_code=409, detail="Accounts are disabled.")
    if not auth.setup_required():
        raise HTTPException(status_code=409, detail="The owner account already exists.")
    try:
        user = auth.create_owner(payload.username, payload.display_name, payload.password)
    except (ValueError, *INTEGRITY_ERRORS) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    raw_token, csrf_token = auth.create_session(user["id"])
    set_session_cookie(response, raw_token)
    return auth_payload({"user": user, "csrf_token": csrf_token})


@app.post("/api/auth/login")
def login(payload: CredentialsRequest, request: Request, response: Response) -> dict:
    if not settings.accounts_enabled:
        raise HTTPException(status_code=409, detail="Accounts are disabled.")
    client = request.client.host if request.client else "unknown"
    try:
        user = auth.authenticate(
            payload.username, payload.password, f"{client}:{payload.username.lower()}"
        )
    except ValueError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    if not user:
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    raw_token, csrf_token = auth.create_session(user["id"])
    set_session_cookie(response, raw_token)
    public_user = database.get_user(user["id"], include_secret=False)
    return auth_payload({"user": public_user, "csrf_token": csrf_token})


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, _: WriteUser) -> dict:
    auth.revoke(request.cookies.get(auth.cookie_name))
    response.delete_cookie(auth.cookie_name, path="/")
    return {"status": "signed_out"}


@app.get("/api/users")
def list_users(user: CurrentUser) -> dict:
    if user["role"] != "admin":
        return {"items": [user]}
    return {"items": database.list_users()}


@app.post("/api/users", status_code=201)
def create_user(payload: CreateUserRequest, _: AdminUser) -> dict:
    try:
        return auth.create_user(
            payload.username, payload.display_name, payload.password, role="member"
        )
    except (ValueError, *INTEGRITY_ERRORS) as error:
        detail = (
            "That username is already in use."
            if isinstance(error, INTEGRITY_ERRORS)
            else str(error)
        )
        raise HTTPException(status_code=400, detail=detail) from error


@app.patch("/api/account/password")
def change_password(
    payload: PasswordChangeRequest, request: Request, user: WriteUser
) -> dict:
    raw_token = request.cookies.get(auth.cookie_name)
    if not raw_token or not settings.accounts_enabled:
        raise HTTPException(
            status_code=409,
            detail="Password changes are unavailable when accounts are disabled.",
        )
    try:
        auth.change_password(
            user["id"], payload.current_password, payload.new_password, raw_token
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "password_changed"}


@app.get("/api/hub/models")
async def search_hub_models(
    user: CurrentUser,
    search: Annotated[str, Query(max_length=200)] = "",
    sort: Literal["trending", "downloads", "updated", "likes"] = "trending",
    task: Annotated[str, Query(max_length=100)] = "",
    library: Annotated[str, Query(max_length=100)] = "",
    app_filter: Annotated[str, Query(alias="app", max_length=100)] = "",
    parameters: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> dict:
    try:
        items = await run_in_threadpool(
            hub.search_models,
            search,
            sort,
            task,
            library,
            app_filter,
            parameters,
            limit,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Hugging Face Hub request failed: {error}"
        ) from error
    local_ids = {model["repo_id"] for model in database.list_local_models()}
    saved_ids = database.saved_repo_ids(user["id"])
    for item in items:
        item["local"] = item["id"] in local_ids
        item["saved"] = item["id"] in saved_ids
    return {"items": items, "count": len(items)}


@app.get("/api/hub/gguf-range")
async def hub_gguf_range(
    request: Request,
    _: CurrentUser,
    repo_id: Annotated[str, Query(max_length=200)],
    filename: Annotated[str, Query(max_length=500)],
    revision: Annotated[str, Query(max_length=200)] = "main",
) -> Response:
    try:
        result = await run_in_threadpool(
            hub.read_gguf_range,
            repo_id,
            filename,
            revision,
            request.headers.get("Range"),
        )
        return Response(
            content=result["content"],
            status_code=result["status_code"],
            media_type="application/octet-stream",
            headers=result["headers"],
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=416, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Unable to read GGUF metadata: {error}"
        ) from error


@app.get("/api/hub/models/{repo_id:path}")
async def hub_model(repo_id: str, user: CurrentUser, revision: str = "main") -> dict:
    try:
        validated = validate_repo_id(repo_id)
        details = await run_in_threadpool(hub.model_details, validated, revision)
        details["model_card"] = await run_in_threadpool(
            hub.read_model_card, validated, revision
        )
        details["local"] = database.get_local_model(validated) is not None
        details["saved"] = validated in database.saved_repo_ids(user["id"])
        return details
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Unable to load model: {error}") from error


def can_access_download(download: dict[str, Any], user: dict[str, Any]) -> bool:
    return (
        download.get("user_id") == user["id"]
        or (user["role"] == "admin" and download.get("user_id") is None)
    )


@app.get("/api/downloads")
def list_downloads(user: CurrentUser) -> dict:
    items = database.list_downloads(
        user_id=user["id"], include_unowned=user["role"] == "admin"
    )
    return {
        "items": items,
        "active": sum(
            item["status"] in {"queued", "preparing", "downloading"} for item in items
        ),
    }


@app.get("/api/downloads/{download_id}")
def get_download(download_id: str, user: CurrentUser) -> dict:
    download = database.get_download(download_id)
    if not download or not can_access_download(download, user):
        raise HTTPException(status_code=404, detail="Download not found")
    return download


@app.post("/api/downloads", status_code=202)
def start_download(payload: DownloadRequest, user: WriteUser) -> dict:
    if database.get_owned_repository(payload.repo_id):
        raise HTTPException(
            status_code=409,
            detail="An account-owned repository already uses this storage path.",
        )
    try:
        return downloads.queue(
            payload.repo_id,
            payload.revision,
            payload.allow_patterns,
            payload.ignore_patterns,
            payload.mode,
            user_id=user["id"],
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/downloads/{download_id}/cancel")
def cancel_download(download_id: str, user: WriteUser) -> dict:
    existing = database.get_download(download_id)
    if not existing or not can_access_download(existing, user):
        raise HTTPException(status_code=404, detail="Download not found")
    try:
        download = downloads.cancel(download_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return download


@app.get("/api/local-models")
def local_models(
    user: CurrentUser, query: Annotated[str, Query(max_length=200)] = ""
) -> dict:
    items = database.list_visible_local_models(user["id"], query)
    return {
        "items": items,
        "count": len(items),
        "total_bytes": sum(item["size_bytes"] for item in items),
    }


@app.post("/api/local-models/scan")
async def scan_local_models(_: WriteUser) -> dict:
    return await run_in_threadpool(refresh_model_index)


def visible_model(repo_id: str, user_id: str) -> dict[str, Any]:
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    model = database.get_visible_local_model(user_id, validated)
    if not model:
        raise HTTPException(status_code=404, detail="Local model not found")
    return model


@app.post("/api/local-models/{repo_id:path}/restore")
async def restore_local_model(repo_id: str, user: WriteUser) -> dict:
    model = visible_model(repo_id, user["id"])
    if model["storage_backend"] != "s3":
        raise HTTPException(status_code=409, detail="This model is not backed by S3.")
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail="Wait for the active download to finish before restoring this cache.",
        )
    try:
        root = await run_in_threadpool(model_storage.restore_repository, model["repo_id"])
        await run_in_threadpool(indexer.index_path, root)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    result = indexer.files_for_model(model["repo_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Local model not found")
    return result


@app.delete("/api/local-models/{repo_id:path}/cache")
async def evict_local_model_cache(repo_id: str, user: WriteUser) -> dict:
    model = visible_model(repo_id, user["id"])
    if model["storage_backend"] != "s3":
        raise HTTPException(status_code=409, detail="Only S3-backed models have a removable cache.")
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail="Wait for the active download to finish before removing this cache.",
        )
    try:
        await run_in_threadpool(model_storage.evict_repository_cache, model["repo_id"])
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    updated = database.set_local_model_cached(model["repo_id"], False)
    return {"status": "evicted", "model": updated}


@app.get("/api/local-models/{repo_id:path}")
def local_model(repo_id: str, user: CurrentUser) -> dict:
    model = visible_model(repo_id, user["id"])
    root = settings.model_storage / model["relative_path"]
    if model["storage_backend"] == "s3" and (not model["cached"] or not root.is_dir()):
        database.set_local_model_cached(model["repo_id"], False)
        remote = model_storage.list_repository_files(model["repo_id"])
        if not remote:
            raise HTTPException(status_code=404, detail="S3 model files were not found.")
        remote["model"] = database.get_local_model(model["repo_id"])
        return remote
    result = indexer.files_for_model(model["repo_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Local model not found")
    return result


def require_runtime_admin(user: dict[str, Any]) -> None:
    if user["role"] != "admin":
        raise HTTPException(
            status_code=403, detail="Administrator access is required."
        )


def runtime_api_principal(request: Request) -> dict[str, Any] | None:
    expected = settings.runtime_api_token
    authorization = request.headers.get("Authorization") or ""
    if not expected or not authorization.startswith("Bearer "):
        return None
    supplied = authorization.removeprefix("Bearer ")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid runtime API token.")
    return {"id": None, "role": "admin", "runtime_api": True}


def require_runtime_reader(request: Request) -> dict[str, Any]:
    principal = runtime_api_principal(request)
    if principal:
        return principal
    user = require_user(request)
    require_runtime_admin(user)
    return user


def require_runtime_writer(request: Request) -> dict[str, Any]:
    principal = runtime_api_principal(request)
    if principal:
        return principal
    user = require_user(request)
    session = request.state.auth_session
    if not auth.verify_csrf(session, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=403, detail="Security token is missing or expired.")
    require_runtime_admin(user)
    return user


RuntimeReader = Annotated[dict[str, Any], Depends(require_runtime_reader)]
RuntimeWriter = Annotated[dict[str, Any], Depends(require_runtime_writer)]


@app.get("/api/runtimes")
def list_runtimes(_: RuntimeReader) -> dict:
    return {"items": runtimes.public_targets()}


@app.get("/api/runtime-jobs")
def list_runtime_jobs(
    _: RuntimeReader, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> dict:
    items = database.list_runtime_jobs(limit)
    return {
        "items": items,
        "active": sum(
            item["status"] in {"queued", "preparing", "transferring", "loading"}
            for item in items
        ),
    }


@app.get("/api/runtime-jobs/{job_id}")
def get_runtime_job(job_id: str, _: RuntimeReader) -> dict:
    job = database.get_runtime_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Runtime job not found.")
    return job


@app.post("/api/runtimes/{target_id}/load", status_code=202)
def load_runtime_model(
    target_id: str, payload: RuntimeLoadRequest, principal: RuntimeWriter
) -> dict:
    model = (
        database.get_local_model(payload.repo_id)
        if principal.get("runtime_api")
        else visible_model(payload.repo_id, principal["id"])
    )
    if not model:
        raise HTTPException(status_code=404, detail="Local model not found")
    try:
        return runtimes.queue(
            target_id,
            model,
            payload.runtime_model_name,
            payload.source_file,
            principal["id"],
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/collections")
def list_collections(user: CurrentUser) -> dict:
    return {"items": database.list_collections(user["id"])}


@app.post("/api/collections", status_code=201)
def create_collection(payload: CollectionRequest, user: WriteUser) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Collection name is required.")
    timestamp = utc_iso()
    try:
        return database.create_collection(
            {
                "id": uuid.uuid4().hex,
                "user_id": user["id"],
                "name": name,
                "description": payload.description.strip(),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    except INTEGRITY_ERRORS as error:
        raise HTTPException(
            status_code=409, detail="You already have a collection with that name."
        ) from error


@app.delete("/api/collections/{collection_id}")
def delete_collection(collection_id: str, user: WriteUser) -> dict:
    if not database.delete_collection(collection_id, user["id"]):
        raise HTTPException(status_code=404, detail="Collection not found.")
    return {"status": "deleted"}


def safe_saved_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "author",
        "pipeline_tag",
        "library_name",
        "license",
        "parameter_count",
        "last_modified",
        "local",
    }
    result = {key: value for key, value in metadata.items() if key in allowed}
    if len(json.dumps(result)) > 16_000:
        raise ValueError("Saved model metadata is too large.")
    return result


@app.get("/api/saved-models")
def list_saved_models(
    user: CurrentUser,
    query: Annotated[str, Query(max_length=200)] = "",
    collection_id: Annotated[str, Query(max_length=100)] = "",
) -> dict:
    items = database.list_saved_models(user["id"], query, collection_id)
    return {"items": items, "count": len(items)}


@app.post("/api/saved-models")
def save_model(payload: SavedModelRequest, user: WriteUser) -> dict:
    timestamp = utc_iso()
    existing = database.get_saved_model(user["id"], payload.repo_id)
    try:
        return database.save_model(
            {
                "id": existing["id"] if existing else uuid.uuid4().hex,
                "user_id": user["id"],
                "repo_id": payload.repo_id,
                "note": payload.note.strip(),
                "metadata_json": json.dumps(safe_saved_metadata(payload.metadata)),
                "created_at": existing["created_at"] if existing else timestamp,
                "updated_at": timestamp,
            },
            payload.collection_ids,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete("/api/saved-models/{repo_id:path}")
def unsave_model(repo_id: str, user: WriteUser) -> dict:
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not database.delete_saved_model(user["id"], validated):
        raise HTTPException(status_code=404, detail="Saved model not found.")
    return {"status": "removed"}


@app.get("/api/uploads/repositories")
def list_upload_repositories(user: CurrentUser) -> dict:
    return {"items": database.list_owned_repositories(user["id"])}


@app.post("/api/uploads/repositories", status_code=201)
def create_upload_repository(payload: RepositoryRequest, user: WriteUser) -> dict:
    try:
        return uploads.create_repository(
            user, payload.slug, payload.description, payload.visibility
        )
    except (ValueError, FileExistsError, *INTEGRITY_ERRORS) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.patch("/api/uploads/repositories")
def update_upload_repository(
    payload: RepositoryUpdateRequest,
    user: WriteUser,
    repo_id: Annotated[str, Query(max_length=200)],
) -> dict:
    try:
        return uploads.update_repository(
            validate_repo_id(repo_id),
            user["id"],
            payload.description,
            payload.visibility,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/uploads/repositories/files/status")
def upload_file_status(
    user: CurrentUser,
    repo_id: Annotated[str, Query(max_length=200)],
    path: Annotated[str, Query(max_length=500)],
) -> dict:
    try:
        return uploads.file_status(repo_id, user["id"], path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.put("/api/uploads/repositories/files")
async def upload_file_chunk(
    request: Request,
    user: WriteUser,
    repo_id: Annotated[str, Query(max_length=200)],
    path: Annotated[str, Query(max_length=500)],
) -> dict:
    try:
        offset = int(request.headers.get("Upload-Offset", "-1"))
        total = int(request.headers.get("Upload-Length", "-1"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Upload headers are invalid.") from error
    try:
        content_length = int(request.headers.get("Content-Length", "0") or 0)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Content length is invalid.") from error
    if content_length > settings.upload_chunk_mb * 1024**2:
        raise HTTPException(status_code=413, detail="Upload chunk is too large.")
    chunks: list[bytes] = []
    received = 0
    limit = settings.upload_chunk_mb * 1024**2
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail="Upload chunk is too large.")
        chunks.append(chunk)
    payload = b"".join(chunks)
    try:
        return await run_in_threadpool(
            uploads.upload_chunk,
            repo_id,
            user["id"],
            path,
            offset,
            total,
            payload,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/uploads/repositories/finalize")
async def finalize_upload_repository(
    user: WriteUser, repo_id: Annotated[str, Query(max_length=200)]
) -> dict:
    try:
        return await run_in_threadpool(uploads.finalize, repo_id, user["id"])
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.delete("/api/uploads/repositories")
async def delete_upload_repository(
    payload: DeleteRepositoryRequest,
    user: WriteUser,
    repo_id: Annotated[str, Query(max_length=200)],
) -> dict:
    try:
        await run_in_threadpool(
            uploads.delete_repository, repo_id, user["id"], payload.confirmation
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "deleted"}


app_directory = Path(__file__).resolve().parent
static_directory = next(
    (
        candidate
        for candidate in (
            app_directory.parent / "static",
            app_directory.parent.parent / "frontend" / "dist",
        )
        if (candidate / "index.html").is_file()
    ),
    None,
)
if static_directory is not None:
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

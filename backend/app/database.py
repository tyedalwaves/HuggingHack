from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite remains usable when the optional adapter is absent.
    psycopg = None
    dict_row = None


if psycopg is None:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)


NAMED_PARAMETER_PATTERN = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def _postgres_query(query: str, parameters: object = ()) -> str:
    if isinstance(parameters, Mapping):
        return NAMED_PARAMETER_PATTERN.sub(r"%(\1)s", query)
    return query.replace("?", "%s")


class _PostgresConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def __enter__(self) -> _PostgresConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object:
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def execute(
        self,
        query: str,
        parameters: Mapping[str, Any] | Sequence[Any] = (),
    ) -> Any:
        return self._connection.execute(_postgres_query(query, parameters), parameters)

    def executemany(
        self,
        query: str,
        parameters: Iterable[Mapping[str, Any] | Sequence[Any]],
    ) -> Any:
        parameter_rows = list(parameters)
        cursor = self._connection.cursor()
        cursor.executemany(
            _postgres_query(query, parameter_rows[0] if parameter_rows else ()),
            parameter_rows,
        )
        return cursor

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._connection.execute(statement)


DOWNLOAD_FIELDS = {
    "status",
    "total_bytes",
    "downloaded_bytes",
    "progress",
    "speed_bps",
    "error",
    "target_path",
    "metadata_json",
    "updated_at",
    "completed_at",
}

RUNTIME_JOB_FIELDS = {
    "status",
    "total_bytes",
    "processed_bytes",
    "progress",
    "message",
    "error",
    "updated_at",
    "completed_at",
}


class Database:
    def __init__(self, target: Path | str):
        value = str(target).strip()
        self.backend = (
            "postgresql"
            if value.startswith(("postgresql://", "postgres://"))
            else "sqlite"
        )
        self.path = Path(target) if self.backend == "sqlite" else None
        self._database_url = value if self.backend == "postgresql" else None
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection | _PostgresConnection:
        if self.backend == "postgresql":
            if psycopg is None or dict_row is None:
                raise RuntimeError(
                    "PostgreSQL support requires the 'psycopg[binary]' dependency."
                )
            connection = psycopg.connect(self._database_url, row_factory=dict_row)
            return _PostgresConnection(connection)
        assert self.path is not None
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock, self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase
                    ON users(LOWER(username));

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expiry
                    ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    speed_bps REAL NOT NULL DEFAULT 0,
                    error TEXT,
                    target_path TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_downloads_status
                    ON downloads(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS local_models (
                    repo_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    modified_at TEXT NOT NULL,
                    downloaded_at TEXT,
                    revision TEXT,
                    sha TEXT,
                    pipeline_tag TEXT,
                    library_name TEXT,
                    license TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    source_url TEXT,
                    managed INTEGER NOT NULL DEFAULT 0,
                    storage_backend TEXT NOT NULL DEFAULT 'filesystem',
                    cached INTEGER NOT NULL DEFAULT 1,
                    remote_uri TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_local_models_modified
                    ON local_models(modified_at DESC);

                CREATE TABLE IF NOT EXISTS runtime_jobs (
                    id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    target_kind TEXT NOT NULL CHECK (target_kind IN ('ollama', 'vllm')),
                    repo_id TEXT NOT NULL,
                    runtime_model_name TEXT NOT NULL,
                    source_file TEXT,
                    status TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    processed_bytes INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_jobs_created
                    ON runtime_jobs(created_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_jobs_active_model
                    ON runtime_jobs(target_id, repo_id)
                    WHERE status IN ('queued', 'preparing', 'transferring', 'loading');

                CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_jobs_active_vllm
                    ON runtime_jobs(target_id)
                    WHERE target_kind = 'vllm'
                      AND status IN ('queued', 'preparing', 'transferring', 'loading');

                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                );

                CREATE TABLE IF NOT EXISTS saved_models (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    repo_id TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, repo_id)
                );

                CREATE INDEX IF NOT EXISTS idx_saved_models_user_updated
                    ON saved_models(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS collection_items (
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    saved_model_id TEXT NOT NULL REFERENCES saved_models(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(collection_id, saved_model_id)
                );

                CREATE TABLE IF NOT EXISTS owned_repositories (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    repo_id TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'private'
                        CHECK (visibility IN ('private', 'shared')),
                    status TEXT NOT NULL DEFAULT 'uploading'
                        CHECK (status IN ('uploading', 'ready')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_owned_repositories_owner
                    ON owned_repositories(owner_id, updated_at DESC);
                """
            )
            columns = self._column_names(connection, "downloads")
            if "user_id" not in columns:
                connection.execute(
                    "ALTER TABLE downloads ADD COLUMN user_id TEXT "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
            local_model_columns = self._column_names(connection, "local_models")
            if "storage_backend" not in local_model_columns:
                connection.execute(
                    "ALTER TABLE local_models ADD COLUMN storage_backend "
                    "TEXT NOT NULL DEFAULT 'filesystem'"
                )
            if "cached" not in local_model_columns:
                connection.execute(
                    "ALTER TABLE local_models ADD COLUMN cached INTEGER NOT NULL DEFAULT 1"
                )
            if "remote_uri" not in local_model_columns:
                connection.execute("ALTER TABLE local_models ADD COLUMN remote_uri TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_user_created "
                "ON downloads(user_id, created_at DESC)"
            )
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),),
            )

    def _column_names(
        self, connection: sqlite3.Connection | _PostgresConnection, table: str
    ) -> set[str]:
        if self.backend == "sqlite":
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        else:
            rows = connection.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = ?
                """,
                (table,),
            ).fetchall()
        return {row["name"] for row in rows}

    @staticmethod
    def _decode_row(
        row: sqlite3.Row | dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("payload_json", "metadata_json", "tags_json", "config_json"):
            if key in result:
                raw = result.pop(key)
                output_key = key.removesuffix("_json")
                try:
                    result[output_key] = json.loads(raw or "{}")
                except (TypeError, json.JSONDecodeError):
                    result[output_key] = {} if key != "tags_json" else []
        if "managed" in result:
            result["managed"] = bool(result["managed"])
        if "cached" in result:
            result["cached"] = bool(result["cached"])
        return result

    @staticmethod
    def _public_user(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("password_hash", None)
        return result

    def count_users(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            return int(row["count"])

    def create_user(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    id, username, display_name, password_hash, role, created_at, updated_at
                ) VALUES (
                    :id, :username, :display_name, :password_hash, :role, :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_user(record["id"], include_secret=False)

    def get_user(self, user_id: str, include_secret: bool = True) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row and include_secret else self._public_user(row)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY role, username"
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def update_user_password(
        self, user_id: str, password_hash: str, updated_at: str
    ) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE users SET password_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (password_hash, updated_at, user_id),
            )

    def create_session(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    token_hash, user_id, csrf_token, created_at, expires_at
                ) VALUES (
                    :token_hash, :user_id, :csrf_token, :created_at, :expires_at
                )
                """,
                record,
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT sessions.*, users.id AS user_record_id, users.username,
                       users.display_name, users.role, users.created_at AS user_created_at
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["user"] = {
            "id": result.pop("user_record_id"),
            "username": result.pop("username"),
            "display_name": result.pop("display_name"),
            "role": result.pop("role"),
            "created_at": result.pop("user_created_at"),
        }
        return result

    def delete_session(self, token_hash: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_other_sessions(self, user_id: str, keep_token_hash: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user_id, keep_token_hash),
            )

    def create_download(self, record: dict[str, Any]) -> dict[str, Any]:
        params = {**record, "user_id": record.get("user_id")}
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO downloads (
                    id, repo_id, revision, status, total_bytes, downloaded_bytes,
                    progress, speed_bps, error, target_path, payload_json,
                    metadata_json, created_at, updated_at, completed_at, user_id
                ) VALUES (
                    :id, :repo_id, :revision, :status, :total_bytes, :downloaded_bytes,
                    :progress, :speed_bps, :error, :target_path, :payload_json,
                    :metadata_json, :created_at, :updated_at, :completed_at, :user_id
                )
                """,
                params,
            )
        return self.get_download(record["id"])

    def update_download(self, download_id: str, **changes: Any) -> dict[str, Any] | None:
        safe_changes = {key: value for key, value in changes.items() if key in DOWNLOAD_FIELDS}
        if not safe_changes:
            return self.get_download(download_id)
        assignments = ", ".join(f"{key} = :{key}" for key in safe_changes)
        safe_changes["id"] = download_id
        with self._write_lock, self.connect() as connection:
            connection.execute(f"UPDATE downloads SET {assignments} WHERE id = :id", safe_changes)
        return self.get_download(download_id)

    def get_download(self, download_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
        return self._decode_row(row)

    def list_downloads(
        self, limit: int = 100, user_id: str | None = None, include_unowned: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if user_id is None:
                rows = connection.execute(
                    "SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            elif include_unowned:
                rows = connection.execute(
                    """
                    SELECT * FROM downloads
                    WHERE user_id = ? OR user_id IS NULL
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM downloads
                    WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [self._decode_row(row) for row in rows]

    def find_active_download(self, repo_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM downloads
                WHERE repo_id = ? AND status IN ('queued', 'preparing', 'downloading')
                ORDER BY created_at DESC LIMIT 1
                """,
                (repo_id,),
            ).fetchone()
        return self._decode_row(row)

    def unfinished_downloads(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM downloads WHERE status IN ('queued', 'preparing', 'downloading')"
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def create_runtime_job(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_jobs (
                    id, target_id, target_name, target_kind, repo_id,
                    runtime_model_name, source_file, status, total_bytes,
                    processed_bytes, progress, message, error, created_at,
                    updated_at, completed_at, user_id
                ) VALUES (
                    :id, :target_id, :target_name, :target_kind, :repo_id,
                    :runtime_model_name, :source_file, :status, :total_bytes,
                    :processed_bytes, :progress, :message, :error, :created_at,
                    :updated_at, :completed_at, :user_id
                )
                """,
                record,
            )
        return self.get_runtime_job(record["id"])

    def update_runtime_job(
        self, job_id: str, **changes: Any
    ) -> dict[str, Any] | None:
        safe_changes = {
            key: value for key, value in changes.items() if key in RUNTIME_JOB_FIELDS
        }
        if not safe_changes:
            return self.get_runtime_job(job_id)
        assignments = ", ".join(f"{key} = :{key}" for key in safe_changes)
        safe_changes["id"] = job_id
        with self._write_lock, self.connect() as connection:
            connection.execute(
                f"UPDATE runtime_jobs SET {assignments} WHERE id = :id",
                safe_changes,
            )
        return self.get_runtime_job(job_id)

    def get_runtime_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._decode_row(row)

    def list_runtime_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def find_active_runtime_job(
        self, target_id: str, repo_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM runtime_jobs
                WHERE target_id = ? AND repo_id = ?
                  AND status IN ('queued', 'preparing', 'transferring', 'loading')
                ORDER BY created_at DESC LIMIT 1
                """,
                (target_id, repo_id),
            ).fetchone()
        return self._decode_row(row)

    def find_active_runtime_target(
        self, target_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM runtime_jobs
                WHERE target_id = ?
                  AND status IN ('queued', 'preparing', 'transferring', 'loading')
                ORDER BY created_at DESC LIMIT 1
                """,
                (target_id,),
            ).fetchone()
        return self._decode_row(row)

    def fail_unfinished_runtime_jobs(self, updated_at: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE runtime_jobs
                SET status = 'failed',
                    error = 'HuggingHack restarted before this runtime job completed.',
                    message = 'Interrupted',
                    updated_at = ?,
                    completed_at = ?
                WHERE status IN ('queued', 'preparing', 'transferring', 'loading')
                """,
                (updated_at, updated_at),
            )

    def upsert_local_model(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO local_models (
                    repo_id, relative_path, size_bytes, file_count, modified_at,
                    downloaded_at, revision, sha, pipeline_tag, library_name,
                    license, tags_json, config_json, source_url, managed,
                    storage_backend, cached, remote_uri
                ) VALUES (
                    :repo_id, :relative_path, :size_bytes, :file_count, :modified_at,
                    :downloaded_at, :revision, :sha, :pipeline_tag, :library_name,
                    :license, :tags_json, :config_json, :source_url, :managed,
                    :storage_backend, :cached, :remote_uri
                )
                ON CONFLICT(repo_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    size_bytes = excluded.size_bytes,
                    file_count = excluded.file_count,
                    modified_at = excluded.modified_at,
                    downloaded_at = excluded.downloaded_at,
                    revision = excluded.revision,
                    sha = excluded.sha,
                    pipeline_tag = excluded.pipeline_tag,
                    library_name = excluded.library_name,
                    license = excluded.license,
                    tags_json = excluded.tags_json,
                    config_json = excluded.config_json,
                    source_url = excluded.source_url,
                    managed = excluded.managed,
                    storage_backend = excluded.storage_backend,
                    cached = excluded.cached,
                    remote_uri = excluded.remote_uri
                """,
                record,
            )

    def list_local_models(self, query: str = "") -> list[dict[str, Any]]:
        with self.connect() as connection:
            if query:
                rows = connection.execute(
                    """
                    SELECT * FROM local_models
                    WHERE LOWER(repo_id) LIKE LOWER(?)
                       OR LOWER(pipeline_tag) LIKE LOWER(?)
                       OR LOWER(library_name) LIKE LOWER(?)
                    ORDER BY modified_at DESC
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%"),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM local_models ORDER BY modified_at DESC"
                ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_local_model(self, repo_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM local_models WHERE repo_id = ?", (repo_id,)
            ).fetchone()
        return self._decode_row(row)

    def prune_local_models(self, relative_paths: set[str]) -> None:
        with self._write_lock, self.connect() as connection:
            rows = connection.execute(
                "SELECT relative_path FROM local_models WHERE storage_backend = 'filesystem'"
            ).fetchall()
            stale = [row["relative_path"] for row in rows if row["relative_path"] not in relative_paths]
            connection.executemany(
                "DELETE FROM local_models WHERE relative_path = ?",
                ((path,) for path in stale),
            )

    def set_local_model_cached(self, repo_id: str, cached: bool) -> dict[str, Any] | None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "UPDATE local_models SET cached = ? WHERE repo_id = ?",
                (int(cached), repo_id),
            )
        return self.get_local_model(repo_id)

    def list_visible_local_models(
        self, user_id: str, query: str = ""
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [user_id]
        query_clause = ""
        if query:
            query_clause = (
                " AND (LOWER(local_models.repo_id) LIKE LOWER(?) "
                "OR LOWER(local_models.pipeline_tag) LIKE LOWER(?) "
                "OR LOWER(local_models.library_name) LIKE LOWER(?))"
            )
            parameters.extend((f"%{query}%", f"%{query}%", f"%{query}%"))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT local_models.*
                FROM local_models
                LEFT JOIN owned_repositories
                    ON owned_repositories.repo_id = local_models.repo_id
                WHERE (
                    owned_repositories.id IS NULL
                    OR owned_repositories.owner_id = ?
                    OR owned_repositories.visibility = 'shared'
                )
                """
                + query_clause
                + " ORDER BY local_models.modified_at DESC",
                parameters,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_visible_local_model(
        self, user_id: str, repo_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT local_models.*
                FROM local_models
                LEFT JOIN owned_repositories
                    ON owned_repositories.repo_id = local_models.repo_id
                WHERE local_models.repo_id = ?
                  AND (
                    owned_repositories.id IS NULL
                    OR owned_repositories.owner_id = ?
                    OR owned_repositories.visibility = 'shared'
                  )
                """,
                (repo_id, user_id),
            ).fetchone()
        return self._decode_row(row)

    def create_collection(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO collections (
                    id, user_id, name, description, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :name, :description, :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_collection(record["id"], record["user_id"])

    def get_collection(self, collection_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM collections WHERE id = ? AND user_id = ?",
                (collection_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT collections.*, COUNT(collection_items.saved_model_id) AS model_count
                FROM collections
                LEFT JOIN collection_items
                    ON collection_items.collection_id = collections.id
                WHERE collections.user_id = ?
                GROUP BY collections.id
                ORDER BY LOWER(collections.name)
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_collection(self, collection_id: str, user_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM collections WHERE id = ? AND user_id = ?",
                (collection_id, user_id),
            )
        return cursor.rowcount > 0

    def save_model(self, record: dict[str, Any], collection_ids: list[str]) -> dict[str, Any]:
        collection_ids = list(dict.fromkeys(collection_ids))
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO saved_models (
                    id, user_id, repo_id, note, metadata_json, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :repo_id, :note, :metadata_json, :created_at, :updated_at
                )
                ON CONFLICT(user_id, repo_id) DO UPDATE SET
                    note = excluded.note,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                record,
            )
            row = connection.execute(
                "SELECT id FROM saved_models WHERE user_id = ? AND repo_id = ?",
                (record["user_id"], record["repo_id"]),
            ).fetchone()
            saved_id = row["id"]
            connection.execute(
                "DELETE FROM collection_items WHERE saved_model_id = ?", (saved_id,)
            )
            if collection_ids:
                valid = connection.execute(
                    """
                    SELECT id FROM collections
                    WHERE user_id = ? AND id IN ({})
                    """.format(",".join("?" for _ in collection_ids)),
                    (record["user_id"], *collection_ids),
                ).fetchall()
                connection.executemany(
                    """
                    INSERT INTO collection_items (
                        collection_id, saved_model_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (item["id"], saved_id, record["updated_at"])
                        for item in valid
                    ),
                )
        return self.get_saved_model(record["user_id"], record["repo_id"])

    def _saved_collections(
        self,
        connection: sqlite3.Connection | _PostgresConnection,
        saved_id: str,
    ) -> list[str]:
        rows = connection.execute(
            "SELECT collection_id FROM collection_items WHERE saved_model_id = ?",
            (saved_id,),
        ).fetchall()
        return [row["collection_id"] for row in rows]

    def get_saved_model(self, user_id: str, repo_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM saved_models WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchone()
            if not row:
                return None
            result = self._decode_row(row)
            result["collections"] = self._saved_collections(connection, result["id"])
        return result

    def list_saved_models(
        self, user_id: str, query: str = "", collection_id: str = ""
    ) -> list[dict[str, Any]]:
        joins = ""
        clauses = ["saved_models.user_id = ?"]
        parameters: list[Any] = [user_id]
        if collection_id:
            joins = (
                " JOIN collection_items ON collection_items.saved_model_id = saved_models.id"
            )
            clauses.append("collection_items.collection_id = ?")
            parameters.append(collection_id)
        if query:
            clauses.append(
                "(LOWER(saved_models.repo_id) LIKE LOWER(?) "
                "OR LOWER(saved_models.note) LIKE LOWER(?))"
            )
            parameters.extend((f"%{query}%", f"%{query}%"))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT saved_models.* FROM saved_models"
                + joins
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY saved_models.updated_at DESC",
                parameters,
            ).fetchall()
            results = []
            for row in rows:
                item = self._decode_row(row)
                item["collections"] = self._saved_collections(connection, item["id"])
                results.append(item)
        return results

    def saved_repo_ids(self, user_id: str) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT repo_id FROM saved_models WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {row["repo_id"] for row in rows}

    def delete_saved_model(self, user_id: str, repo_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM saved_models WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            )
        return cursor.rowcount > 0

    def create_owned_repository(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO owned_repositories (
                    id, owner_id, repo_id, description, visibility, status,
                    created_at, updated_at
                ) VALUES (
                    :id, :owner_id, :repo_id, :description, :visibility, :status,
                    :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_owned_repository(record["repo_id"], record["owner_id"])

    def get_owned_repository(
        self, repo_id: str, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            if owner_id:
                row = connection.execute(
                    """
                    SELECT owned_repositories.*, users.username AS owner_username,
                           users.display_name AS owner_display_name
                    FROM owned_repositories
                    JOIN users ON users.id = owned_repositories.owner_id
                    WHERE repo_id = ? AND owner_id = ?
                    """,
                    (repo_id, owner_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT owned_repositories.*, users.username AS owner_username,
                           users.display_name AS owner_display_name
                    FROM owned_repositories
                    JOIN users ON users.id = owned_repositories.owner_id
                    WHERE repo_id = ?
                    """,
                    (repo_id,),
                ).fetchone()
        return dict(row) if row else None

    def list_owned_repositories(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT owned_repositories.*, users.username AS owner_username,
                       users.display_name AS owner_display_name,
                       local_models.size_bytes, local_models.file_count,
                       local_models.modified_at
                FROM owned_repositories
                JOIN users ON users.id = owned_repositories.owner_id
                LEFT JOIN local_models ON local_models.repo_id = owned_repositories.repo_id
                WHERE owner_id = ? OR visibility = 'shared'
                ORDER BY owned_repositories.updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_owned_repository(
        self, repo_id: str, owner_id: str, **changes: Any
    ) -> dict[str, Any] | None:
        allowed = {
            key: value
            for key, value in changes.items()
            if key in {"description", "visibility", "status", "updated_at"}
        }
        if not allowed:
            return self.get_owned_repository(repo_id, owner_id)
        assignments = ", ".join(f"{key} = :{key}" for key in allowed)
        parameters = {**allowed, "repo_id": repo_id, "owner_id": owner_id}
        with self._write_lock, self.connect() as connection:
            connection.execute(
                f"""
                UPDATE owned_repositories SET {assignments}
                WHERE repo_id = :repo_id AND owner_id = :owner_id
                """,
                parameters,
            )
        return self.get_owned_repository(repo_id, owner_id)

    def delete_owned_repository(self, repo_id: str, owner_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            owned = connection.execute(
                "SELECT 1 FROM owned_repositories WHERE repo_id = ? AND owner_id = ?",
                (repo_id, owner_id),
            ).fetchone()
            if not owned:
                return False
            cursor = connection.execute(
                "DELETE FROM owned_repositories WHERE repo_id = ? AND owner_id = ?",
                (repo_id, owner_id),
            )
            connection.execute("DELETE FROM local_models WHERE repo_id = ?", (repo_id,))
        return cursor.rowcount > 0

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list_env(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return parsed


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    metadata_db: str
    cors_origins: list[str]
    serve_frontend: bool
    frontend_dist: Path
    max_package_scan_files: int
    max_upload_bytes: int
    storage_backend: str
    storage_uri: str
    auth_token: str | None
    admin_password: str | None
    session_ttl_hours: int
    login_max_failures: int
    login_lockout_seconds: int
    log_retention_days: int
    maintenance_interval_seconds: int
    pipeline_workers: int
    external_command_timeout_seconds: int
    external_command_log_max_chars: int
    allow_shell_commands: bool


def load_settings() -> Settings:
    workspace_root = Path(os.environ.get("SCENARA_MODEL_WORKSPACE", Path.cwd())).resolve()
    # 默认使用持久化 SQLite 文件：':memory:' 会在服务重启时静默清空全部元数据，
    # 仅当显式配置时才使用（/health 会标记 metadata_persistent=false）。
    metadata_db = os.environ.get("SCENARA_MODEL_METADATA_DB", "artifacts/scenara_model.sqlite3")
    if metadata_db != ":memory:" and not metadata_db.startswith(("postgresql://", "postgres://")) and not Path(metadata_db).is_absolute():
        metadata_db = str(workspace_root / metadata_db)
    return Settings(
        workspace_root=workspace_root,
        metadata_db=metadata_db,
        cors_origins=_list_env("SCENARA_MODEL_CORS_ORIGINS", ["*"]),
        serve_frontend=_bool_env("SCENARA_MODEL_SERVE_FRONTEND", True),
        frontend_dist=workspace_root / os.environ.get("SCENARA_MODEL_FRONTEND_DIST", "frontend/dist"),
        max_package_scan_files=_int_env("SCENARA_MODEL_MAX_PACKAGE_SCAN_FILES", 500),
        max_upload_bytes=_int_env("SCENARA_MODEL_MAX_UPLOAD_BYTES", 500 * 1024 * 1024),
        storage_backend=os.environ.get("SCENARA_MODEL_STORAGE_BACKEND", "local"),
        storage_uri=os.environ.get("SCENARA_MODEL_STORAGE_URI", str(workspace_root / "artifacts" / "object-store")),
        auth_token=os.environ.get("SCENARA_MODEL_AUTH_TOKEN"),
        admin_password=os.environ.get("SCENARA_MODEL_ADMIN_PASSWORD"),
        session_ttl_hours=_int_env("SCENARA_MODEL_SESSION_TTL_HOURS", 24),
        login_max_failures=_int_env("SCENARA_MODEL_LOGIN_MAX_FAILURES", 5),
        login_lockout_seconds=_int_env("SCENARA_MODEL_LOGIN_LOCKOUT_SECONDS", 300),
        log_retention_days=_int_env("SCENARA_MODEL_LOG_RETENTION_DAYS", 30, minimum=0),
        maintenance_interval_seconds=_int_env("SCENARA_MODEL_MAINTENANCE_INTERVAL_SECONDS", 3600, minimum=60),
        pipeline_workers=_int_env("SCENARA_MODEL_PIPELINE_WORKERS", 2),
        external_command_timeout_seconds=_int_env("SCENARA_MODEL_EXTERNAL_COMMAND_TIMEOUT_SECONDS", 3600),
        external_command_log_max_chars=_int_env("SCENARA_MODEL_EXTERNAL_COMMAND_LOG_MAX_CHARS", 20000),
        allow_shell_commands=_bool_env("SCENARA_MODEL_ALLOW_SHELL_COMMANDS", False),
    )

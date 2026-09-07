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


def _secret(name: str) -> str | None:
    value = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if value and file_name:
        raise ValueError(f"{name} and {name}_FILE cannot both be configured")
    if value is not None and value.strip():
        return value.strip()
    if not file_name:
        return None
    try:
        content = Path(file_name).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read {name}_FILE") from exc
    if not content:
        raise ValueError(f"{name}_FILE is empty")
    return content


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
    deployment_feedback_secret: str | None
    deployment_feedback_max_age_seconds: int
    auth_mode: str = "local"
    service_token: str | None = None
    context_signing_key: str | None = None
    context_max_age_seconds: int = 300
    data_platform_url: str = ""
    data_platform_service_token: str | None = None
    data_platform_context_signing_key: str | None = None
    data_platform_timeout_seconds: float = 10.0
    data_platform_max_retries: int = 2
    deployment_profile: str = "development"

    def validate(self) -> None:
        if self.deployment_profile not in {"development", "production"}:
            raise ValueError("SCENARA_MODEL_DEPLOYMENT_PROFILE must be development or production")
        if self.deployment_profile == "production" and self.auth_mode != "core":
            raise ValueError("SCENARA_MODEL_AUTH_MODE must be core in production")
        if self.deployment_profile == "production":
            if not self.deployment_feedback_secret:
                raise ValueError("SCENARA_MODEL_DEPLOYMENT_FEEDBACK_SECRET is required in production")
            if self.metadata_db == ":memory:" or not self.metadata_db.startswith(("postgresql://", "postgres://")):
                raise ValueError("SCENARA_MODEL_METADATA_DB must use PostgreSQL in production")
            if self.storage_backend not in {"s3", "minio"}:
                raise ValueError("SCENARA_MODEL_STORAGE_BACKEND must be s3 or minio in production")
        if self.auth_mode not in {"local", "core"}:
            raise ValueError("SCENARA_MODEL_AUTH_MODE must be local or core")
        if self.auth_mode == "core":
            if not self.service_token:
                raise ValueError("SCENARA_MODEL_SERVICE_TOKEN is required when SCENARA_MODEL_AUTH_MODE=core")
            if len(self.service_token) < 24:
                raise ValueError("SCENARA_MODEL_SERVICE_TOKEN must contain at least 24 characters")
            if not self.context_signing_key:
                raise ValueError("SCENARA_MODEL_CONTEXT_SIGNING_KEY is required when SCENARA_MODEL_AUTH_MODE=core")
            if len(self.context_signing_key) < 32:
                raise ValueError("SCENARA_MODEL_CONTEXT_SIGNING_KEY must contain at least 32 characters")
            if self.context_signing_key == self.service_token:
                raise ValueError("SCENARA_MODEL_CONTEXT_SIGNING_KEY must differ from SCENARA_MODEL_SERVICE_TOKEN")
            if self.serve_frontend:
                raise ValueError("SCENARA_MODEL_SERVE_FRONTEND must be false when SCENARA_MODEL_AUTH_MODE=core")
            if not self.data_platform_url:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_URL is required when SCENARA_MODEL_AUTH_MODE=core")
            if self.deployment_profile == "production" and not self.data_platform_url.startswith("https://"):
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_URL must use HTTPS in production")
            if not self.data_platform_service_token:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_SERVICE_TOKEN is required when SCENARA_MODEL_AUTH_MODE=core")
            if len(self.data_platform_service_token) < 24:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_SERVICE_TOKEN must contain at least 24 characters")
            if not self.data_platform_context_signing_key:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_CONTEXT_SIGNING_KEY is required when SCENARA_MODEL_AUTH_MODE=core")
            if len(self.data_platform_context_signing_key) < 32:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_CONTEXT_SIGNING_KEY must contain at least 32 characters")
            if self.data_platform_context_signing_key == self.data_platform_service_token:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_CONTEXT_SIGNING_KEY must differ from SCENARA_MODEL_DATA_PLATFORM_SERVICE_TOKEN")
            if self.data_platform_timeout_seconds <= 0:
                raise ValueError("SCENARA_MODEL_DATA_PLATFORM_TIMEOUT_SECONDS must be positive")


def load_settings() -> Settings:
    workspace_root = Path(os.environ.get("SCENARA_MODEL_WORKSPACE", Path.cwd())).resolve()
    # 默认使用持久化 SQLite 文件：':memory:' 会在服务重启时静默清空全部元数据，
    # 仅当显式配置时才使用（/health 会标记 metadata_persistent=false）。
    metadata_db = _secret("SCENARA_MODEL_METADATA_DB") or "artifacts/scenara_model.sqlite3"
    if metadata_db != ":memory:" and not metadata_db.startswith(("postgresql://", "postgres://")) and not Path(metadata_db).is_absolute():
        metadata_db = str(workspace_root / metadata_db)
    settings = Settings(
        workspace_root=workspace_root,
        metadata_db=metadata_db,
        cors_origins=_list_env("SCENARA_MODEL_CORS_ORIGINS", ["*"]),
        serve_frontend=_bool_env("SCENARA_MODEL_SERVE_FRONTEND", False),
        frontend_dist=workspace_root / os.environ.get("SCENARA_MODEL_FRONTEND_DIST", "frontend/dist"),
        max_package_scan_files=_int_env("SCENARA_MODEL_MAX_PACKAGE_SCAN_FILES", 500),
        max_upload_bytes=_int_env("SCENARA_MODEL_MAX_UPLOAD_BYTES", 500 * 1024 * 1024),
        storage_backend=os.environ.get("SCENARA_MODEL_STORAGE_BACKEND", "local"),
        storage_uri=os.environ.get("SCENARA_MODEL_STORAGE_URI", str(workspace_root / "artifacts" / "object-store")),
        auth_token=_secret("SCENARA_MODEL_AUTH_TOKEN"),
        admin_password=_secret("SCENARA_MODEL_ADMIN_PASSWORD"),
        session_ttl_hours=_int_env("SCENARA_MODEL_SESSION_TTL_HOURS", 24),
        login_max_failures=_int_env("SCENARA_MODEL_LOGIN_MAX_FAILURES", 5),
        login_lockout_seconds=_int_env("SCENARA_MODEL_LOGIN_LOCKOUT_SECONDS", 300),
        log_retention_days=_int_env("SCENARA_MODEL_LOG_RETENTION_DAYS", 30, minimum=0),
        maintenance_interval_seconds=_int_env("SCENARA_MODEL_MAINTENANCE_INTERVAL_SECONDS", 3600, minimum=60),
        pipeline_workers=_int_env("SCENARA_MODEL_PIPELINE_WORKERS", 2),
        external_command_timeout_seconds=_int_env("SCENARA_MODEL_EXTERNAL_COMMAND_TIMEOUT_SECONDS", 3600),
        external_command_log_max_chars=_int_env("SCENARA_MODEL_EXTERNAL_COMMAND_LOG_MAX_CHARS", 20000),
        allow_shell_commands=_bool_env("SCENARA_MODEL_ALLOW_SHELL_COMMANDS", False),
        deployment_feedback_secret=_secret("SCENARA_MODEL_DEPLOYMENT_FEEDBACK_SECRET"),
        deployment_feedback_max_age_seconds=_int_env("SCENARA_MODEL_DEPLOYMENT_FEEDBACK_MAX_AGE_SECONDS", 300),
        auth_mode=os.environ.get("SCENARA_MODEL_AUTH_MODE", "local").strip().lower(),
        service_token=_secret("SCENARA_MODEL_SERVICE_TOKEN"),
        context_signing_key=_secret("SCENARA_MODEL_CONTEXT_SIGNING_KEY"),
        context_max_age_seconds=_int_env("SCENARA_MODEL_CONTEXT_MAX_AGE_SECONDS", 300),
        data_platform_url=os.environ.get("SCENARA_MODEL_DATA_PLATFORM_URL", "").strip().rstrip("/"),
        data_platform_service_token=_secret("SCENARA_MODEL_DATA_PLATFORM_SERVICE_TOKEN"),
        data_platform_context_signing_key=_secret("SCENARA_MODEL_DATA_PLATFORM_CONTEXT_SIGNING_KEY"),
        data_platform_timeout_seconds=max(0.1, float(os.environ.get("SCENARA_MODEL_DATA_PLATFORM_TIMEOUT_SECONDS", "10"))),
        data_platform_max_retries=max(0, min(5, int(os.environ.get("SCENARA_MODEL_DATA_PLATFORM_MAX_RETRIES", "2")))),
        deployment_profile=os.environ.get("SCENARA_MODEL_DEPLOYMENT_PROFILE", "development").strip().lower(),
    )
    settings.validate()
    return settings

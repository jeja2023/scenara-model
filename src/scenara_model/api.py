from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
import socket
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from scenara_model import __version__
from scenara_model.adapters.registry import list_adapters
from scenara_model.auth import generate_session_token, hash_password, token_digest, verify_password
from scenara_model.contracts import validate_models_fragment, validate_release_decision
from scenara_model.dataset_versions import DatasetVersionReference, validate_dataset_version_reference
from scenara_model.datasets.manifest import validate_manifest
from scenara_model.naming import parse_artifact_name
from scenara_model.object_store import object_store_from_settings
from scenara_model.packaging.model_package import validate_model_package
from scenara_model.pipeline import collect_pipeline_artifacts, create_package_from_experiment, load_error_cases, run_experiment_pipeline
from scenara_model.settings import load_settings
from scenara_model.storage import metadata_store_from_uri
from scenara_model.utils import read_yaml

logger = logging.getLogger("scenara_model.api")

SETTINGS = load_settings()
WORKSPACE_ROOT = SETTINGS.workspace_root
STORE = metadata_store_from_uri(SETTINGS.metadata_db)
EXECUTOR = ThreadPoolExecutor(max_workers=SETTINGS.pipeline_workers, thread_name_prefix="scenara-model-pipeline")

# 同步执行超长流水线会阻塞 HTTP worker；超过该时长的配置应使用 async 模式。
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


# 默认管理员账户：首次启动（users 表为空）时自动创建。
DEFAULT_ADMIN_USERNAME = "admin"
# 不再使用固定弱口令：未配置 SCENARA_MODEL_ADMIN_PASSWORD 时生成随机口令，只在启动日志中
# 出现一次，避免"装完即弱口令"的默认不安全状态。
GENERATED_ADMIN_PASSWORD_BYTES = 12

# 本实例标识：多实例部署时区分任务归属，避免启动回收误杀其他实例的运行中任务。
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# 无需登录即可访问的 API 路径（登录本身与健康检查）。
PUBLIC_API_PATHS = {"/api/auth/login"}


class _LoginThrottle:
    """登录失败限流：同一 (用户名, 客户端 IP) 连续失败达阈值后临时锁定。

    进程内状态，单实例部署足够；多实例应在网关层补统一限流。
    限流同时保护 PBKDF2 校验本身——210k 迭代是 CPU 密集操作，无限次爆破
    也是一条无需认证即可触发的 DoS 放大路径。
    """

    def __init__(self, max_failures: int, lockout_seconds: int) -> None:
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}

    def retry_after(self, key: str) -> int:
        with self._lock:
            entry = self._failures.get(key)
            if entry is None:
                return 0
            count, blocked_until = entry
            if count < self._max_failures:
                return 0
            remaining = blocked_until - time.monotonic()
            if remaining <= 0:
                del self._failures[key]
                return 0
            return int(remaining) + 1

    def record_failure(self, key: str) -> None:
        with self._lock:
            count = self._failures.get(key, (0, 0.0))[0] + 1
            self._failures[key] = (count, time.monotonic() + self._lockout_seconds)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


LOGIN_THROTTLE = _LoginThrottle(SETTINGS.login_max_failures, SETTINGS.login_lockout_seconds)


def _bootstrap_admin_user() -> str | None:
    """users 表为空时创建默认管理员，保证系统首次启动即可登录。

    返回自动生成的口令；配置了 SCENARA_MODEL_ADMIN_PASSWORD 或用户已存在时返回 None。
    """
    try:
        if STORE.count_users() > 0:
            return None
        password = SETTINGS.admin_password
        generated = not password
        if generated:
            password = secrets.token_urlsafe(GENERATED_ADMIN_PASSWORD_BYTES)
        salt, digest = hash_password(password)
        STORE.create_user(DEFAULT_ADMIN_USERNAME, salt, digest, role="admin")
        STORE.record_audit_event(actor="system", action="user.bootstrap", target=DEFAULT_ADMIN_USERNAME, detail={})
        if generated:
            logger.warning(
                "created admin user %r with a generated password: %s\n"
                "请记录该口令；可设置 SCENARA_MODEL_ADMIN_PASSWORD 或执行 `scenara-model user set-password` 修改",
                DEFAULT_ADMIN_USERNAME,
                password,
            )
            return password
        logger.info("created admin user %r with SCENARA_MODEL_ADMIN_PASSWORD", DEFAULT_ADMIN_USERNAME)
        return None
    except Exception:  # noqa: BLE001 - 引导失败不应阻止服务可用（例如只读 DB）
        logger.exception("admin user bootstrap failed")
        return None


def _recover_jobs_on_startup() -> None:
    """服务重启后回收孤儿任务：进程内队列不会幸存，DB 状态必须收敛。"""
    try:
        orphans = STORE.recover_orphaned_jobs(worker_id=WORKER_ID)
        for job in orphans:
            STORE.record_pipeline_job_log(int(job["id"]), "job", "orphaned by service restart")
            STORE.record_audit_event(
                actor="system",
                action="pipeline.job.orphaned",
                target=str(job["id"]),
                detail={"config_path": job.get("config_path", "")},
            )
        queued = STORE.list_queued_pipeline_jobs()
        for job in queued:
            EXECUTOR.submit(_run_pipeline_job, int(job["id"]), job.get("request") or {})
        if orphans or queued:
            logger.warning(
                "pipeline reconcile on startup: %d orphaned job(s) failed, %d queued job(s) resubmitted",
                len(orphans),
                len(queued),
            )
    except Exception:  # noqa: BLE001 - 启动恢复失败不应阻止服务可用
        logger.exception("pipeline job reconcile failed on startup")


async def _maintenance_loop() -> None:
    """周期清理过期会话、任务日志与审计事件。"""
    interval = SETTINGS.maintenance_interval_seconds
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(STORE.purge_expired_auth_sessions)
            await asyncio.to_thread(STORE.purge_old_records, retention_days=SETTINGS.log_retention_days)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 维护失败不应影响服务
            logger.exception("periodic maintenance failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _bootstrap_admin_user()
    try:
        STORE.purge_expired_auth_sessions()
    except Exception:  # noqa: BLE001 - 清理失败不影响启动
        logger.exception("expired auth session purge failed")
    _recover_jobs_on_startup()
    maintenance = asyncio.create_task(_maintenance_loop())
    try:
        yield
    finally:
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance
        EXECUTOR.shutdown(wait=False, cancel_futures=True)
        STORE.close()


app = FastAPI(
    title="Scenara Model",
    version=__version__,
    description="Management API for vision model research artifacts, dataset manifests, experiments, and delivery packages.",
    lifespan=lifespan,
)

if SETTINGS.serve_frontend and SETTINGS.frontend_dist.exists():
    assets_dir = SETTINGS.frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


class ApiModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)


def workspace_path(value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    resolved = candidate.resolve()
    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        raise HTTPException(status_code=400, detail=f"Path escapes workspace: {value}")
    return resolved


def workspace_relative(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def _storage_uri() -> str:
    if SETTINGS.storage_backend == "local":
        return str(workspace_path(SETTINGS.storage_uri))
    return SETTINGS.storage_uri


def _safe_frontend_file(path: str) -> Path | None:
    frontend_root = SETTINGS.frontend_dist.resolve()
    requested = (frontend_root / path).resolve()
    if requested != frontend_root and frontend_root not in requested.parents:
        return None
    if requested.exists() and requested.is_file():
        return requested
    return None


class PackageValidationRequest(ApiModel):
    package_dir: str = Field(default="shared-models")
    model_id: str | None = None
    strict_hash: bool = False
    strict_sidecars: bool = True
    strict_examples: bool = True
    strict_onnx: bool = False
    persist: bool = True


class ManifestValidationRequest(ApiModel):
    path: str
    min_split_counts: dict[str, int] = Field(default_factory=dict)
    allowed_labels: list[str] = Field(default_factory=list)
    check_local_files: bool = False


class ContractValidationRequest(ApiModel):
    kind: str = Field(pattern="^(models-fragment|release-decision)$")
    path: str


class ExperimentRecord(ApiModel):
    id: str
    task: str
    dataset: str
    model: str
    status: str = "planned"
    package: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class PipelineRunRequest(ApiModel):
    config_path: str
    package: bool = False
    output_root: str = "shared-models"
    persist: bool = True
    async_run: bool = Field(default=False, alias="async")


class PackageCreateRequest(ApiModel):
    config_path: str
    onnx_path: str | None = None
    output_root: str = "shared-models"
    project_name: str | None = None
    overwrite: bool = True


class ErrorAnalysisRequest(ApiModel):
    path: str


class DatasetVersionRegisterRequest(ApiModel):
    reference: DatasetVersionReference | None = None
    manifest_path: str
    name: str | None = None
    version: str | None = None
    task: str | None = None
    dataset_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    allowed_labels: list[str] = Field(default_factory=list)
    min_split_counts: dict[str, int] = Field(default_factory=dict)
    status: str = "registered"


class ModelRegistryRegisterRequest(ApiModel):
    package_dir: str
    model_id: str | None = None
    stage: str = Field(default="candidate", pattern="^(smoke|candidate|staging|production|archived)$")
    metrics: dict[str, float] = Field(default_factory=dict)


class ReleaseApprovalRequest(ApiModel):
    path: str
    status: str = Field(default="pending", pattern="^(pending|approved|rejected)$")


class DeploymentRolloutRequest(ApiModel):
    model_id: str
    environment: str = Field(default="production", pattern="^(development|staging|production)$")
    strategy: str = Field(default="gray", pattern="^(immediate|gray|canary)$")
    status: str = Field(default="planned", pattern="^(planned|running|completed|rolled_back|failed|cancelled)$")
    traffic_percent: int = Field(default=0, ge=0, le=100)
    rollback_target: str | None = None


def _resolve_bearer_identity(authorization: str | None) -> dict[str, Any] | None:
    """解析 Authorization 头：静态令牌或有效会话令牌均可，无效返回 None。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    if not token:
        return None
    if SETTINGS.auth_token and hmac.compare_digest(token, SETTINGS.auth_token):
        return {"username": "api-token", "role": "service", "session": None}
    session = STORE.get_auth_session(token_digest(token))
    if session is not None:
        return {"username": session["username"], "role": "user", "session": session}
    return None


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


def require_auth(request: Request) -> dict[str, Any]:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        identity = _resolve_bearer_identity(request.headers.get("Authorization"))
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return identity


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
    """全局认证：除登录接口外，全部 /api 路径都要求有效令牌。

    /health 与前端静态资源保持公开（容器健康检查、登录页本身依赖它们）。
    解析结果缓存到 request.state，供路由级 Depends(require_auth) 复用。
    """
    path = request.url.path
    if path.startswith("/api") and path not in PUBLIC_API_PATHS and request.method != "OPTIONS":
        identity = _resolve_bearer_identity(request.headers.get("Authorization"))
        if identity is None:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        request.state.identity = identity
    return await call_next(request)


# CORS 必须在认证中间件之后注册（add_middleware 置于栈外层），
# 保证 401 响应也带 CORS 头、预检请求不被认证拦截。
app.add_middleware(
    CORSMiddleware,
    allow_origins=SETTINGS.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _pipeline_payload(config_path: Path, output_root: Path, request: PipelineRunRequest) -> dict[str, Any]:
    return {
        "config_path": str(config_path),
        "package": request.package,
        "output_root": str(output_root),
        "persist": request.persist,
    }


def _record_pipeline_artifacts(report: dict[str, Any], *, job_id: int | None = None, run_id: int | None = None) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for artifact in collect_pipeline_artifacts(report):
        indexed.append(
            STORE.record_pipeline_artifact(
                job_id=job_id,
                run_id=run_id,
                name=str(artifact["name"]),
                kind=str(artifact["kind"]),
                path=str(artifact.get("path")) if artifact.get("path") else None,
                uri=str(artifact.get("uri")) if artifact.get("uri") else None,
                size=int(artifact["size"]) if artifact.get("size") is not None else None,
            )
        )
    return indexed


class _JobLogBuffer:
    """外部命令日志缓冲：按条数或时间批量落库。"""

    def __init__(self, job_id: int, *, max_entries: int = 200, max_interval: float = 1.0) -> None:
        self._job_id = job_id
        self._max_entries = max_entries
        self._max_interval = max_interval
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str]] = []
        self._last_flush = time.monotonic()

    def add(self, stream: str, line: str) -> None:
        with self._lock:
            self._pending.append((stream, line))
            due = len(self._pending) >= self._max_entries or (time.monotonic() - self._last_flush) >= self._max_interval
            if not due:
                return
            entries, self._pending = self._pending, []
            self._last_flush = time.monotonic()
        self._write(entries)

    def flush(self) -> None:
        with self._lock:
            entries, self._pending = self._pending, []
            self._last_flush = time.monotonic()
        self._write(entries)

    def _write(self, entries: list[tuple[str, str]]) -> None:
        if not entries:
            return
        try:
            STORE.record_pipeline_job_logs(self._job_id, entries)
        except Exception:  # noqa: BLE001 - 日志落库失败不应中断训练任务
            logger.exception("failed to persist %d job log line(s) for job %s", len(entries), self._job_id)


class _CancelCheck:
    """将高频取消轮询节流为秒级查库，并在同一路径续任务心跳。"""

    def __init__(self, job_id: int, *, interval: float = 3.0) -> None:
        self._job_id = job_id
        self._interval = interval
        self._checked_at: float | None = None
        self._cancelled = False

    def __call__(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if self._checked_at is not None and now - self._checked_at < self._interval:
            return False
        self._checked_at = now
        try:
            job = STORE.get_pipeline_job(self._job_id)
        except KeyError:
            return False
        try:
            STORE.heartbeat_pipeline_job(self._job_id, WORKER_ID)
        except Exception:  # noqa: BLE001 - 心跳失败不应中断任务
            logger.debug("heartbeat failed for job %s", self._job_id, exc_info=True)
        self._cancelled = job["status"] == "cancellation_requested"
        return self._cancelled


def _pipeline_job_detail(job_id: int) -> dict[str, Any]:
    job = STORE.get_pipeline_job(job_id)
    job["logs"] = STORE.list_pipeline_job_logs(job_id)
    job["artifacts"] = STORE.list_pipeline_artifacts(job_id=job_id)
    return job


def _run_pipeline_job(job_id: int, payload: dict[str, Any]) -> None:
    log_buffer = _JobLogBuffer(job_id)

    def event_sink(stage: str, message: str, detail: dict[str, Any]) -> None:
        log_buffer.flush()
        STORE.record_pipeline_job_log(job_id, stage, message, detail)

    def log_sink(stream: str, line: str) -> None:
        log_buffer.add(stream, line)

    should_cancel = _CancelCheck(job_id)

    try:
        job = STORE.claim_pipeline_job(job_id, WORKER_ID)
        if job["status"] == "cancelled":
            STORE.record_pipeline_job_log(job_id, "job", "cancelled before start")
            return
        if job["status"] != "running":
            # 状态守卫未命中（已被其他 worker 认领或已完结），放弃本次执行。
            STORE.record_pipeline_job_log(job_id, "job", f"skipped: job is already {job['status']}")
            return
        STORE.record_pipeline_job_log(job_id, "job", "started", {"config_path": payload["config_path"]})
        report = run_experiment_pipeline(
            payload["config_path"],
            package=bool(payload.get("package")),
            output_root=payload.get("output_root", "shared-models"),
            event_sink=event_sink,
            should_cancel=should_cancel,
            log_sink=log_sink,
        )
        run_record = STORE.record_pipeline_run(payload["config_path"], report) if payload.get("persist", True) else None
        _record_pipeline_artifacts(report, job_id=job_id, run_id=int(run_record["id"]) if run_record else None)
        current = STORE.get_pipeline_job(job_id)
        if report.get("status") == "cancelled" or current["status"] == "cancellation_requested":
            STORE.complete_pipeline_job(job_id, report, status="cancelled")
            STORE.record_pipeline_job_log(job_id, "job", "cancelled", {"config_path": payload["config_path"], "package": bool(payload.get("package"))})
            STORE.record_audit_event(
                actor="api",
                action="pipeline.job.cancelled",
                target=str(job_id),
                detail={"config_path": payload["config_path"], "package": bool(payload.get("package"))},
            )
            return
        if report.get("status") == "failed":
            STORE.fail_pipeline_job(job_id, "Pipeline stage failed", report)
            STORE.record_audit_event(actor="api", action="pipeline.job.failed", target=str(job_id), detail={"config_path": payload["config_path"], "package": bool(payload.get("package"))})
            return
        STORE.complete_pipeline_job(job_id, report, status="completed")
        STORE.record_audit_event(
            actor="api",
            action="pipeline.job.completed",
            target=str(job_id),
            detail={"config_path": payload["config_path"], "package": bool(payload.get("package"))},
        )
    except Exception as exc:  # noqa: BLE001
        STORE.record_pipeline_job_log(job_id, "job", "failed", {"error": str(exc)})
        STORE.fail_pipeline_job(job_id, str(exc))
        STORE.record_audit_event(actor="api", action="pipeline.job.failed", target=str(job_id), detail={"error": str(exc)})
    finally:
        log_buffer.flush()

def _queue_pipeline_job(payload: dict[str, Any]) -> dict[str, Any]:
    # 同一配置的并发运行共享输出目录，会互相覆盖产物，必须拒绝。
    if STORE.has_active_pipeline_job(payload["config_path"]):
        raise HTTPException(
            status_code=409,
            detail=f"An active pipeline job already exists for {payload['config_path']}; wait for it to finish or cancel it first.",
        )
    job = STORE.create_pipeline_job(payload["config_path"], payload)
    EXECUTOR.submit(_run_pipeline_job, int(job["id"]), payload)
    STORE.record_audit_event(
        actor="api",
        action="pipeline.job.queued",
        target=str(job["id"]),
        detail={"config_path": payload["config_path"], "package": bool(payload.get("package"))},
    )
    return job


def _metadata_journal_mode() -> str:
    if SETTINGS.metadata_db == ":memory:":
        return "memory"
    if SETTINGS.metadata_db.startswith(("postgresql://", "postgres://")):
        return "postgresql"
    return STORE.journal_mode()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "workspace": str(WORKSPACE_ROOT),
        "metadata_db": SETTINGS.metadata_db,
        "metadata_persistent": SETTINGS.metadata_db != ":memory:",
        "metadata_journal_mode": _metadata_journal_mode(),
        "serve_frontend": SETTINGS.serve_frontend and SETTINGS.frontend_dist.exists(),
        "storage_backend": SETTINGS.storage_backend,
        "storage_uri": SETTINGS.storage_uri,
        "auth_required": True,
        "pipeline_workers": SETTINGS.pipeline_workers,
        "external_shell_commands_allowed": SETTINGS.allow_shell_commands,
    }


@app.post("/api/auth/login")
def login(request: LoginRequest, http_request: Request) -> dict[str, Any]:
    client_host = http_request.client.host if http_request.client else "unknown"
    throttle_key = f"{request.username}|{client_host}"
    retry_after = LOGIN_THROTTLE.retry_after(throttle_key)
    if retry_after:
        STORE.record_audit_event(
            actor=request.username,
            action="auth.login.throttled",
            target=request.username,
            detail={"retry_after": retry_after},
        )
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {retry_after} 秒后重试",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        user = STORE.get_user_by_username(request.username)
    except KeyError:
        user = None
    if user is None or not verify_password(request.password, user["password_salt"], user["password_hash"]):
        LOGIN_THROTTLE.record_failure(throttle_key)
        # 用户不存在与密码错误返回同一提示，避免用户名枚举。
        STORE.record_audit_event(actor=request.username, action="auth.login.failed", target=request.username, detail={})
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    LOGIN_THROTTLE.reset(throttle_key)
    token = generate_session_token()
    expires_at = (datetime.now(UTC) + timedelta(hours=SETTINGS.session_ttl_hours)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    STORE.create_auth_session(token_digest(token), int(user["id"]), user["username"], expires_at)
    STORE.record_audit_event(actor=user["username"], action="auth.login", target=user["username"], detail={})
    return {"token": token, "username": user["username"], "role": user["role"], "expires_at": expires_at}


@app.post("/api/auth/logout")
def logout(identity: dict[str, Any] = Depends(require_auth), authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if identity.get("session") is not None and authorization:
        token = authorization[len("Bearer "):].strip()
        STORE.revoke_auth_session(token_digest(token))
        STORE.record_audit_event(actor=identity["username"], action="auth.logout", target=identity["username"], detail={})
    return {"ok": True}


@app.get("/api/auth/me")
def me(identity: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    session = identity.get("session")
    return {
        "username": identity["username"],
        "role": identity["role"],
        "expires_at": session["expires_at"] if session else None,
    }


@app.post("/api/packages/validate", dependencies=[Depends(require_auth)])
def validate_package_endpoint(request: PackageValidationRequest) -> dict[str, Any]:
    package_dir = workspace_path(request.package_dir)
    result = validate_model_package(
        package_dir,
        model_id=request.model_id,
        strict_hash=request.strict_hash,
        strict_sidecars=request.strict_sidecars,
        strict_examples=request.strict_examples,
        strict_onnx=request.strict_onnx,
    )
    report = result.to_dict()
    if request.persist:
        validation = STORE.record_package_validation(report)
        STORE.record_audit_event(actor="api", action="package.validate", target=str(package_dir), detail={"ok": report["ok"]})
        return {"validation": validation}
    return {"validation": report}


@app.get("/api/packages/scan")
def scan_packages(
    root: str = Query(default="shared-models"),
    strict_hash: bool = Query(default=False),
    strict_examples: bool = Query(default=True),
) -> dict[str, Any]:
    resolved_root = workspace_path(root)
    if not resolved_root.exists():
        return {"root": str(resolved_root), "packages": []}
    packages: list[dict[str, Any]] = []
    model_files: list[Path] = []
    for model_file in resolved_root.rglob("*.onnx"):
        model_files.append(model_file)
        if len(model_files) > SETTINGS.max_package_scan_files:
            raise HTTPException(
                status_code=400,
                detail=f"Too many ONNX files under {root}; limit is {SETTINGS.max_package_scan_files}",
            )
    model_files.sort()
    for model_file in model_files:
        result = validate_model_package(
            model_file.parent,
            model_id=model_file.name,
            strict_hash=strict_hash,
            strict_examples=strict_examples,
        )
        packages.append(result.to_dict())
    return {"root": str(resolved_root), "packages": packages}

@app.get("/api/package-validations")
def list_package_validations(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"validations": STORE.list_package_validations(limit)}


@app.post("/api/manifests/validate")
def validate_manifest_endpoint(request: ManifestValidationRequest) -> dict[str, Any]:
    result = validate_manifest(
        workspace_path(request.path),
        min_split_counts=request.min_split_counts or None,
        allowed_labels=request.allowed_labels or None,
        check_local_files=request.check_local_files,
    )
    return {"manifest": result.to_dict()}


@app.post("/api/contracts/validate")
def validate_contract_endpoint(request: ContractValidationRequest) -> dict[str, Any]:
    path = workspace_path(request.path)
    if request.kind == "models-fragment":
        result = validate_models_fragment(path)
    else:
        result = validate_release_decision(path)
    return {"contract": result.to_dict()}


@app.get("/api/experiments")
def list_experiments(index_path: str = Query(default="experiments/index.yml")) -> dict[str, Any]:
    records = STORE.list_experiments()
    index_file = workspace_path(index_path)
    index_records: list[dict[str, Any]] = []
    if index_file.exists():
        data = read_yaml(index_file)
        raw_records = data.get("experiments", [])
        if isinstance(raw_records, list):
            index_records = [record for record in raw_records if isinstance(record, dict)]
    return {"experiments": records, "index": index_records}


@app.post("/api/experiments", dependencies=[Depends(require_auth)])
def upsert_experiment(record: ExperimentRecord) -> dict[str, Any]:
    saved = STORE.upsert_experiment(record.model_dump())
    STORE.record_audit_event(actor="api", action="experiment.upsert", target=record.id, detail={"status": record.status})
    return {"experiment": saved}


@app.get("/api/adapters")
def adapters() -> dict[str, Any]:
    return {"adapters": list_adapters()}


@app.post("/api/pipelines/run", dependencies=[Depends(require_auth)])
def run_pipeline_endpoint(request: PipelineRunRequest) -> dict[str, Any]:
    config_path = workspace_path(request.config_path)
    output_root = workspace_path(request.output_root)
    payload = _pipeline_payload(config_path, output_root, request)
    if request.async_run:
        return {"job": _queue_pipeline_job(payload)}
    report = run_experiment_pipeline(config_path, package=request.package, output_root=output_root)
    action = "pipeline.run"
    if report.get("status") == "failed":
        action = "pipeline.run.failed"
    elif report.get("status") == "cancelled":
        action = "pipeline.run.cancelled"
    STORE.record_audit_event(actor="api", action=action, target=str(config_path), detail={"package": request.package, "status": report.get("status")})
    if request.persist:
        run_record = STORE.record_pipeline_run(str(config_path), report)
        _record_pipeline_artifacts(report, run_id=int(run_record["id"]))
        return {"run": run_record}
    return {"run": report}


@app.get("/api/pipelines/runs")
def list_pipeline_runs(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"runs": STORE.list_pipeline_runs(limit)}


@app.get("/api/pipelines/jobs")
def list_pipeline_jobs(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"jobs": STORE.list_pipeline_jobs(limit)}


@app.get("/api/pipelines/jobs/{job_id}")
def get_pipeline_job(job_id: int) -> dict[str, Any]:
    try:
        return {"job": _pipeline_job_detail(job_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc


@app.get("/api/pipelines/jobs/{job_id}/logs")
def list_pipeline_job_logs(
    job_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    since_id: int | None = Query(default=None, ge=0),
    tail: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        STORE.get_pipeline_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc
    return {"logs": STORE.list_pipeline_job_logs(job_id, limit, since_id=since_id, tail=tail)}


@app.get("/api/pipelines/jobs/{job_id}/artifacts")
def list_pipeline_job_artifacts(job_id: int, limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    try:
        STORE.get_pipeline_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc
    return {"artifacts": STORE.list_pipeline_artifacts(job_id=job_id, limit=limit)}


@app.post("/api/pipelines/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
def cancel_pipeline_job(job_id: int) -> dict[str, Any]:
    try:
        job = STORE.request_pipeline_job_cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc
    STORE.record_audit_event(actor="api", action="pipeline.job.cancel", target=str(job_id), detail={"status": job["status"]})
    return {"job": job}


@app.post("/api/pipelines/jobs/{job_id}/retry", dependencies=[Depends(require_auth)])
def retry_pipeline_job(job_id: int) -> dict[str, Any]:
    try:
        job = STORE.get_pipeline_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline job not found") from exc
    # 只允许重试已完结的任务：对 running 任务重放会产生并发重复执行互相覆盖产物。
    if job["status"] not in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is {job['status']}; only completed/failed/cancelled jobs can be retried.",
        )
    return {"job": _queue_pipeline_job(job["request"])}


@app.get("/api/pipelines/artifacts/{artifact_id}/download")
def download_pipeline_artifact(artifact_id: int) -> FileResponse:
    try:
        artifact = STORE.get_pipeline_artifact(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    raw_path = artifact.get("path")
    if not raw_path:
        raise HTTPException(status_code=404, detail="Artifact has no local file path")
    resolved = workspace_path(raw_path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Artifact file no longer exists")
    return FileResponse(resolved, filename=resolved.name, media_type="application/octet-stream")


@app.post("/api/packages/create", dependencies=[Depends(require_auth)])
def create_package_endpoint(request: PackageCreateRequest) -> dict[str, Any]:
    config_path = workspace_path(request.config_path)
    onnx_path = workspace_path(request.onnx_path) if request.onnx_path else None
    output_root = workspace_path(request.output_root)
    result = create_package_from_experiment(
        config_path,
        onnx_path=onnx_path,
        output_root=output_root,
        project_name=request.project_name,
        overwrite=request.overwrite,
    )
    STORE.record_audit_event(actor="api", action="package.create", target=result["artifact_name"], detail=result["validation"])
    return {"package": result}


@app.post("/api/uploads", dependencies=[Depends(require_auth)])
def upload_file(file: UploadFile, target_dir: str = "artifacts/uploads") -> dict[str, Any]:
    resolved_dir = workspace_path(target_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)
    destination = resolved_dir / Path(file.filename or "upload.bin").name
    total_bytes = 0
    try:
        with destination.open("wb") as handle:
            while chunk := file.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > SETTINGS.max_upload_bytes:
                    raise HTTPException(status_code=413, detail=f"Upload exceeds {SETTINGS.max_upload_bytes} bytes")
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    try:
        stored = object_store_from_settings(SETTINGS.storage_backend, _storage_uri()).put_file(destination, destination.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    STORE.record_audit_event(actor="api", action="file.upload", target=str(destination), detail=stored.to_dict())
    return {"upload": {"path": str(destination), "stored": stored.to_dict()}}


@app.post("/api/error-analysis")
def error_analysis_endpoint(request: ErrorAnalysisRequest) -> dict[str, Any]:
    return {"analysis": load_error_cases(workspace_path(request.path))}


@app.get("/api/audit-events")
def list_audit_events(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"events": STORE.list_audit_events(limit)}


@app.get("/api/datasets/versions")
def list_dataset_versions(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"datasets": STORE.list_dataset_versions(limit)}


@app.post("/api/datasets/versions", dependencies=[Depends(require_auth)])
def register_dataset_version(request: DatasetVersionRegisterRequest) -> dict[str, Any]:
    manifest_path = workspace_path(request.manifest_path)
    reference = request.reference
    if reference is not None:
        try:
            reference = validate_dataset_version_reference(reference, manifest_path=manifest_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        dataset_id = reference.dataset_id
        name = reference.dataset_id
        version = reference.version
        task = request.task or "unknown"
        status = "published"
        reference_payload = reference.model_dump(mode="json")
    else:
        if not request.name or not request.version or not request.task:
            raise HTTPException(status_code=400, detail="reference or legacy name/version/task fields are required")
        dataset_id = request.dataset_id or f"{request.name}_v{request.version}"
        name = request.name
        version = request.version
        task = request.task
        status = request.status
        reference_payload = {}
    validation = validate_manifest(
        manifest_path,
        min_split_counts=request.min_split_counts or None,
        allowed_labels=request.allowed_labels or None,
        check_local_files=True,
    )
    if not validation.ok:
        raise HTTPException(status_code=400, detail={"manifest": validation.to_dict()})
    dataset = STORE.upsert_dataset_version(
        {
            "dataset_id": dataset_id,
            "name": name,
            "version": version,
            "task": task,
            "manifest_path": workspace_relative(manifest_path),
            "split_counts": validation.split_counts,
            "labels": request.labels,
            "status": status,
            "reference": reference_payload,
        }
    )
    STORE.record_audit_event(
        actor="api",
        action="dataset.register",
        target=dataset_id,
        detail={
            "rows": validation.total_rows,
            "manifest_sha256": reference.manifest_sha256 if reference is not None else None,
        },
    )
    return {"dataset": dataset, "manifest": validation.to_dict()}


@app.get("/api/models/registry")
def list_model_registry(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"models": STORE.list_model_registry_entries(limit)}


@app.post("/api/models/registry", dependencies=[Depends(require_auth)])
def register_model(request: ModelRegistryRegisterRequest) -> dict[str, Any]:
    package_dir = workspace_path(request.package_dir)
    validation = validate_model_package(
        package_dir,
        model_id=request.model_id,
        strict_hash=True,
        strict_examples=True,
        strict_onnx=True,
        strict_provenance=request.stage != "smoke",
    )
    if not validation.ok or validation.model_file is None:
        raise HTTPException(status_code=400, detail={"validation": validation.to_dict()})
    artifact = parse_artifact_name(validation.model_file.name)
    task = "model"
    card_metrics: dict[str, float] = {}
    if validation.model_card and validation.model_card.exists():
        card = read_yaml(validation.model_card)
        model_section = card.get("model", {}) if isinstance(card, dict) else {}
        if isinstance(model_section, dict):
            task = str(model_section.get("task") or task)
        raw_metrics = card.get("metrics", {}) if isinstance(card, dict) else {}
        if isinstance(raw_metrics, dict):
            card_metrics = {
                str(name): float(value)
                for name, value in raw_metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
    if request.metrics and request.metrics != card_metrics:
        raise HTTPException(status_code=400, detail="Registry metrics must match the packaged model-card metrics")
    try:
        package_namespace = package_dir.resolve().relative_to(workspace_path("shared-models")).as_posix()
    except ValueError:
        package_namespace = workspace_relative(package_dir).replace("\\", "/")
    model_id = "/".join(part for part in (package_namespace, validation.model_file.name) if part and part != ".")
    model = STORE.upsert_model_registry_entry(
        {
            "model_id": model_id,
            "package_dir": workspace_relative(package_dir),
            "artifact_name": validation.model_file.name,
            "version": artifact.version,
            "task": task,
            "metrics": card_metrics,
            "stage": request.stage,
        }
    )
    STORE.record_audit_event(actor="api", action="model.register", target=model_id, detail={"stage": request.stage})
    return {"model": model, "validation": validation.to_dict()}


@app.get("/api/releases/approvals")
def list_release_approvals(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"approvals": STORE.list_release_approvals(limit)}


@app.post("/api/releases/approvals", dependencies=[Depends(require_auth)])
def record_release_approval(request: ReleaseApprovalRequest) -> dict[str, Any]:
    decision_path = workspace_path(request.path)
    validation = validate_release_decision(decision_path)
    if not validation.ok:
        raise HTTPException(status_code=400, detail={"contract": validation.to_dict()})
    data = read_yaml(decision_path)
    decision = data.get("decision", {}) if isinstance(data, dict) else {}
    model_id = str(decision.get("model") or "").replace("\\", "/")
    try:
        registered_model = STORE.get_model_registry_entry(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Model is not registered: {model_id}") from exc
    if request.status == "approved" and registered_model.get("stage") == "smoke":
        raise HTTPException(status_code=400, detail="Smoke-stage models cannot receive a release approval")
    approval = STORE.record_release_approval(
        {
            "model_id": model_id,
            "recommendation": str(decision.get("recommendation")),
            "status": request.status,
            "decision": decision,
        }
    )
    STORE.record_audit_event(actor="api", action="release.approval", target=approval["model_id"], detail={"status": request.status})
    return {"approval": approval, "contract": validation.to_dict()}


@app.get("/api/deployments/rollouts")
def list_deployment_rollouts(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"rollouts": STORE.list_deployment_rollouts(limit)}


@app.post("/api/deployments/rollouts", dependencies=[Depends(require_auth)])
def create_deployment_rollout(request: DeploymentRolloutRequest) -> dict[str, Any]:
    if request.environment in {"staging", "production"}:
        try:
            registered_model = STORE.get_model_registry_entry(request.model_id)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"Model is not registered: {request.model_id}") from exc
        if registered_model.get("stage") == "smoke":
            raise HTTPException(status_code=400, detail="Smoke-stage models cannot roll out to staging or production")
        approvals = STORE.list_release_approvals(limit=500)
        if not any(item.get("model_id") == request.model_id and item.get("status") == "approved" for item in approvals):
            raise HTTPException(status_code=400, detail="An approved release is required before staging or production rollout")
        if request.environment == "production":
            if not request.rollback_target:
                raise HTTPException(status_code=400, detail="A rollback_target is required for production rollout")
            try:
                STORE.get_model_registry_entry(request.rollback_target)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=f"Rollback target is not registered: {request.rollback_target}") from exc
    rollout = STORE.upsert_deployment_rollout(request.model_dump())
    STORE.record_audit_event(
        actor="api",
        action="deployment.rollout",
        target=request.model_id,
        detail={"environment": request.environment, "traffic_percent": request.traffic_percent},
    )
    return {"rollout": rollout}


@app.get("/api/templates")
def templates() -> dict[str, Any]:
    return {
        "model_card": "configs/export/model-card.template.yml",
        "dataset": "configs/datasets/example_dataset.yml",
        "experiment": "configs/experiments/detection_yolo_baseline.yml",
        "labeling_guideline": "labeling/guidelines/detection_template.md",
        "quality_rules": "labeling/quality_rules/default_detection.yml",
        "release_decision": "configs/export/release-decision.template.yml",
        "detection_pipeline": "configs/experiments/detection_yolo_baseline.yml",
        "reid_pipeline": "configs/experiments/reid_baseline.yml",
        "classification_pipeline": "configs/experiments/classification_baseline.yml",
        "segmentation_pipeline": "configs/experiments/segmentation_baseline.yml",
    }


@app.get("/{path:path}", include_in_schema=False)
def frontend_fallback(path: str) -> FileResponse:
    if SETTINGS.serve_frontend:
        requested = _safe_frontend_file(path)
        if requested is not None:
            return FileResponse(requested)
        index_file = _safe_frontend_file("index.html")
        if index_file is not None:
            return FileResponse(index_file)
    raise HTTPException(status_code=404, detail="Frontend build is not available")

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "20260726_060_job_heartbeat"

# 流水线任务终态：一旦进入不可回退。
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def utc_now_iso() -> str:
    """统一时间戳格式：带 Z 后缀的毫秒级 ISO8601，SQLite/PostgreSQL 两后端输出一致。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _required_lastrowid(cursor: Any) -> int:
    value = cursor.lastrowid
    if value is None:
        raise RuntimeError("Database insert did not return a row ID.")
    return int(value)


class MetadataStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        # 每线程一条长连接；_connections 仅用于 close() 时统一回收。
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        if str(path) != ":memory:":
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._reset_empty_database_files()
        self.initialize()

    def initialize(self) -> None:
        connection = self._shared_connection if str(self.path) == ":memory:" else None
        if connection is not None:
            self._initialize_connection(connection)
            return
        try:
            with self.connect() as connection:
                self._initialize_connection(connection)
        except sqlite3.DatabaseError:
            if not self._reset_empty_database_files():
                raise
            with self.connect() as connection:
                self._initialize_connection(connection)

    def _initialize_connection(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                dataset TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                package TEXT,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS package_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_dir TEXT NOT NULL,
                model_file TEXT,
                ok INTEGER NOT NULL,
                sha256 TEXT,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_path TEXT NOT NULL,
                status TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_path TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                started_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
                worker_id TEXT,
                heartbeat_at TEXT,
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stream TEXT NOT NULL,
                message TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                run_id INTEGER,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT,
                uri TEXT,
                size INTEGER,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                task TEXT NOT NULL,
                manifest_path TEXT NOT NULL,
                split_counts_json TEXT NOT NULL DEFAULT '{}',
                labels_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'registered',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL UNIQUE,
                package_dir TEXT NOT NULL,
                artifact_name TEXT NOT NULL,
                version TEXT NOT NULL,
                task TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                stage TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS release_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deployment_rollouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL,
                traffic_percent INTEGER NOT NULL DEFAULT 0,
                rollback_target TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._ensure_pipeline_job_columns(connection)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions(expires_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status ON pipeline_jobs(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_job_logs_job_id ON pipeline_job_logs(job_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_artifacts_job_id ON pipeline_artifacts(job_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_artifacts_run_id ON pipeline_artifacts(run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_model_registry_stage ON model_registry(stage)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_release_approvals_model_id ON release_approvals(model_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_deployment_rollouts_model_id ON deployment_rollouts(model_id)")
        connection.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (SCHEMA_VERSION,))

    def _ensure_pipeline_job_columns(self, connection: Any) -> None:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(pipeline_jobs)").fetchall()}
        for column in ("worker_id", "heartbeat_at"):
            if column not in existing:
                connection.execute(f"ALTER TABLE pipeline_jobs ADD COLUMN {column} TEXT")

    def _configure_connection(self, connection: sqlite3.Connection, *, in_memory: bool, use_wal: bool = True) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        if in_memory:
            # 内存库没有崩溃恢复语义，MEMORY 是唯一适用的模式。
            applied = self._configure_journal_mode(connection, ("MEMORY",))
        elif use_wal:
            # 顺序必须最安全优先：MEMORY 在文件库上总会成功，一旦排在前面就永远
            # 轮不到 WAL，而 MEMORY 模式下进程崩溃极可能损坏整个数据库文件。
            applied = self._configure_journal_mode(connection, ("WAL", "TRUNCATE", "DELETE"))
        else:
            applied = self._configure_journal_mode(connection, ("TRUNCATE", "DELETE"))
        # synchronous=NORMAL 只在 WAL 下既安全又快；回滚日志模式下降级会丢失最近
        # 提交的事务，因此保持默认的 FULL。
        target = "NORMAL" if applied == "WAL" else "FULL"
        try:
            connection.execute(f"PRAGMA synchronous={target}")
        except sqlite3.DatabaseError:
            pass

    def _configure_journal_mode(self, connection: sqlite3.Connection, modes: tuple[str, ...]) -> str:
        """按顺序尝试并返回真正生效的 journal mode。

        PRAGMA journal_mode 失败时通常不抛异常，而是返回当前仍然生效的模式
        （例如网络文件系统上 WAL 会静默回落），因此必须比对返回值而非只看异常。
        """
        for mode in modes:
            try:
                row = connection.execute(f"PRAGMA journal_mode={mode}").fetchone()
            except sqlite3.DatabaseError:
                continue
            applied = str(row[0]).upper() if row else ""
            if applied == mode.upper():
                return applied
        return ""

    def journal_mode(self) -> str:
        """返回当前生效的 journal mode，供健康检查与回归测试断言。"""
        with self.connect() as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).upper() if row else ""

    def _database_family(self, path: Path) -> list[Path]:
        return [Path(str(path) + suffix) for suffix in ("", "-wal", "-shm", "-journal")]

    def _reset_empty_database_files(self) -> bool:
        if str(self.path) == ":memory:" or not isinstance(self.path, Path):
            return False
        try:
            is_empty = self.path.exists() and self.path.stat().st_size == 0
        except OSError:
            return False
        if not is_empty:
            return False
        # WAL 模式下存在"主库 0 字节 + 已提交数据全在 -wal"的合法状态；
        # 此时删除 WAL 会静默丢已提交数据，必须交给 SQLite 自行恢复。
        wal_file = Path(str(self.path) + "-wal")
        try:
            if wal_file.exists() and wal_file.stat().st_size > 0:
                return False
        except OSError:
            return False
        targets = self._database_family(self.path)
        removed_any = False
        for target in targets:
            if not target.exists():
                continue
            try:
                target.unlink()
                removed_any = True
            except OSError:
                return False
        return removed_any and all(not target.exists() for target in targets)

    @property
    def _shared_connection(self) -> sqlite3.Connection | None:
        if str(self.path) != ":memory:":
            return None
        connection = getattr(self, "_memory_connection", None)
        if connection is None:
            connection = sqlite3.connect(":memory:", check_same_thread=False, timeout=30.0)
            self._configure_connection(connection, in_memory=True)
            self._memory_connection = connection
        return connection

    def _open_file_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            self._configure_connection(connection, in_memory=False, use_wal=True)
        except sqlite3.DatabaseError:
            connection.close()
            if not self._reset_empty_database_files():
                raise
            connection = sqlite3.connect(self.path, timeout=30.0)
            self._configure_connection(connection, in_memory=False, use_wal=False)
        return connection

    def _thread_connection(self) -> sqlite3.Connection:
        """返回当前线程的长连接，不存在则建立。

        原实现每次操作都 connect/close 并用进程级锁串行化：训练期间的取消轮询
        与逐行日志写入会产生每秒数百次建连，把所有 HTTP 请求一起拖住。
        WAL + busy_timeout 已经提供并发读和写排队，不需要额外的进程级锁。
        """
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            return connection
        connection = self._open_file_connection()
        self._local.connection = connection
        with self._lock:
            self._connections.append(connection)
        return connection

    @contextmanager
    def connect(self) -> Iterator[Any]:
        shared = self._shared_connection
        if shared is not None:
            # 内存库跨线程共享同一条连接，必须串行化。
            with self._lock:
                yield shared
                shared.commit()
            return
        connection = self._thread_connection()
        try:
            yield connection
            connection.commit()
        except BaseException:
            # 连接现在跨操作复用，不回滚会把失败事务的状态带给下一次操作。
            connection.rollback()
            raise

    def close(self) -> None:
        """关闭全部已打开连接；进程退出或测试清理时调用。"""
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def upsert_experiment(self, payload: dict[str, Any]) -> dict[str, Any]:
        metrics = payload.get("metrics") or {}
        record = {
            "id": payload["id"],
            "task": payload["task"],
            "dataset": payload["dataset"],
            "model": payload["model"],
            "status": payload.get("status", "planned"),
            "package": payload.get("package"),
            "metrics_json": json.dumps(metrics, ensure_ascii=False),
            "updated_at": utc_now_iso(),
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO experiments (id, task, dataset, model, status, package, metrics_json)
                VALUES (:id, :task, :dataset, :model, :status, :package, :metrics_json)
                ON CONFLICT(id) DO UPDATE SET
                    task=excluded.task,
                    dataset=excluded.dataset,
                    model=excluded.model,
                    status=excluded.status,
                    package=excluded.package,
                    metrics_json=excluded.metrics_json,
                    updated_at=:updated_at
                """,
                record,
            )
        return self.get_experiment(payload["id"])

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return self._experiment_row(row)

    def list_experiments(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._experiment_row(row) for row in rows]

    def record_package_validation(self, report: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO package_validations (package_dir, model_file, ok, sha256, report_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report.get("package_dir"),
                    report.get("model_file"),
                    1 if report.get("ok") else 0,
                    report.get("sha256"),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            validation_id = _required_lastrowid(cursor)
        return self.get_package_validation(validation_id)

    def list_package_validations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM package_validations ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._package_validation_row(row) for row in rows]

    def record_pipeline_run(self, config_path: str, report: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pipeline_runs (config_path, status, report_json)
                VALUES (?, ?, ?)
                """,
                (
                    config_path,
                    str(report.get("status", "unknown")),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            run_id = _required_lastrowid(cursor)
        return self.get_pipeline_run(run_id)

    def list_pipeline_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pipeline_runs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._pipeline_run_row(row) for row in rows]

    def get_pipeline_run(self, run_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(str(run_id))
        return self._pipeline_run_row(row)

    def create_pipeline_job(self, config_path: str, request: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pipeline_jobs (config_path, status, request_json)
                VALUES (?, 'queued', ?)
                """,
                (config_path, json.dumps(request, ensure_ascii=False)),
            )
            job_id = _required_lastrowid(cursor)
        return self.get_pipeline_job(job_id)

    def claim_pipeline_job(self, job_id: int, worker_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs
                SET status='running', started_at=?, updated_at=?, worker_id=?, heartbeat_at=?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, worker_id or None, now, job_id),
            )
        return self.get_pipeline_job(job_id)

    def heartbeat_pipeline_job(self, job_id: int, worker_id: str) -> None:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs
                SET heartbeat_at=?, updated_at=?
                WHERE id = ? AND worker_id = ? AND status IN ('running', 'cancellation_requested')
                """,
                (now, now, job_id, worker_id),
            )

    def mark_pipeline_job_running(self, job_id: int) -> dict[str, Any]:
        return self.claim_pipeline_job(job_id, "")

    def complete_pipeline_job(self, job_id: int, result: dict[str, Any], *, status: str = "completed") -> dict[str, Any]:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs
                SET status=?, result_json=?, completed_at=?, updated_at=?
                WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled')
                """,
                (status, json.dumps(result, ensure_ascii=False), now, now, job_id),
            )
        return self.get_pipeline_job(job_id)

    def fail_pipeline_job(self, job_id: int, error: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs
                SET status='failed', error=?, result_json=?, completed_at=?, updated_at=?
                WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled')
                """,
                (error, json.dumps(result, ensure_ascii=False) if result is not None else None, now, now, job_id),
            )
        return self.get_pipeline_job(job_id)

    def request_pipeline_job_cancel(self, job_id: int) -> dict[str, Any]:
        # 单条带守卫的 UPDATE 完成状态转移，杜绝 read-then-write 竞态回退终态。
        self.get_pipeline_job(job_id)  # 确认存在，缺失时抛 KeyError
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs
                SET status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE 'cancellation_requested' END,
                    cancelled_at=?, updated_at=?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (now, now, job_id),
            )
        return self.get_pipeline_job(job_id)

    def recover_orphaned_jobs(
        self,
        *,
        worker_id: str = "",
        stale_after_seconds: int = 120,
        error: str = "orphaned by service restart",
    ) -> list[dict[str, Any]]:
        """回收本 worker 的遗留任务和心跳超时任务，不影响其他存活实例。"""
        now = datetime.now(UTC)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        cutoff = (now - timedelta(seconds=stale_after_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM pipeline_jobs
                WHERE status IN ('running', 'cancellation_requested')
                  AND (worker_id = ? OR worker_id IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
                """,
                (worker_id, cutoff),
            ).fetchall()
            orphan_ids = [int(row["id"] if isinstance(row, dict) else row[0]) for row in rows]
            if orphan_ids:
                connection.execute(
                    f"""
                    UPDATE pipeline_jobs
                    SET status='failed', error=?, completed_at=?, updated_at=?
                    WHERE id IN ({','.join('?' * len(orphan_ids))})
                    """,
                    (error, now_iso, now_iso, *orphan_ids),
                )
        return [self.get_pipeline_job(job_id) for job_id in orphan_ids]

    def list_queued_pipeline_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pipeline_jobs WHERE status = 'queued' ORDER BY id ASC"
            ).fetchall()
        return [self._pipeline_job_row(row) for row in rows]

    def has_active_pipeline_job(self, config_path: str) -> bool:
        """同一配置存在未完结任务时返回 True，用于阻止并发重复执行互相覆盖产物。"""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM pipeline_jobs
                WHERE config_path = ? AND status IN ('queued', 'running', 'cancellation_requested')
                LIMIT 1
                """,
                (config_path,),
            ).fetchone()
        return row is not None

    def list_pipeline_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pipeline_jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._pipeline_job_row(row) for row in rows]

    def get_pipeline_job(self, job_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(str(job_id))
        return self._pipeline_job_row(row)

    def record_pipeline_job_log(
        self,
        job_id: int,
        stream: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = detail or {}
        created_at = utc_now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pipeline_job_logs (job_id, stream, message, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, stream, message, json.dumps(payload, ensure_ascii=False), created_at),
            )
            log_id = _required_lastrowid(cursor)
        # 不回读：这是训练期间最热的写路径，多一次 SELECT 会让开销翻倍。
        return {
            "id": log_id,
            "job_id": job_id,
            "stream": stream,
            "message": message,
            "detail": payload,
            "created_at": created_at,
        }

    def record_pipeline_job_logs(self, job_id: int, entries: list[tuple[str, str]]) -> int:
        """批量写入 (stream, message) 日志行，单事务提交。

        外部命令输出逐行到达，逐行独立事务会把写放大到每秒数百次；
        调用方应缓冲后批量落库。
        """
        if not entries:
            return 0
        created_at = utc_now_iso()
        with self.connect() as connection:
            for stream, message in entries:
                connection.execute(
                    """
                    INSERT INTO pipeline_job_logs (job_id, stream, message, detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, stream, message, "{}", created_at),
                )
        return len(entries)

    def list_pipeline_job_logs(
        self,
        job_id: int,
        limit: int = 200,
        *,
        since_id: int | None = None,
        tail: bool = False,
    ) -> list[dict[str, Any]]:
        """列出任务日志。since_id 用于增量拉取；tail=True 返回最新 N 条（升序返回）。"""
        with self.connect() as connection:
            if since_id is not None:
                rows = connection.execute(
                    """
                    SELECT * FROM pipeline_job_logs
                    WHERE job_id = ? AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (job_id, since_id, limit),
                ).fetchall()
            elif tail:
                rows = connection.execute(
                    """
                    SELECT * FROM pipeline_job_logs
                    WHERE job_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (job_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM pipeline_job_logs
                    WHERE job_id = ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (job_id, limit),
                ).fetchall()
        return [self._pipeline_job_log_row(row) for row in rows]

    def get_pipeline_job_log(self, log_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_job_logs WHERE id = ?", (log_id,)).fetchone()
        if row is None:
            raise KeyError(str(log_id))
        return self._pipeline_job_log_row(row)

    def record_pipeline_artifact(
        self,
        *,
        name: str,
        kind: str,
        job_id: int | None = None,
        run_id: int | None = None,
        path: str | None = None,
        uri: str | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pipeline_artifacts (job_id, run_id, name, kind, path, uri, size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, run_id, name, kind, path, uri, size),
            )
            artifact_id = _required_lastrowid(cursor)
        return self.get_pipeline_artifact(artifact_id)

    def list_pipeline_artifacts(
        self,
        *,
        job_id: int | None = None,
        run_id: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if job_id is None and run_id is None:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM pipeline_artifacts ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._pipeline_artifact_row(row) for row in rows]
        field = "job_id" if job_id is not None else "run_id"
        value = job_id if job_id is not None else run_id
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM pipeline_artifacts WHERE {field} = ? ORDER BY id ASC LIMIT ?",
                (value, limit),
            ).fetchall()
        return [self._pipeline_artifact_row(row) for row in rows]

    def get_pipeline_artifact(self, artifact_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(str(artifact_id))
        return self._pipeline_artifact_row(row)

    def record_audit_event(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = detail or {}
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_events (actor, action, target, detail_json)
                VALUES (?, ?, ?, ?)
                """,
                (actor, action, target, json.dumps(payload, ensure_ascii=False)),
            )
            event_id = _required_lastrowid(cursor)
        return self.get_audit_event(event_id)

    def list_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._audit_event_row(row) for row in rows]

    def upsert_dataset_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        labels = payload.get("labels") or []
        split_counts = payload.get("split_counts") or {}
        record = {
            "dataset_id": payload["dataset_id"],
            "name": payload["name"],
            "version": payload["version"],
            "task": payload["task"],
            "manifest_path": payload["manifest_path"],
            "split_counts_json": json.dumps(split_counts, ensure_ascii=False),
            "labels_json": json.dumps(labels, ensure_ascii=False),
            "status": payload.get("status", "registered"),
            "updated_at": utc_now_iso(),
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO dataset_versions
                    (dataset_id, name, version, task, manifest_path, split_counts_json, labels_json, status)
                VALUES
                    (:dataset_id, :name, :version, :task, :manifest_path, :split_counts_json, :labels_json, :status)
                ON CONFLICT(dataset_id) DO UPDATE SET
                    name=excluded.name,
                    version=excluded.version,
                    task=excluded.task,
                    manifest_path=excluded.manifest_path,
                    split_counts_json=excluded.split_counts_json,
                    labels_json=excluded.labels_json,
                    status=excluded.status,
                    updated_at=:updated_at
                """,
                record,
            )
        return self.get_dataset_version(payload["dataset_id"])

    def get_dataset_version(self, dataset_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM dataset_versions WHERE dataset_id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise KeyError(dataset_id)
        return self._dataset_version_row(row)

    def list_dataset_versions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dataset_versions ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._dataset_version_row(row) for row in rows]

    def upsert_model_registry_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        metrics = payload.get("metrics") or {}
        record = {
            "model_id": payload["model_id"],
            "package_dir": payload["package_dir"],
            "artifact_name": payload["artifact_name"],
            "version": payload["version"],
            "task": payload["task"],
            "metrics_json": json.dumps(metrics, ensure_ascii=False),
            "stage": payload.get("stage", "candidate"),
            "updated_at": utc_now_iso(),
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_registry
                    (model_id, package_dir, artifact_name, version, task, metrics_json, stage)
                VALUES
                    (:model_id, :package_dir, :artifact_name, :version, :task, :metrics_json, :stage)
                ON CONFLICT(model_id) DO UPDATE SET
                    package_dir=excluded.package_dir,
                    artifact_name=excluded.artifact_name,
                    version=excluded.version,
                    task=excluded.task,
                    metrics_json=excluded.metrics_json,
                    stage=excluded.stage,
                    updated_at=:updated_at
                """,
                record,
            )
        return self.get_model_registry_entry(payload["model_id"])

    def get_model_registry_entry(self, model_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM model_registry WHERE model_id = ?", (model_id,)).fetchone()
        if row is None:
            raise KeyError(model_id)
        return self._model_registry_row(row)

    def list_model_registry_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_registry ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._model_registry_row(row) for row in rows]

    def record_release_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO release_approvals (model_id, recommendation, status, decision_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    payload["model_id"],
                    payload["recommendation"],
                    payload.get("status", "pending"),
                    json.dumps(payload.get("decision", {}), ensure_ascii=False),
                ),
            )
            approval_id = _required_lastrowid(cursor)
        return self.get_release_approval(approval_id)

    def get_release_approval(self, approval_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM release_approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(str(approval_id))
        return self._release_approval_row(row)

    def list_release_approvals(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM release_approvals ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._release_approval_row(row) for row in rows]

    def upsert_deployment_rollout(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO deployment_rollouts
                    (model_id, environment, strategy, status, traffic_percent, rollback_target)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["model_id"],
                    payload.get("environment", "production"),
                    payload.get("strategy", "gray"),
                    payload.get("status", "planned"),
                    int(payload.get("traffic_percent", 0)),
                    payload.get("rollback_target"),
                ),
            )
            rollout_id = _required_lastrowid(cursor)
        return self.get_deployment_rollout(rollout_id)

    def get_deployment_rollout(self, rollout_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM deployment_rollouts WHERE id = ?", (rollout_id,)).fetchone()
        if row is None:
            raise KeyError(str(rollout_id))
        return self._deployment_rollout_row(row)

    def list_deployment_rollouts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM deployment_rollouts ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._deployment_rollout_row(row) for row in rows]

    def count_users(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"])

    def create_user(self, username: str, password_salt: str, password_hash: str, *, role: str = "admin") -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO users (username, password_salt, password_hash, role) VALUES (?, ?, ?, ?)",
                (username, password_salt, password_hash, role),
            )
        return self.get_user_by_username(username)

    def get_user_by_username(self, username: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise KeyError(username)
        return self._user_row(row)

    def update_user_password(self, username: str, password_salt: str, password_hash: str) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                "UPDATE users SET password_salt = ?, password_hash = ?, updated_at = ? WHERE username = ?",
                (password_salt, password_hash, utc_now_iso(), username),
            )
        # 密码变更后撤销该用户全部会话，避免旧令牌继续有效。
        user = self.get_user_by_username(username)
        with self.connect() as connection:
            connection.execute("UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user["id"],))
        return user

    def create_auth_session(self, token_hash: str, user_id: int, username: str, expires_at: str) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, username, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, username, expires_at),
            )
            row = connection.execute("SELECT * FROM auth_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return self._auth_session_row(row)

    def get_auth_session(self, token_hash: str) -> dict[str, Any] | None:
        """返回未撤销且未过期的会话；无效则返回 None。"""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE token_hash = ? AND revoked = 0 AND expires_at > ?",
                (token_hash, utc_now_iso()),
            ).fetchone()
        return self._auth_session_row(row) if row is not None else None

    def revoke_auth_session(self, token_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE auth_sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,))

    def purge_expired_auth_sessions(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM auth_sessions WHERE revoked = 1 OR expires_at <= ?",
                (utc_now_iso(),),
            )
            deleted = getattr(cursor, "rowcount", 0) or 0
        return int(deleted)

    def purge_old_records(self, *, retention_days: int) -> dict[str, int]:
        """按保留期清理任务日志与审计事件。"""
        if retention_days <= 0:
            return {"pipeline_job_logs": 0, "audit_events": 0}
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        deleted: dict[str, int] = {}
        with self.connect() as connection:
            for table in ("pipeline_job_logs", "audit_events"):
                cursor = connection.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
                deleted[table] = int(getattr(cursor, "rowcount", 0) or 0)
        return deleted

    def get_audit_event(self, event_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM audit_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(str(event_id))
        return self._audit_event_row(row)

    def get_package_validation(self, validation_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM package_validations WHERE id = ?", (validation_id,)).fetchone()
        if row is None:
            raise KeyError(str(validation_id))
        return self._package_validation_row(row)

    @staticmethod
    def _experiment_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["metrics"] = json.loads(record.pop("metrics_json") or "{}")
        return record

    @staticmethod
    def _package_validation_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["ok"] = bool(record["ok"])
        record["report"] = json.loads(record.pop("report_json") or "{}")
        return record

    @staticmethod
    def _pipeline_run_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["report"] = json.loads(record.pop("report_json") or "{}")
        return record

    @staticmethod
    def _pipeline_job_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["request"] = json.loads(record.pop("request_json") or "{}")
        result_json = record.pop("result_json")
        record["result"] = json.loads(result_json) if result_json else None
        return record

    @staticmethod
    def _pipeline_job_log_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["detail"] = json.loads(record.pop("detail_json") or "{}")
        return record

    @staticmethod
    def _pipeline_artifact_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _audit_event_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["detail"] = json.loads(record.pop("detail_json") or "{}")
        return record

    @staticmethod
    def _dataset_version_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["split_counts"] = json.loads(record.pop("split_counts_json") or "{}")
        record["labels"] = json.loads(record.pop("labels_json") or "[]")
        return record

    @staticmethod
    def _model_registry_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["metrics"] = json.loads(record.pop("metrics_json") or "{}")
        return record

    @staticmethod
    def _release_approval_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["decision"] = json.loads(record.pop("decision_json") or "{}")
        return record

    @staticmethod
    def _deployment_rollout_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _user_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _auth_session_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["revoked"] = bool(record["revoked"])
        return record


class _PostgresCursorAdapter:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor
        self._lastrowid: int | None = None
        self._lastrowid_loaded = False

    @property
    def lastrowid(self) -> int | None:
        if not self._lastrowid_loaded:
            self._lastrowid_loaded = True
            if self.cursor.description:
                row = self.cursor.fetchone()
                if row:
                    self._lastrowid = int(row["id"] if isinstance(row, dict) else row[0])
        return self._lastrowid

    def fetchone(self) -> Any:
        return self.cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self.cursor.fetchall()


class _PostgresConnectionAdapter:
    _returning_tables = {
        "package_validations",
        "pipeline_runs",
        "pipeline_jobs",
        "audit_events",
        "pipeline_job_logs",
        "pipeline_artifacts",
        "release_approvals",
        "deployment_rollouts",
        "users",
        "auth_sessions",
    }

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, sql: str, params: Any = None) -> _PostgresCursorAdapter:
        prepared = self._prepare_sql(sql, params)
        cursor = self.connection.execute(prepared, params or ())
        return _PostgresCursorAdapter(cursor)

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _prepare_sql(self, sql: str, params: Any) -> str:
        prepared = sql
        if isinstance(params, dict):
            # 按词边界替换命名参数，避免前缀参数名（:task 与 :task_id）互相破坏。
            prepared = re.sub(r":(\w+)\b", r"%(\1)s", prepared)
        else:
            prepared = prepared.replace("?", "%s")
        stripped = prepared.strip()
        upper = stripped.upper()
        if upper.startswith("INSERT INTO") and "RETURNING" not in upper and "ON CONFLICT" not in upper:
            parts = stripped.split()
            table = parts[2].strip('"') if len(parts) > 2 else ""
            if table in self._returning_tables:
                prepared = prepared.rstrip().rstrip(";") + " RETURNING id"
        return prepared


class PostgresMetadataStore(MetadataStore):
    def __init__(self, dsn: str) -> None:
        self.path = dsn
        self._pool: Any = None
        self._pool_lock = threading.Lock()
        self.initialize()

    @property
    def _shared_connection(self) -> None:
        return None

    def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        with self._pool_lock:
            if self._pool is not None:
                return self._pool
            try:
                from psycopg_pool import ConnectionPool  # pyright: ignore[reportMissingImports]
            except ImportError:
                self._pool = False  # psycopg_pool 未安装时回落到按需建连
                return self._pool
            self._pool = ConnectionPool(
                str(self.path),
                min_size=1,
                max_size=int(os.environ.get("VMLAB_PG_POOL_MAX_SIZE", "8")),
                timeout=30,
                open=True,
            )
            return self._pool

    @contextmanager
    def connect(self) -> Iterator[Any]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - optional deployment extra
            raise RuntimeError("psycopg is required for PostgreSQL metadata storage; install vision-model-lab[postgres].") from exc
        pool = self._ensure_pool()
        if pool:
            # 连接池路径：借还连接，PG 自身保证并发安全，无需进程级锁。
            with pool.connection() as connection:
                connection.row_factory = dict_row
                adapter = _PostgresConnectionAdapter(connection)
                yield adapter
                connection.commit()
            return
        row_factory: Any = dict_row
        connection = psycopg.connect(str(self.path), row_factory=row_factory, connect_timeout=10)
        adapter = _PostgresConnectionAdapter(connection)
        try:
            yield adapter
            adapter.commit()
        finally:
            adapter.close()

    def _initialize_connection(self, connection: Any) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                dataset TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                package TEXT,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for table_sql in (
            """
            CREATE TABLE IF NOT EXISTS package_validations (
                id BIGSERIAL PRIMARY KEY,
                package_dir TEXT NOT NULL,
                model_file TEXT,
                ok INTEGER NOT NULL,
                sha256 TEXT,
                report_json TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id BIGSERIAL PRIMARY KEY,
                config_path TEXT NOT NULL,
                status TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                id BIGSERIAL PRIMARY KEY,
                config_path TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                cancelled_at TIMESTAMPTZ,
                worker_id TEXT,
                heartbeat_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id BIGSERIAL PRIMARY KEY,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pipeline_job_logs (
                id BIGSERIAL PRIMARY KEY,
                job_id BIGINT NOT NULL,
                stream TEXT NOT NULL,
                message TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pipeline_artifacts (
                id BIGSERIAL PRIMARY KEY,
                job_id BIGINT,
                run_id BIGINT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT,
                uri TEXT,
                size BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dataset_versions (
                id BIGSERIAL PRIMARY KEY,
                dataset_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                task TEXT NOT NULL,
                manifest_path TEXT NOT NULL,
                split_counts_json TEXT NOT NULL DEFAULT '{}',
                labels_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'registered',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS model_registry (
                id BIGSERIAL PRIMARY KEY,
                model_id TEXT NOT NULL UNIQUE,
                package_dir TEXT NOT NULL,
                artifact_name TEXT NOT NULL,
                version TEXT NOT NULL,
                task TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                stage TEXT NOT NULL DEFAULT 'candidate',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS release_approvals (
                id BIGSERIAL PRIMARY KEY,
                model_id TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS deployment_rollouts (
                id BIGSERIAL PRIMARY KEY,
                model_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL,
                traffic_percent INTEGER NOT NULL DEFAULT 0,
                rollback_target TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id BIGSERIAL PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                user_id BIGINT NOT NULL,
                username TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMPTZ NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """,
        ):
            connection.execute(table_sql)
        self._ensure_pipeline_job_columns(connection)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions(expires_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status ON pipeline_jobs(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_job_logs_job_id ON pipeline_job_logs(job_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_artifacts_job_id ON pipeline_artifacts(job_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_artifacts_run_id ON pipeline_artifacts(run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_model_registry_stage ON model_registry(stage)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_release_approvals_model_id ON release_approvals(model_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_deployment_rollouts_model_id ON deployment_rollouts(model_id)")
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (?) ON CONFLICT(version) DO NOTHING",
            (SCHEMA_VERSION,),
        )

    def _ensure_pipeline_job_columns(self, connection: Any) -> None:
        connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS worker_id TEXT")
        connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ")


def metadata_store_from_uri(uri: str | Path = ":memory:") -> MetadataStore:
    value = str(uri)
    if value.startswith(("postgresql://", "postgres://")):
        return PostgresMetadataStore(value)
    return MetadataStore(uri)

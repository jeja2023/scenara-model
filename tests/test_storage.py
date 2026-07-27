from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from vision_model_lab.storage import MetadataStore


def test_metadata_store_initializes_file_database_without_probe_files(workspace_tmp_path: Path) -> None:
    database = workspace_tmp_path / "store.sqlite3"

    store = MetadataStore(database)
    saved = store.upsert_experiment(
        {
            "id": "storage_file_test",
            "task": "classification",
            "dataset": "dataset_v1.0.0",
            "model": "resnet50",
            "status": "completed",
            "metrics": {"accuracy": 1.0},
        }
    )

    assert saved["id"] == "storage_file_test"
    assert database.exists()
    assert not list(workspace_tmp_path.glob(".vmlab-wal-probe-*"))


def test_file_database_uses_wal_not_memory_journal(workspace_tmp_path: Path) -> None:
    """回归：文件库必须真正启用 WAL。

    MEMORY 在文件库上总会成功，一旦排在候选序列最前面 WAL 就永远不会生效，
    而 MEMORY 模式下进程崩溃极可能损坏数据库文件。
    """
    store = MetadataStore(workspace_tmp_path / "journal.sqlite3")

    assert store.journal_mode() == "WAL"


def test_metadata_store_pipeline_job_lifecycle() -> None:
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {"package": False})
    running = store.mark_pipeline_job_running(int(job["id"]))
    completed = store.complete_pipeline_job(int(job["id"]), {"status": "completed"})

    assert job["status"] == "queued"
    assert running["status"] == "running"
    assert completed["status"] == "completed"
    assert completed["result"] == {"status": "completed"}
    assert store.list_pipeline_jobs()[0]["id"] == job["id"]

def test_metadata_store_job_logs_artifacts_and_mlops_records() -> None:
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {"package": True})
    log = store.record_pipeline_job_log(int(job["id"]), "training", "completed", {"status": "completed"})
    artifact = store.record_pipeline_artifact(job_id=int(job["id"]), name="model.onnx", kind="onnx", path="model.onnx", uri="model.onnx", size=12)
    dataset = store.upsert_dataset_version(
        {
            "dataset_id": "people_v1.0.0",
            "name": "people",
            "version": "1.0.0",
            "task": "detection",
            "manifest_path": "data/train.jsonl",
            "split_counts": {"train": 10},
            "labels": ["person"],
        }
    )
    model = store.upsert_model_registry_entry(
        {
            "model_id": "cross_camera_tracking/model.onnx",
            "package_dir": "shared-models/cross_camera_tracking",
            "artifact_name": "model.onnx",
            "version": "1.0.0",
            "task": "detection",
            "metrics": {"map50": 0.5},
            "stage": "candidate",
        }
    )
    approval = store.record_release_approval(
        {"model_id": model["model_id"], "recommendation": "gray_release", "status": "approved", "decision": {"model": model["model_id"]}}
    )
    rollout = store.upsert_deployment_rollout(
        {"model_id": model["model_id"], "environment": "production", "strategy": "gray", "status": "planned", "traffic_percent": 10}
    )

    assert log["detail"] == {"status": "completed"}
    assert artifact["kind"] == "onnx"
    assert store.list_pipeline_job_logs(int(job["id"]))[0]["id"] == log["id"]
    assert store.list_pipeline_artifacts(job_id=int(job["id"]))[0]["id"] == artifact["id"]
    assert dataset["split_counts"] == {"train": 10}
    assert model["metrics"] == {"map50": 0.5}
    assert approval["status"] == "approved"
    assert rollout["traffic_percent"] == 10


def test_alembic_upgrade_supports_plain_sqlite_path_and_creates_base_tables(workspace_tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    database = workspace_tmp_path / "alembic_upgrade.sqlite3"
    env = {**os.environ, "VMLAB_WORKSPACE": str(repo_root), "VMLAB_METADATA_DB": str(database)}

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=repo_root, env=env, check=True, capture_output=True, text=True)

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        pipeline_job_columns = {row[1] for row in connection.execute("pragma table_info(pipeline_jobs)")}

    assert {
        "experiments",
        "package_validations",
        "pipeline_runs",
        "pipeline_jobs",
        "audit_events",
        "pipeline_job_logs",
        "pipeline_artifacts",
        "dataset_versions",
        "model_registry",
        "release_approvals",
        "deployment_rollouts",
    } <= tables
    assert {"worker_id", "heartbeat_at"} <= pipeline_job_columns


def test_terminal_job_status_cannot_be_reverted_by_cancel() -> None:
    """回归：completed/failed/cancelled 终态不得被取消请求回退。"""
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {"package": False})
    store.mark_pipeline_job_running(int(job["id"]))
    store.complete_pipeline_job(int(job["id"]), {"status": "completed"})

    after_cancel = store.request_pipeline_job_cancel(int(job["id"]))

    assert after_cancel["status"] == "completed"
    assert after_cancel["cancelled_at"] is None


def test_complete_does_not_overwrite_cancelled_job() -> None:
    """回归：已取消任务不得被迟到的 complete 覆盖为 completed。"""
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {"package": False})
    cancelled = store.request_pipeline_job_cancel(int(job["id"]))
    assert cancelled["status"] == "cancelled"

    after_complete = store.complete_pipeline_job(int(job["id"]), {"status": "completed"})

    assert after_complete["status"] == "cancelled"


def test_cancel_running_job_requests_cancellation() -> None:
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {"package": False})
    store.mark_pipeline_job_running(int(job["id"]))
    cancelled = store.request_pipeline_job_cancel(int(job["id"]))

    assert cancelled["status"] == "cancellation_requested"


def test_recover_orphaned_jobs_marks_running_as_failed() -> None:
    """回归：服务重启后 running/cancellation_requested 任务必须被回收为 failed。"""
    store = MetadataStore(":memory:")

    running_job = store.create_pipeline_job("configs/experiments/a.yml", {})
    store.mark_pipeline_job_running(int(running_job["id"]))
    queued_job = store.create_pipeline_job("configs/experiments/b.yml", {})

    orphans = store.recover_orphaned_jobs()

    assert [job["id"] for job in orphans] == [running_job["id"]]
    assert store.get_pipeline_job(int(running_job["id"]))["status"] == "failed"
    assert store.get_pipeline_job(int(queued_job["id"]))["status"] == "queued"
    assert [job["id"] for job in store.list_queued_pipeline_jobs()] == [queued_job["id"]]


def test_recover_orphaned_jobs_spares_other_live_workers() -> None:
    store = MetadataStore(":memory:")
    mine = int(store.create_pipeline_job("configs/a.yml", {})["id"])
    theirs = int(store.create_pipeline_job("configs/b.yml", {})["id"])
    store.claim_pipeline_job(mine, "host-a:1")
    store.claim_pipeline_job(theirs, "host-b:2")
    store.heartbeat_pipeline_job(theirs, "host-b:2")

    recovered = store.recover_orphaned_jobs(worker_id="host-a:1")

    assert [int(job["id"]) for job in recovered] == [mine]
    assert store.get_pipeline_job(mine)["status"] == "failed"
    assert store.get_pipeline_job(theirs)["status"] == "running"


def test_purge_old_records_applies_retention_window() -> None:
    store = MetadataStore(":memory:")
    job = store.create_pipeline_job("configs/experiments/example.yml", {})
    store.record_pipeline_job_log(int(job["id"]), "stdout", "expired")
    store.record_audit_event(actor="test", action="expired", target="record", detail={})
    with store.connect() as connection:
        connection.execute("UPDATE pipeline_job_logs SET created_at = '2000-01-01T00:00:00.000Z'")
        connection.execute("UPDATE audit_events SET created_at = '2000-01-01T00:00:00.000Z'")

    deleted = store.purge_old_records(retention_days=30)

    assert deleted == {"pipeline_job_logs": 1, "audit_events": 1}
    assert store.list_pipeline_job_logs(int(job["id"])) == []
    assert store.list_audit_events() == []


def test_pipeline_job_logs_since_id_and_tail() -> None:
    """回归：日志支持 since_id 增量拉取与 tail 取尾。"""
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {})
    ids = [int(store.record_pipeline_job_log(int(job["id"]), "stdout", f"line {i}")["id"]) for i in range(10)]

    incremental = store.list_pipeline_job_logs(int(job["id"]), since_id=ids[6])
    assert [log["id"] for log in incremental] == ids[7:]

    tail = store.list_pipeline_job_logs(int(job["id"]), limit=3, tail=True)
    assert [log["id"] for log in tail] == ids[-3:]


def test_timestamps_are_timezone_annotated_iso8601() -> None:
    """回归：时间戳必须是带 Z 后缀的 ISO8601，浏览器解析不产生时区偏移。"""
    store = MetadataStore(":memory:")

    job = store.create_pipeline_job("configs/experiments/example.yml", {})
    running = store.mark_pipeline_job_running(int(job["id"]))

    assert "T" in job["created_at"] and job["created_at"].endswith("Z")
    assert running["started_at"] is not None and running["started_at"].endswith("Z")


def test_has_active_pipeline_job_detects_concurrent_config() -> None:
    store = MetadataStore(":memory:")

    store.create_pipeline_job("configs/experiments/example.yml", {})

    assert store.has_active_pipeline_job("configs/experiments/example.yml") is True
    assert store.has_active_pipeline_job("configs/experiments/other.yml") is False


def test_reset_preserves_nonempty_wal_file(workspace_tmp_path: Path) -> None:
    """回归：主库 0 字节但 WAL 非空时 reset 不得删除（已提交数据可能全在 WAL 中）。"""
    database = workspace_tmp_path / "wal_guard.sqlite3"
    database.write_bytes(b"")
    wal_file = Path(str(database) + "-wal")
    wal_file.write_bytes(b"fake-wal-with-committed-data")

    # 直接验证 reset 守卫本身：WAL 非空时必须拒绝清理（返回 False 且不删文件）。
    store = MetadataStore.__new__(MetadataStore)
    store.path = database
    store._lock = __import__("threading").RLock()

    assert store._reset_empty_database_files() is False
    assert wal_file.exists(), "non-empty WAL must never be deleted by the reset logic"


def test_prepare_sql_handles_prefix_parameter_names_and_literal_question_marks() -> None:
    """回归：PG SQL 改写必须按词边界替换命名参数，前缀参数名不得互相破坏。"""
    from vision_model_lab.storage import _PostgresConnectionAdapter

    adapter = _PostgresConnectionAdapter.__new__(_PostgresConnectionAdapter)

    prepared = adapter._prepare_sql(
        "UPDATE t SET task=:task, task_id=:task_id WHERE id=:id",
        {"task": "a", "task_id": 1, "id": 2},
    )
    assert prepared == "UPDATE t SET task=%(task)s, task_id=%(task_id)s WHERE id=%(id)s"

    inserted = adapter._prepare_sql("INSERT INTO audit_events (actor) VALUES (?)", ("x",))
    assert inserted.rstrip().endswith("RETURNING id")

    non_returning = adapter._prepare_sql("INSERT INTO unknown_table (a) VALUES (?)", ("x",))
    assert "RETURNING" not in non_returning


def test_storage_migrate_stamps_legacy_database_created_by_builtin_ddl(workspace_tmp_path: Path) -> None:
    """回归：旧版内置 DDL 创建的存量库（无 Alembic 戳记）migrate 不得报 table already exists。"""
    repo_root = Path(__file__).resolve().parents[1]
    database = workspace_tmp_path / "legacy.sqlite3"
    # 先用运行时内置 DDL 建库（模拟 0.4.x 存量库，无 alembic_version）。
    MetadataStore(database)

    env = {**os.environ, "VMLAB_WORKSPACE": str(repo_root)}
    result = subprocess.run(
        [sys.executable, "-m", "vision_model_lab.cli", "storage", "migrate", "--uri", str(database)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "stamped existing schema" in result.stdout
    # 幂等：再跑一次走正常 upgrade 路径。
    second = subprocess.run(
        [sys.executable, "-m", "vision_model_lab.cli", "storage", "migrate", "--uri", str(database)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert '"migrated_via": "alembic"' in second.stdout

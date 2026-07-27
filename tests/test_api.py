from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vision_model_lab.api as api
from vision_model_lab.storage import MetadataStore
from vision_model_lab.utils import write_yaml

TEST_ADMIN_PASSWORD = "test-admin-password"


@pytest.fixture(autouse=True)
def isolated_api_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    previous_store = api.STORE
    api.STORE = MetadataStore(":memory:")
    # 引导口令已改为随机生成，测试注入固定值以保持可预期。
    monkeypatch.setattr(api, "SETTINGS", replace(api.SETTINGS, admin_password=TEST_ADMIN_PASSWORD))
    try:
        yield
    finally:
        api.STORE = previous_store


def _client() -> TestClient:
    """已登录的测试客户端：全部 /api 接口现在都要求认证。"""
    client = TestClient(api.app)
    # 测试不经过 lifespan（无 with 块），显式引导默认 admin 账户。
    api._bootstrap_admin_user()
    token = client.post(
        "/api/auth/login",
        json={"username": api.DEFAULT_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    ).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_health_endpoint() -> None:
    client = _client()

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "workspace" in body
    assert "metadata_db" in body
    assert "metadata_journal_mode" in body


def test_health_does_not_run_sqlite_pragma_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "SETTINGS", replace(api.SETTINGS, metadata_db="postgresql://localhost/vmlab"))

    def unexpected_journal_mode() -> str:
        raise AssertionError("PostgreSQL health checks must not execute SQLite PRAGMA statements")

    monkeypatch.setattr(api.STORE, "journal_mode", unexpected_journal_mode)

    assert api.health()["metadata_journal_mode"] == "postgresql"


def test_auth_dependency_reuses_middleware_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    original = api.STORE.get_auth_session
    calls = 0

    def counted(token_hash: str) -> dict[str, object] | None:
        nonlocal calls
        calls += 1
        return original(token_hash)

    monkeypatch.setattr(api.STORE, "get_auth_session", counted)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert calls == 1


def test_cancel_check_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    job = api.STORE.create_pipeline_job("configs/experiments/example.yml", {})
    original = api.STORE.get_pipeline_job
    calls = 0
    now = [1.0]

    def counted(job_id: int) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original(job_id)

    monkeypatch.setattr(api.STORE, "get_pipeline_job", counted)
    monkeypatch.setattr(api.time, "monotonic", lambda: now[0])
    should_cancel = api._CancelCheck(int(job["id"]), interval=60.0)

    assert [should_cancel() for _ in range(50)] == [False] * 50
    assert calls == 1

    now[0] += 60.0
    assert should_cancel() is False
    assert calls == 2


def test_job_log_buffer_flushes_in_one_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    job = api.STORE.create_pipeline_job("configs/experiments/example.yml", {})
    original = api.STORE.record_pipeline_job_logs
    batches: list[list[tuple[str, str]]] = []

    def counted(job_id: int, entries: list[tuple[str, str]]) -> int:
        batches.append(list(entries))
        return original(job_id, entries)

    monkeypatch.setattr(api.STORE, "record_pipeline_job_logs", counted)
    buffer = api._JobLogBuffer(int(job["id"]), max_entries=10, max_interval=60.0)

    buffer.add("stdout", "one")
    buffer.add("stderr", "two")
    assert batches == []

    buffer.flush()

    assert batches == [[("stdout", "one"), ("stderr", "two")]]


def test_manifest_validation_endpoint() -> None:
    client = _client()

    response = client.post("/api/manifests/validate", json={"path": "data/manifests/example_train_v1.jsonl"})

    assert response.status_code == 200
    assert response.json()["manifest"]["ok"] is True


def test_experiment_endpoint_round_trip() -> None:
    client = _client()
    payload = {
        "id": "api_test_experiment",
        "task": "detection",
        "dataset": "dataset_v1.0.0",
        "model": "yolov8n",
        "status": "planned",
        "metrics": {"map50": 0.5},
    }

    post_response = client.post("/api/experiments", json=payload)
    get_response = client.get("/api/experiments")

    assert post_response.status_code == 200
    assert get_response.status_code == 200
    assert any(item["id"] == "api_test_experiment" for item in get_response.json()["experiments"])


def test_path_escape_is_rejected() -> None:
    client = _client()

    response = client.post("/api/manifests/validate", json={"path": "../outside.jsonl"})

    assert response.status_code == 400


def test_templates_endpoint() -> None:
    client = _client()

    response = client.get("/api/templates")

    assert response.status_code == 200
    assert "model_card" in response.json()


def test_contract_validation_endpoint() -> None:
    client = _client()

    response = client.post(
        "/api/contracts/validate",
        json={"kind": "models-fragment", "path": "configs/export/models.fragment.template.yml"},
    )

    assert response.status_code == 200
    assert response.json()["contract"]["ok"] is True


def test_adapters_endpoint() -> None:
    client = _client()

    response = client.get("/api/adapters")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()["adapters"]}
    assert "detection_yolo_baseline" in names


def test_pipeline_package_audit_and_error_analysis_endpoints() -> None:
    client = _client()

    run_response = client.post(
        "/api/pipelines/run",
        json={"config_path": "configs/experiments/detection_yolo_baseline.yml", "package": True},
    )
    runs_response = client.get("/api/pipelines/runs")
    package_response = client.post(
        "/api/packages/create",
        json={"config_path": "configs/experiments/detection_yolo_baseline.yml"},
    )
    error_response = client.post("/api/error-analysis", json={"path": "data/manifests/example_train_v1.jsonl"})
    audit_response = client.get("/api/audit-events")

    assert run_response.status_code == 200
    assert run_response.json()["run"]["report"]["status"] == "completed"
    assert runs_response.status_code == 200
    assert package_response.status_code == 200
    assert package_response.json()["package"]["validation"]["ok"] is True
    assert error_response.status_code == 200
    assert error_response.json()["analysis"]["total"] == 2
    assert audit_response.status_code == 200
    assert audit_response.json()["events"]


def test_frontend_fallback_does_not_serve_workspace_files() -> None:
    client = _client()

    response = client.get("/%2e%2e/%2e%2e/pyproject.toml")

    assert response.status_code in {200, 404}
    assert "[build-system]" not in response.text


def test_package_validate_rejects_absolute_model_id_outside_package() -> None:
    client = _client()
    outside_model = Path(
        "experiments/local_runs/person_detector_20260603_001/export/person_detector_yolov8n_v1.0.0_fp32.onnx"
    ).resolve()

    response = client.post(
        "/api/packages/validate",
        json={
            "package_dir": "shared-models",
            "model_id": str(outside_model),
            "strict_examples": False,
            "persist": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["validation"]["issues"][0]["code"] == "package.model_outside_package"


def test_async_pipeline_job_completes() -> None:
    client = _client()

    response = client.post(
        "/api/pipelines/run",
        json={"config_path": "configs/experiments/detection_yolo_baseline.yml", "package": False, "async": True},
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    job = response.json()["job"]
    for _ in range(40):
        job_response = client.get(f"/api/pipelines/jobs/{job_id}")
        assert job_response.status_code == 200
        job = job_response.json()["job"]
        if job["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.1)

    assert job["status"] == "completed"
    assert job["result"]["status"] == "completed"


def test_async_pipeline_job_can_be_cancelled(workspace_tmp_path: Path) -> None:
    client = _client()
    marker = workspace_tmp_path / "cancel_marker.txt"
    config = workspace_tmp_path / "cancel_pipeline.yml"
    write_yaml(
        config,
        {
            "experiment": {"id": "cancel_pipeline_job", "task": "classification"},
            "dataset": {"name": "cancel_dataset", "version": "1.0.0"},
            "model": {"architecture": "resnet50", "version": "1.0.0"},
            "training": {
                "adapter": "classification_baseline",
                "command": [
                    sys.executable,
                    "-c",
                    f"import time; from pathlib import Path; Path(r'{marker.resolve()}').write_text('started', encoding='utf-8'); time.sleep(5)",
                ],
            },
            "evaluation": {"adapter": "classification_baseline"},
            "export": {"adapter": "classification_baseline", "artifact_name": "cancel_classifier_resnet50_v1.0.0_fp32.onnx"},
        },
    )

    response = client.post(
        "/api/pipelines/run",
        json={"config_path": str(config), "package": False, "async": True},
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["id"]

    for _ in range(50):
        job = client.get(f"/api/pipelines/jobs/{job_id}").json()["job"]
        if job["status"] == "running":
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"job never started: {job['status']}")

    cancel_response = client.post(f"/api/pipelines/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200

    for _ in range(80):
        job_response = client.get(f"/api/pipelines/jobs/{job_id}")
        assert job_response.status_code == 200
        job = job_response.json()["job"]
        if job["status"] == "cancelled":
            break
        time.sleep(0.1)

    assert job["status"] == "cancelled"
    assert job["result"]["status"] == "cancelled"
    assert job["result"]["cancelled_stage"] == "training"


def test_mlops_registry_release_and_rollout_endpoints() -> None:
    client = _client()

    dataset_response = client.post(
        "/api/datasets/versions",
        json={
            "name": "person_detection_dataset",
            "version": "1.0.0",
            "task": "detection",
            "manifest_path": "data/manifests/example_train_v1.jsonl",
            "labels": ["person"],
            "min_split_counts": {"train": 1, "val": 1},
        },
    )
    model_response = client.post(
        "/api/models/registry",
        json={
            "package_dir": "shared-models/cross_camera_tracking",
            "model_id": "person_detector_yolov8n_v1.0.0_fp32.onnx",
            "stage": "candidate",
            "metrics": {"map50": 0.5},
        },
    )
    approval_response = client.post(
        "/api/releases/approvals",
        json={"path": "configs/export/release-decision.template.yml", "status": "approved"},
    )
    rollout_response = client.post(
        "/api/deployments/rollouts",
        json={
            "model_id": "cross_camera_tracking/person_detector_yolov8n_v1.0.0_fp32.onnx",
            "environment": "production",
            "strategy": "gray",
            "status": "planned",
            "traffic_percent": 10,
            "rollback_target": "cross_camera_tracking/person_detector_yolov8n_v0.9.0_fp32.onnx",
        },
    )

    assert dataset_response.status_code == 200
    assert model_response.status_code == 200
    assert approval_response.status_code == 200
    assert rollout_response.status_code == 200
    assert client.get("/api/datasets/versions").json()["datasets"]
    assert client.get("/api/models/registry").json()["models"]
    assert client.get("/api/releases/approvals").json()["approvals"]
    assert client.get("/api/deployments/rollouts").json()["rollouts"]

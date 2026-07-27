from __future__ import annotations

from pathlib import Path

import pytest

from vision_model_lab.adapters.registry import list_adapters, run_stage
from vision_model_lab.pipeline import run_experiment_pipeline
from vision_model_lab.utils import write_yaml


def test_command_env_strips_platform_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：DSN 口令与管理员口令绝不能流入外部训练命令。"""
    from vision_model_lab.adapters.local_tasks import _command_env

    monkeypatch.setenv("VMLAB_METADATA_DB", "postgresql://u:secret@db:5432/vmlab")
    monkeypatch.setenv("VMLAB_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("VMLAB_AUTH_TOKEN", "tok")
    monkeypatch.setenv("HF_TOKEN", "hf-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH", raising=False)

    env = _command_env()

    assert "VMLAB_METADATA_DB" not in env
    assert "VMLAB_ADMIN_PASSWORD" not in env
    assert "VMLAB_AUTH_TOKEN" not in env
    assert "HF_TOKEN" not in env
    assert env["PATH"] == "/usr/bin"

    # 显式放行后才透传。
    monkeypatch.setenv("VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH", "HF_TOKEN")
    assert _command_env()["HF_TOKEN"] == "hf-secret"


def test_detection_baseline_pipeline_runs_and_packages() -> None:
    result = run_experiment_pipeline(Path("configs/experiments/detection_yolo_baseline.yml"), package=True)

    assert result["status"] == "completed"
    assert result["training"]["status"] == "completed"
    assert result["export"]["onnx"].endswith("person_detector_yolov8n_v1.0.0_fp32.onnx")
    assert result["evaluation"]["metrics"]["map50"] == 0.01
    assert result["package"]["validation"]["ok"] is True


def test_reid_classification_and_segmentation_adapters_are_registered_and_runnable() -> None:
    adapters = {item["name"] for item in list_adapters()}

    assert {
        "detection_yolo_baseline",
        "reid_baseline",
        "classification_baseline",
        "segmentation_baseline",
        "ultralytics_yolo",
        "torchreid",
        "torchvision_classifier",
        "segmentation_framework",
    } <= adapters

    for config in [
        "configs/experiments/reid_baseline.yml",
        "configs/experiments/classification_baseline.yml",
        "configs/experiments/segmentation_baseline.yml",
    ]:
        export_result = run_stage("export", Path(config))
        eval_result = run_stage("evaluation", Path(config))
        assert export_result.status == "completed"
        assert eval_result.status == "completed"
        assert Path(export_result.payload["onnx"]).exists()


def test_external_training_command_is_executed(workspace_tmp_path: Path) -> None:
    marker = workspace_tmp_path / "marker.txt"
    config = workspace_tmp_path / "config.yml"
    write_yaml(
        config,
        {
            "experiment": {"id": "external_command_test", "task": "classification"},
            "dataset": {"name": "external_dataset", "version": "1.0.0"},
            "model": {"architecture": "resnet50", "version": "1.0.0"},
            "training": {
                "adapter": "classification_baseline",
                "command": [
                    "python",
                    "-c",
                    f"from pathlib import Path; Path(r'{marker.resolve()}').write_text('ok', encoding='utf-8')",
                ],
            },
            "evaluation": {"adapter": "classification_baseline"},
            "export": {
                "adapter": "classification_baseline",
                "artifact_name": "external_classifier_resnet50_v1.0.0_fp32.onnx",
            },
        },
    )

    result = run_stage("training", config)

    assert result.status == "completed"
    assert marker.read_text(encoding="utf-8") == "ok"


def test_external_shell_string_command_is_disabled_by_default(workspace_tmp_path: Path) -> None:
    config = workspace_tmp_path / "config.yml"
    write_yaml(
        config,
        {
            "experiment": {"id": "shell_disabled_test", "task": "classification"},
            "dataset": {"name": "external_dataset", "version": "1.0.0"},
            "model": {"architecture": "resnet50", "version": "1.0.0"},
            "training": {"adapter": "classification_baseline", "command": "echo unsafe"},
        },
    )

    result = run_stage("training", config)

    assert result.status == "failed"
    assert result.payload["message"] == "External training command failed."
    assert result.payload["external"]["error_code"] == "external.shell_disabled"

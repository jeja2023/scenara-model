from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scenara_model.adapters.registry import list_adapters, run_stage
from scenara_model.pipeline import run_experiment_pipeline
from scenara_model.utils import sha256_file, write_yaml


def test_command_env_strips_platform_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：DSN 口令与管理员口令绝不能流入外部训练命令。"""
    from scenara_model.adapters.local_tasks import _command_env

    monkeypatch.setenv("SCENARA_MODEL_METADATA_DB", "postgresql://u:secret@db:5432/scenara_model")
    monkeypatch.setenv("SCENARA_MODEL_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("SCENARA_MODEL_AUTH_TOKEN", "tok")
    monkeypatch.setenv("HF_TOKEN", "hf-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("SCENARA_MODEL_EXTERNAL_COMMAND_ENV_PASSTHROUGH", raising=False)

    env = _command_env()

    assert "SCENARA_MODEL_METADATA_DB" not in env
    assert "SCENARA_MODEL_ADMIN_PASSWORD" not in env
    assert "SCENARA_MODEL_AUTH_TOKEN" not in env
    assert "HF_TOKEN" not in env
    assert env["PATH"] == "/usr/bin"

    # 显式放行后才透传。
    monkeypatch.setenv("SCENARA_MODEL_EXTERNAL_COMMAND_ENV_PASSTHROUGH", "HF_TOKEN")
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
        "fastreid",
        "torchvision_classifier",
        "segmentation_framework",
        "paddleocr",
        "paddlevideo",
        "fashion_multihead",
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


@pytest.mark.parametrize(
    ("task", "adapter"),
    (("ocr", "paddleocr"), ("behavior", "paddlevideo"), ("fashion", "fashion_multihead")),
)
def test_multidomain_production_adapters_fail_closed_without_commands(
    workspace_tmp_path: Path,
    task: str,
    adapter: str,
) -> None:
    config = workspace_tmp_path / f"{task}.yml"
    write_yaml(
        config,
        {
            "experiment": {"id": f"{task}_production_gate", "task": task},
            "dataset": {"name": f"{task}_dataset", "version": "1.0.0"},
            "model": {"architecture": "external", "version": "1.0.0"},
            "training": {"adapter": adapter},
        },
    )
    result = run_stage("training", config)
    assert result.status == "failed"
    assert "training.command is required for a production training adapter" in result.payload[
        "preflight_issues"
    ]


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


def test_fastreid_adapter_requires_pinned_provenance_and_cmc_map_metrics(workspace_tmp_path: Path) -> None:
    manifests: dict[str, Path] = {}
    for split in ("train", "val", "test"):
        manifest = workspace_tmp_path / f"{split}.jsonl"
        manifest.write_text(
            f'{{"image":"s3://bucket/{split}.jpg","split":"{split}","source":"camera_01","dataset_version":"1.0.0"}}\n',
            encoding="utf-8",
        )
        manifests[split] = manifest
    lockfile = workspace_tmp_path / "fastreid.lock.json"
    lockfile.write_text('{"fastreid":"pinned"}\n', encoding="utf-8")
    checkpoint = workspace_tmp_path / "run" / "model_best.pth"
    produced_onnx = workspace_tmp_path / "run" / "model_best.onnx"
    metrics = workspace_tmp_path / "run" / "metrics.json"
    prepared_dataset = workspace_tmp_path / "prepared_dataset"
    config = workspace_tmp_path / "fastreid.yml"
    onnx_code = (
        "from pathlib import Path\n"
        "import onnx\n"
        "from onnx import TensorProto, helper\n"
        f"p = Path(r'{produced_onnx}')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "graph = helper.make_graph([helper.make_node('Identity', ['input'], ['output'])], 'fastreid', "
        "[helper.make_tensor_value_info('input', TensorProto.FLOAT, [1, 3, 256, 128])], "
        "[helper.make_tensor_value_info('output', TensorProto.FLOAT, [1, 3, 256, 128])])\n"
        "model = helper.make_model(graph, opset_imports=[helper.make_operatorsetid('', 17)])\n"
        "model.ir_version = 8\n"
        "onnx.save(model, p)\n"
    )
    metrics_code = (
        "import json\n"
        "from pathlib import Path\n"
        f"p = Path(r'{metrics}')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps({'map': 0.71, 'rank1': 0.80, 'rank5': 0.91, 'rank10': 0.95}), encoding='utf-8')\n"
    )
    write_yaml(
        config,
        {
            "experiment": {"id": "fastreid_adapter_test", "task": "reid"},
            "dataset": {
                "name": "portrait_review",
                "version": "1.0.0",
                "train_manifest": str(manifests["train"]),
                "val_manifest": str(manifests["val"]),
                "test_manifest": str(manifests["test"]),
            },
            "model": {"architecture": "osnet_ibn_x1_0", "version": "1.0.0", "input_size": [256, 128]},
            "training": {
                "adapter": "fastreid",
                "framework": {
                    "name": "fastreid",
                    "repository": "https://github.com/JDAI-CV/fast-reid.git",
                    "revision": "a" * 40,
                    "environment_lock": str(lockfile),
                    "environment_lock_sha256": sha256_file(lockfile),
                },
                "command": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; p=Path(r'{checkpoint}'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'checkpoint')",
                ],
                "data_prepare_command": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path(r'{prepared_dataset}').mkdir(parents=True, exist_ok=True)",
                ],
                "prepared_dataset_root": str(prepared_dataset),
                "produced_checkpoint": str(checkpoint),
            },
            "export": {
                "adapter": "fastreid",
                "artifact_name": "person_reid_osnet_ibn_x1_0_v1.0.0_fp32.onnx",
                "command": [sys.executable, "-c", onnx_code],
                "produced_onnx": str(produced_onnx),
            },
            "evaluation": {
                "adapter": "fastreid",
                "protocol": "fastreid-cmc-map-v1",
                "manifest": str(manifests["test"]),
                "command": [sys.executable, "-c", metrics_code],
                "produced_metrics": str(metrics),
            },
            "package": {"profile": "smoke"},
        },
    )

    training = run_stage("training", config)
    evaluation = run_stage("evaluation", config)

    assert training.status == "completed"
    assert training.payload["framework"]["revision"] == "a" * 40
    assert evaluation.status == "completed"
    assert evaluation.payload["metrics"] == {"map": 0.71, "rank1": 0.8, "rank5": 0.91, "rank10": 0.95}

    missing_rank10 = metrics_code.replace(", 'rank10': 0.95", "")
    payload = __import__("yaml").safe_load(config.read_text(encoding="utf-8"))
    payload["evaluation"]["command"] = [sys.executable, "-c", missing_rank10]
    invalid_config = workspace_tmp_path / "fastreid_missing_rank10.yml"
    write_yaml(invalid_config, payload)
    invalid_evaluation = run_stage("evaluation", invalid_config)

    assert invalid_evaluation.status == "failed"
    assert "measured metrics are missing: rank10" in invalid_evaluation.payload["metric_issues"]

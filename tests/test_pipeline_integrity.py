from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scenara_model.adapters.registry import run_stage
from scenara_model.pipeline import run_experiment_pipeline
from scenara_model.utils import sha256_file, write_yaml


def _base_config(workspace_tmp_path: Path, experiment_id: str) -> dict:
    return {
        "experiment": {"id": experiment_id, "task": "classification"},
        "dataset": {"name": "regression_dataset", "version": "1.0.0"},
        "model": {"architecture": "resnet50", "version": "1.0.0"},
        "evaluation": {"adapter": "classification_baseline"},
        "export": {
            "adapter": "classification_baseline",
            "artifact_name": f"{experiment_id}_resnet50_v1.0.0_fp32.onnx",
        },
        "training": {"adapter": "classification_baseline"},
    }


def _make_real_onnx_script(target: Path, shape: list[int] | None = None) -> list[str]:
    """构造一个外部命令：产出与合成桩结构不同的真实 ONNX（含 Add 节点）。"""
    input_shape = shape or [1, 4]
    code = (
        "import onnx\n"
        "from onnx import TensorProto, helper\n"
        "node = helper.make_node('Add', ['input', 'input'], ['output'])\n"
        "graph = helper.make_graph([node], 'external_graph',"
        f" [helper.make_tensor_value_info('input', TensorProto.FLOAT, {input_shape})],"
        f" [helper.make_tensor_value_info('output', TensorProto.FLOAT, {input_shape})])\n"
        "model = helper.make_model(graph, producer_name='external-exporter',"
        " opset_imports=[helper.make_operatorsetid('', 17)])\n"
        "model.ir_version = 8\n"
        f"onnx.save(model, r'{target}')\n"
    )
    return [sys.executable, "-c", code]


def test_production_package_requires_real_evidence_and_writes_provenance(workspace_tmp_path: Path) -> None:
    config = workspace_tmp_path / "config.yml"
    checkpoint = workspace_tmp_path / "run" / "best.pt"
    produced_onnx = workspace_tmp_path / "run" / "best.onnx"
    metrics = workspace_tmp_path / "run" / "metrics.json"
    examples = workspace_tmp_path / "examples"
    examples.mkdir()
    (examples / "input.jpg").write_bytes(bytes.fromhex("ffd8ffe000104a46494600010101000100010000ffd9"))
    (examples / "input.expected.json").write_text('{"predictions": [{"label": "positive", "score": 0.9}]}', encoding="utf-8")
    for split in ("train", "val", "test"):
        manifest = workspace_tmp_path / f"{split}.jsonl"
        manifest.write_text(
            f'{{"image":"s3://bucket/{split}.jpg","split":"{split}","source":"camera_01","dataset_version":"1.0.0"}}\n',
            encoding="utf-8",
        )
    training_code = f"from pathlib import Path; p=Path(r'{checkpoint}'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'fresh-checkpoint')"
    metrics_code = f"import json; p=r'{metrics}'; open(p, 'w', encoding='utf-8').write(json.dumps({{'accuracy': 0.93, 'f1': 0.91}}))"
    payload = {
        "experiment": {"id": "production_evidence", "task": "classification", "project": "quality"},
        "dataset": {
            "name": "quality_dataset",
            "version": "1.0.0",
            "train_manifest": str(workspace_tmp_path / "train.jsonl"),
            "val_manifest": str(workspace_tmp_path / "val.jsonl"),
            "test_manifest": str(workspace_tmp_path / "test.jsonl"),
        },
        "model": {"architecture": "resnet50", "version": "1.0.0", "input_size": [224, 224]},
        "labels": ["negative", "positive"],
        "training": {
            "adapter": "torchvision_classifier",
            "command": [sys.executable, "-c", training_code],
            "produced_checkpoint": str(checkpoint),
        },
        "export": {
            "adapter": "torchvision_classifier",
            "artifact_name": "quality_classifier_resnet50_v1.0.0_fp32.onnx",
            "command": _make_real_onnx_script(produced_onnx, [1, 3, 224, 224]),
            "produced_onnx": str(produced_onnx),
        },
        "evaluation": {
            "adapter": "torchvision_classifier",
            "command": [sys.executable, "-c", metrics_code],
            "produced_metrics": str(metrics),
            "primary_metric": "accuracy",
        },
        "package": {
            "profile": "production",
            "examples_dir": str(examples),
            "metric_thresholds": {"accuracy": 0.8, "f1": 0.8},
            "limitations": ["Synthetic test fixture; replace with validated business data before release."],
        },
    }
    write_yaml(config, payload)

    report = run_experiment_pipeline(config, package=True, output_root=workspace_tmp_path / "packages")

    assert report["status"] == "completed"
    assert report["package"]["profile"] == "production"
    card = __import__("yaml").safe_load(
        (workspace_tmp_path / "packages" / "quality" / "quality_classifier_resnet50_v1.0.0_fp32.model-card.yml").read_text(encoding="utf-8")
    )
    assert card["metrics"] == {"accuracy": 0.93, "f1": 0.91}
    assert card["provenance"]["export"]["onnx_source"] == "external_command"
    assert card["provenance"]["evaluation"]["metrics_source"] == "measured"
    assert card["provenance"]["training"]["checkpoint"]["sha256"]


def test_production_package_rejects_synthetic_baseline(workspace_tmp_path: Path) -> None:
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "production_rejects_synthetic")
    payload["package"] = {
        "profile": "production",
        "metric_thresholds": {"accuracy": 0.8},
        "examples_dir": str(workspace_tmp_path / "examples"),
        "limitations": ["fixture"],
    }
    (workspace_tmp_path / "examples").mkdir()
    write_yaml(config, payload)

    report = run_experiment_pipeline(config, package=True, output_root=workspace_tmp_path / "packages")

    assert report["status"] == "failed"
    assert report["failed_stage"] == "package"
    assert "production training must execute" in report["failed_reason"]


def test_production_training_rejects_stale_checkpoint(workspace_tmp_path: Path) -> None:
    checkpoint = workspace_tmp_path / "best.pt"
    checkpoint.write_bytes(b"stale")
    manifests: dict[str, str] = {}
    for split in ("train", "val"):
        path = workspace_tmp_path / f"{split}.jsonl"
        path.write_text(
            f'{{"image":"s3://bucket/{split}.jpg","split":"{split}","source":"camera_01","dataset_version":"1.0.0"}}\n',
            encoding="utf-8",
        )
        manifests[f"{split}_manifest"] = str(path)
    config = workspace_tmp_path / "config.yml"
    write_yaml(
        config,
        {
            "experiment": {"id": "stale_checkpoint", "task": "classification"},
            "dataset": {"name": "dataset", "version": "1.0.0", **manifests},
            "model": {"architecture": "resnet50", "version": "1.0.0"},
            "training": {
                "adapter": "torchvision_classifier",
                "command": [sys.executable, "-c", "pass"],
                "produced_checkpoint": str(checkpoint),
            },
        },
    )

    result = run_stage("training", config)

    assert result.status == "failed"
    assert "fresh checkpoint" in result.payload["message"]


def test_same_experiment_cannot_run_concurrently(workspace_tmp_path: Path) -> None:
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "exclusive_run")
    payload["training"]["command"] = [sys.executable, "-c", "import time; time.sleep(1)"]
    write_yaml(config, payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_experiment_pipeline, config) for _ in range(2)]
        reports = [future.result(timeout=10) for future in futures]

    assert sorted(report["status"] for report in reports) == ["completed", "failed"]
    failed = next(report for report in reports if report["status"] == "failed")
    assert failed["failed_stage"] == "job"
    assert "already active" in failed["failed_reason"]


def test_external_export_result_is_not_overwritten_by_synthetic_model(workspace_tmp_path: Path) -> None:
    """回归：外部导出命令产出的真实 ONNX 绝不能被合成桩模型覆盖。"""
    produced = workspace_tmp_path / "produced_model.onnx"
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "external_export_keep")
    payload["export"]["command"] = _make_real_onnx_script(produced)
    payload["export"]["produced_onnx"] = str(produced)
    write_yaml(config, payload)

    result = run_stage("export", config)

    assert result.status == "completed"
    output_path = Path(result.payload["onnx"])
    assert output_path.exists()
    # 最终产物必须与外部命令产物逐字节一致（未被桩模型覆盖）。
    assert sha256_file(output_path) == sha256_file(produced)
    report = json.loads(Path(result.payload["report"]).read_text(encoding="utf-8"))
    assert report["onnx_source"] == "external_command"


def test_export_does_not_silently_reuse_stale_onnx(workspace_tmp_path: Path) -> None:
    """回归：默认不复用已存在的 ONNX；显式 reuse_existing=true 时才复用。"""
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "export_no_reuse")
    write_yaml(config, payload)

    first = run_stage("export", config)
    output_path = Path(first.payload["onnx"])
    stale_marker = b"stale"
    original_bytes = output_path.read_bytes()
    # 第二次导出应重新生成而不是保留旧文件内容。
    output_path.write_bytes(original_bytes + stale_marker)
    second = run_stage("export", config)
    assert second.status == "completed"
    assert Path(second.payload["onnx"]).read_bytes() == original_bytes

    # reuse_existing=true 时复用现有可加载产物。
    payload["export"]["reuse_existing"] = True
    write_yaml(config, payload)
    third = run_stage("export", config)
    report = json.loads(Path(third.payload["report"]).read_text(encoding="utf-8"))
    assert report["onnx_source"] == "reused"


def test_training_failure_short_circuits_pipeline_and_blocks_packaging(workspace_tmp_path: Path) -> None:
    """回归：训练失败必须短路，绝不产出模型包。"""
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "training_failure_short_circuit")
    payload["training"]["command"] = [sys.executable, "-c", "import sys; sys.exit(1)"]
    write_yaml(config, payload)
    output_root = workspace_tmp_path / "packages"

    report = run_experiment_pipeline(config, package=True, output_root=output_root)

    assert report["status"] == "failed"
    assert report["failed_stage"] == "training"
    assert "package" not in report or not isinstance(report.get("package"), dict) or "validation" not in report.get("package", {})
    assert not list(output_root.rglob("*.onnx"))
    # 导出/评估阶段不应被执行。
    assert "export" not in report


def test_evaluation_reads_produced_metrics_and_marks_source(workspace_tmp_path: Path) -> None:
    """回归：外部评估命令产出的真实指标必须回读，并标注 metrics_source=measured。"""
    metrics_file = workspace_tmp_path / "metrics.json"
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "eval_produced_metrics")
    payload["evaluation"] = {
        "adapter": "classification_baseline",
        "command": [
            sys.executable,
            "-c",
            f"import json; json.dump({{'accuracy': 0.93, 'f1': 0.91}}, open(r'{metrics_file}', 'w'))",
        ],
        "produced_metrics": str(metrics_file),
        "expected_metrics": {"accuracy": 0.5},
    }
    write_yaml(config, payload)

    run_stage("export", config)
    result = run_stage("evaluation", config)

    assert result.status == "completed"
    assert result.payload["metrics"] == {"accuracy": 0.93, "f1": 0.91}
    assert result.payload["metrics_source"] == "measured"


def test_evaluation_fails_when_produced_metrics_missing(workspace_tmp_path: Path) -> None:
    """回归：声明了 produced_metrics 但文件缺失时评估必须失败，而非静默回落自报值。"""
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "eval_missing_metrics")
    payload["evaluation"] = {
        "adapter": "classification_baseline",
        "command": [sys.executable, "-c", "pass"],
        "produced_metrics": str(workspace_tmp_path / "never_written.json"),
    }
    write_yaml(config, payload)

    run_stage("export", config)
    result = run_stage("evaluation", config)

    assert result.status == "failed"


def test_evaluation_without_external_command_marks_declared_source(workspace_tmp_path: Path) -> None:
    """回归：自报指标必须被明确标注为 declared，供发布链路区分。"""
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "eval_declared_metrics")
    payload["evaluation"] = {
        "adapter": "classification_baseline",
        "expected_metrics": {"accuracy": 0.8},
    }
    write_yaml(config, payload)

    run_stage("export", config)
    result = run_stage("evaluation", config)

    assert result.status == "completed"
    assert result.payload["metrics_source"] == "declared"
    assert result.payload["metrics"]["accuracy"] == 0.8


def test_external_command_with_large_output_does_not_deadlock(workspace_tmp_path: Path) -> None:
    """回归：外部命令输出超过管道缓冲区（64KB）不得死锁。"""
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "large_output_no_deadlock")
    # 输出约 2MB 文本，远超管道缓冲区。
    payload["training"]["command"] = [
        sys.executable,
        "-c",
        "import sys\nfor i in range(20000):\n    print('x' * 100)\nsys.exit(0)",
    ]
    write_yaml(config, payload)

    result = run_stage("training", config)

    assert result.status == "completed"
    assert result.payload["external"]["ok"] is True


def test_external_command_output_decodes_utf8_on_any_locale(workspace_tmp_path: Path) -> None:
    """回归：外部命令输出 UTF-8 中文时不得抛 UnicodeDecodeError。"""
    config = workspace_tmp_path / "config.yml"
    payload = _base_config(workspace_tmp_path, "utf8_output")
    payload["training"]["command"] = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write('训练进度：第 1 轮完成\\n'.encode('utf-8'))",
    ]
    write_yaml(config, payload)

    result = run_stage("training", config)

    assert result.status == "completed"
    assert "训练进度" in result.payload["external"]["stdout"]


def test_log_truncation_keeps_tail(workspace_tmp_path: Path) -> None:
    """回归：日志截断必须保留尾部（Traceback 所在位置）。"""
    from scenara_model.adapters.local_tasks import _truncate_log

    text = "HEAD_MARKER\n" + ("x" * 50000) + "\nTAIL_TRACEBACK_MARKER"
    truncated = _truncate_log(text, 20000)
    assert "TAIL_TRACEBACK_MARKER" in truncated
    assert "HEAD_MARKER" in truncated
    assert len(truncated) < 21000

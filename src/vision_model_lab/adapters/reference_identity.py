from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vision_model_lab.adapters.base import AdapterResult, LogLineSink
from vision_model_lab.export.onnx_checks import check_onnx_loadable
from vision_model_lab.utils import ensure_dir, read_yaml, write_json


def _experiment_dir(config: dict[str, Any], root: str | Path = "experiments/local_runs") -> Path:
    experiment = config.get("experiment", {})
    experiment_id = experiment.get("id", "reference_identity")
    workspace = Path(os.environ.get("VMLAB_WORKSPACE", Path.cwd())).resolve()
    return ensure_dir(workspace / root / str(experiment_id))


def _cancelled_result(config_path: str | Path, *, stage: str, report_path: Path) -> AdapterResult:
    message = f"{stage} cancelled before execution."
    write_json(
        report_path,
        {
            "status": "cancelled",
            "adapter": "reference_identity",
            "config": str(config_path),
            "stage": stage,
            "message": message,
        },
    )
    return AdapterResult("cancelled", report_path, {"report": str(report_path), "message": message})


def run_training(config_path: str | Path) -> Path:
    config = read_yaml(config_path)
    output_dir = _experiment_dir(config)
    report = {
        "status": "completed",
        "adapter": "reference_identity",
        "config": str(config_path),
        "message": "Reference adapter does not train weights; it records a deterministic pipeline smoke run.",
    }
    write_json(output_dir / "train.report.json", report)
    return output_dir / "train.report.json"


def export_onnx(config_path: str | Path) -> Path:
    config = read_yaml(config_path)
    output_dir = ensure_dir(_experiment_dir(config) / "export")
    artifact_name = config.get("export", {}).get("artifact_name", "reference_identity_v0.1.0_fp32.onnx")
    output_path = output_dir / artifact_name

    import onnx
    from onnx import TensorProto, helper

    node = helper.make_node("Identity", ["input"], ["output"])
    graph = helper.make_graph(
        [node],
        "reference_identity_graph",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1])],
    )
    model = helper.make_model(
        graph,
        producer_name="vision-model-lab-reference",
        opset_imports=[helper.make_operatorsetid("", 17)],
    )
    model.ir_version = 8
    onnx.save(model, output_path)
    write_json(output_dir / "export.report.json", {"status": "completed", "onnx": str(output_path)})
    return output_path


def run_evaluation(config_path: str | Path, onnx_path: str | Path | None = None) -> Path:
    config = read_yaml(config_path)
    output_dir = ensure_dir(_experiment_dir(config) / "eval")
    if onnx_path is None:
        artifact_name = config.get("export", {}).get("artifact_name", "reference_identity_v0.1.0_fp32.onnx")
        resolved_onnx_path = _experiment_dir(config) / "export" / str(artifact_name)
    else:
        resolved_onnx_path = Path(onnx_path)
    check = check_onnx_loadable(resolved_onnx_path)
    report = {
        "status": "completed",
        "adapter": "reference_identity",
        "onnx": str(resolved_onnx_path),
        "check": check,
        "metrics": {"identity_loadable": 1.0},
    }
    report_path = output_dir / "eval.report.json"
    write_json(report_path, report)
    return report_path


class ReferenceIdentityAdapter:
    name = "reference_identity"
    task = "reference"
    description = "Deterministic identity ONNX smoke adapter."

    def train(
        self,
        config_path: str | Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        if should_cancel and should_cancel():
            config = read_yaml(config_path)
            return _cancelled_result(config_path, stage="training", report_path=_experiment_dir(config) / "train.report.json")
        report_path = run_training(config_path)
        return AdapterResult("completed", report_path, {"report": str(report_path)})

    def export(
        self,
        config_path: str | Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        if should_cancel and should_cancel():
            config = read_yaml(config_path)
            report_path = ensure_dir(_experiment_dir(config) / "export") / "export.report.json"
            return _cancelled_result(config_path, stage="export", report_path=report_path)
        onnx_path = export_onnx(config_path)
        return AdapterResult("completed", onnx_path, {"onnx": str(onnx_path)})

    def evaluate(
        self,
        config_path: str | Path,
        onnx_path: str | Path | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        if should_cancel and should_cancel():
            config = read_yaml(config_path)
            report_path = ensure_dir(_experiment_dir(config) / "eval") / "eval.report.json"
            return _cancelled_result(config_path, stage="evaluation", report_path=report_path)
        report_path = run_evaluation(config_path, onnx_path)
        return AdapterResult("completed", report_path, {"report": str(report_path)})


REFERENCE_IDENTITY_ADAPTER = ReferenceIdentityAdapter()

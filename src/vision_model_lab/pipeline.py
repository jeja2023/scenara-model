from __future__ import annotations

import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vision_model_lab.adapters.registry import run_stage
from vision_model_lab.packaging.model_package import create_model_package, validate_model_package
from vision_model_lab.utils import ensure_dir, read_jsonl, read_yaml, write_json

SYNTHETIC_JPEG_BYTES = bytes.fromhex("ffd8ffe000104a46494600010101000100010000ffd9")


def _write_synthetic_image(path: Path) -> None:
    try:
        current = path.read_bytes()[:3] if path.exists() else b""
    except OSError:
        current = b""
    if current != bytes.fromhex("ffd8ff"):
        path.write_bytes(SYNTHETIC_JPEG_BYTES)


PipelineEventSink = Callable[[str, str, dict[str, Any]], None]
CancelCheck = Callable[[], bool]
LogLineSink = Callable[[str, str], None]


def _emit(event_sink: PipelineEventSink | None, stage: str, message: str, detail: dict[str, Any] | None = None) -> None:
    if event_sink is not None:
        event_sink(stage, message, detail or {})


def _cancel_requested(should_cancel: CancelCheck | None) -> bool:
    return bool(should_cancel and should_cancel())


def _pipeline_status(stage_results: list[dict[str, Any]]) -> str:
    statuses = {str(result.get("status", "")) for result in stage_results}
    if "cancelled" in statuses:
        return "cancelled"
    return "failed" if "failed" in statuses else "completed"


def _cancelled_report(
    config_path: str | Path,
    stage_payloads: dict[str, dict[str, Any]],
    *,
    cancelled_stage: str,
    reason: str,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "cancelled",
        "config": str(config_path),
        "cancelled_stage": cancelled_stage,
        "cancelled_reason": reason,
        **stage_payloads,
    }
    report["artifacts"] = collect_pipeline_artifacts(report)
    return report


def _failed_report(
    config_path: str | Path,
    stage_payloads: dict[str, dict[str, Any]],
    *,
    failed_stage: str,
    reason: str,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "failed",
        "config": str(config_path),
        "failed_stage": failed_stage,
        "failed_reason": reason,
        **stage_payloads,
    }
    report["artifacts"] = collect_pipeline_artifacts(report)
    return report


def collect_pipeline_artifacts(report: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    export = report.get("export", {})
    if isinstance(export, dict):
        onnx = export.get("onnx")
        if onnx:
            path = Path(str(onnx))
            artifacts.append({
                "name": path.name,
                "kind": "onnx",
                "path": str(path),
                "uri": str(path),
                "size": path.stat().st_size if path.exists() else None,
            })
        report_path = export.get("report")
        if report_path:
            path = Path(str(report_path))
            artifacts.append({
                "name": path.name,
                "kind": "export_report",
                "path": str(path),
                "uri": str(path),
                "size": path.stat().st_size if path.exists() else None,
            })
    package = report.get("package", {})
    if isinstance(package, dict):
        package_dir = package.get("package_dir")
        artifact_name = package.get("artifact_name")
        if package_dir and artifact_name:
            path = Path(str(package_dir)) / str(artifact_name)
            artifacts.append({
                "name": path.name,
                "kind": "model_package",
                "path": str(path),
                "uri": str(path),
                "size": path.stat().st_size if path.exists() else None,
            })
    return artifacts


def _record_stage_result(
    stage_payloads: dict[str, dict[str, Any]],
    stage: str,
    result: Any,
    event_sink: PipelineEventSink | None,
) -> dict[str, Any]:
    payload = result.to_dict()
    stage_payloads[stage] = payload
    _emit(event_sink, stage, str(payload.get("status", result.status)), payload)
    return payload


def _run_stage_safely(
    stage: str,
    config_path: str | Path,
    *,
    onnx_path: str | Path | None = None,
    should_cancel: CancelCheck | None = None,
    log_sink: LogLineSink | None = None,
) -> Any:
    """阶段级异常兜底：任何适配器异常转成结构化 failed 载荷，保留已完成阶段的结果。"""
    from vision_model_lab.adapters.base import AdapterResult

    try:
        return run_stage(stage, config_path, onnx_path=onnx_path, should_cancel=should_cancel, log_sink=log_sink)
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status="failed",
            path=Path(str(config_path)),
            payload={
                "error": str(exc),
                "traceback": traceback.format_exc(limit=10),
                "message": f"Stage {stage} raised an unhandled exception.",
            },
        )


def run_experiment_pipeline(
    config_path: str | Path,
    *,
    package: bool = False,
    output_root: str | Path = "shared-models",
    event_sink: PipelineEventSink | None = None,
    should_cancel: CancelCheck | None = None,
    log_sink: LogLineSink | None = None,
) -> dict[str, Any]:
    stage_payloads: dict[str, dict[str, Any]] = {}

    if _cancel_requested(should_cancel):
        _emit(event_sink, "job", "cancelled before training", {"config_path": str(config_path)})
        return _cancelled_report(config_path, stage_payloads, cancelled_stage="training", reason="Cancellation requested before training started")

    _emit(event_sink, "training", "started", {"config_path": str(config_path)})
    train_result = _run_stage_safely("training", config_path, should_cancel=should_cancel, log_sink=log_sink)
    train_payload = _record_stage_result(stage_payloads, "training", train_result, event_sink)
    if train_payload.get("status") == "cancelled":
        return _cancelled_report(
            config_path,
            stage_payloads,
            cancelled_stage="training",
            reason=str(train_payload.get("message") or "Training was cancelled"),
        )
    if train_payload.get("status") == "failed":
        # 训练失败必须短路：继续导出只会产出无训练权重的废品并被打上合格标签。
        return _failed_report(
            config_path,
            stage_payloads,
            failed_stage="training",
            reason=str(train_payload.get("message") or train_payload.get("error") or "Training failed"),
        )
    if _cancel_requested(should_cancel):
        return _cancelled_report(config_path, stage_payloads, cancelled_stage="training", reason="Cancellation requested after training")

    _emit(event_sink, "export", "started", {"config_path": str(config_path)})
    export_result = _run_stage_safely("export", config_path, should_cancel=should_cancel, log_sink=log_sink)
    export_payload = _record_stage_result(stage_payloads, "export", export_result, event_sink)
    if export_payload.get("status") == "cancelled":
        return _cancelled_report(
            config_path,
            stage_payloads,
            cancelled_stage="export",
            reason=str(export_payload.get("message") or "Export was cancelled"),
        )
    if export_payload.get("status") == "failed":
        return _failed_report(
            config_path,
            stage_payloads,
            failed_stage="export",
            reason=str(export_payload.get("message") or export_payload.get("error") or "Export failed"),
        )
    if _cancel_requested(should_cancel):
        return _cancelled_report(config_path, stage_payloads, cancelled_stage="export", reason="Cancellation requested after export")

    onnx_path = export_result.payload.get("onnx") or export_result.path
    _emit(event_sink, "evaluation", "started", {"onnx_path": str(onnx_path)})
    eval_result = _run_stage_safely("evaluation", config_path, onnx_path=onnx_path, should_cancel=should_cancel, log_sink=log_sink)
    eval_payload = _record_stage_result(stage_payloads, "evaluation", eval_result, event_sink)
    if eval_payload.get("status") == "cancelled":
        return _cancelled_report(
            config_path,
            stage_payloads,
            cancelled_stage="evaluation",
            reason=str(eval_payload.get("message") or "Evaluation was cancelled"),
        )
    if eval_payload.get("status") == "failed":
        return _failed_report(
            config_path,
            stage_payloads,
            failed_stage="evaluation",
            reason=str(eval_payload.get("message") or eval_payload.get("error") or "Evaluation failed"),
        )

    result: dict[str, Any] = {
        "status": _pipeline_status(list(stage_payloads.values())),
        "config": str(config_path),
        **stage_payloads,
    }
    if package:
        if _cancel_requested(should_cancel):
            return _cancelled_report(config_path, stage_payloads, cancelled_stage="package", reason="Cancellation requested before package creation")
        # 打包前强制校验全流水线状态：任何非 completed 状态都不允许产出交付物。
        if result["status"] != "completed":
            return _failed_report(
                config_path,
                stage_payloads,
                failed_stage="package",
                reason=f"Refusing to package a pipeline whose status is {result['status']!r}",
            )
        _emit(event_sink, "package", "started", {"output_root": str(output_root)})
        try:
            result["package"] = create_package_from_experiment(config_path, onnx_path=onnx_path, output_root=output_root)
        except Exception as exc:  # noqa: BLE001
            stage_payloads["package"] = {
                "status": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=10),
            }
            _emit(event_sink, "package", "failed", stage_payloads["package"])
            return _failed_report(config_path, stage_payloads, failed_stage="package", reason=str(exc))
        _emit(event_sink, "package", "completed", result["package"])
    result["artifacts"] = collect_pipeline_artifacts(result)
    return result


def _labels_from_config(config: dict[str, Any]) -> list[str]:
    labels = config.get("labels")
    if isinstance(labels, list) and labels:
        return [str(label) for label in labels]
    task = str(config.get("experiment", {}).get("task") or config.get("dataset", {}).get("task") or "model")
    defaults = {
        "detection": ["person"],
        "classification": ["negative", "positive"],
        "segmentation": ["background", "target"],
        "reid": ["identity"],
    }
    return defaults.get(task, [task])


def _example_dir_for(config_path: str | Path, artifact_name: str) -> Path:
    config = read_yaml(config_path)
    experiment_id = str(config.get("experiment", {}).get("id", "experiment"))
    examples_dir = ensure_dir(Path("artifacts/examples") / experiment_id / artifact_name.removesuffix(".onnx"))
    image_path = examples_dir / "frame_001.jpg"
    expected_path = examples_dir / "frame_001.expected.json"
    _write_synthetic_image(image_path)
    if not expected_path.exists():
        write_json(expected_path, {"outputs": [], "source": "synthetic_example"})
    return examples_dir


def create_package_from_experiment(
    config_path: str | Path,
    *,
    onnx_path: str | Path | None = None,
    output_root: str | Path = "shared-models",
    project_name: str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    config = read_yaml(config_path)
    if onnx_path is None:
        export_result = run_stage("export", config_path)
        if export_result.status != "completed":
            raise RuntimeError(f"Export stage did not complete (status={export_result.status}); refusing to package.")
        exported_onnx = export_result.payload.get("onnx")
        onnx_path = Path(exported_onnx) if exported_onnx else export_result.path
    onnx_path = Path(onnx_path)
    artifact_name = onnx_path.name
    experiment = config.get("experiment", {})
    model = config.get("model", {})
    project = project_name or str(config.get("package", {}).get("project") or experiment.get("project") or "lab_baselines")
    examples_dir = _example_dir_for(config_path, artifact_name)
    package_dir = create_model_package(
        output_root=output_root,
        project_name=project,
        artifact_name=artifact_name,
        model_file=onnx_path,
        labels=_labels_from_config(config),
        task=str(experiment.get("task") or config.get("dataset", {}).get("task") or "model"),
        architecture=str(model.get("architecture") or "baseline"),
        examples_dir=examples_dir,
        overwrite=overwrite,
    )
    validation = validate_model_package(package_dir, model_id=artifact_name, strict_hash=True, strict_onnx=True)
    return {
        "package_dir": str(package_dir),
        "artifact_name": artifact_name,
        "validation": validation.to_dict(),
    }


def load_error_cases(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.exists():
        return {"path": str(resolved), "total": 0, "by_type": {}, "cases": []}
    if resolved.suffix.lower() == ".jsonl":
        rows = read_jsonl(resolved)
    else:
        data = read_yaml(resolved)
        rows = data.get("cases", []) if isinstance(data.get("cases"), list) else []
    by_type: dict[str, int] = {}
    for row in rows:
        error_type = str(row.get("type") or row.get("error_type") or "unknown")
        by_type[error_type] = by_type.get(error_type, 0) + 1
    return {"path": str(resolved), "total": len(rows), "by_type": by_type, "cases": rows[:100]}

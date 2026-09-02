from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from scenara_model.adapters.base import AdapterResult, LogLineSink, TaskAdapter
from scenara_model.adapters.local_tasks import (
    CLASSIFICATION_BASELINE,
    DETECTION_YOLO_BASELINE,
    FASHION_MULTIHEAD_ADAPTER,
    PADDLEOCR_ADAPTER,
    PADDLEVIDEO_ADAPTER,
    REID_BASELINE,
    SEGMENTATION_BASELINE,
    SEGMENTATION_FRAMEWORK_ADAPTER,
    TORCHREID_ADAPTER,
    TORCHVISION_CLASSIFICATION_ADAPTER,
    ULTRALYTICS_YOLO_ADAPTER,
)
from scenara_model.adapters.reference_identity import REFERENCE_IDENTITY_ADAPTER
from scenara_model.utils import read_yaml

ADAPTERS: dict[str, TaskAdapter] = {
    REFERENCE_IDENTITY_ADAPTER.name: REFERENCE_IDENTITY_ADAPTER,
    DETECTION_YOLO_BASELINE.name: DETECTION_YOLO_BASELINE,
    REID_BASELINE.name: REID_BASELINE,
    CLASSIFICATION_BASELINE.name: CLASSIFICATION_BASELINE,
    SEGMENTATION_BASELINE.name: SEGMENTATION_BASELINE,
    ULTRALYTICS_YOLO_ADAPTER.name: ULTRALYTICS_YOLO_ADAPTER,
    TORCHREID_ADAPTER.name: TORCHREID_ADAPTER,
    TORCHVISION_CLASSIFICATION_ADAPTER.name: TORCHVISION_CLASSIFICATION_ADAPTER,
    SEGMENTATION_FRAMEWORK_ADAPTER.name: SEGMENTATION_FRAMEWORK_ADAPTER,
    PADDLEOCR_ADAPTER.name: PADDLEOCR_ADAPTER,
    PADDLEVIDEO_ADAPTER.name: PADDLEVIDEO_ADAPTER,
    FASHION_MULTIHEAD_ADAPTER.name: FASHION_MULTIHEAD_ADAPTER,
}


DEFAULT_TASK_ADAPTERS = {
    "reference": "reference_identity",
    "detection": "detection_yolo_baseline",
    "reid": "reid_baseline",
    "classification": "classification_baseline",
    "segmentation": "segmentation_baseline",
    "ocr": "paddleocr",
    "behavior": "paddlevideo",
    "fashion": "fashion_multihead",
}


def list_adapters() -> list[dict[str, str]]:
    return [
        {
            "name": adapter.name,
            "task": adapter.task,
            "description": adapter.description,
        }
        for adapter in ADAPTERS.values()
    ]


def resolve_adapter(config_path: str | Path, stage: str) -> TaskAdapter:
    config = read_yaml(config_path)
    stage_config = config.get(stage, {})
    experiment = config.get("experiment", {})
    task = str(experiment.get("task") or config.get("dataset", {}).get("task") or "")
    adapter_name = ""
    if isinstance(stage_config, dict):
        adapter_name = str(stage_config.get("adapter") or "")
    if not adapter_name:
        adapter_name = DEFAULT_TASK_ADAPTERS.get(task, "")
    if not adapter_name or adapter_name not in ADAPTERS:
        available = ", ".join(sorted(ADAPTERS))
        raise ValueError(f"No adapter registered for stage={stage} task={task!r}. Available adapters: {available}")
    return ADAPTERS[adapter_name]


def run_stage(
    stage: str,
    config_path: str | Path,
    *,
    onnx_path: str | Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    log_sink: LogLineSink | None = None,
) -> AdapterResult:
    adapter = resolve_adapter(config_path, stage)
    if stage == "training":
        return adapter.train(config_path, should_cancel=should_cancel, log_sink=log_sink)
    if stage == "export":
        return adapter.export(config_path, should_cancel=should_cancel, log_sink=log_sink)
    if stage == "evaluation":
        return adapter.evaluate(config_path, onnx_path, should_cancel=should_cancel, log_sink=log_sink)
    raise ValueError(f"Unsupported stage: {stage}")

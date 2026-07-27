from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .naming import is_semver, parse_artifact_name
from .utils import read_yaml

REQUIRED_TOP_LEVEL = {
    "model",
    "dataset",
    "input",
    "output",
    "metrics",
    "deployment",
    "limitations",
}

REQUIRED_FIELDS = {
    "model": {"name", "version", "task", "architecture", "precision", "format", "sha256"},
    "dataset": {"train", "val", "test"},
    "input": {"layout", "shape", "dtype", "color", "resize", "normalize"},
    "output": {"format"},
    "metrics": set(),
    "deployment": {"runtime", "max_batch_size"},
}


@dataclass
class ModelCardIssue:
    code: str
    message: str
    path: str = ""


@dataclass
class ModelCardValidation:
    path: Path
    ok: bool
    data: dict[str, Any]
    issues: list[ModelCardIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def validate_model_card(
    path: str | Path,
    *,
    artifact_filename: str | None = None,
    expected_sha256: str | None = None,
    strict_hash: bool = False,
) -> ModelCardValidation:
    resolved = Path(path)
    issues: list[ModelCardIssue] = []
    try:
        data = read_yaml(resolved)
    except Exception as exc:  # noqa: BLE001
        return ModelCardValidation(
            path=resolved,
            ok=False,
            data={},
            issues=[ModelCardIssue("model_card.read_error", str(exc), str(resolved))],
        )

    missing_sections = sorted(REQUIRED_TOP_LEVEL - set(data))
    for section in missing_sections:
        issues.append(ModelCardIssue("model_card.missing_section", f"Missing section: {section}", section))

    for section, fields in REQUIRED_FIELDS.items():
        section_data = data.get(section, {})
        if not isinstance(section_data, dict):
            issues.append(ModelCardIssue("model_card.invalid_section", f"Section must be an object: {section}", section))
            continue
        for field_name in sorted(fields - set(section_data)):
            issues.append(
                ModelCardIssue(
                    "model_card.missing_field",
                    f"Missing field: {section}.{field_name}",
                    f"{section}.{field_name}",
                )
            )

    model = data.get("model", {})
    if isinstance(model, dict):
        version = str(model.get("version", ""))
        if version and not is_semver(version):
            issues.append(ModelCardIssue("model_card.invalid_version", "model.version must be semantic version", "model.version"))
        if model.get("format") and str(model["format"]).lower() != "onnx":
            issues.append(ModelCardIssue("model_card.invalid_format", "model.format must be onnx", "model.format"))
        if model.get("precision") and str(model["precision"]).lower() not in {"fp32", "fp16", "int8"}:
            issues.append(
                ModelCardIssue("model_card.invalid_precision", "model.precision must be fp32, fp16, or int8", "model.precision")
            )

    input_section = data.get("input", {})
    if isinstance(input_section, dict):
        shape = input_section.get("shape")
        if not isinstance(shape, list) or len(shape) != 4:
            issues.append(ModelCardIssue("model_card.invalid_shape", "input.shape must be a 4-item list", "input.shape"))
        elif not all(isinstance(value, int) and value > 0 for value in shape):
            issues.append(ModelCardIssue("model_card.invalid_shape", "input.shape values must be positive integers", "input.shape"))
        layout = str(input_section.get("layout", "")).lower()
        if layout and layout not in {"nchw", "nhwc"}:
            issues.append(ModelCardIssue("model_card.invalid_layout", "input.layout must be nchw or nhwc", "input.layout"))
        dtype = str(input_section.get("dtype", "")).lower()
        if dtype and dtype not in {"float32", "float16", "uint8", "int8"}:
            issues.append(ModelCardIssue("model_card.invalid_dtype", "input.dtype must be float32, float16, uint8, or int8", "input.dtype"))

    metrics = data.get("metrics", {})
    if isinstance(metrics, dict):
        thresholds = data.get("metric_thresholds", {})
        if thresholds is not None and not isinstance(thresholds, dict):
            issues.append(ModelCardIssue("model_card.invalid_metric_thresholds", "metric_thresholds must be an object", "metric_thresholds"))
        elif isinstance(thresholds, dict):
            for metric_name, threshold in thresholds.items():
                actual = metrics.get(metric_name)
                if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                    issues.append(
                        ModelCardIssue(
                            "model_card.invalid_metric_threshold",
                            f"metric_thresholds.{metric_name} must be numeric",
                            f"metric_thresholds.{metric_name}",
                        )
                    )
                elif not isinstance(actual, (int, float)) or isinstance(actual, bool):
                    issues.append(
                        ModelCardIssue(
                            "model_card.metric_missing",
                            f"metrics.{metric_name} must exist and be numeric when a threshold is configured",
                            f"metrics.{metric_name}",
                        )
                    )
                elif float(actual) < float(threshold):
                    issues.append(
                        ModelCardIssue(
                            "model_card.metric_below_threshold",
                            f"metrics.{metric_name} is below threshold {threshold}",
                            f"metrics.{metric_name}",
                        )
                    )

    deployment = data.get("deployment", {})
    if isinstance(deployment, dict):
        max_batch_size = deployment.get("max_batch_size")
        if max_batch_size is not None and (not isinstance(max_batch_size, int) or max_batch_size < 1):
            issues.append(ModelCardIssue("model_card.invalid_max_batch_size", "deployment.max_batch_size must be a positive integer", "deployment.max_batch_size"))

    limitations = data.get("limitations")
    if limitations is not None and (not isinstance(limitations, list) or not limitations):
        issues.append(ModelCardIssue("model_card.invalid_limitations", "limitations must be a non-empty list", "limitations"))

    if artifact_filename:
        try:
            artifact = parse_artifact_name(artifact_filename)
        except ValueError as exc:
            issues.append(ModelCardIssue("model_card.invalid_artifact_name", str(exc), "model.name"))
        else:
            if isinstance(model, dict):
                if str(model.get("version", "")) != artifact.version:
                    issues.append(
                        ModelCardIssue(
                            "model_card.version_mismatch",
                            f"model.version must match artifact version {artifact.version}",
                            "model.version",
                        )
                    )
                if str(model.get("precision", "")).lower() != artifact.precision:
                    issues.append(
                        ModelCardIssue(
                            "model_card.precision_mismatch",
                            f"model.precision must match artifact precision {artifact.precision}",
                            "model.precision",
                        )
                    )

    card_sha256 = ""
    if isinstance(model, dict):
        card_sha256 = str(model.get("sha256") or "")
    if strict_hash and not card_sha256:
        issues.append(ModelCardIssue("model_card.empty_sha256", "model.sha256 is required in strict hash mode", "model.sha256"))
    if expected_sha256 and card_sha256 and card_sha256 != expected_sha256:
        issues.append(
            ModelCardIssue(
                "model_card.sha256_mismatch",
                "model.sha256 does not match ONNX file digest",
                "model.sha256",
            )
        )

    return ModelCardValidation(path=resolved, ok=not issues, data=data, issues=issues)


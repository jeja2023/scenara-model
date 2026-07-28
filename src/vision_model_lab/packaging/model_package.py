from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from vision_model_lab.model_card import ModelCardIssue, validate_model_card
from vision_model_lab.naming import parse_artifact_name
from vision_model_lab.utils import ensure_dir, non_empty_lines, sha256_file, unique_preserve_order, write_yaml

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _atomic_copy(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_text(destination: Path, value: str) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class PackageIssue:
    severity: str
    code: str
    message: str
    path: str = ""


@dataclass
class PackageValidation:
    package_dir: Path
    ok: bool
    model_file: Path | None = None
    model_card: Path | None = None
    labels_file: Path | None = None
    examples_dir: Path | None = None
    sha256: str | None = None
    onnx_checked: bool = False
    ort_checked: bool = False
    issues: list[PackageIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_dir": str(self.package_dir),
            "ok": self.ok,
            "model_file": str(self.model_file) if self.model_file else None,
            "model_card": str(self.model_card) if self.model_card else None,
            "labels_file": str(self.labels_file) if self.labels_file else None,
            "examples_dir": str(self.examples_dir) if self.examples_dir else None,
            "sha256": self.sha256,
            "onnx_checked": self.onnx_checked,
            "ort_checked": self.ort_checked,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def _issue(code: str, message: str, path: str = "", severity: str = "error") -> PackageIssue:
    return PackageIssue(severity=severity, code=code, message=message, path=path)


def _resolve_model_file(package_dir: Path, model_id: str | None) -> tuple[Path | None, PackageIssue | None]:
    resolved_package_dir = package_dir.resolve()
    if model_id:
        candidate_id = Path(model_id)
        if candidate_id.is_absolute() or ".." in candidate_id.parts:
            return None, _issue(
                "package.model_outside_package",
                "model_id must be a file name or relative path inside the package directory",
                str(model_id),
            )
        candidate = (resolved_package_dir / candidate_id).resolve()
        if candidate != resolved_package_dir and resolved_package_dir not in candidate.parents:
            return None, _issue("package.model_outside_package", "model_id escapes package directory", str(model_id))
        return candidate, None
    model_files = sorted(resolved_package_dir.glob("*.onnx"))
    if len(model_files) == 1:
        return model_files[0], None
    return None, None


def _validate_labels(labels_file: Path) -> list[PackageIssue]:
    issues: list[PackageIssue] = []
    if not labels_file.exists():
        return [_issue("package.missing_labels", "Labels file is required", str(labels_file))]
    labels = non_empty_lines(labels_file)
    if not labels:
        issues.append(_issue("package.empty_labels", "Labels file must contain at least one label", str(labels_file)))
    duplicate_count = len(labels) - len(unique_preserve_order(labels))
    if duplicate_count:
        issues.append(_issue("package.duplicate_labels", "Labels file contains duplicate labels", str(labels_file)))
    return issues


def _has_valid_image_signature(path: Path) -> bool:
    try:
        header = path.read_bytes()[:16]
    except OSError:
        return False
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(bytes.fromhex("ffd8ff"))
    if suffix == ".png":
        return header.startswith(bytes.fromhex("89504e470d0a1a0a"))
    if suffix == ".bmp":
        return header.startswith(b"BM")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_label_value(label: Any, labels: set[str], path: Path, field: str) -> list[PackageIssue]:
    if label is None:
        return []
    if not isinstance(label, str) or not label.strip():
        return [_issue("package.invalid_expected_schema", f"{field} label must be a non-empty string", str(path))]
    if labels and label not in labels:
        return [_issue("package.expected_label_mismatch", f"Expected output label is not in labels.txt: {label}", str(path))]
    return []


def _validate_prediction_items(items: Any, path: Path, labels: set[str], field: str) -> list[PackageIssue]:
    issues: list[PackageIssue] = []
    if not isinstance(items, list):
        return [_issue("package.invalid_expected_schema", f"{field} must be a list", str(path))]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(_issue("package.invalid_expected_schema", f"{field}[{index}] must be an object", str(path)))
            continue
        label = item.get("label", item.get("class", item.get("class_name")))
        issues.extend(_validate_label_value(label, labels, path, f"{field}[{index}]"))
        score = item.get("score", item.get("confidence"))
        if score is not None and (not _is_number(score) or not 0 <= float(score) <= 1):
            issues.append(_issue("package.invalid_expected_schema", f"{field}[{index}].score must be between 0 and 1", str(path)))
        if field == "detections":
            bbox = item.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(_is_number(value) for value in bbox):
                issues.append(_issue("package.invalid_expected_schema", f"{field}[{index}].bbox must contain 4 numbers", str(path)))
    return issues


def _validate_expected_output(data: Any, path: Path, *, labels: set[str]) -> list[PackageIssue]:
    if not isinstance(data, dict):
        return [_issue("package.invalid_expected_json", "Expected output JSON root must be an object", str(path))]
    known_keys = {"outputs", "predictions", "detections", "embedding", "mask"}
    if not any(key in data for key in known_keys):
        return [
            _issue(
                "package.invalid_expected_schema",
                "Expected output JSON must include outputs, predictions, detections, embedding, or mask",
                str(path),
            )
        ]
    issues: list[PackageIssue] = []
    if "outputs" in data and not isinstance(data["outputs"], list):
        issues.append(_issue("package.invalid_expected_schema", "outputs must be a list", str(path)))
    if "predictions" in data:
        issues.extend(_validate_prediction_items(data["predictions"], path, labels, "predictions"))
    if "detections" in data:
        issues.extend(_validate_prediction_items(data["detections"], path, labels, "detections"))
    if "embedding" in data:
        embedding = data["embedding"]
        if not isinstance(embedding, list) or not embedding or not all(_is_number(value) for value in embedding):
            issues.append(_issue("package.invalid_expected_schema", "embedding must be a non-empty number list", str(path)))
    if "mask" in data:
        mask = data["mask"]
        if not isinstance(mask, (str, dict)):
            issues.append(_issue("package.invalid_expected_schema", "mask must be a URI string or object", str(path)))
    return issues

def _validate_examples(examples_dir: Path, *, strict_examples: bool, labels: set[str] | None = None) -> list[PackageIssue]:
    issues: list[PackageIssue] = []
    allowed_labels = labels or set()
    if not examples_dir.exists():
        severity = "error" if strict_examples else "warning"
        return [_issue("package.missing_examples", "Examples directory is required", str(examples_dir), severity)]
    if not examples_dir.is_dir():
        return [_issue("package.invalid_examples", "Examples path must be a directory", str(examples_dir))]

    inputs = [path for path in examples_dir.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS]
    expected = [
        path
        for path in examples_dir.iterdir()
        if path.suffix.lower() == ".json" and (path.name.endswith(".expected.json") or path.name.startswith("expected_"))
    ]
    severity = "error" if strict_examples else "warning"
    if not inputs:
        issues.append(_issue("package.missing_example_inputs", "At least one example image is required", str(examples_dir), severity))
    for image_path in inputs:
        if not _has_valid_image_signature(image_path):
            issues.append(_issue("package.invalid_example_image", "Example image has an invalid or unsupported file signature", str(image_path), severity))
    if not expected:
        issues.append(_issue("package.missing_expected_outputs", "At least one expected JSON output is required", str(examples_dir), severity))
    for expected_path in expected:
        try:
            with expected_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            issues.extend(_validate_expected_output(data, expected_path, labels=allowed_labels))
        except Exception as exc:  # noqa: BLE001
            issues.append(_issue("package.invalid_expected_json", f"Invalid expected output JSON: {exc}", str(expected_path)))
    return issues


def _validate_onnx(
    model_file: Path,
    model_card_data: dict[str, Any] | None = None,
    *,
    enforce_contract: bool = False,
) -> tuple[list[PackageIssue], bool, bool]:
    issues: list[PackageIssue] = []
    onnx_checked = False
    ort_checked = False
    try:
        import onnx

        model = onnx.load(str(model_file))
        onnx.checker.check_model(model)
        onnx_checked = True
    except Exception as exc:  # noqa: BLE001
        issues.append(_issue("package.onnx_check_failed", f"onnx.checker failed: {exc}", str(model_file)))
        return issues, onnx_checked, ort_checked

    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(model_file), providers=["CPUExecutionProvider"])
        ort_checked = True
        input_contract = model_card_data.get("input", {}) if enforce_contract and isinstance(model_card_data, dict) else {}
        if isinstance(input_contract, dict) and session.get_inputs():
            runtime_input = session.get_inputs()[0]
            expected_shape = input_contract.get("shape")
            if isinstance(expected_shape, list):
                runtime_shape = runtime_input.shape
                if len(runtime_shape) != len(expected_shape) or any(
                    isinstance(actual, int) and actual != expected
                    for actual, expected in zip(runtime_shape, expected_shape, strict=False)
                ):
                    issues.append(
                        _issue(
                            "package.onnx_input_shape_mismatch",
                            f"Model card input shape {expected_shape} does not match ONNX input shape {runtime_shape}",
                            str(model_file),
                        )
                    )
            expected_dtype = str(input_contract.get("dtype") or "").lower()
            runtime_dtype = {
                "tensor(float)": "float32",
                "tensor(float16)": "float16",
                "tensor(uint8)": "uint8",
                "tensor(int8)": "int8",
            }.get(runtime_input.type)
            if expected_dtype and runtime_dtype and expected_dtype != runtime_dtype:
                issues.append(
                    _issue(
                        "package.onnx_input_dtype_mismatch",
                        f"Model card input dtype {expected_dtype} does not match ONNX input dtype {runtime_dtype}",
                        str(model_file),
                    )
                )
    except Exception as exc:  # noqa: BLE001
        issues.append(_issue("package.ort_load_failed", f"ONNX Runtime CPU load failed: {exc}", str(model_file)))

    return issues, onnx_checked, ort_checked


def validate_model_package(
    package_dir: str | Path,
    *,
    model_id: str | None = None,
    strict_hash: bool = False,
    strict_sidecars: bool = True,
    strict_examples: bool = True,
    strict_onnx: bool = False,
    strict_provenance: bool = False,
) -> PackageValidation:
    resolved = Path(package_dir)
    issues: list[PackageIssue] = []

    if not resolved.exists() or not resolved.is_dir():
        return PackageValidation(
            package_dir=resolved,
            ok=False,
            issues=[_issue("package.not_found", "Package directory does not exist", str(resolved))],
        )

    model_file, model_issue = _resolve_model_file(resolved, model_id)
    if model_issue:
        return PackageValidation(package_dir=resolved, ok=False, issues=[model_issue])
    if not model_file:
        model_count = len(list(resolved.glob("*.onnx")))
        return PackageValidation(
            package_dir=resolved,
            ok=False,
            issues=[
                _issue(
                    "package.model_ambiguous",
                    f"Expected exactly one ONNX file or an explicit model_id, found {model_count}",
                    str(resolved),
                )
            ],
        )

    if not model_file.exists():
        return PackageValidation(
            package_dir=resolved,
            ok=False,
            issues=[_issue("package.model_not_found", "ONNX model file does not exist", str(model_file))],
        )

    try:
        parse_artifact_name(model_file.name)
    except ValueError as exc:
        issues.append(_issue("package.invalid_model_name", str(exc), str(model_file)))

    digest = sha256_file(model_file)
    base = model_file.with_suffix("")
    model_card = base.with_name(f"{base.name}.model-card.yml")
    labels_file = base.with_name(f"{base.name}.labels.txt")
    examples_dir = base.with_name(f"{base.name}.examples")

    if strict_sidecars:
        if not model_card.exists():
            issues.append(_issue("package.missing_model_card", "Model card is required", str(model_card)))
        if not labels_file.exists():
            issues.append(_issue("package.missing_labels", "Labels file is required", str(labels_file)))

    model_card_data: dict[str, Any] = {}
    if model_card.exists():
        card_result = validate_model_card(
            model_card,
            artifact_filename=model_file.name,
            expected_sha256=digest,
            strict_hash=strict_hash,
            strict_provenance=strict_provenance,
        )
        model_card_data = card_result.data
        for card_issue in card_result.issues:
            assert isinstance(card_issue, ModelCardIssue)
            issues.append(_issue(card_issue.code, card_issue.message, card_issue.path))

    labels: set[str] = set()
    if labels_file.exists():
        labels = set(non_empty_lines(labels_file))
        issues.extend(_validate_labels(labels_file))

    issues.extend(_validate_examples(examples_dir, strict_examples=strict_examples, labels=labels))

    onnx_checked = False
    ort_checked = False
    if strict_onnx:
        onnx_issues, onnx_checked, ort_checked = _validate_onnx(
            model_file,
            model_card_data,
            enforce_contract=strict_provenance,
        )
        issues.extend(onnx_issues)

    ok = not any(issue.severity == "error" for issue in issues)
    return PackageValidation(
        package_dir=resolved,
        ok=ok,
        model_file=model_file,
        model_card=model_card,
        labels_file=labels_file,
        examples_dir=examples_dir,
        sha256=digest,
        onnx_checked=onnx_checked,
        ort_checked=ort_checked,
        issues=issues,
    )


def default_model_card(
    *,
    artifact_name: str,
    task: str,
    architecture: str,
    labels_name: str,
    sha256: str,
) -> dict[str, Any]:
    artifact = parse_artifact_name(artifact_name)
    model_name = artifact.stem.removesuffix(f"_v{artifact.version}_{artifact.precision}")
    return {
        "model": {
            "name": model_name,
            "version": artifact.version,
            "task": task,
            "architecture": architecture,
            "precision": artifact.precision,
            "format": "onnx",
            "sha256": sha256,
        },
        "dataset": {
            "train": "dataset_v0.0.0",
            "val": "dataset_v0.0.0",
            "test": "dataset_test_v0.0.0",
        },
        "input": {
            "layout": "nchw",
            "shape": [1, 3, 640, 640],
            "dtype": "float32",
            "color": "rgb",
            "resize": "letterbox",
            "normalize": "none",
        },
        "output": {
            "format": task,
            "classes": labels_name,
            "recommended_confidence": 0.25,
            "recommended_iou": 0.45,
        },
        "metrics": {
            "precision": 0.0,
            "recall": 0.0,
            "latency_ms": {"gpu": 0.0},
        },
        "deployment": {
            "runtime": "onnxruntime",
            "min_gpu_memory_mb": 0,
            "max_batch_size": 1,
            "supports_dynamic_batch": False,
        },
        "limitations": ["Fill in known failure modes before release."],
    }


def _onnx_input_contract(path: Path) -> tuple[list[int] | None, str | None]:
    try:
        import onnx

        model = onnx.load(str(path))
        value = model.graph.input[0]
        tensor = value.type.tensor_type
        shape: list[int] = []
        for dimension in tensor.shape.dim:
            if not dimension.dim_value:
                return None, None
            shape.append(int(dimension.dim_value))
        dtype_by_enum: dict[int, str] = {
            int(onnx.TensorProto.FLOAT): "float32",
            int(onnx.TensorProto.FLOAT16): "float16",
            int(onnx.TensorProto.UINT8): "uint8",
            int(onnx.TensorProto.INT8): "int8",
        }
        dtype = dtype_by_enum.get(int(tensor.elem_type))
        return shape, dtype
    except Exception:  # noqa: BLE001
        return None, None


def create_model_package(
    *,
    output_root: str | Path,
    project_name: str,
    artifact_name: str,
    model_file: str | Path,
    labels_file: str | Path | None = None,
    labels: list[str] | None = None,
    task: str,
    architecture: str,
    examples_dir: str | Path | None = None,
    model_card: str | Path | None = None,
    model_card_data: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> Path:
    parse_artifact_name(artifact_name)
    source_model = Path(model_file)
    if not source_model.exists():
        raise FileNotFoundError(f"Model file does not exist: {source_model}")

    destination_dir = ensure_dir(Path(output_root) / project_name)
    destination_model = destination_dir / artifact_name
    if destination_model.exists() and not overwrite:
        raise FileExistsError(f"Model already exists: {destination_model}")
    _atomic_copy(source_model, destination_model)

    # copy2 保留源文件 mtime，(size, mtime) 缓存键在这里会命中上一个模型的摘要。
    digest = sha256_file(destination_model, use_cache=False)
    stem = destination_model.stem
    destination_labels = destination_dir / f"{stem}.labels.txt"
    if labels_file:
        _atomic_copy(Path(labels_file), destination_labels)
    else:
        label_values = labels or []
        if not label_values:
            raise ValueError("Either labels_file or labels must be provided")
        _atomic_write_text(destination_labels, "\n".join(label_values) + "\n")

    destination_card = destination_dir / f"{stem}.model-card.yml"
    if model_card and model_card_data is not None:
        raise ValueError("Specify either model_card or model_card_data, not both")
    if model_card:
        _atomic_copy(Path(model_card), destination_card)
    elif model_card_data is not None:
        card_data = deepcopy(model_card_data)
        model_section = card_data.setdefault("model", {})
        if not isinstance(model_section, dict):
            raise ValueError("model_card_data.model must be an object")
        model_section["sha256"] = digest
        write_yaml(destination_card, card_data)
    else:
        card = default_model_card(
            artifact_name=artifact_name,
            task=task,
            architecture=architecture,
            labels_name=destination_labels.name,
            sha256=digest,
        )
        input_shape, input_dtype = _onnx_input_contract(destination_model)
        if input_shape is not None and len(input_shape) == 4:
            card["input"]["shape"] = input_shape
        if input_dtype is not None:
            card["input"]["dtype"] = input_dtype
        write_yaml(
            destination_card,
            card,
        )

    destination_examples = ensure_dir(destination_dir / f"{stem}.examples")
    if examples_dir:
        for source in Path(examples_dir).iterdir():
            if source.is_file():
                _atomic_copy(source, destination_examples / source.name)

    return destination_dir


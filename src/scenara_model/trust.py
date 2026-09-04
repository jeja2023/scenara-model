from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scenara_model.dataset_versions import reference_from_config, reference_validation_issues
from scenara_model.datasets.manifest import validate_manifest
from scenara_model.naming import parse_artifact_name
from scenara_model.utils import read_json, sha256_file


@dataclass(frozen=True)
class PackageTrust:
    profile: str
    issues: list[str]

    @property
    def ok(self) -> bool:
        return not self.issues


def package_profile(config: dict[str, Any]) -> str:
    package = config.get("package", {})
    if not isinstance(package, dict):
        return "production"
    return str(package.get("profile") or "production").strip().lower()


def load_stage_reports(config: dict[str, Any], *, workspace: str | Path) -> dict[str, dict[str, Any]]:
    experiment = config.get("experiment", {})
    experiment_id = str(experiment.get("id") or "") if isinstance(experiment, dict) else ""
    if not experiment_id:
        return {}
    root = Path(workspace).resolve() / "experiments" / "local_runs" / experiment_id
    paths = {
        "training": root / "train.report.json",
        "export": root / "export" / "export.report.json",
        "evaluation": root / "eval" / "eval.report.json",
    }
    reports: dict[str, dict[str, Any]] = {}
    for stage, path in paths.items():
        if path.exists():
            try:
                data = read_json(path)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                reports[stage] = data
    return reports


def _workspace_path(workspace: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError(f"Path escapes workspace: {value}")
    return resolved


def _manifest_issues(config: dict[str, Any], workspace: Path) -> list[str]:
    dataset = config.get("dataset", {})
    if not isinstance(dataset, dict):
        return ["dataset must be an object"]
    issues: list[str] = []
    for split in ("train", "val", "test"):
        key = f"{split}_manifest"
        value = dataset.get(key)
        if not value:
            issues.append(f"dataset.{key} is required for a production package")
            continue
        try:
            path = _workspace_path(workspace, str(value))
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not path.exists():
            issues.append(f"dataset.{key} was not found: {path}")
            continue
        validation = validate_manifest(path, min_split_counts={split: 1}, check_local_files=True)
        if not validation.ok:
            codes = ", ".join(sorted({issue.code for issue in validation.issues}))
            issues.append(f"dataset.{key} is invalid ({codes})")
    return issues


def validate_package_trust(
    config: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    *,
    workspace: str | Path,
) -> PackageTrust:
    profile = package_profile(config)
    if profile == "smoke":
        return PackageTrust(profile=profile, issues=[])
    if profile != "production":
        return PackageTrust(profile=profile, issues=["package.profile must be production or smoke"])

    issues: list[str] = []
    training = stages.get("training", {})
    export = stages.get("export", {})
    evaluation = stages.get("evaluation", {})
    if training.get("status") != "completed":
        issues.append("training stage did not complete")
    if not isinstance(training.get("external"), dict) or training["external"].get("ok") is not True:
        issues.append("production training must execute a successful external command")
    checkpoint = training.get("checkpoint")
    if not isinstance(checkpoint, dict) or not checkpoint.get("sha256"):
        issues.append("training checkpoint provenance is missing")
    if export.get("onnx_source") != "external_command":
        issues.append("production export must use an external ONNX artifact")
    if export.get("status") != "completed":
        issues.append("export stage did not complete")
    if evaluation.get("status") != "completed":
        issues.append("evaluation stage did not complete")
    if evaluation.get("metrics_source") != "measured":
        issues.append("production evaluation must provide measured metrics")

    package = config.get("package", {})
    package = package if isinstance(package, dict) else {}
    thresholds = package.get("metric_thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        issues.append("package.metric_thresholds is required for a production package")
    else:
        metrics = evaluation.get("metrics", {})
        for name, threshold in thresholds.items():
            actual = metrics.get(name) if isinstance(metrics, dict) else None
            if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                issues.append(f"measured metric is missing: {name}")
            elif not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                issues.append(f"metric threshold must be numeric: {name}")
            elif float(actual) < float(threshold):
                issues.append(f"metric {name}={actual} is below threshold {threshold}")

    examples_dir = package.get("examples_dir")
    if not examples_dir:
        issues.append("package.examples_dir is required for a production package")
    else:
        try:
            resolved_examples = _workspace_path(Path(workspace).resolve(), str(examples_dir))
        except ValueError as exc:
            issues.append(str(exc))
        else:
            if not resolved_examples.is_dir():
                issues.append(f"package.examples_dir was not found: {examples_dir}")
    limitations = package.get("limitations")
    if not isinstance(limitations, list) or not limitations or any("fill in" in str(item).lower() for item in limitations):
        issues.append("package.limitations must contain real known limitations")
    training_config = config.get("training", {}) if isinstance(config.get("training"), dict) else {}
    if training_config.get("adapter") == "fastreid":
        license_info = package.get("license")
        if not isinstance(license_info, dict):
            issues.append("package.license is required for a FastReID production package")
        else:
            for key in ("name", "source_url", "sha256", "approval_reference"):
                if not str(license_info.get(key) or "").strip():
                    issues.append(f"package.license.{key} is required for a FastReID production package")
            digest = str(license_info.get("sha256") or "").lower()
            if digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                issues.append("package.license.sha256 must be a SHA-256 digest")
    issues.extend(_manifest_issues(config, Path(workspace).resolve()))
    issues.extend(reference_validation_issues(config, Path(workspace).resolve()))
    return PackageTrust(profile=profile, issues=issues)


def build_model_card_data(
    config: dict[str, Any],
    *,
    config_path: str | Path,
    stages: dict[str, dict[str, Any]],
    artifact_name: str,
    labels_name: str,
    workspace: str | Path,
) -> dict[str, Any]:
    artifact = parse_artifact_name(artifact_name)
    experiment = config.get("experiment", {}) if isinstance(config.get("experiment"), dict) else {}
    dataset = config.get("dataset", {}) if isinstance(config.get("dataset"), dict) else {}
    model = config.get("model", {}) if isinstance(config.get("model"), dict) else {}
    package = config.get("package", {}) if isinstance(config.get("package"), dict) else {}
    task = str(experiment.get("task") or dataset.get("task") or "model")
    input_section = config.get("input") or config.get("export", {}).get("input", {})
    input_section = input_section if isinstance(input_section, dict) else {}
    input_size = model.get("input_size") or input_section.get("size") or [640, 640]
    if isinstance(input_size, list) and len(input_size) == 4:
        shape = [int(value) for value in input_size]
    elif isinstance(input_size, list) and len(input_size) == 2:
        shape = [1, 3, int(input_size[0]), int(input_size[1])]
    else:
        shape = [1, 3, 640, 640]
    dataset_name = str(dataset.get("name") or "dataset")
    dataset_version = str(dataset.get("version") or "0.0.0")
    dataset_id = f"{dataset_name}_v{dataset_version}"
    reference = reference_from_config(config)
    metrics = stages.get("evaluation", {}).get("metrics", {})
    metrics = dict(metrics) if isinstance(metrics, dict) else {}
    training = stages.get("training", {})
    export = stages.get("export", {})
    evaluation = stages.get("evaluation", {})
    workspace_path = Path(workspace).resolve()

    def relative(value: Any) -> Any:
        if not value:
            return value
        try:
            return Path(str(value)).resolve().relative_to(workspace_path).as_posix()
        except ValueError:
            return str(value)

    checkpoint = training.get("checkpoint")
    if isinstance(checkpoint, dict):
        checkpoint = dict(checkpoint)
        checkpoint["path"] = relative(checkpoint.get("path"))
    config_resolved = Path(config_path).resolve()
    config_sha256 = sha256_file(config_resolved, use_cache=False)
    code_revision = os.environ.get("GIT_COMMIT", "").strip()
    if not code_revision:
        try:
            completed = subprocess.run(
                ["git", "-C", str(workspace_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if completed.returncode == 0:
                code_revision = completed.stdout.strip()
        except OSError:
            pass
    deployment = {"runtime": "onnxruntime", "max_batch_size": 1, "supports_dynamic_batch": False}
    if isinstance(config.get("deployment"), dict):
        deployment.update(config["deployment"])
    configured_limitations = package.get("limitations")
    if isinstance(configured_limitations, list) and configured_limitations:
        limitations = [str(item) for item in configured_limitations]
    else:
        limitations = ["Smoke-test artifact; not approved for production use."]
    return {
        "model": {
            "name": artifact.stem.removesuffix(f"_v{artifact.version}_{artifact.precision}"),
            "version": artifact.version,
            "task": task,
            "architecture": str(model.get("architecture") or task),
            "precision": artifact.precision,
            "format": "onnx",
            "sha256": "",
        },
        "dataset": {
            "train": str(dataset.get("train_id") or dataset_id),
            "val": str(dataset.get("val_id") or dataset_id),
            "test": str(dataset.get("test_id") or dataset_id),
            "reference": reference.model_dump(mode="json") if reference is not None else None,
        },
        "input": {
            "layout": str(input_section.get("layout") or "nchw").lower(),
            "shape": shape,
            "dtype": str(input_section.get("dtype") or "float32").lower(),
            "color": str(input_section.get("color") or "rgb").lower(),
            "resize": str(input_section.get("resize") or "letterbox"),
            "normalize": input_section.get("normalize", "none"),
        },
        "output": {
            "format": str(
                (config.get("output", {}).get("format") if isinstance(config.get("output"), dict) else None)
                or export.get("output_format")
                or task
            ),
            "classes": labels_name,
        },
        "metrics": metrics,
        "metric_thresholds": dict(package.get("metric_thresholds", {})) if isinstance(package.get("metric_thresholds", {}), dict) else {},
        **({"license": dict(package["license"])} if isinstance(package.get("license"), dict) else {}),
        "deployment": deployment,
        "limitations": limitations,
        "provenance": {
            "profile": package_profile(config),
            "experiment_id": str(experiment.get("id") or ""),
            "config_path": relative(config_path),
            "config_sha256": config_sha256,
            "code_revision": code_revision or "unknown",
            "training": {
                "adapter": training.get("adapter") or (config.get("training", {}).get("adapter") if isinstance(config.get("training"), dict) else None),
                "command": config.get("training", {}).get("command") if isinstance(config.get("training"), dict) else None,
                "checkpoint": checkpoint,
                **({"framework": training["framework"]} if isinstance(training.get("framework"), dict) else {}),
            },
            "export": {
                "onnx_source": export.get("onnx_source", ""),
                "report": relative(export.get("report")),
            },
            "evaluation": {
                "metrics_source": evaluation.get("metrics_source"),
                "report": relative(evaluation.get("report")),
            },
        },
    }

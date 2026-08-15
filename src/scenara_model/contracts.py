from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scenara_model.naming import parse_artifact_name
from scenara_model.utils import read_yaml

ALLOWED_RELEASE_RECOMMENDATIONS = {"reject", "lab_only", "gray_release", "production"}
REQUIRED_MODEL_CONFIG_FIELDS = {"task", "type", "runtime", "version", "precision", "input", "output", "artifact"}


@dataclass
class ContractIssue:
    code: str
    message: str
    path: str = ""


@dataclass
class ContractValidation:
    path: Path
    ok: bool
    issues: list[ContractIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def validate_models_fragment(path: str | Path) -> ContractValidation:
    resolved = Path(path)
    issues: list[ContractIssue] = []
    try:
        data = read_yaml(resolved)
    except Exception as exc:  # noqa: BLE001
        return ContractValidation(resolved, False, [ContractIssue("contract.read_error", str(exc), str(resolved))])

    models = data.get("models")
    if not isinstance(models, dict) or not models:
        return ContractValidation(resolved, False, [ContractIssue("contract.models_missing", "models must be a non-empty object", "models")])

    for model_id, config in models.items():
        model_name = Path(str(model_id)).name
        try:
            artifact = parse_artifact_name(model_name)
        except ValueError as exc:
            issues.append(ContractIssue("contract.invalid_model_id", str(exc), str(model_id)))
            continue
        if not isinstance(config, dict):
            issues.append(ContractIssue("contract.invalid_model_config", "Model config must be an object", str(model_id)))
            continue
        missing = sorted(REQUIRED_MODEL_CONFIG_FIELDS - set(config))
        for field_name in missing:
            issues.append(ContractIssue("contract.missing_model_field", f"Missing field: {field_name}", f"{model_id}.{field_name}"))
        if str(config.get("version", "")) != artifact.version:
            issues.append(
                ContractIssue(
                    "contract.version_mismatch",
                    f"Config version must match artifact version {artifact.version}",
                    f"{model_id}.version",
                )
            )
        if str(config.get("precision", "")).lower() != artifact.precision:
            issues.append(
                ContractIssue(
                    "contract.precision_mismatch",
                    f"Config precision must match artifact precision {artifact.precision}",
                    f"{model_id}.precision",
                )
            )
        for object_field in ("input", "output", "artifact"):
            if object_field in config and not isinstance(config.get(object_field), dict):
                issues.append(ContractIssue("contract.invalid_model_config", f"{object_field} must be an object", f"{model_id}.{object_field}"))
        artifact_config = config.get("artifact", {})
        if isinstance(artifact_config, dict):
            for sidecar in ("model_card", "labels"):
                if not str(artifact_config.get(sidecar) or "").strip():
                    issues.append(
                        ContractIssue(
                            "contract.missing_sidecar_reference",
                            f"artifact.{sidecar} is required",
                            f"{model_id}.artifact.{sidecar}",
                        )
                    )

    return ContractValidation(resolved, ok=not issues, issues=issues)


def validate_release_decision(path: str | Path) -> ContractValidation:
    resolved = Path(path)
    issues: list[ContractIssue] = []
    try:
        data = read_yaml(resolved)
    except Exception as exc:  # noqa: BLE001
        return ContractValidation(resolved, False, [ContractIssue("contract.read_error", str(exc), str(resolved))])

    decision = data.get("decision")
    if not isinstance(decision, dict):
        return ContractValidation(resolved, False, [ContractIssue("contract.decision_missing", "decision must be an object", "decision")])

    for field_name in ("model", "recommendation", "reason", "required_service_checks"):
        if field_name not in decision:
            issues.append(ContractIssue("contract.missing_decision_field", f"Missing field: {field_name}", f"decision.{field_name}"))

    recommendation = decision.get("recommendation")
    if recommendation and recommendation not in ALLOWED_RELEASE_RECOMMENDATIONS:
        issues.append(
            ContractIssue(
                "contract.invalid_recommendation",
                f"recommendation must be one of {sorted(ALLOWED_RELEASE_RECOMMENDATIONS)}",
                "decision.recommendation",
            )
        )

    model = decision.get("model")
    if model:
        try:
            parse_artifact_name(Path(str(model)).name)
        except ValueError as exc:
            issues.append(ContractIssue("contract.invalid_decision_model", str(exc), "decision.model"))

    for list_field in ("reason", "required_service_checks"):
        value = decision.get(list_field)
        if value is not None and (not isinstance(value, list) or not value):
            issues.append(ContractIssue("contract.invalid_decision_list", f"{list_field} must be a non-empty list", f"decision.{list_field}"))
        elif isinstance(value, list):
            normalized = [str(item).strip() for item in value]
            if any(not item for item in normalized):
                issues.append(ContractIssue("contract.invalid_decision_list", f"{list_field} must not contain empty values", f"decision.{list_field}"))
            if len(normalized) != len(set(normalized)):
                issues.append(ContractIssue("contract.duplicate_decision_list", f"{list_field} must not contain duplicate values", f"decision.{list_field}"))

    if recommendation in {"gray_release", "production"} and not decision.get("rollback_target"):
        issues.append(
            ContractIssue(
                "contract.rollback_required",
                "rollback_target is required for gray_release or production",
                "decision.rollback_target",
            )
        )

    return ContractValidation(resolved, ok=not issues, issues=issues)


from __future__ import annotations

from pathlib import Path

from scenara_model.contracts import validate_models_fragment, validate_release_decision


def test_models_fragment_template_is_valid() -> None:
    result = validate_models_fragment(Path("configs/export/models.fragment.template.yml"))

    assert result.ok


def test_release_decision_template_is_valid() -> None:
    result = validate_release_decision(Path("configs/export/release-decision.template.yml"))

    assert result.ok


def test_release_decision_requires_rollback_for_production(workspace_tmp_path: Path) -> None:
    decision = workspace_tmp_path / "decision.yml"
    decision.write_text(
        """
decision:
  model: cross_camera_tracking/person_detector_yolov8n_v1.0.0_fp32.onnx
  recommendation: production
  reason:
    - ok
  required_service_checks:
    - smoke_test
""",
        encoding="utf-8",
    )

    result = validate_release_decision(decision)

    assert not result.ok
    assert "contract.rollback_required" in {issue.code for issue in result.issues}

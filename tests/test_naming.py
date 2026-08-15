from __future__ import annotations

import pytest

from scenara_model.naming import parse_artifact_name


def test_parse_artifact_name_accepts_standard_name() -> None:
    artifact = parse_artifact_name("person_detector_yolov8n_v1.2.3_fp32.onnx")

    assert artifact.family == "person_detector_yolov8n"
    assert artifact.version == "1.2.3"
    assert artifact.precision == "fp32"


@pytest.mark.parametrize(
    "name",
    [
        "PersonDetector_v1.0.0_fp32.onnx",
        "person_detector_yolov8n_1.0.0_fp32.onnx",
        "person_detector_yolov8n_v1_fp32.onnx",
        "person_detector_yolov8n_v1.0.0_float32.onnx",
    ],
)
def test_parse_artifact_name_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError):
        parse_artifact_name(name)


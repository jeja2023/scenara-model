from __future__ import annotations

from pathlib import Path

from vision_model_lab.packaging.model_package import create_model_package, validate_model_package

SAMPLE_JPEG_BYTES = bytes.fromhex("ffd8ffe000104a46494600010101000100010000ffd9")


def _write_identity_onnx(path: Path) -> None:
    import onnx
    from onnx import TensorProto, helper

    node = helper.make_node("Identity", ["input"], ["output"])
    graph = helper.make_graph(
        [node],
        "identity_graph",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1])],
    )
    model = helper.make_model(graph, producer_name="vision-model-lab-test", opset_imports=[helper.make_operatorsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, path)


def test_create_and_validate_model_package_strict(workspace_tmp_path: Path) -> None:
    model = workspace_tmp_path / "source.onnx"
    _write_identity_onnx(model)

    examples = workspace_tmp_path / "examples"
    examples.mkdir()
    (examples / "frame_001.jpg").write_bytes(SAMPLE_JPEG_BYTES)
    (examples / "frame_001.expected.json").write_text('{"outputs":[]}', encoding="utf-8")

    package_dir = create_model_package(
        output_root=workspace_tmp_path / "shared-models",
        project_name="cross_camera_tracking",
        artifact_name="person_detector_identity_v1.0.0_fp32.onnx",
        model_file=model,
        labels=["person"],
        task="detection",
        architecture="identity",
        examples_dir=examples,
    )

    result = validate_model_package(
        package_dir,
        model_id="person_detector_identity_v1.0.0_fp32.onnx",
        strict_hash=True,
        strict_onnx=True,
    )

    assert result.ok
    assert result.sha256
    assert result.onnx_checked
    assert result.ort_checked


def test_validate_model_package_reports_missing_sidecars(workspace_tmp_path: Path) -> None:
    model = workspace_tmp_path / "person_detector_identity_v1.0.0_fp32.onnx"
    _write_identity_onnx(model)

    result = validate_model_package(workspace_tmp_path, strict_hash=True)

    assert not result.ok
    assert {issue.code for issue in result.issues} >= {
        "package.missing_model_card",
        "package.missing_labels",
        "package.missing_examples",
    }


def test_validate_model_package_rejects_model_id_outside_package(workspace_tmp_path: Path) -> None:
    package_dir = workspace_tmp_path / "package"
    package_dir.mkdir()
    outside_model = workspace_tmp_path / "outside_v1.0.0_fp32.onnx"
    _write_identity_onnx(outside_model)

    result = validate_model_package(package_dir, model_id=str(outside_model.resolve()), strict_examples=False)

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"package.model_outside_package"}

def test_validate_model_package_rejects_expected_label_mismatch(workspace_tmp_path: Path) -> None:
    model = workspace_tmp_path / "source.onnx"
    _write_identity_onnx(model)
    examples = workspace_tmp_path / "examples"
    examples.mkdir()
    (examples / "frame_001.jpg").write_bytes(SAMPLE_JPEG_BYTES)
    (examples / "frame_001.expected.json").write_text(
        '{"detections":[{"label":"car","score":0.9,"bbox":[0,0,10,10]}]}',
        encoding="utf-8",
    )
    package_dir = create_model_package(
        output_root=workspace_tmp_path / "shared-models",
        project_name="cross_camera_tracking",
        artifact_name="person_detector_identity_v1.0.0_fp32.onnx",
        model_file=model,
        labels=["person"],
        task="detection",
        architecture="identity",
        examples_dir=examples,
    )

    result = validate_model_package(package_dir, model_id="person_detector_identity_v1.0.0_fp32.onnx")

    assert not result.ok
    assert "package.expected_label_mismatch" in {issue.code for issue in result.issues}

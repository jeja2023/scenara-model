from __future__ import annotations

from pathlib import Path

from scenara_model.datasets.manifest import validate_manifest


def test_validate_manifest_accepts_example_manifest() -> None:
    result = validate_manifest(Path("data/manifests/example_train_v1.jsonl"))

    assert result.ok
    assert result.total_rows == 2
    assert result.split_counts == {"train": 1, "val": 1}


def test_validate_manifest_reports_duplicate_and_bad_version(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "bad.jsonl"
    manifest.write_text(
        "\n".join(
            [
                '{"image":"s3://bucket/a.jpg","split":"train","source":"cam","dataset_version":"1.0.0"}',
                '{"image":"s3://bucket/a.jpg","split":"holdout","source":"cam","dataset_version":"v1"}',
            ]
        ),
        encoding="utf-8",
    )

    result = validate_manifest(manifest)

    assert not result.ok
    assert {issue.code for issue in result.issues} >= {
        "manifest.duplicate_image",
        "manifest.invalid_split",
        "manifest.invalid_dataset_version",
    }

def test_validate_manifest_enforces_min_split_counts_and_allowed_labels(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "labels.jsonl"
    manifest.write_text(
        '{"image":"s3://bucket/a.jpg","split":"train","source":"cam","dataset_version":"1.0.0","label":"car"}',
        encoding="utf-8",
    )

    result = validate_manifest(manifest, min_split_counts={"train": 2, "val": 1}, allowed_labels=["person"])

    assert not result.ok
    assert {issue.code for issue in result.issues} >= {"manifest.label_not_allowed", "manifest.min_split_count"}


def test_validate_manifest_can_check_local_images(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "local.jsonl"
    manifest.write_text(
        '{"image":"images/missing.jpg","split":"train","source":"camera","dataset_version":"1.0.0"}\n',
        encoding="utf-8",
    )

    result = validate_manifest(manifest, check_local_files=True)

    assert not result.ok
    assert "manifest.image_not_found" in {issue.code for issue in result.issues}


def test_validate_manifest_accepts_structured_data_platform_document(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "data-platform.json"
    manifest.write_text(
        '{"version":"1.0.1","samples":[{"sample_id":"smp_1",'
        '"content_ref":{"bucket":"scenara-datasets","key":"e2e/sample.png",'
        '"version":"version:abc","checksum":"sha256:'
        + "a" * 64
        + '","size_bytes":1,"content_type":"image/png"},'
        '"dataset_split":"train","source_system":"scenara-core"}]}',
        encoding="utf-8",
    )

    result = validate_manifest(manifest, check_local_files=True)

    assert result.ok
    assert result.total_rows == 1
    assert result.split_counts == {"train": 1}

from __future__ import annotations

from pathlib import Path

import pytest

from scenara_model.dataset_versions import DatasetVersionReference, validate_dataset_version_reference


def test_dataset_version_reference_rejects_missing_consumer_authorization() -> None:
    with pytest.raises(ValueError, match="authorize scenara-model"):
        DatasetVersionReference(
            dataset_id="dataset_v1.0.0",
            version="1.0.0",
            manifest_uri="s3://bucket/dataset.jsonl#sha256=" + "a" * 64,
            manifest_sha256="a" * 64,
            lineage_refs=("https://data.example/lineage.json#sha256=" + "b" * 64,),
            authorization_id="grant_1",
            authorized_consumer_repository_ids=("another-repo",),
            created_at="2026-08-18T00:00:00Z",
        )


def test_dataset_version_reference_validates_local_manifest_digest(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "train.jsonl"
    manifest.write_text('{"image":"s3://bucket/train.jpg","split":"train","source":"camera","dataset_version":"1.0.0"}\n', encoding="utf-8")
    reference = DatasetVersionReference(
        dataset_id="dataset_v1.0.0",
        version="1.0.0",
        manifest_uri="s3://bucket/dataset.jsonl#sha256=50bfb57b1debec2863cbcff29f282ce284873db473b08d0bfbfd89e11b9212ad",
        manifest_sha256="50bfb57b1debec2863cbcff29f282ce284873db473b08d0bfbfd89e11b9212ad",
        lineage_refs=("https://data.example/lineage.json#sha256=" + "b" * 64,),
        authorization_id="grant_1",
        authorized_consumer_repository_ids=("scenara-model",),
        created_at="2026-08-18T00:00:00Z",
    )

    assert validate_dataset_version_reference(reference, manifest_path=manifest) == reference

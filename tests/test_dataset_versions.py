from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scenara_model.dataset_versions import (
    DatasetVersionReference,
    reference_validation_issues,
    validate_dataset_version_reference,
)


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


def test_multidomain_training_requires_matching_domain_and_annotation_schema(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "behavior.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    reference = {
        "schema_version": "1.0",
        "dataset_id": "behavior.training",
        "version": "1.0.0",
        "manifest_uri": f"s3://bucket/behavior.jsonl#sha256={digest}",
        "manifest_sha256": digest,
        "lineage_refs": ("https://data.example/lineage.json#sha256=" + "b" * 64,),
        "authorization_id": "grant_behavior",
        "authorized_consumer_repository_ids": ("scenara-model",),
        "created_at": "2026-08-18T00:00:00Z",
        "domain": "behavior",
        "annotation_schema_ids": ("scenara.behavior.action.v1",),
    }
    config = {
        "experiment": {"task": "behavior"},
        "dataset": {
            "dataset_id": "behavior.training",
            "version": "1.0.0",
            "reference_manifest_path": str(manifest.resolve()),
            "reference": reference,
        },
    }
    assert reference_validation_issues(config, workspace_tmp_path) == []
    config["experiment"]["task"] = "fashion"
    assert "domain must be fashion" in reference_validation_issues(config, workspace_tmp_path)[0]


def test_reid_training_accepts_surveillance_review_dataset_reference(workspace_tmp_path: Path) -> None:
    manifest = workspace_tmp_path / "surveillance-review.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    config = {
        "experiment": {"task": "reid"},
        "dataset": {
            "dataset_id": "portrait.surveillance-review",
            "version": "1.0.0",
            "reference_manifest_path": str(manifest.resolve()),
            "reference": {
                "schema_version": "1.0",
                "dataset_id": "portrait.surveillance-review",
                "version": "1.0.0",
                "manifest_uri": f"s3://bucket/surveillance-review.jsonl#sha256={digest}",
                "manifest_sha256": digest,
                "lineage_refs": ("https://data.example/lineage.json#sha256=" + "b" * 64,),
                "authorization_id": "grant_surveillance_reid",
                "authorized_consumer_repository_ids": ("scenara-model",),
                "created_at": "2026-08-30T00:00:00Z",
                "domain": "portrait",
                "annotation_schema_ids": ("scenara.portrait.surveillance-review.v1",),
            },
        },
    }
    assert reference_validation_issues(config, workspace_tmp_path) == []

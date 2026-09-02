from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATASET_VERSION_SCHEMA_VERSION = "1.0"
CONSUMER_REPOSITORY_ID = "scenara-model"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
IMMUTABLE_URI_PATTERN = r"^.+(?:@sha256:|#sha256=)[0-9a-f]{64}$"
RFC3339_UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"


class DatasetVersionReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = DATASET_VERSION_SCHEMA_VERSION
    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?$")
    manifest_uri: str = Field(pattern=IMMUTABLE_URI_PATTERN, max_length=2048)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    lineage_refs: tuple[str, ...] = Field(min_length=1, max_length=100)
    authorization_id: str = Field(min_length=1, max_length=256)
    authorized_consumer_repository_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    created_at: str = Field(pattern=RFC3339_UTC_PATTERN)
    domain: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    annotation_schema_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator("created_at")
    @classmethod
    def valid_created_at(cls, value: str) -> str:
        datetime.fromisoformat(value[:-1] + "+00:00")
        return value

    @field_validator("lineage_refs")
    @classmethod
    def valid_lineage_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            len(item) > 2048 or re.fullmatch(IMMUTABLE_URI_PATTERN, item) is None for item in value
        ):
            raise ValueError("lineage_refs must contain unique immutable SHA-256 references")
        return value

    @model_validator(mode="after")
    def validate_digest_and_consumer(self) -> DatasetVersionReference:
        if not self.manifest_uri.endswith((f"@sha256:{self.manifest_sha256}", f"#sha256={self.manifest_sha256}")):
            raise ValueError("manifest_uri digest must match manifest_sha256")
        if CONSUMER_REPOSITORY_ID not in self.authorized_consumer_repository_ids:
            raise ValueError("DatasetVersionReference does not authorize scenara-model")
        if len(self.annotation_schema_ids) != len(set(self.annotation_schema_ids)):
            raise ValueError("annotation_schema_ids must be unique")
        return self


def validate_dataset_version_reference(
    reference: DatasetVersionReference,
    *,
    manifest_path: str | Path | None = None,
    expected_dataset_id: str | None = None,
    expected_version: str | None = None,
) -> DatasetVersionReference:
    if expected_dataset_id is not None and reference.dataset_id != expected_dataset_id:
        raise ValueError("dataset reference dataset_id does not match the training configuration")
    if expected_version is not None and reference.version != expected_version:
        raise ValueError("dataset reference version does not match the training configuration")
    if manifest_path is not None:
        path = Path(manifest_path)
        if not path.is_file():
            raise ValueError(f"dataset manifest was not found: {path}")
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != reference.manifest_sha256:
            raise ValueError("dataset manifest checksum does not match DatasetVersionReference")
    return reference


def reference_from_config(config: dict[str, Any]) -> DatasetVersionReference | None:
    dataset = config.get("dataset")
    if not isinstance(dataset, dict):
        return None
    raw = dataset.get("reference")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("dataset.reference must be an object")
    return DatasetVersionReference.model_validate(raw)


def reference_validation_issues(config: dict[str, Any], workspace: str | Path) -> list[str]:
    dataset = config.get("dataset")
    if not isinstance(dataset, dict):
        return ["dataset must be an object"]
    try:
        reference = reference_from_config(config)
    except ValueError as exc:
        return [f"dataset.reference is invalid: {exc}"]
    if reference is None:
        return ["dataset.reference is required for production training"]

    expected_version = dataset.get("version")
    expected_dataset_id = dataset.get("dataset_id")
    manifest_path = dataset.get("reference_manifest_path")
    if not manifest_path:
        return ["dataset.reference_manifest_path is required for production training"]
    candidate = Path(str(manifest_path))
    if not candidate.is_absolute():
        candidate = Path(workspace) / candidate
    try:
        validate_dataset_version_reference(
            reference,
            manifest_path=candidate.resolve(),
            expected_dataset_id=str(expected_dataset_id) if expected_dataset_id else None,
            expected_version=str(expected_version) if expected_version else None,
        )
    except ValueError as exc:
        return [str(exc)]
    task = str(config.get("experiment", {}).get("task") or dataset.get("task") or "")
    expected_domain = {
        "reid": "portrait",
        "ocr": "ocr",
        "behavior": "behavior",
        "fashion": "fashion",
    }.get(task)
    if expected_domain is not None and reference.domain != expected_domain:
        return [f"dataset reference domain must be {expected_domain} for task {task}"]
    expected_schema = {
        "reid": "scenara.portrait.surveillance-review.v1",
        "ocr": "scenara.ocr.document.v1",
        "behavior": "scenara.behavior.action.v1",
        "fashion": "scenara.fashion.style.v1",
    }.get(task)
    if expected_schema is not None and expected_schema not in reference.annotation_schema_ids:
        return [f"dataset reference must include annotation schema {expected_schema}"]
    return []


__all__ = [
    "CONSUMER_REPOSITORY_ID",
    "DATASET_VERSION_SCHEMA_VERSION",
    "DatasetVersionReference",
    "reference_from_config",
    "reference_validation_issues",
    "validate_dataset_version_reference",
]

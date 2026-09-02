from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BundleFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=128)

    @field_validator("path")
    @classmethod
    def portable_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("bundle file paths must use forward slashes")
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("bundle file paths must stay inside the bundle")
        return path.as_posix()


class ArtifactBundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    model_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?$")
    domain: Literal["portrait", "ocr", "behavior", "fashion"]
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    adapter: str = Field(min_length=2, max_length=64)
    runtime_model_id: str = Field(pattern=r"^[^/\\\s]+/[^/\\\s]+$", max_length=384)
    artifact_format: Literal["paddle", "pytorch", "bundle"]
    files: tuple[BundleFile, ...] = Field(min_length=1, max_length=1000)
    model_card_path: str = Field(min_length=1, max_length=512)
    label_schema_ids: tuple[str, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_manifest(self) -> ArtifactBundleManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("bundle file paths must be unique")
        if self.model_card_path not in paths:
            raise ValueError("model_card_path must reference a file in the bundle")
        expected_schema = {
            "portrait": "scenara.portrait.surveillance-review.v1",
            "ocr": "scenara.ocr.document.v1",
            "behavior": "scenara.behavior.action.v1",
            "fashion": "scenara.fashion.style.v1",
        }[self.domain]
        if expected_schema not in self.label_schema_ids:
            raise ValueError(f"bundle must declare label schema {expected_schema}")
        return self


@dataclass(frozen=True, slots=True)
class BundleIssue:
    code: str
    message: str
    path: str = ""


@dataclass(slots=True)
class BundleValidation:
    bundle_dir: Path
    ok: bool
    manifest: ArtifactBundleManifest | None = None
    manifest_sha256: str | None = None
    issues: list[BundleIssue] = field(default_factory=list)


def validate_artifact_bundle(bundle_dir: str | Path) -> BundleValidation:
    root = Path(bundle_dir).resolve()
    manifest_path = root / "bundle-manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        return BundleValidation(
            bundle_dir=root,
            ok=False,
            issues=[BundleIssue("bundle.manifest_missing", "bundle-manifest.json is required", str(manifest_path))],
        )
    content = manifest_path.read_bytes()
    try:
        manifest = ArtifactBundleManifest.model_validate_json(content)
    except Exception as exc:  # noqa: BLE001
        return BundleValidation(
            bundle_dir=root,
            ok=False,
            issues=[BundleIssue("bundle.manifest_invalid", str(exc), str(manifest_path))],
        )
    issues: list[BundleIssue] = []
    for item in manifest.files:
        candidate = (root / PurePosixPath(item.path)).resolve()
        if root not in candidate.parents:
            issues.append(BundleIssue("bundle.path_escape", "bundle file escapes its root", item.path))
            continue
        if not candidate.is_file():
            issues.append(BundleIssue("bundle.file_missing", "declared bundle file is missing", item.path))
            continue
        if candidate.stat().st_size != item.size_bytes:
            issues.append(BundleIssue("bundle.size_mismatch", "declared file size does not match", item.path))
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != item.sha256:
            issues.append(BundleIssue("bundle.digest_mismatch", "declared file SHA-256 does not match", item.path))
        guessed = mimetypes.guess_type(candidate.name)[0]
        if guessed and item.media_type not in {guessed, "application/octet-stream"}:
            issues.append(BundleIssue("bundle.media_type_mismatch", "declared media type is inconsistent", item.path))
    return BundleValidation(
        bundle_dir=root,
        ok=not issues,
        manifest=manifest,
        manifest_sha256=hashlib.sha256(content).hexdigest(),
        issues=issues,
    )


def admission_payload(
    validation: BundleValidation,
    *,
    source_uri: str,
    license_id: str,
    model_card_uri: str,
    evaluation_evidence: tuple[str, ...],
    vram_mb: int,
    regression_samples: tuple[str, ...],
    production_ready: bool = False,
) -> dict[str, object]:
    if not validation.ok or validation.manifest is None or validation.manifest_sha256 is None:
        raise ValueError("only a valid artifact bundle can produce an admission payload")
    digest = validation.manifest_sha256
    if not source_uri.endswith((f"@sha256:{digest}", f"#sha256={digest}")):
        raise ValueError("source_uri digest must match bundle manifest SHA-256")
    manifest = validation.manifest
    return {
        "schema_version": "1.0",
        "model_id": manifest.model_id,
        "version": manifest.version,
        "capability": manifest.capability,
        "adapter": manifest.adapter,
        "runtime_model_id": manifest.runtime_model_id,
        "sha256": digest,
        "source_uri": source_uri,
        "license_id": license_id,
        "model_card": model_card_uri,
        "evaluation_evidence": list(evaluation_evidence),
        "vram_mb": vram_mb,
        "regression_samples": list(regression_samples),
        "production_ready": production_ready,
        "domain": manifest.domain,
        "artifact_format": "bundle",
        "artifact_files": [item.model_dump(mode="json") for item in manifest.files],
    }


__all__ = [
    "ArtifactBundleManifest",
    "BundleFile",
    "BundleIssue",
    "BundleValidation",
    "admission_payload",
    "validate_artifact_bundle",
]

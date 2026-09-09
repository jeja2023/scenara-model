from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scenara_model.naming import is_semver
from scenara_model.utils import read_jsonl

ALLOWED_SPLITS = {"train", "val", "test", "regression", "edge", "query", "gallery"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
REQUIRED_FIELDS = {"image", "split", "source", "dataset_version"}


@dataclass
class ManifestIssue:
    code: str
    message: str
    line: int | None = None
    field: str | None = None


@dataclass
class ManifestValidation:
    path: Path
    ok: bool
    total_rows: int
    split_counts: dict[str, int]
    issues: list[ManifestIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "total_rows": self.total_rows,
            "split_counts": self.split_counts,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def validate_manifest(
    path: str | Path,
    *,
    min_split_counts: dict[str, int] | None = None,
    allowed_labels: list[str] | None = None,
    check_local_files: bool = False,
) -> ManifestValidation:
    resolved = Path(path)
    issues: list[ManifestIssue] = []
    try:
        rows = _read_manifest_rows(resolved)
    except Exception as exc:  # noqa: BLE001
        return ManifestValidation(
            path=resolved,
            ok=False,
            total_rows=0,
            split_counts={},
            issues=[ManifestIssue("manifest.read_error", str(exc))],
        )

    split_counts: dict[str, int] = {}
    seen_images: set[str] = set()
    allowed_label_set = set(allowed_labels or [])

    for index, row in enumerate(rows, start=1):
        missing = sorted(REQUIRED_FIELDS - set(row))
        for field_name in missing:
            issues.append(ManifestIssue("manifest.missing_field", f"Missing field: {field_name}", index, field_name))

        image = row.get("image")
        if image:
            image_value = str(image)
            if image_value in seen_images:
                issues.append(ManifestIssue("manifest.duplicate_image", f"Duplicate image: {image_value}", index, "image"))
            seen_images.add(image_value)
            suffix = Path(image_value.split("?", 1)[0]).suffix.lower()
            if suffix and suffix not in IMAGE_EXTENSIONS:
                issues.append(ManifestIssue("manifest.invalid_image_extension", f"Unsupported image extension: {suffix}", index, "image"))
            if check_local_files and "://" not in image_value:
                image_path = Path(image_value)
                if not image_path.is_absolute():
                    image_path = resolved.parent / image_path
                if not image_path.resolve().is_file():
                    issues.append(ManifestIssue("manifest.image_not_found", f"Image file was not found: {image_path.resolve()}", index, "image"))
        elif "image" in row:
            issues.append(ManifestIssue("manifest.empty_field", "image must not be empty", index, "image"))

        source = row.get("source")
        if "source" in row and not str(source or "").strip():
            issues.append(ManifestIssue("manifest.empty_field", "source must not be empty", index, "source"))

        label = row.get("label")
        if label is not None and not isinstance(label, str):
            issues.append(ManifestIssue("manifest.invalid_label", "label must be a string when present", index, "label"))
        elif isinstance(label, str) and allowed_label_set and label not in allowed_label_set:
            issues.append(ManifestIssue("manifest.label_not_allowed", f"label is not in allowed labels: {label}", index, "label"))

        split = str(row.get("split", ""))
        if split:
            split_counts[split] = split_counts.get(split, 0) + 1
            if split not in ALLOWED_SPLITS:
                issues.append(ManifestIssue("manifest.invalid_split", f"Invalid split: {split}", index, "split"))

        dataset_version = str(row.get("dataset_version", ""))
        if "dataset_version" in row and not dataset_version.strip():
            issues.append(ManifestIssue("manifest.empty_field", "dataset_version must not be empty", index, "dataset_version"))
        if dataset_version and not is_semver(dataset_version):
            issues.append(
                ManifestIssue(
                    "manifest.invalid_dataset_version",
                    "dataset_version must be semantic version like 1.2.0",
                    index,
                    "dataset_version",
                )
            )

        tags = row.get("tags")
        if tags is not None and not isinstance(tags, list):
            issues.append(ManifestIssue("manifest.invalid_tags", "tags must be a list when present", index, "tags"))
        elif isinstance(tags, list) and not all(isinstance(tag, str) and tag.strip() for tag in tags):
            issues.append(ManifestIssue("manifest.invalid_tags", "tags must contain non-empty strings", index, "tags"))

    for split, minimum in (min_split_counts or {}).items():
        if split_counts.get(split, 0) < minimum:
            issues.append(
                ManifestIssue(
                    "manifest.min_split_count",
                    f"split {split} has {split_counts.get(split, 0)} rows; expected at least {minimum}",
                    None,
                    "split",
                )
            )

    return ManifestValidation(
        path=resolved,
        ok=not issues,
        total_rows=len(rows),
        split_counts=split_counts,
        issues=issues,
    )


def _read_manifest_rows(path: Path) -> list[dict[str, Any]]:
    """Read native JSONL manifests and the structured Data-platform document.

    ``scenara-data`` publishes an immutable JSON document containing a
    ``samples`` array.  Model's local training tools use JSONL rows, so the
    adapter normalizes the document in memory while leaving the downloaded
    bytes untouched for DatasetVersionReference SHA-256 verification.
    """

    raw = path.read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return read_jsonl(path)

    if not isinstance(document, dict) or not isinstance(document.get("samples"), list):
        return read_jsonl(path)

    dataset_version = str(document.get("version") or "")
    rows: list[dict[str, Any]] = []
    for sample in document["samples"]:
        if not isinstance(sample, dict):
            raise ValueError("Data-platform manifest samples must be objects")
        sample_id = str(sample.get("sample_id") or "")
        reference = sample.get("content_ref") or sample.get("source_ref")
        if not isinstance(reference, dict):
            raise ValueError(f"Data-platform sample {sample_id} has no object reference")

        bucket = str(reference.get("bucket") or "")
        key = str(reference.get("key") or "")
        checksum = str(
            reference.get("checksum")
            or sample.get("content_sha256")
            or ""
        )
        digest = checksum.removeprefix("sha256:")
        if not bucket or not key or len(digest) != 64:
            raise ValueError(f"Data-platform sample {sample_id} has an invalid object reference")

        image = f"s3://{bucket}/{quote(key, safe='/')}"
        version = reference.get("version")
        if version:
            image += f"?versionId={quote(str(version), safe='')}"
        image += f"#sha256={digest}"

        split = str(sample.get("dataset_split") or "")
        if split == "validation":
            split = "val"

        source = str(
            sample.get("source_system")
            or sample.get("source_resource_id")
            or f"scenara-data://sample/{sample_id}"
        )
        rows.append(
            {
                "sample_id": sample_id,
                "image": image,
                "split": split,
                "source": source,
                "dataset_version": dataset_version,
                "content_sha256": checksum,
                "media_type": sample.get("media_type"),
                "metadata": sample.get("metadata", {}),
            }
        )
    return rows


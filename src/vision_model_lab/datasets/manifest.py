from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vision_model_lab.naming import is_semver
from vision_model_lab.utils import read_jsonl

ALLOWED_SPLITS = {"train", "val", "test", "regression", "edge"}
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
        rows = read_jsonl(resolved)
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


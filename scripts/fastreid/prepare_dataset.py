from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

JPEG_SUFFIXES = {".jpg", ".jpeg"}
REQUIRED_FIELDS = {"image", "person_id", "camera_id"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            value = line.strip()
            if not value:
                continue
            try:
                row = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: every JSONL value must be an object")
            rows.append(row)
    return rows


def _positive_int(row: dict[str, Any], field: str, *, path: Path, index: int, maximum: int | None = None) -> int:
    value = row.get(field)
    if value is None or isinstance(value, bool):
        raise ValueError(f"{path}:{index}: {field} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}:{index}: {field} must be a positive integer") from exc
    if number < 1 or (maximum is not None and number > maximum):
        suffix = f" no greater than {maximum}" if maximum is not None else ""
        raise ValueError(f"{path}:{index}: {field} must be a positive integer{suffix}")
    return number


def _source_image(row: dict[str, Any], *, manifest: Path, index: int) -> Path:
    missing = sorted(REQUIRED_FIELDS - set(row))
    if missing:
        raise ValueError(f"{manifest}:{index}: missing ReID fields: {', '.join(missing)}")
    image = str(row["image"])
    if "://" in image:
        raise ValueError(f"{manifest}:{index}: image must be a local staged JPEG, not a remote URI")
    source = Path(image)
    if not source.is_absolute():
        source = manifest.parent / source
    source = source.resolve()
    if source.suffix.lower() not in JPEG_SUFFIXES:
        raise ValueError(f"{manifest}:{index}: FastReID materialization requires .jpg or .jpeg images")
    if not source.is_file():
        raise ValueError(f"{manifest}:{index}: image was not found: {source}")
    return source


def _copy_rows(rows: list[dict[str, Any]], *, manifest: Path, destination: Path, role: str | None = None) -> list[tuple[int, int]]:
    identities: list[tuple[int, int]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(rows, start=1):
        if role is not None and row.get("reid_role") != role:
            continue
        source = _source_image(row, manifest=manifest, index=index)
        person_id = _positive_int(row, "person_id", path=manifest, index=index)
        # Market1501's file-name parser only accepts a one-digit camera ID.
        camera_id = _positive_int(row, "camera_id", path=manifest, index=index, maximum=9)
        target = destination / f"{person_id:06d}_c{camera_id}_{index:06d}.jpg"
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        identities.append((person_id, camera_id))
    return identities


def materialize(*, train_manifest: Path, test_manifest: Path, output: Path, overwrite: bool) -> dict[str, Any]:
    for manifest in (train_manifest, test_manifest):
        if not manifest.is_file():
            raise ValueError(f"manifest was not found: {manifest}")
    if output.exists():
        if not overwrite:
            raise ValueError(f"output already exists: {output}; pass --overwrite to replace this generated directory")
        shutil.rmtree(output)

    train_rows = _read_jsonl(train_manifest)
    test_rows = _read_jsonl(test_manifest)
    if not train_rows or not test_rows:
        raise ValueError("train and test manifests must both contain at least one row")
    if any(row.get("split") != "train" for row in train_rows):
        raise ValueError("the FastReID train manifest may contain only split=train rows")
    if any(row.get("split") != "test" for row in test_rows):
        raise ValueError("the FastReID test manifest may contain only split=test rows")
    invalid_roles = sorted({str(row.get("reid_role")) for row in test_rows if row.get("reid_role") not in {"query", "gallery"}})
    if invalid_roles:
        raise ValueError("test rows require reid_role=query or reid_role=gallery")

    train_items = _copy_rows(train_rows, manifest=train_manifest, destination=output / "bounding_box_train")
    query_items = _copy_rows(test_rows, manifest=test_manifest, destination=output / "query", role="query")
    gallery_items = _copy_rows(test_rows, manifest=test_manifest, destination=output / "bounding_box_test", role="gallery")
    if not query_items or not gallery_items:
        raise ValueError("the fixed test set requires both query and gallery rows")
    counts = Counter(person_id for person_id, _ in train_items)
    sparse = sorted(person_id for person_id, count in counts.items() if count < 4)
    if sparse:
        raise ValueError("each training identity requires at least four images for NUM_INSTANCE=4; insufficient IDs: " + ", ".join(map(str, sparse[:20])))
    gallery_by_id = {person_id for person_id, _ in gallery_items}
    invalid_queries = sorted({person_id for person_id, _ in query_items if person_id not in gallery_by_id})
    if invalid_queries:
        raise ValueError("every query identity must appear in the gallery; missing IDs: " + ", ".join(map(str, invalid_queries[:20])))

    report = {
        "format": "Market1501-compatible",
        "train_manifest": str(train_manifest),
        "train_manifest_sha256": hashlib.sha256(train_manifest.read_bytes()).hexdigest(),
        "test_manifest": str(test_manifest),
        "test_manifest_sha256": hashlib.sha256(test_manifest.read_bytes()).hexdigest(),
        "train_images": len(train_items),
        "train_identities": len(counts),
        "query_images": len(query_items),
        "gallery_images": len(gallery_items),
    }
    (output / "scenara-reid-materialization.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize immutable ReID manifests into FastReID's Market1501-compatible layout.")
    parser.add_argument("--train-manifest", type=Path, required=True)
    parser.add_argument("--test-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="replace only the explicitly selected generated output directory")
    args = parser.parse_args(argv)
    try:
        report = materialize(
            train_manifest=args.train_manifest.resolve(),
            test_manifest=args.test_manifest.resolve(),
            output=args.output.resolve(),
            overwrite=args.overwrite,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

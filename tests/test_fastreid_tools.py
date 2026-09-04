from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

JPEG = bytes.fromhex("ffd8ffe000104a46494600010101000100010000ffd9")


def _manifest_row(image: Path, *, split: str, person_id: int, camera_id: int, role: str | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "image": image.name,
        "split": split,
        "source": "camera",
        "dataset_version": "1.0.0",
        "person_id": person_id,
        "camera_id": camera_id,
    }
    if role:
        row["reid_role"] = role
    return row


def test_fastreid_manifest_materializer_creates_fixed_market1501_layout(workspace_tmp_path: Path) -> None:
    rows: list[dict[str, object]] = []
    for index in range(4):
        image = workspace_tmp_path / f"train_{index}.jpg"
        image.write_bytes(JPEG)
        rows.append(_manifest_row(image, split="train", person_id=1, camera_id=1))
    train_manifest = workspace_tmp_path / "train.jsonl"
    train_manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    query = workspace_tmp_path / "query.jpg"
    gallery = workspace_tmp_path / "gallery.jpg"
    query.write_bytes(JPEG)
    gallery.write_bytes(JPEG)
    test_manifest = workspace_tmp_path / "test.jsonl"
    test_manifest.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _manifest_row(query, split="test", person_id=1, camera_id=1, role="query"),
                _manifest_row(gallery, split="test", person_id=1, camera_id=2, role="gallery"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    output = workspace_tmp_path / "fastreid"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/fastreid/prepare_dataset.py",
            "--train-manifest",
            str(train_manifest),
            "--test-manifest",
            str(test_manifest),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["train_images"] == 4
    assert report["query_images"] == 1
    assert report["gallery_images"] == 1
    assert len(list((output / "bounding_box_train").glob("*.jpg"))) == 4
    assert len(list((output / "query").glob("*.jpg"))) == 1
    assert len(list((output / "bounding_box_test").glob("*.jpg"))) == 1


def test_fastreid_materializer_rejects_remote_images(workspace_tmp_path: Path) -> None:
    train_manifest = workspace_tmp_path / "train.jsonl"
    train_manifest.write_text(
        json.dumps(
            {
                "image": "s3://bucket/person.jpg",
                "split": "train",
                "source": "camera",
                "dataset_version": "1.0.0",
                "person_id": 1,
                "camera_id": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    test_manifest = workspace_tmp_path / "test.jsonl"
    query = workspace_tmp_path / "query.jpg"
    gallery = workspace_tmp_path / "gallery.jpg"
    query.write_bytes(JPEG)
    gallery.write_bytes(JPEG)
    test_manifest.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _manifest_row(query, split="test", person_id=1, camera_id=1, role="query"),
                _manifest_row(gallery, split="test", person_id=1, camera_id=2, role="gallery"),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/fastreid/prepare_dataset.py",
            "--train-manifest",
            str(train_manifest),
            "--test-manifest",
            str(test_manifest),
            "--output",
            str(workspace_tmp_path / "output"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 2
    assert "image must be a local staged JPEG" in completed.stderr

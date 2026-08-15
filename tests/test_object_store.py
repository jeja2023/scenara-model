from __future__ import annotations

from pathlib import Path

import pytest

from scenara_model.object_store import LocalObjectStore, S3ObjectStore


def test_local_object_store_rejects_escaping_keys(workspace_tmp_path: Path) -> None:
    source = workspace_tmp_path / "source.txt"
    source.write_text("ok", encoding="utf-8")
    store = LocalObjectStore(workspace_tmp_path / "store")

    with pytest.raises(ValueError):
        store.put_file(source, "../escape.txt")

class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str]] = []

    def upload_file(self, source: str, bucket: str, key: str, ExtraArgs: dict[str, str]) -> None:  # noqa: N803
        self.uploads.append((source, bucket, key))


def test_s3_object_store_uploads_with_prefix(workspace_tmp_path: Path) -> None:
    source = workspace_tmp_path / "source.bin"
    source.write_bytes(b"ok")
    client = FakeS3Client()
    store = S3ObjectStore("s3://bucket/prefix", client=client)

    stored = store.put_file(source, "models/model.onnx")

    assert client.uploads == [(str(source), "bucket", "prefix/models/model.onnx")]
    assert stored.uri == "s3://bucket/prefix/models/model.onnx"
    with pytest.raises(ValueError):
        store.put_file(source, "../escape.onnx")

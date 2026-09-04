from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class StoredObject:
    backend: str
    uri: str
    path: Path | None
    size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "uri": self.uri,
            "path": str(self.path) if self.path else None,
            "size": self.size,
        }


class ObjectStore(Protocol):
    def put_file(self, source: str | Path, key: str) -> StoredObject:
        ...


class S3Client(Protocol):
    def upload_file(self, source: str, bucket: str, key: str, ExtraArgs: dict[str, str]) -> object:  # noqa: N803
        ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        ...

    def delete_object(self, *, Bucket: str, Key: str) -> object:  # noqa: N803
        ...


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _destination_for(self, key: str) -> Path:
        key_path = Path(key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise ValueError(f"Object key must stay within storage root: {key}")
        destination = (self.root / key_path).resolve()
        if destination != self.root and self.root not in destination.parents:
            raise ValueError(f"Object key escapes storage root: {key}")
        return destination

    def put_file(self, source: str | Path, key: str) -> StoredObject:
        source_path = Path(source)
        destination = self._destination_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        return StoredObject(
            backend="local",
            uri=str(destination),
            path=destination,
            size=destination.stat().st_size,
        )


class S3ObjectStore:
    def __init__(
        self,
        uri: str,
        *,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client: S3Client | None = None,
    ) -> None:
        parsed = urlparse(uri)
        if parsed.scheme not in {"s3", "minio"} or not parsed.netloc:
            raise ValueError("S3 object storage URI must look like s3://bucket/prefix or minio://bucket/prefix")
        self.backend = "minio" if parsed.scheme == "minio" else "s3"
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")
        if client is not None:
            self.client = client
        else:
            try:
                import boto3  # pyright: ignore[reportMissingImports]
            except ImportError as exc:  # pragma: no cover - depends on optional deployment extra
                raise RuntimeError("boto3 is required for s3/minio object storage; install scenara-model[s3].") from exc
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("SCENARA_MODEL_S3_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("SCENARA_MODEL_S3_SECRET_ACCESS_KEY"),
            )

    def _key_for(self, key: str) -> str:
        key_path = Path(key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise ValueError(f"Object key must stay within bucket prefix: {key}")
        normalized = "/".join(part for part in key_path.parts if part not in {"", "."})
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def put_file(self, source: str | Path, key: str) -> StoredObject:
        source_path = Path(source)
        object_key = self._key_for(key)
        extra_args = {"ContentType": "application/octet-stream"}
        self.client.upload_file(str(source_path), self.bucket, object_key, ExtraArgs=extra_args)
        uri_scheme = "minio" if self.backend == "minio" else "s3"
        return StoredObject(
            backend=self.backend,
            uri=f"{uri_scheme}://{self.bucket}/{object_key}",
            path=None,
            size=source_path.stat().st_size,
        )


def object_store_from_settings(backend: str, uri: str) -> ObjectStore:
    normalized = backend.lower()
    if normalized == "local":
        return LocalObjectStore(uri)
    if normalized in {"s3", "minio"}:
        endpoint = os.environ.get("SCENARA_MODEL_S3_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3")
        region = os.environ.get("SCENARA_MODEL_S3_REGION") or os.environ.get("AWS_REGION")
        return S3ObjectStore(uri, endpoint_url=endpoint, region_name=region)
    raise ValueError(f"Unsupported object storage backend: {backend}")

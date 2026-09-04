from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from scenara_model.object_store import S3ObjectStore
from scenara_model.storage import metadata_store_from_uri


def _s3_probe(*, backend: str, uri: str, evidence_dir: Path, keep: bool) -> dict[str, object]:
    if backend not in {"s3", "minio"}:
        raise ValueError("target qualification requires --storage-backend s3 or minio")
    store = S3ObjectStore(
        uri,
        endpoint_url=os.environ.get("SCENARA_MODEL_S3_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3"),
        region_name=os.environ.get("SCENARA_MODEL_S3_REGION") or os.environ.get("AWS_REGION"),
    )
    run_id = uuid4().hex
    content = json.dumps({"qualification": "scenara-model", "run_id": run_id}, sort_keys=True).encode("utf-8")
    source = evidence_dir / f"object-store-{run_id}.json"
    source.write_bytes(content)
    key = f"qualification/{run_id}/probe.json"
    stored = store.put_file(source, key)
    object_key = store._key_for(key)
    response = store.client.get_object(Bucket=store.bucket, Key=object_key)
    restored = response["Body"].read()
    if restored != content:
        raise RuntimeError("S3 read-after-write content mismatch")
    if not keep:
        store.client.delete_object(Bucket=store.bucket, Key=object_key)
    return {
        "backend": store.backend,
        "uri": stored.uri,
        "sha256": hashlib.sha256(content).hexdigest(),
        "read_after_write": True,
        "retained": keep,
    }


def _postgres_probe(dsn: str) -> dict[str, object]:
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("--metadata-db must be a PostgreSQL DSN for target qualification")
    store = metadata_store_from_uri(dsn)
    experiment_id = f"qualification_{uuid4().hex}"
    record = store.upsert_experiment(
        {
            "id": experiment_id,
            "task": "qualification",
            "dataset": "infrastructure",
            "model": "metadata-store",
            "status": "completed",
            "metrics": {"round_trip": 1.0},
        }
    )
    if record["id"] != experiment_id:
        raise RuntimeError("PostgreSQL metadata write/read round trip returned another record")
    return {"backend": "postgresql", "experiment_id": experiment_id, "write_read_round_trip": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Perform explicit PostgreSQL and S3/MinIO target-environment qualification probes.")
    parser.add_argument("--metadata-db", required=True, help="PostgreSQL target DSN")
    parser.add_argument("--storage-backend", choices=("s3", "minio"), required=True)
    parser.add_argument("--storage-uri", required=True, help="s3://bucket/prefix or minio://bucket/prefix")
    parser.add_argument("--evidence-dir", type=Path, default=Path("artifacts/qualification"))
    parser.add_argument("--keep-object-evidence", action="store_true", help="retain the probe object for an external audit")
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = {
            "ok": True,
            "postgres": _postgres_probe(args.metadata_db),
            "object_store": _s3_probe(
                backend=args.storage_backend,
                uri=args.storage_uri,
                evidence_dir=evidence_dir,
                keep=args.keep_object_evidence,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "error": str(exc)}
    report_path = evidence_dir / "target-environment-qualification.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

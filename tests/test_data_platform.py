from __future__ import annotations

import hashlib
import json

import httpx

from scenara_model.data_platform import DataPlatformClient, DataPlatformContext


def test_data_platform_client_fetches_and_verifies_immutable_dataset(tmp_path) -> None:
    manifest = {"schema_version": "1.0", "dataset_id": "behavior-training", "version": "1.0.0", "items": []}
    manifest_bytes = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/reference"):
            return httpx.Response(
                200,
                json={
                    "dataset_id": "behavior-training",
                    "version": "1.0.0",
                    "manifest_uri": f"https://data.example/manifest#sha256={digest}",
                    "manifest_sha256": digest,
                    "lineage_refs": ["https://data.example/lineage#sha256=" + "b" * 64],
                    "authorization_id": "grant-model-test",
                    "authorized_consumer_repository_ids": ["scenara-model"],
                    "created_at": "2026-09-05T00:00:00Z",
                    "domain": "behavior",
                    "annotation_schema_ids": ["scenara.behavior.action.v1"],
                },
            )
        return httpx.Response(200, json=manifest)

    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://data.example")
    client = DataPlatformClient(
        "https://ignored.example",
        service_token="data-service-token",
        context_signing_key="data-context-signing-key-that-is-long-enough",
        client=raw,
        max_retries=0,
    )
    reference, path = client.fetch_dataset_version(
        "dsv_behavior_1",
        context=DataPlatformContext(
            tenant_id="tenant-a",
            project_id="project-a",
            principal_id="model-user",
            principal_type="user",
            scopes=("data.dataset.read",),
            entitlements=("data",),
            request_id="req-model-data",
            trace_id="0123456789abcdef0123456789abcdef",
        ),
        workspace_root=tmp_path,
    )

    assert reference.manifest_sha256 == digest
    assert path.read_bytes() == manifest_bytes
    assert len(calls) == 2
    assert all(request.headers["x-scenara-context-signature"] for request in calls)
    raw.close()

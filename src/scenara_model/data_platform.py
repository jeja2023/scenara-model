"""Client for reading immutable Dataset Version inputs from scenara-data."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from scenara_model.dataset_versions import DatasetVersionReference


class DataPlatformError(RuntimeError):
    """A safe, normalized error returned by the Data service."""


@dataclass(frozen=True)
class DataPlatformContext:
    tenant_id: str
    project_id: str
    principal_id: str
    principal_type: str
    scopes: tuple[str, ...]
    entitlements: tuple[str, ...]
    request_id: str
    trace_id: str


class DataPlatformClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str,
        context_signing_key: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._context_signing_key = context_signing_key
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout_seconds)
        self._owns_client = client is None
        self._max_retries = max(0, min(5, max_retries))

    def fetch_dataset_version(
        self,
        dataset_version_id: str,
        *,
        context: DataPlatformContext,
        workspace_root: str | Path,
    ) -> tuple[DatasetVersionReference, Path]:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", dataset_version_id) is None:
            raise DataPlatformError("invalid Data dataset version identifier")
        reference_payload = self._request(
            "GET",
            f"/internal/v1/dataset-versions/{quote(dataset_version_id, safe='')}/reference",
            context=context,
        )
        reference = DatasetVersionReference.model_validate(reference_payload)
        manifest_payload = self._request(
            "GET",
            f"/internal/v1/dataset-versions/{quote(dataset_version_id, safe='')}/manifest",
            context=context,
        )
        if not isinstance(manifest_payload, dict):
            raise DataPlatformError("scenara-data returned an invalid dataset manifest")

        destination = Path(workspace_root) / "artifacts" / "remote-datasets" / f"{reference.dataset_id}-{reference.version}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        manifest_bytes = _canonical_json(manifest_payload)
        if hashlib.sha256(manifest_bytes).hexdigest() != reference.manifest_sha256:
            raise DataPlatformError("scenara-data dataset manifest digest does not match its reference")
        temporary.write_bytes(manifest_bytes)
        os.replace(temporary, destination)
        return reference, destination

    def _request(self, method: str, path: str, *, context: DataPlatformContext) -> object:
        headers = self._headers(method, path, context)
        try:
            for attempt in range(self._max_retries + 1):
                try:
                    response = self._client.request(method, path, headers=headers)
                except httpx.RequestError as exc:
                    if attempt >= self._max_retries:
                        raise DataPlatformError("scenara-data is unavailable") from exc
                    time.sleep(0.05 * (2**attempt))
                    continue
                if response.status_code >= 500 and attempt < self._max_retries:
                    time.sleep(0.05 * (2**attempt))
                    continue
                try:
                    payload: object = response.json()
                except ValueError as exc:
                    raise DataPlatformError("scenara-data returned invalid JSON") from exc
                if response.is_error:
                    detail = payload.get("error", {}) if isinstance(payload, dict) else {}
                    message = detail.get("message", "scenara-data request failed") if isinstance(detail, dict) else "scenara-data request failed"
                    raise DataPlatformError(str(message))
                if isinstance(payload, dict) and "data" in payload:
                    return payload["data"]
                return payload
        except DataPlatformError:
            raise
        except httpx.HTTPError as exc:
            raise DataPlatformError("scenara-data is unavailable") from exc
        raise DataPlatformError("scenara-data request failed")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _headers(self, method: str, path: str, context: DataPlatformContext) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._service_token}",
            "X-Scenara-Tenant-Id": context.tenant_id,
            "X-Scenara-Project-Id": context.project_id,
            "X-Scenara-Principal-Id": context.principal_id,
            "X-Scenara-Principal-Type": "service_account",
            "X-Scenara-Permission-Scopes": ",".join(sorted(set(context.scopes))),
            "X-Scenara-Product-Entitlements": ",".join(sorted(set(context.entitlements))),
            "X-Request-Id": context.request_id,
            "X-Trace-Id": context.trace_id,
        }
        timestamp = int(time.time())
        headers["X-Scenara-Context-Timestamp"] = str(timestamp)
        headers["X-Scenara-Context-Signature"] = sign_request_context(
            self._context_signing_key,
            method=method,
            path=path,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            principal_id=context.principal_id,
            principal_type="service_account",
            scopes=context.scopes,
            entitlements=context.entitlements,
            request_id=context.request_id,
            trace_id=context.trace_id,
            timestamp=timestamp,
        )
        return headers


def sign_request_context(
    signing_key: str,
    *,
    method: str,
    path: str,
    tenant_id: str,
    project_id: str,
    principal_id: str,
    principal_type: str,
    scopes: tuple[str, ...],
    entitlements: tuple[str, ...],
    request_id: str,
    trace_id: str,
    timestamp: int,
) -> str:
    payload = {
        "entitlements": sorted(set(entitlements)),
        "method": method.upper(),
        "path": path,
        "principal_id": principal_id,
        "principal_type": principal_type,
        "project_id": project_id,
        "request_id": request_id,
        "scopes": sorted(set(scopes)),
        "tenant_id": tenant_id,
        "timestamp": timestamp,
        "trace_id": trace_id,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(signing_key.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = ["DataPlatformClient", "DataPlatformContext", "DataPlatformError", "sign_request_context"]

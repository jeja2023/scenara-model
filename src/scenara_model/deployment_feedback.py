from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RFC3339_UTC = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
SHA256 = r"^[0-9a-f]{64}$"
MODEL_RELEASE_STATUSES = {"candidate", "validated", "approved", "active", "retired"}


class DeploymentFeedbackError(ValueError):
    """Base class for deployment-feedback transport and contract failures."""


class DeploymentFeedbackSignatureError(DeploymentFeedbackError):
    """The signed webhook transport headers are absent, stale, or invalid."""


class ModelDeploymentEvent(BaseModel):
    """The published `deployment-feedback` v1.2.0 payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    runtime_model_id: str = Field(min_length=1)
    package_sha256: str = Field(pattern=SHA256)
    action: str = Field(min_length=1)
    from_status: Literal["candidate", "validated", "approved", "active", "retired"] | None
    to_status: Literal["candidate", "validated", "approved", "active", "retired"]
    reason: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    audit_id: str = Field(min_length=1)
    created_at: str = Field(pattern=RFC3339_UTC)
    domain: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{1,63}$")

    def created_at_epoch_ms(self) -> int:
        return _utc_epoch_ms(self.created_at)


class DeploymentFeedbackEnvelope(BaseModel):
    """Core's signed event envelope around a ModelDeploymentEvent payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=1)
    event_type: Literal["model.deployment.changed"]
    event_version: Literal["1.0"]
    occurred_at: str = Field(pattern=RFC3339_UTC)
    producer: Literal["scenara"]
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    data: ModelDeploymentEvent

    @model_validator(mode="after")
    def envelope_matches_payload(self) -> DeploymentFeedbackEnvelope:
        pairs = {
            "event_id": (self.event_id, self.data.event_id),
            "tenant_id": (self.tenant_id, self.data.tenant_id),
            "project_id": (self.project_id, self.data.project_id),
        }
        mismatches = [name for name, (outer, inner) in pairs.items() if outer != inner]
        if mismatches:
            raise ValueError("event envelope does not match data for: " + ", ".join(mismatches))
        _utc_epoch_ms(self.occurred_at)
        self.data.created_at_epoch_ms()
        return self


@dataclass(frozen=True)
class VerifiedDeploymentFeedback:
    envelope: DeploymentFeedbackEnvelope
    body_sha256: str


def _utc_epoch_ms(value: str) -> int:
    if re.fullmatch(RFC3339_UTC, value) is None:
        raise ValueError("timestamp must be a UTC RFC3339 string ending in Z")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise ValueError("timestamp must be UTC")
    return int(parsed.timestamp() * 1000)


def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    """Match Core's v1 webhook signature exactly."""
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_deployment_feedback(
    *,
    body: bytes,
    event_id_header: str | None,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: str | None,
    max_age_seconds: int,
    now: float | None = None,
) -> VerifiedDeploymentFeedback:
    """Verify transport integrity before accepting an at-least-once Core event."""
    if not secret:
        raise DeploymentFeedbackSignatureError("deployment-feedback webhook secret is not configured")
    if not timestamp_header or not timestamp_header.isdecimal():
        raise DeploymentFeedbackSignatureError("Scenara-Timestamp must be a Unix timestamp")
    timestamp = int(timestamp_header)
    current = time.time() if now is None else now
    if abs(current - timestamp) > max_age_seconds:
        raise DeploymentFeedbackSignatureError("webhook timestamp is outside the permitted replay window")
    expected = sign_webhook(secret, timestamp, body)
    if not signature_header or not hmac.compare_digest(signature_header, expected):
        raise DeploymentFeedbackSignatureError("webhook signature is invalid")
    try:
        decoded: Any = json.loads(body)
        envelope = DeploymentFeedbackEnvelope.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise DeploymentFeedbackError(f"deployment-feedback payload is invalid: {exc}") from exc
    if event_id_header != envelope.event_id:
        raise DeploymentFeedbackError("Scenara-Event-Id does not match the event envelope")
    return VerifiedDeploymentFeedback(envelope=envelope, body_sha256=hashlib.sha256(body).hexdigest())


__all__ = [
    "DeploymentFeedbackEnvelope",
    "DeploymentFeedbackError",
    "DeploymentFeedbackSignatureError",
    "MODEL_RELEASE_STATUSES",
    "ModelDeploymentEvent",
    "VerifiedDeploymentFeedback",
    "sign_webhook",
    "verify_deployment_feedback",
]

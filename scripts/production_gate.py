"""Static safety gate for the independent scenara model production compose."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compose.production.yml"


def main() -> int:
    if not COMPOSE.is_file():
        print(f"missing production compose: {COMPOSE}")
        return 1
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = document.get("services", {}).get("model-api", {})
    migration = document.get("services", {}).get("model-migrate", {})
    environment = service.get("environment", {})
    problems: list[str] = []
    required_environment = {
        "SCENARA_MODEL_DEPLOYMENT_PROFILE": "production",
        "SCENARA_MODEL_AUTH_MODE": "core",
        "SCENARA_MODEL_METADATA_DB_FILE": "/run/secrets/model_database_url",
        "SCENARA_MODEL_SERVICE_TOKEN_FILE": "/run/secrets/model_service_token",
        "SCENARA_MODEL_CONTEXT_SIGNING_KEY_FILE": "/run/secrets/model_context_signing_key",
        "SCENARA_MODEL_DATA_PLATFORM_SERVICE_TOKEN_FILE": "/run/secrets/model_data_service_token",
        "SCENARA_MODEL_DATA_PLATFORM_CONTEXT_SIGNING_KEY_FILE": "/run/secrets/model_data_context_signing_key",
        "SCENARA_MODEL_DEPLOYMENT_FEEDBACK_SECRET_FILE": "/run/secrets/model_deployment_feedback_secret",
        "SCENARA_MODEL_SERVE_FRONTEND": "false",
    }
    for name, expected in required_environment.items():
        if environment.get(name) != expected:
            problems.append(f"production environment must set {name}={expected}")
    if "SCENARA_MODEL_IMAGE" not in str(service.get("image", "")):
        problems.append("production image must be supplied by SCENARA_MODEL_IMAGE")
    if "SCENARA_MODEL_IMAGE" not in str(migration.get("image", "")):
        problems.append("production migration image must be supplied by SCENARA_MODEL_IMAGE")
    if migration.get("command") != ["alembic", "upgrade", "head"]:
        problems.append("production model migration must run alembic upgrade head")
    if service.get("depends_on", {}).get("model-migrate", {}).get("condition") != "service_completed_successfully":
        problems.append("model API must depend on successful production migration")
    if "ports" in service:
        problems.append("production model service must not expose host ports")
    if service.get("read_only") is not True:
        problems.append("production model service must use read_only filesystem")
    if service.get("cap_drop") != ["ALL"]:
        problems.append("production model service must drop all capabilities")
    if service.get("security_opt") != ["no-new-privileges:true"]:
        problems.append("production model service must disable privilege escalation")
    secrets = set(service.get("secrets", []))
    declared_secrets = set(document.get("secrets", {}))
    required_secrets = {
        "model_database_url",
        "model_service_token",
        "model_context_signing_key",
        "model_data_service_token",
        "model_data_context_signing_key",
        "model_deployment_feedback_secret",
        "model_s3_access_key",
        "model_s3_secret_key",
    }
    if not required_secrets.issubset(secrets) or not required_secrets.issubset(declared_secrets):
        problems.append("production model service must mount all external secrets")
    if problems:
        print("\n".join(problems))
        return 1
    print("scenara model production compose gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scenara_model.settings import load_settings


def test_serve_frontend_defaults_to_false(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SCENARA_MODEL_SERVE_FRONTEND", raising=False)

    assert load_settings().serve_frontend is False


def test_serve_frontend_can_be_enabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SCENARA_MODEL_SERVE_FRONTEND", "true")

    assert load_settings().serve_frontend is True


def test_production_requires_core_authentication() -> None:
    settings = load_settings()
    with pytest.raises(ValueError, match="AUTH_MODE"):
        replace(settings, deployment_profile="production").validate()


def test_core_authentication_requires_distinct_data_credentials() -> None:
    settings = load_settings()
    core = replace(
        settings,
        deployment_profile="production",
        auth_mode="core",
        service_token="s" * 24,
        context_signing_key="c" * 32,
        data_platform_url="https://data.example",
        data_platform_service_token="d" * 32,
        data_platform_context_signing_key="d" * 32,
        deployment_feedback_secret="f" * 32,
        metadata_db="postgresql://model@db/model",
        storage_backend="s3",
    )
    with pytest.raises(ValueError, match="must differ"):
        core.validate()


def test_secret_values_can_be_loaded_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service_file = tmp_path / "service-token"
    service_file.write_text("service-token-from-file", encoding="utf-8")
    monkeypatch.delenv("SCENARA_MODEL_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("SCENARA_MODEL_SERVICE_TOKEN_FILE", str(service_file))
    assert load_settings().service_token == "service-token-from-file"

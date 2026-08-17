from __future__ import annotations

from scenara_model.settings import load_settings


def test_serve_frontend_defaults_to_false(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SCENARA_MODEL_SERVE_FRONTEND", raising=False)

    assert load_settings().serve_frontend is False


def test_serve_frontend_can_be_enabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SCENARA_MODEL_SERVE_FRONTEND", "true")

    assert load_settings().serve_frontend is True

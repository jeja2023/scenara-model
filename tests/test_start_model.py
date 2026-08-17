from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import start_model  # noqa: E402


def test_frontend_build_available_requires_index_and_assets(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SCENARA_MODEL_FRONTEND_DIST", "frontend/dist")

    assert start_model.frontend_build_available(tmp_path) is False

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("", encoding="utf-8")
    assert start_model.frontend_build_available(tmp_path) is False

    (dist / "assets").mkdir()
    assert start_model.frontend_build_available(tmp_path) is True


def test_start_api_does_not_print_root_entry_when_frontend_disabled(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []

    def fake_call(command: list[str], *, cwd: str) -> int:
        calls.append(command)
        assert cwd == str(tmp_path)
        return 0

    monkeypatch.setattr(start_model.subprocess, "call", fake_call)
    monkeypatch.setitem(os.environ, "SCENARA_MODEL_METADATA_DB", "artifacts/scenara_model.sqlite3")

    assert start_model.start_api(
        tmp_path,
        Path("python"),
        "127.0.0.1",
        8080,
        legacy_frontend_enabled=False,
        legacy_frontend_available=False,
    ) == 0

    output = capsys.readouterr().out
    assert "迁移期管理台：未启用" in output
    assert "http://127.0.0.1:8080/\n" not in output
    assert "http://127.0.0.1:8080/docs" in output
    assert calls


def test_start_api_prints_legacy_frontend_entry_when_available(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(start_model.subprocess, "call", lambda command, *, cwd: 0)
    monkeypatch.setitem(os.environ, "SCENARA_MODEL_METADATA_DB", "artifacts/scenara_model.sqlite3")

    assert start_model.start_api(
        tmp_path,
        Path("python"),
        "127.0.0.1",
        8080,
        legacy_frontend_enabled=True,
        legacy_frontend_available=True,
    ) == 0

    output = capsys.readouterr().out
    assert "迁移期管理台：http://127.0.0.1:8080/" in output

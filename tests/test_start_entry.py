from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_start_entry() -> ModuleType:
    spec = importlib.util.spec_from_file_location("start_entry_under_test", ROOT / "start.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_start_model(monkeypatch, calls: list[list[str]]) -> None:  # type: ignore[no-untyped-def]
    fake_module = ModuleType("start_model")

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    fake_module.main = fake_main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "start_model", fake_module)


def test_start_py_defaults_to_legacy_frontend(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    fake_start_model(monkeypatch, calls)

    assert load_start_entry().main(["--port", "8090"]) == 0

    assert calls == [["--host", "127.0.0.1", "--port", "8090", "--with-legacy-frontend"]]


def test_start_py_backend_only_does_not_enable_legacy_frontend(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    fake_start_model(monkeypatch, calls)

    assert load_start_entry().main(["--host", "0.0.0.0", "--port", "8090", "--skip-install", "--backend-only"]) == 0

    assert calls == [["--host", "0.0.0.0", "--port", "8090", "--skip-install"]]

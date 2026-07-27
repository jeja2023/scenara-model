from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def workspace_tmp_path() -> Iterator[Path]:
    root = Path("artifacts/test_runs")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"pytest_{uuid4().hex}"
    path.mkdir(parents=False, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

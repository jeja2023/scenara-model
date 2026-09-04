from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


def test_backup_tool_refuses_to_overwrite_an_existing_archive(workspace_tmp_path: Path) -> None:
    archive = workspace_tmp_path / "existing.dump"
    archive.write_bytes(b"existing backup")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/backup_postgres.py",
            "--dsn",
            "postgresql://example.invalid/scenara_model",
            "--output",
            str(archive),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 2
    assert archive.read_bytes() == b"existing backup"
    assert "refusing to overwrite backup" in completed.stderr


def test_restore_tool_requires_an_explicit_recovery_target_confirmation(workspace_tmp_path: Path) -> None:
    archive = workspace_tmp_path / "backup.dump"
    archive.write_bytes(b"archive")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/restore_postgres.py",
            "--dsn",
            "postgresql://example.invalid/recovery",
            "--archive",
            str(archive),
            "--sha256",
            hashlib.sha256(archive.read_bytes()).hexdigest(),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 2
    assert "confirm-recovery-target" in completed.stderr

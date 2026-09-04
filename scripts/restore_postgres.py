from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a verified PostgreSQL metadata backup into an explicitly approved empty recovery target.")
    parser.add_argument("--dsn", required=True, help="recovery-target PostgreSQL DSN, never the source production database")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="expected archive SHA-256")
    parser.add_argument("--pg-restore", default="pg_restore", help="pg_restore executable or absolute path")
    parser.add_argument("--confirm-recovery-target", action="store_true", help="required acknowledgement that the named target may be replaced")
    args = parser.parse_args(argv)
    archive = args.archive.resolve()
    if not args.confirm_recovery_target:
        parser.error("--confirm-recovery-target is required; never restore into an unverified target")
    if not archive.is_file():
        parser.error(f"archive was not found: {archive}")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != args.sha256.lower():
        parser.error("archive SHA-256 does not match; refusing restoration")
    completed = subprocess.run(
        [args.pg_restore, "--clean", "--if-exists", "--no-owner", "--no-acl", f"--dbname={args.dsn}", str(archive)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        parser.exit(2, "pg_restore failed; inspect the recovery target before retrying.\n")
    print("PostgreSQL metadata restore completed; run scripts/qualify_target_environment.py against the recovery target before promotion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

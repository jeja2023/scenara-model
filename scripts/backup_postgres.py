from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a verified PostgreSQL custom-format metadata backup.")
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN; pass it through a protected deployment secret.")
    parser.add_argument("--output", type=Path, required=True, help="new .dump file to create")
    parser.add_argument("--pg-dump", default="pg_dump", help="pg_dump executable or absolute path")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        parser.error(f"refusing to overwrite backup: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [args.pg_dump, "--format=custom", "--no-owner", "--no-acl", "--file", str(output), f"--dbname={args.dsn}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        parser.exit(2, "pg_dump failed; no backup was retained.\n")
    report = {
        "path": str(output),
        "size": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "format": "postgresql-custom",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

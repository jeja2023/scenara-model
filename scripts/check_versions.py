"""Verify that all published version declarations remain synchronized."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    init_text = (ROOT / "src" / "scenara_model" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if match is None:
        print("[version] unable to parse __version__", file=sys.stderr)
        return 2
    expected = match.group(1)
    expected_semver = expected.replace(".dev", "-dev.")
    problems: list[str] = []

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if found is None or found.group(1) != expected:
        problems.append(f"pyproject.toml = {found.group(1) if found else 'missing'}")

    package_json = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    if package_json.get("version") != expected_semver:
        problems.append(f"frontend/package.json = {package_json.get('version')}")

    lock_path = ROOT / "frontend" / "package-lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("version") != expected_semver:
            problems.append(f"frontend/package-lock.json = {lock.get('version')}")
        root_entry = lock.get("packages", {}).get("", {})
        if root_entry.get("version") != expected_semver:
            problems.append(f'frontend/package-lock.json packages[""] = {root_entry.get("version")}')

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    changelog_versions = re.findall(r"^## \[([^\]]+)\]", changelog, re.MULTILINE)
    latest_published = next((version for version in changelog_versions if version.lower() != "unreleased"), None)
    if latest_published != expected_semver:
        problems.append(f"CHANGELOG.md latest published = {latest_published or 'missing'}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if f"当前版本：`{expected_semver}`" not in readme:
        problems.append("README.md does not declare the expected current version")

    if problems:
        for problem in problems:
            print(f"[version mismatch] expected {expected}, found {problem}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "version": expected}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

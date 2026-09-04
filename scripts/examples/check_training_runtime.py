from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="????????????")
    parser.add_argument("--require-module", action="append", default=[])
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--min-cuda-devices", type=int, default=1)
    parser.add_argument("--require-python", help="required major.minor interpreter version, for example 3.11")
    parser.add_argument("--require-file-sha256", action="append", default=[], metavar="PATH=SHA256")
    parser.add_argument("--require-module-git-revision", action="append", default=[], metavar="MODULE=COMMIT")
    args = parser.parse_args()

    report: dict[str, object] = {"ok": True, "modules": {}, "cuda": {"required": args.require_cuda}}
    issues: list[str] = []
    modules = report["modules"]
    assert isinstance(modules, dict)
    for name in args.require_module:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            modules[name] = {"ok": False, "error": str(exc)}
            issues.append(f"Unable to import required module {name}: {exc}")
        else:
            modules[name] = {"ok": True, "version": str(getattr(module, "__version__", "unknown"))}

    if args.require_python:
        actual_python = f"{sys.version_info.major}.{sys.version_info.minor}"
        report["python"] = actual_python
        if actual_python != args.require_python:
            issues.append(f"Python {args.require_python} is required; detected {actual_python}")

    lockfiles: dict[str, object] = {}
    for value in args.require_file_sha256:
        if "=" not in value:
            issues.append("--require-file-sha256 must use PATH=SHA256")
            continue
        raw_path, expected = value.rsplit("=", 1)
        path = Path(raw_path)
        if not path.is_file():
            lockfiles[raw_path] = {"ok": False, "error": "file not found"}
            issues.append(f"Required runtime file was not found: {path}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        lockfiles[raw_path] = {"ok": actual == expected.lower(), "sha256": actual}
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or actual != expected.lower():
            issues.append(f"Required runtime file digest does not match: {path}")
    if args.require_file_sha256:
        report["files"] = lockfiles

    revisions: dict[str, object] = {}
    for value in args.require_module_git_revision:
        if "=" not in value:
            issues.append("--require-module-git-revision must use MODULE=COMMIT")
            continue
        module_name, expected = value.rsplit("=", 1)
        try:
            module = importlib.import_module(module_name)
            location = Path(str(getattr(module, "__file__", ""))).resolve()
            if not location.is_file():
                raise ValueError("module has no source file")
            completed = subprocess.run(
                ["git", "-C", str(location.parent), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            actual = completed.stdout.strip().lower() if completed.returncode == 0 else ""
        except (ImportError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            revisions[module_name] = {"ok": False, "error": str(exc)}
            issues.append(f"Unable to validate Git revision for module {module_name}: {exc}")
            continue
        revisions[module_name] = {"ok": actual == expected.lower(), "revision": actual or None}
        if not actual or actual != expected.lower():
            issues.append(f"Module {module_name} does not match required Git revision")
    if args.require_module_git_revision:
        report["module_revisions"] = revisions

    if args.require_cuda:
        try:
            import torch  # pyright: ignore[reportMissingImports]

            available = bool(torch.cuda.is_available())
            device_count = int(torch.cuda.device_count()) if available else 0
            report["cuda"] = {
                "required": True,
                "available": available,
                "device_count": device_count,
                "torch_cuda_version": torch.version.cuda,
            }
            if not available or device_count < args.min_cuda_devices:
                issues.append(f"CUDA requires at least {args.min_cuda_devices} device(s); detected {device_count}")
        except Exception as exc:  # noqa: BLE001
            report["cuda"] = {"required": True, "available": False, "error": str(exc)}
            issues.append(f"Unable to validate CUDA: {exc}")

    report["issues"] = issues
    report["ok"] = not issues
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())

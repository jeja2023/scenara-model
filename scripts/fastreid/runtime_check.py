from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "deploy" / "training" / "fastreid-environment.lock.json"
SOURCE_PATH = ROOT / "third_party" / "fast-reid"


def _git_revision(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git rev-parse failed")
    return completed.stdout.strip().lower()


def inspect_runtime(*, require_cuda: bool) -> dict[str, object]:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    import torch  # pyright: ignore[reportMissingImports]
    import torchvision  # pyright: ignore[reportMissingImports]

    import fastreid  # pyright: ignore[reportMissingImports]

    actual_revision = _git_revision(SOURCE_PATH)
    cuda_available = bool(torch.cuda.is_available())
    report = {
        "ok": True,
        "lock_path": str(LOCK_PATH),
        "lock_sha256": hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "torch": str(torch.__version__),
        "torchvision": str(torchvision.__version__),
        "torch_cuda": torch.version.cuda,
        "fastreid": str(getattr(fastreid, "__version__", "unknown")),
        "fastreid_revision": actual_revision,
        "cuda": {"required": require_cuda, "available": cuda_available, "device_count": int(torch.cuda.device_count()) if cuda_available else 0},
        "issues": [],
    }
    issues = report["issues"]
    assert isinstance(issues, list)
    if report["python"] != lock["python"]:
        issues.append(f"Python {lock['python']} is required; detected {report['python']}")
    if not str(torch.__version__).startswith(str(lock["pytorch"])):
        issues.append(f"PyTorch {lock['pytorch']} is required; detected {torch.__version__}")
    if not str(torchvision.__version__).startswith(str(lock["torchvision"])):
        issues.append(f"torchvision {lock['torchvision']} is required; detected {torchvision.__version__}")
    if torch.version.cuda != lock["cuda"]:
        issues.append(f"CUDA runtime {lock['cuda']} is required; detected {torch.version.cuda}")
    if actual_revision != lock["fastreid"]["revision"]:
        issues.append("FastReID source revision does not match the immutable environment lock")
    if require_cuda and not cuda_available:
        issues.append("CUDA is not available inside the FastReID runtime")
    report["ok"] = not issues
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the pinned FastReID Linux GPU runtime.")
    parser.add_argument("--no-cuda-check", action="store_true", help="only for image build-time import validation")
    args = parser.parse_args(argv)
    try:
        report = inspect_runtime(require_cuda=not args.no_cuda_check)
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "issues": [str(exc)]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

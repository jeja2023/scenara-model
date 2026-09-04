from __future__ import annotations

import argparse
import collections
import collections.abc
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "third_party" / "fast-reid"


def _git_revision(source: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "could not read FastReID Git revision")
    return completed.stdout.strip().lower()


def _install_python_compatibility_shims() -> None:
    """Keep an upstream Python 3.9-era import working for a non-production smoke run."""
    if not hasattr(collections, "Mapping"):
        collections.Mapping = collections.abc.Mapping  # type: ignore[attr-defined]


def run_smoke(source: Path) -> dict[str, Any]:
    if not source.is_dir():
        raise RuntimeError(f"FastReID source is not present: {source}")
    _install_python_compatibility_shims()
    sys.path.insert(0, str(source))
    import torch  # pyright: ignore[reportMissingImports]
    import torchvision  # pyright: ignore[reportMissingImports]

    from fastreid.config import get_cfg  # pyright: ignore[reportMissingImports]
    from fastreid.engine import DefaultTrainer  # pyright: ignore[reportMissingImports]

    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch cannot access CUDA on this Windows host")
    cfg = get_cfg()
    cfg.defrost()
    cfg.MODEL.DEVICE = "cuda"
    cfg.MODEL.BACKBONE.NAME = "build_resnet_backbone"
    cfg.MODEL.BACKBONE.DEPTH = "18x"
    cfg.MODEL.BACKBONE.FEAT_DIM = 512
    cfg.MODEL.BACKBONE.PRETRAIN = False
    cfg.MODEL.BACKBONE.WITH_IBN = False
    cfg.MODEL.HEADS.NUM_CLASSES = 2
    cfg.MODEL.HEADS.WITH_BNNECK = True
    cfg.freeze()
    model = DefaultTrainer.build_model(cfg)
    model.eval()
    images = torch.zeros((1, 3, 64, 32), dtype=torch.float32, device="cuda")
    with torch.inference_mode():
        embeddings = model({"images": images.clone()})
    torch.cuda.synchronize()
    if not embeddings.is_cuda or embeddings.shape != (1, 512):
        raise RuntimeError(f"unexpected FastReID output: device={embeddings.device}, shape={tuple(embeddings.shape)}")
    return {
        "ok": True,
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "torch": str(torch.__version__),
        "torchvision": str(torchvision.__version__),
        "torch_cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "fastreid_revision": _git_revision(source),
        "embedding_device": str(embeddings.device),
        "embedding_shape": list(embeddings.shape),
        "scope": "Windows smoke only; production training remains Linux/Ubuntu.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a non-production Windows GPU smoke test for the pinned FastReID source.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts" / "fastreid-windows-smoke.json")
    args = parser.parse_args(argv)
    try:
        report = run_smoke(args.source.resolve())
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "error": str(exc), "scope": "Windows smoke only; production training remains Linux/Ubuntu."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import importlib
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an external model-training runtime")
    parser.add_argument("--require-module", action="append", default=[])
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--min-cuda-devices", type=int, default=1)
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

    if args.require_cuda:
        try:
            import torch

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

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vision_model_lab.adapters.registry import run_stage
from vision_model_lab.export.onnx_checks import check_onnx_loadable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ONNX export entry point")
    parser.add_argument("--config", type=Path, default=Path("configs/export/onnx_export.yml"))
    parser.add_argument("--validate-only", type=Path, help="Validate an existing ONNX file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.validate_only:
        print(json.dumps(check_onnx_loadable(args.validate_only), ensure_ascii=False, indent=2))
        return 0
    result = run_stage("export", args.config)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "completed" else 3


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scenara_model.pipeline import run_experiment_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run train/export/evaluate pipeline")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path("shared-models"))
    args = parser.parse_args()
    result = run_experiment_pipeline(args.config, package=args.package, output_root=args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    status = result.get("status")
    if status == "completed":
        return 0
    # 取消（4）与失败（2）区分退出码，便于 CI 编排判断。
    return 4 if status == "cancelled" else 2


if __name__ == "__main__":
    sys.exit(main())

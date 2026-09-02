from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scenara_model.adapters.registry import run_stage


def main() -> int:
    parser = argparse.ArgumentParser(description="??????")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    result = run_stage("training", args.config)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "completed" else 3


if __name__ == "__main__":
    sys.exit(main())

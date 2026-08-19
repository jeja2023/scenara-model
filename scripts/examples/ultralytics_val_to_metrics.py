"""示例：运行 Ultralytics 验证并把指标写成平台可回读的 JSON 文件。

配合 configs/experiments/detection_ultralytics_external.yml 的
evaluation.produced_metrics 契约使用。部署环境需安装 ultralytics。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ultralytics val and emit a metrics JSON file")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data", default=None, help="Ultralytics data YAML（缺省时沿用模型训练配置）")
    args = parser.parse_args()

    try:
        from ultralytics import YOLO  # pyright: ignore[reportMissingImports]
    except ImportError:
        print("ultralytics is not installed in this environment", file=sys.stderr)
        return 2

    model = YOLO(args.weights)
    results = model.val(data=args.data) if args.data else model.val()
    metrics = {
        "map50": float(results.box.map50),
        "map50_95": float(results.box.map),
        "precision": float(results.box.mp),
        "recall": float(results.box.mr),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _load_cfg(args: argparse.Namespace) -> Any:
    os.environ["FASTREID_DATASETS"] = str(args.dataset_root.resolve())
    from fastreid.config import get_cfg  # pyright: ignore[reportMissingImports]

    cfg = get_cfg()
    cfg.merge_from_file(str(args.config_file))
    overrides = ["OUTPUT_DIR", str(args.output.resolve())]
    if args.weights:
        overrides.extend(["MODEL.WEIGHTS", str(args.weights.resolve())])
    cfg.merge_from_list(overrides)
    cfg.freeze()
    return cfg


def _normalized_metrics(results: Any) -> dict[str, float]:
    if not isinstance(results, dict) or not results:
        raise ValueError("FastReID did not return evaluation metrics")
    nested = next(iter(results.values()))
    if not isinstance(nested, dict):
        raise ValueError("FastReID returned an invalid evaluation result")
    mapping = {"map": "mAP", "rank1": "Rank-1", "rank5": "Rank-5", "rank10": "Rank-10"}
    normalized: dict[str, float] = {}
    for output_name, source_name in mapping.items():
        value = nested.get(source_name)
        if not isinstance(value, (int, float)):
            raise ValueError(f"FastReID evaluation did not return {source_name}")
        normalized[output_name] = float(value) / 100.0
    return normalized


def _worker(args: argparse.Namespace) -> dict[str, float] | None:
    from fastreid.engine import DefaultTrainer  # pyright: ignore[reportMissingImports]
    from fastreid.utils import comm  # pyright: ignore[reportMissingImports]
    from fastreid.utils.checkpoint import Checkpointer  # pyright: ignore[reportMissingImports]

    cfg = _load_cfg(args)
    if args.mode == "train":
        trainer = DefaultTrainer(cfg)
        trainer.resume_or_load(resume=args.resume)
        results = trainer.train()
        return _normalized_metrics(results) if results else None
    cfg = cfg.clone()
    cfg.defrost()
    cfg.MODEL.BACKBONE.PRETRAIN = False
    cfg.freeze()
    model = DefaultTrainer.build_model(cfg)
    Checkpointer(model).load(cfg.MODEL.WEIGHTS)
    metrics = _normalized_metrics(DefaultTrainer.test(cfg, model))
    if comm.is_main_process():
        args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run revision-pinned FastReID training or fixed-set CMC/mAP evaluation.")
    parser.add_argument("mode", choices=("train", "evaluate"))
    parser.add_argument("--config-file", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--metrics-output", type=Path)
    parser.add_argument("--num-gpus", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.num_gpus < 1:
        parser.error("--num-gpus must be positive")
    if args.mode == "evaluate" and args.metrics_output is None:
        parser.error("--metrics-output is required for fixed-set evaluation")
    if args.mode == "evaluate" and args.weights is None:
        parser.error("--weights is required for evaluation")
    from fastreid.engine import launch  # pyright: ignore[reportMissingImports]

    launch(_worker, args.num_gpus, num_machines=1, machine_rank=0, dist_url="auto", args=(args,))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

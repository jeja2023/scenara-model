from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a pinned FastReID checkpoint to ONNX embeddings.")
    parser.add_argument("--config-file", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--width", type=int, default=128)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args(argv)
    if not args.weights.is_file():
        parser.error(f"checkpoint was not found: {args.weights}")
    import torch  # pyright: ignore[reportMissingImports]

    from fastreid.config import get_cfg  # pyright: ignore[reportMissingImports]
    from fastreid.engine import DefaultTrainer  # pyright: ignore[reportMissingImports]
    from fastreid.utils.checkpoint import Checkpointer  # pyright: ignore[reportMissingImports]

    cfg = get_cfg()
    cfg.merge_from_file(str(args.config_file))
    cfg.defrost()
    cfg.MODEL.BACKBONE.PRETRAIN = False
    cfg.MODEL.WEIGHTS = str(args.weights)
    cfg.freeze()
    model = DefaultTrainer.build_model(cfg)
    Checkpointer(model).load(str(args.weights))
    model.eval()

    class EmbeddingExport(torch.nn.Module):
        def __init__(self, wrapped: torch.nn.Module) -> None:
            super().__init__()
            self.wrapped = wrapped

        def forward(self, images: torch.Tensor) -> torch.Tensor:
            # FastReID normalizes tensors in-place, so clone protects ONNX's
            # model input from mutation while preserving the framework path.
            return self.wrapped({"images": images.clone()})

    device = torch.device(cfg.MODEL.DEVICE)
    sample = torch.zeros((1, 3, args.height, args.width), dtype=torch.float32, device=device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        EmbeddingExport(model),
        sample,
        str(args.output),
        input_names=["input"],
        output_names=["embedding"],
        opset_version=args.opset,
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path


def check_onnx_loadable(path: str | Path) -> dict[str, object]:
    model_path = Path(path)
    import onnx
    import onnxruntime as ort

    model = onnx.load(str(model_path))
    onnx.checker.check_model(model)
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    return {
        "path": str(model_path),
        "inputs": [item.name for item in session.get_inputs()],
        "outputs": [item.name for item in session.get_outputs()],
        "providers": session.get_providers(),
    }


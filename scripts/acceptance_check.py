from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 600) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command),
            "returncode": None,
            "stdout": (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": f"Command timed out after {timeout} seconds",
            "ok": False,
        }
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline acceptance checks")
    parser.add_argument("--runtime-base-url", help="Also check a running API instance")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest when the caller already ran it (e.g. CI)")
    args = parser.parse_args()

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    checks = []
    if not args.skip_pytest:
        checks.append(run([sys.executable, "-m", "pytest"], env=env))
    checks.extend([
        run([sys.executable, "scripts/check_versions.py"]),
        run([sys.executable, "scripts/prepare_dataset.py", "data/manifests/example_train_v1.jsonl", "--json"]),
        run([sys.executable, "scripts/validate_contract.py", "models-fragment", "configs/export/models.fragment.template.yml", "--json"]),
        run([sys.executable, "scripts/validate_contract.py", "release-decision", "configs/export/release-decision.template.yml", "--json"]),
        run([sys.executable, "scripts/train.py", "--config", "configs/experiments/reference_identity.yml"]),
        run([sys.executable, "scripts/export_onnx.py", "--config", "configs/experiments/reference_identity.yml"]),
        run([sys.executable, "scripts/evaluate.py", "--config", "configs/experiments/reference_identity.yml"]),
        run([sys.executable, "scripts/run_pipeline.py", "--config", "configs/experiments/detection_yolo_baseline.yml", "--package"]),
        run([sys.executable, "scripts/validate_model_package.py", "shared-models", "--strict-hash", "--strict-onnx"]),
        run([sys.executable, "scripts/hash_artifact.py", "MODEL_RND_TRAINING_PLAN.md"]),
        run([sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); from scenara_model.api import app; print(app.title)"]),
    ])
    if args.runtime_base_url:
        checks.append(run([sys.executable, "scripts/runtime_check.py", "--base-url", args.runtime_base_url]))
    report = {"ok": all(bool(item["ok"]) for item in checks), "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())

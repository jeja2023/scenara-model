"""景枢模型平台一键启动入口。

用法（任意目录下均可）：
    python start.py [--host 127.0.0.1] [--port 8080] [--skip-install] [--with-legacy-frontend]

等价于 python scripts/start_model.py，参数原样透传。
"""
from __future__ import annotations

import sys
from pathlib import Path

MIN_PYTHON = (3, 11)


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上版本，当前为 {sys.version.split()[0]}。", flush=True)
        return 1

    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "scripts"))
    from start_model import main as start_model_main

    return start_model_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

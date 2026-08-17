"""景枢模型平台一键启动入口。

用法（任意目录下均可）：
    python start.py [--host 127.0.0.1] [--port 8080] [--skip-install] [--backend-only]

默认会构建并启用迁移期前端，以便本地一键启动前后端。
如仅需后端，可传 `--backend-only`。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="景枢模型平台一键启动入口。")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", type=int, default=8080, help="监听端口。")
    parser.add_argument("--skip-install", action="store_true", help="跳过 Python 依赖安装。")
    parser.add_argument("--backend-only", action="store_true", help="仅启动后端，不构建迁移期前端。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上版本，当前为 {sys.version.split()[0]}。", flush=True)
        return 1

    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "scripts"))
    from start_model import main as start_model_main

    start_args = ["--host", args.host, "--port", str(args.port)]
    if args.skip_install:
        start_args.append("--skip-install")
    if not args.backend_only:
        start_args.append("--with-legacy-frontend")
    return start_model_main(start_args)


if __name__ == "__main__":
    raise SystemExit(main())

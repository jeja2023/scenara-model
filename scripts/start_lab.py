from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


class ChineseArgumentParser(argparse.ArgumentParser):
    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法:")

    def format_help(self) -> str:
        return super().format_help().replace("usage:", "用法:").replace("options:", "选项:")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = ChineseArgumentParser(description="本地启动视觉模型实验室。", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出。")
    parser.add_argument("--host", "--hostname", "-HostName", dest="host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", "-Port", dest="port", type=int, default=8080, help="监听端口。")
    parser.add_argument("--skip-install", "-SkipInstall", dest="skip_install", action="store_true", help="跳过 Python 依赖安装。")
    parser.add_argument(
        "--skip-frontend-build",
        "-SkipFrontendBuild",
        dest="skip_frontend_build",
        action="store_true",
        help="跳过前端依赖安装与构建。",
    )
    return parser.parse_args(argv)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        # 不覆盖用户在 shell 中显式导出的变量（与 python-dotenv 默认行为一致）。
        if name and name not in os.environ:
            os.environ[name] = value


def ensure_env_file(root: Path) -> None:
    """.env 不入库；首次启动时自动从 .env.example 复制一份本地配置。"""
    env_file = root / ".env"
    example = root / ".env.example"
    if not env_file.exists() and example.exists():
        env_file.write_text(example.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print("[准备] 已从 .env.example 生成本地 .env（该文件不会提交到 git）。", flush=True)


def venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def run(command: Sequence[str | Path], *, cwd: Path, quiet: bool = False) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"[执行] {printable}", flush=True)
    if quiet:
        # 静默模式：不实时输出子进程日志，仅在失败时完整打印以便排查。
        result = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        if result.returncode != 0:
            print(result.stdout, flush=True)
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return
    subprocess.run([str(part) for part in command], cwd=str(cwd), check=True)


def command_for_executable(executable: str, *args: str) -> list[str]:
    suffix = Path(executable).suffix.lower()
    if os.name == "nt" and suffix in {".bat", ".cmd"}:
        return ["cmd", "/c", executable, *args]
    return [executable, *args]


def ensure_virtualenv(root: Path) -> Path:
    python_path = venv_python(root)
    if python_path.exists():
        return python_path

    print("[准备] 正在创建 .venv...", flush=True)
    run([sys.executable, "-m", "venv", ".venv"], cwd=root)
    if not python_path.exists():
        raise RuntimeError(f"虚拟环境 Python 未创建成功：{python_path}")
    return python_path


def ensure_frontend(root: Path, skip_build: bool) -> None:
    if skip_build:
        return

    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        print("[提示] 未找到 npm，跳过前端构建；如已存在 frontend/dist，将继续使用现有构建产物。")
        return

    frontend = root / "frontend"
    if not frontend.exists():
        print("[提示] 未找到 frontend 目录，跳过前端构建。")
        return

    if not (frontend / "node_modules").exists():
        print("[准备] 正在安装前端依赖（详细日志已隐藏，失败时才显示）...", flush=True)
        run(command_for_executable(npm, "ci"), cwd=frontend, quiet=True)

    print("[准备] 正在构建前端...", flush=True)
    run(command_for_executable(npm, "run", "build"), cwd=frontend)


def configure_environment(root: Path) -> None:
    ensure_env_file(root)
    load_dotenv(root / ".env")

    if not os.environ.get("VMLAB_WORKSPACE") or os.environ["VMLAB_WORKSPACE"] == ".":
        os.environ["VMLAB_WORKSPACE"] = str(root)
    os.environ.setdefault("VMLAB_METADATA_DB", "artifacts/vision_model_lab.sqlite3")

    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "artifacts" / "object-store").mkdir(parents=True, exist_ok=True)


def start_api(root: Path, python_path: Path, host: str, port: int) -> int:
    print("", flush=True)
    print("视觉模型实验室正在启动...", flush=True)
    print(f"管理台：    http://{host}:{port}/", flush=True)
    print(f"接口文档：  http://{host}:{port}/docs", flush=True)
    print(f"健康检查：  http://{host}:{port}/health", flush=True)
    print("", flush=True)

    command = [
        str(python_path),
        "scripts/serve_api.py",
        "--host",
        host,
        "--port",
        str(port),
        "--metadata-db",
        os.environ["VMLAB_METADATA_DB"],
    ]
    try:
        return subprocess.call(command, cwd=str(root))
    except KeyboardInterrupt:
        return 130


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)

    configure_environment(root)
    python_path = ensure_virtualenv(root)

    if not args.skip_install:
        print("[准备] 正在安装 Python 依赖（详细日志已隐藏，失败时才显示）...", flush=True)
        run([python_path, "-m", "pip", "install", "-q", "-e", ".[dev]"], cwd=root, quiet=True)

    ensure_frontend(root, args.skip_frontend_build)

    print("[准备] 正在初始化元数据存储...", flush=True)
    run(
        [
            python_path,
            "-m",
            "vision_model_lab.cli",
            "storage",
            "migrate",
            "--uri",
            os.environ["VMLAB_METADATA_DB"],
        ],
        cwd=root,
    )

    return start_api(root, python_path, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())

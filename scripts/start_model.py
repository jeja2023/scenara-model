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
    parser = ChineseArgumentParser(description="本地启动景枢模型平台。", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出。")
    parser.add_argument("--host", "--hostname", "-HostName", dest="host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", "-Port", dest="port", type=int, default=8080, help="监听端口。")
    parser.add_argument("--skip-install", "-SkipInstall", dest="skip_install", action="store_true", help="跳过 Python 依赖安装。")
    parser.add_argument(
        "--with-legacy-frontend",
        dest="with_legacy_frontend",
        action="store_true",
        help="构建并启用迁移期独立前端；常规开发应通过 scenara 统一 Console 接入。",
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


def bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


def ensure_frontend(root: Path, enabled: bool) -> None:
    if not enabled:
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


def frontend_dist(root: Path) -> Path:
    configured = Path(os.environ.get("SCENARA_MODEL_FRONTEND_DIST", "frontend/dist"))
    if configured.is_absolute():
        return configured
    return root / configured


def frontend_build_available(root: Path) -> bool:
    dist = frontend_dist(root)
    return (dist / "index.html").is_file() and (dist / "assets").is_dir()


def configure_environment(root: Path) -> None:
    ensure_env_file(root)
    load_dotenv(root / ".env")

    if not os.environ.get("SCENARA_MODEL_WORKSPACE") or os.environ["SCENARA_MODEL_WORKSPACE"] == ".":
        os.environ["SCENARA_MODEL_WORKSPACE"] = str(root)
    os.environ.setdefault("SCENARA_MODEL_METADATA_DB", "artifacts/scenara_model.sqlite3")

    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "artifacts" / "object-store").mkdir(parents=True, exist_ok=True)


def start_api(root: Path, python_path: Path, host: str, port: int, *, legacy_frontend_enabled: bool, legacy_frontend_available: bool) -> int:
    print("", flush=True)
    print("景枢模型平台正在启动...", flush=True)
    if legacy_frontend_enabled and legacy_frontend_available:
        print(f"迁移期管理台：http://{host}:{port}/", flush=True)
    elif legacy_frontend_enabled:
        print(f"迁移期管理台：未找到构建产物（{frontend_dist(root)}），根路径暂不可用。", flush=True)
    else:
        print("迁移期管理台：未启用（使用 --with-legacy-frontend 构建并托管）。", flush=True)
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
        os.environ["SCENARA_MODEL_METADATA_DB"],
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

    if args.with_legacy_frontend:
        os.environ["SCENARA_MODEL_SERVE_FRONTEND"] = "true"
    ensure_frontend(root, args.with_legacy_frontend)
    legacy_frontend_enabled = bool_env("SCENARA_MODEL_SERVE_FRONTEND", False)
    legacy_frontend_available = frontend_build_available(root)
    if legacy_frontend_enabled and not legacy_frontend_available:
        os.environ["SCENARA_MODEL_SERVE_FRONTEND"] = "false"

    print("[准备] 正在初始化元数据存储...", flush=True)
    run(
        [
            python_path,
            "-m",
            "scenara_model.cli",
            "storage",
            "migrate",
            "--uri",
            os.environ["SCENARA_MODEL_METADATA_DB"],
        ],
        cwd=root,
    )

    return start_api(
        root,
        python_path,
        args.host,
        args.port,
        legacy_frontend_enabled=legacy_frontend_enabled,
        legacy_frontend_available=legacy_frontend_available,
    )


if __name__ == "__main__":
    raise SystemExit(main())

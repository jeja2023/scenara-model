from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vision_model_lab.adapters.base import AdapterResult
from vision_model_lab.export.onnx_checks import check_onnx_loadable
from vision_model_lab.utils import ensure_dir, read_yaml, write_json


LogLineSink = Callable[[str, str], None]
"""外部命令逐行日志回调：(stream, line)。"""


def _workspace_root() -> Path:
    return Path(os.environ.get("VMLAB_WORKSPACE", Path.cwd())).resolve()


def _experiment_dir(config: dict[str, Any], root: str | Path = "experiments/local_runs") -> Path:
    experiment = config.get("experiment", {})
    experiment_id = experiment.get("id") or f"{experiment.get('task', 'task')}_local"
    # 产物目录统一锚定 workspace，避免服务进程 CWD 与 workspace 不一致时产物"失联"。
    return ensure_dir(_workspace_root() / root / str(experiment_id))


def _artifact_name(config: dict[str, Any], task: str, architecture: str) -> str:
    export_config = config.get("export", {})
    if export_config.get("artifact_name"):
        return str(export_config["artifact_name"])
    version = str(config.get("model", {}).get("version") or config.get("dataset", {}).get("version") or "1.0.0")
    precision = str(export_config.get("precision", "fp32")).lower()
    return f"{task}_{architecture}_v{version}_{precision}.onnx"


def _write_identity_like_onnx(path: Path, *, input_shape: list[int], output_shape: list[int], graph_name: str) -> None:
    import onnx
    from onnx import TensorProto, helper

    if input_shape == output_shape:
        node = helper.make_node("Identity", ["input"], ["output"])
    else:
        total = 1
        for dimension in output_shape:
            total *= int(dimension)
        tensor = helper.make_tensor("constant_output", TensorProto.FLOAT, output_shape, [0.0] * total)
        node = helper.make_node("Constant", [], ["output"], value=tensor)
    graph = helper.make_graph(
        [node],
        graph_name,
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, input_shape)],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, output_shape)],
    )
    model = helper.make_model(graph, producer_name="vision-model-lab-local-task", opset_imports=[helper.make_operatorsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, path)


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(parsed, minimum)


def _truncate_log(value: str | None, limit: int) -> str:
    """截断日志时保留头部与尾部——失败排障最需要的 Traceback 在尾部。"""
    if value is None:
        return ""
    value = value.strip()
    if len(value) <= limit:
        return value
    head = max(limit // 5, 1)
    tail = limit - head
    return value[:head] + "\n...[中间日志已截断]...\n" + value[-tail:]


def _resolve_command_cwd(cwd: str | Path) -> Path:
    root = _workspace_root()
    candidate = Path(cwd)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"External command cwd escapes workspace: {cwd}")
    return resolved


# 训练脚本确实需要的少数平台变量：workspace 用于定位数据与产物目录。
_ENV_ALLOWLIST = {"VMLAB_WORKSPACE"}
# 平台配置与凭证一律不进入子进程。VMLAB_METADATA_DB 会携带 PostgreSQL DSN 中的
# 明文口令，VMLAB_ADMIN_PASSWORD 是控制台管理员口令。
_ENV_BLOCKED_PREFIXES = ("VMLAB_", "AWS_", "POSTGRES_", "MINIO_", "PG")
_ENV_BLOCKED_KEYWORDS = ("PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "ACCESS_KEY", "PRIVATE_KEY", "DSN")


def _env_passthrough() -> set[str]:
    """部署方显式放行的变量名（逗号分隔），用于训练确需的第三方凭证。"""
    raw = os.environ.get("VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH", "")
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _is_sensitive_env(name: str, passthrough: set[str]) -> bool:
    upper = name.upper()
    if upper in _ENV_ALLOWLIST or upper in passthrough:
        return False
    if upper.startswith(_ENV_BLOCKED_PREFIXES):
        return True
    return any(keyword in upper for keyword in _ENV_BLOCKED_KEYWORDS)


def _command_env() -> dict[str, str]:
    """外部命令环境：剥离平台配置与全部凭证类变量。

    按前缀 + 关键字匹配而非精确列举——精确列举会在新增配置项时静默失效，
    VMLAB_METADATA_DB（含 PG 口令）与 VMLAB_ADMIN_PASSWORD 就是这样漏掉的。
    训练确需的第三方凭证（如 HF_TOKEN）请通过
    VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH 显式放行。
    """
    passthrough = _env_passthrough()
    return {key: value for key, value in os.environ.items() if not _is_sensitive_env(key, passthrough)}


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """终止整棵进程树：真实训练命令普遍多进程（DataLoader worker、torchrun 等）。"""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            # Windows 下 taskkill /T 递归终止整棵子进程树。
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            # POSIX 下配合 start_new_session=True 对整个进程组发信号。
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class _StreamCollector:
    """后台线程逐行消费子进程输出，避免 PIPE 缓冲区写满导致的管道死锁。"""

    def __init__(self, stream: Any, name: str, limit: int, on_line: LogLineSink | None) -> None:
        self.name = name
        self._limit = limit
        self._on_line = on_line
        self._chunks: list[str] = []
        self._size = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                with self._lock:
                    # 内存中最多保留 4 倍截断上限，避免超长输出撑爆内存。
                    if self._size < self._limit * 4:
                        self._chunks.append(line)
                        self._size += len(line)
                if self._on_line is not None:
                    stripped = line.rstrip("\r\n")
                    if stripped:
                        try:
                            self._on_line(self.name, stripped)
                        except Exception:
                            pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout)

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)


def _cancelled_result(report_path: Path, *, adapter: str, stage: str, config_path: str | Path, message: str) -> AdapterResult:
    write_json(
        report_path,
        {
            "status": "cancelled",
            "adapter": adapter,
            "config": str(config_path),
            "stage": stage,
            "message": message,
        },
    )
    return AdapterResult("cancelled", report_path, {"report": str(report_path), "message": message})


def _run_external_command(
    command: str | list[str],
    *,
    cwd: str | Path = ".",
    stage: str,
    should_cancel: Callable[[], bool] | None = None,
    log_sink: LogLineSink | None = None,
) -> dict[str, Any]:
    timeout = _int_env("VMLAB_EXTERNAL_COMMAND_TIMEOUT_SECONDS", 3600)
    log_limit = _int_env("VMLAB_EXTERNAL_COMMAND_LOG_MAX_CHARS", 20000)
    allow_shell = _bool_env("VMLAB_ALLOW_SHELL_COMMANDS", False)
    try:
        resolved_cwd = _resolve_command_cwd(cwd)
    except ValueError as exc:
        return {"command": str(command), "returncode": None, "stdout": "", "stderr": str(exc), "ok": False, "error_code": "external.cwd_escape"}

    if isinstance(command, str):
        if not allow_shell:
            return {
                "command": command,
                "returncode": None,
                "stdout": "",
                "stderr": "String shell commands are disabled; use an argv list or set VMLAB_ALLOW_SHELL_COMMANDS=true.",
                "ok": False,
                "error_code": "external.shell_disabled",
            }
        run_command: str | list[str] = command
        rendered = command
        shell = True
    elif isinstance(command, list) and command:
        run_command = [str(item) for item in command]
        rendered = " ".join(run_command)
        shell = False
    else:
        return {
            "command": str(command),
            "returncode": None,
            "stdout": "",
            "stderr": "Command must be a non-empty string or argv list",
            "ok": False,
            "error_code": "external.invalid_command",
        }

    if should_cancel and should_cancel():
        return {
            "command": rendered,
            "returncode": None,
            "stdout": "",
            "stderr": f"Command cancelled before starting {stage}",
            "ok": False,
            "cancelled": True,
            "error_code": "external.cancelled",
        }

    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        # 独立会话使整个进程组可以被一起终止。
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        process = subprocess.Popen(
            run_command,
            cwd=resolved_cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_command_env(),
            shell=shell,
            **popen_kwargs,
        )
    except OSError as exc:
        return {"command": rendered, "returncode": None, "stdout": "", "stderr": str(exc), "ok": False, "error_code": "external.spawn_failed"}

    stdout_collector = _StreamCollector(process.stdout, "stdout", log_limit, log_sink)
    stderr_collector = _StreamCollector(process.stderr, "stderr", log_limit, log_sink)

    def _finalize(*, returncode: int | None, ok: bool, cancelled: bool = False, error_code: str | None = None, fallback_stderr: str = "") -> dict[str, Any]:
        stdout_collector.join()
        stderr_collector.join()
        result: dict[str, Any] = {
            "command": rendered,
            "returncode": returncode,
            "stdout": _truncate_log(stdout_collector.text(), log_limit),
            "stderr": _truncate_log(stderr_collector.text(), log_limit) or fallback_stderr,
            "ok": ok,
        }
        if cancelled:
            result["cancelled"] = True
        if error_code:
            result["error_code"] = error_code
        return result

    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            return _finalize(returncode=process.returncode, ok=process.returncode == 0)
        if should_cancel and should_cancel():
            _terminate_process_tree(process)
            return _finalize(
                returncode=None,
                ok=False,
                cancelled=True,
                error_code="external.cancelled",
                fallback_stderr=f"Command cancelled during {stage}",
            )
        if time.monotonic() >= deadline:
            _terminate_process_tree(process)
            return _finalize(
                returncode=None,
                ok=False,
                error_code="external.timeout",
                fallback_stderr=f"Command timed out after {timeout} seconds",
            )
        time.sleep(0.1)


def _command_cwd(stage_config: Any) -> str | Path:
    if isinstance(stage_config, dict):
        return stage_config.get("command_cwd", ".")
    return "."


def _resolve_workspace_file(path: str | Path) -> Path:
    root = _workspace_root()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def _read_produced_metrics(evaluation_config: dict[str, Any]) -> tuple[dict[str, float] | None, str | None]:
    """回读外部评估命令产出的真实指标文件；返回 (metrics, error)。"""
    produced = evaluation_config.get("produced_metrics") or evaluation_config.get("metrics_file")
    if not produced:
        return None, None
    try:
        metrics_path = _resolve_workspace_file(str(produced))
    except ValueError as exc:
        return None, str(exc)
    if not metrics_path.exists():
        return None, f"produced_metrics file not found: {metrics_path}"
    try:
        with metrics_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"produced_metrics is not valid JSON: {exc}"
    if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
        data = data["metrics"]
    if not isinstance(data, dict):
        return None, "produced_metrics JSON root must be an object"
    metrics: dict[str, float] = {}
    for key, value in data.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[str(key)] = float(value)
    if not metrics:
        return None, "produced_metrics contained no numeric metrics"
    return metrics, None


@dataclass(frozen=True)
class LocalTaskAdapter:
    name: str
    task: str
    description: str
    output_format: str
    default_metrics: dict[str, float]
    default_labels: list[str]
    output_shape: list[int]

    def train(
        self,
        config_path: str | Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        config = read_yaml(config_path)
        output_dir = _experiment_dir(config)
        report_path = output_dir / "train.report.json"
        if should_cancel and should_cancel():
            return _cancelled_result(report_path, adapter=self.name, stage="training", config_path=config_path, message="Training cancelled before execution.")
        training = config.get("training", {})
        status = "completed"
        message = "Local baseline recorded a reproducible run; replace training.command for framework training."
        external_result: dict[str, Any] | None = None
        if external_command := training.get("command") if isinstance(training, dict) else None:
            external_result = _run_external_command(
                external_command,
                cwd=_command_cwd(training),
                stage="training",
                should_cancel=should_cancel,
                log_sink=log_sink,
            )
            if external_result.get("cancelled"):
                status = "cancelled"
                message = "External training command was cancelled."
            elif external_result["ok"]:
                message = "External training command executed."
            else:
                status = "failed"
                message = "External training command failed."
        if should_cancel and should_cancel() and status != "cancelled":
            return _cancelled_result(report_path, adapter=self.name, stage="training", config_path=config_path, message="Training cancelled before report finalization.")
        report = {
            "status": status,
            "adapter": self.name,
            "task": self.task,
            "config": str(config_path),
            "dataset": config.get("dataset", {}),
            "model": config.get("model", {}),
            "training": training,
            "message": message,
        }
        if external_result:
            report["external"] = external_result
        write_json(report_path, report)
        payload: dict[str, Any] = {"report": str(report_path), "message": message}
        if external_result:
            payload["external"] = external_result
        return AdapterResult(status=status, path=report_path, payload=payload)

    def export(
        self,
        config_path: str | Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        config = read_yaml(config_path)
        architecture = str(config.get("model", {}).get("architecture", self.task))
        output_dir = ensure_dir(_experiment_dir(config) / "export")
        artifact_name = _artifact_name(config, self.task, architecture)
        output_path = output_dir / artifact_name
        report_path = output_dir / "export.report.json"
        if should_cancel and should_cancel():
            return _cancelled_result(report_path, adapter=self.name, stage="export", config_path=config_path, message="Export cancelled before execution.")

        export_config = config.get("export", {}) if isinstance(config.get("export"), dict) else {}
        external_command = export_config.get("command")
        reuse_existing = bool(export_config.get("reuse_existing", False))
        external_result: dict[str, Any] | None = None

        # 只有显式声明 reuse_existing 时才复用已有 ONNX，避免改配置重跑后静默交付旧模型。
        if reuse_existing and not external_command and output_path.exists():
            try:
                check = check_onnx_loadable(output_path)
            except Exception as exc:
                report = {
                    "status": "failed",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(output_path),
                    "message": f"Existing ONNX artifact is not loadable and reuse_existing is set: {exc}",
                }
                write_json(report_path, report)
                return AdapterResult("failed", report_path, {"report": str(report_path)})
            if should_cancel and should_cancel():
                return _cancelled_result(report_path, adapter=self.name, stage="export", config_path=config_path, message="Export cancelled before report finalization.")
            report = {
                "status": "completed",
                "adapter": self.name,
                "task": self.task,
                "onnx": str(output_path),
                "check": check,
                "output_format": self.output_format,
                "labels": config.get("labels", self.default_labels),
                "onnx_source": "reused",
                "message": "Reused existing loadable ONNX artifact (export.reuse_existing=true).",
            }
            write_json(report_path, report)
            return AdapterResult(status="completed", path=output_path, payload={"onnx": str(output_path), "report": str(report_path)})

        if external_command:
            external_result = _run_external_command(
                external_command,
                cwd=_command_cwd(export_config),
                stage="export",
                should_cancel=should_cancel,
                log_sink=log_sink,
            )
            if external_result.get("cancelled"):
                report = {
                    "status": "cancelled",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(output_path),
                    "external": external_result,
                    "message": "External export command was cancelled.",
                }
                write_json(report_path, report)
                return AdapterResult("cancelled", report_path, {"report": str(report_path), "external": external_result, "message": "External export command was cancelled."})
            if not external_result["ok"]:
                report = {
                    "status": "failed",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(output_path),
                    "external": external_result,
                    "message": "External export command failed.",
                }
                write_json(report_path, report)
                return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})
            produced = export_config.get("produced_onnx")
            if produced:
                try:
                    produced_path = _resolve_workspace_file(str(produced))
                except ValueError as exc:
                    report = {"status": "failed", "adapter": self.name, "task": self.task, "message": str(exc), "external": external_result}
                    write_json(report_path, report)
                    return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})
                if not produced_path.exists():
                    report = {
                        "status": "failed",
                        "adapter": self.name,
                        "task": self.task,
                        "onnx": str(output_path),
                        "message": f"export.command completed but produced_onnx was not found: {produced_path}",
                        "external": external_result,
                    }
                    write_json(report_path, report)
                    return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})
                if produced_path != output_path:
                    output_path.write_bytes(produced_path.read_bytes())
            elif not output_path.exists():
                report = {
                    "status": "failed",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(output_path),
                    "message": "export.command completed but no produced_onnx or target ONNX was found.",
                    "external": external_result,
                }
                write_json(report_path, report)
                return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})

            # 关键修复：外部命令产出的真实 ONNX 直接校验并返回，绝不落入合成桩模型覆盖。
            try:
                check = check_onnx_loadable(output_path)
            except Exception as exc:
                report = {
                    "status": "failed",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(output_path),
                    "external": external_result,
                    "message": f"External export produced an ONNX file that failed validation: {exc}",
                }
                write_json(report_path, report)
                return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})
            if should_cancel and should_cancel():
                return _cancelled_result(report_path, adapter=self.name, stage="export", config_path=config_path, message="Export cancelled before report finalization.")
            report = {
                "status": "completed",
                "adapter": self.name,
                "task": self.task,
                "onnx": str(output_path),
                "check": check,
                "output_format": self.output_format,
                "labels": config.get("labels", self.default_labels),
                "onnx_source": "external_command",
                "external": external_result,
            }
            write_json(report_path, report)
            return AdapterResult(
                status="completed",
                path=output_path,
                payload={"onnx": str(output_path), "report": str(report_path), "external": external_result},
            )

        # 无外部命令：生成合成基线模型（仅作为 baseline 兜底，报告中明确标注来源）。
        model_input = config.get("model", {}).get("input_size") or export_config.get("input", {}).get("shape")
        input_shape = [1, 1] if self.task == "reid" else [1, 3, 640, 640]
        if isinstance(model_input, list) and len(model_input) == 2:
            input_shape = [1, 3, int(model_input[0]), int(model_input[1])]
        elif isinstance(model_input, list) and len(model_input) == 4:
            input_shape = [int(value) for value in model_input]
        _write_identity_like_onnx(
            output_path,
            input_shape=input_shape,
            output_shape=input_shape if self.output_format == "identity" else self.output_shape,
            graph_name=f"{self.name}_graph",
        )
        if should_cancel and should_cancel():
            return _cancelled_result(report_path, adapter=self.name, stage="export", config_path=config_path, message="Export cancelled before report finalization.")
        check = check_onnx_loadable(output_path)
        report = {
            "status": "completed",
            "adapter": self.name,
            "task": self.task,
            "onnx": str(output_path),
            "check": check,
            "output_format": self.output_format,
            "labels": config.get("labels", self.default_labels),
            "onnx_source": "synthetic_baseline",
        }
        write_json(report_path, report)
        return AdapterResult(status="completed", path=output_path, payload={"onnx": str(output_path), "report": str(report_path)})

    def evaluate(
        self,
        config_path: str | Path,
        onnx_path: str | Path | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        config = read_yaml(config_path)
        output_dir = ensure_dir(_experiment_dir(config) / "eval")
        report_path = output_dir / "eval.report.json"
        if should_cancel and should_cancel():
            return _cancelled_result(report_path, adapter=self.name, stage="evaluation", config_path=config_path, message="Evaluation cancelled before execution.")
        evaluation_config = config.get("evaluation", {}) if isinstance(config.get("evaluation"), dict) else {}
        external_command = evaluation_config.get("command")
        external_result: dict[str, Any] | None = None
        if onnx_path is None:
            architecture = str(config.get("model", {}).get("architecture", self.task))
            onnx_path = _experiment_dir(config) / "export" / _artifact_name(config, self.task, architecture)
        if not Path(onnx_path).exists():
            export_result = self.export(config_path, should_cancel=should_cancel, log_sink=log_sink)
            if export_result.status == "cancelled":
                report = {"status": "cancelled", "adapter": self.name, "task": self.task, "export": export_result.to_dict(), "message": "Evaluation cancelled while preparing export."}
                write_json(report_path, report)
                return AdapterResult("cancelled", report_path, {"report": str(report_path), "export": export_result.to_dict(), "message": "Evaluation cancelled while preparing export."})
            if export_result.status != "completed":
                report = {"status": "failed", "adapter": self.name, "task": self.task, "export": export_result.to_dict()}
                write_json(report_path, report)
                return AdapterResult("failed", report_path, {"report": str(report_path)})
            onnx_path = export_result.payload.get("onnx") or export_result.path
        if external_command:
            external_result = _run_external_command(
                external_command,
                cwd=_command_cwd(evaluation_config),
                stage="evaluation",
                should_cancel=should_cancel,
                log_sink=log_sink,
            )
            if external_result.get("cancelled"):
                report = {
                    "status": "cancelled",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(onnx_path),
                    "external": external_result,
                    "message": "External evaluation command was cancelled.",
                }
                write_json(report_path, report)
                return AdapterResult("cancelled", report_path, {"report": str(report_path), "external": external_result, "message": "External evaluation command was cancelled."})
            if not external_result["ok"]:
                report = {
                    "status": "failed",
                    "adapter": self.name,
                    "task": self.task,
                    "onnx": str(onnx_path),
                    "external": external_result,
                    "message": "External evaluation command failed.",
                }
                write_json(report_path, report)
                return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})
        if should_cancel and should_cancel():
            return _cancelled_result(report_path, adapter=self.name, stage="evaluation", config_path=config_path, message="Evaluation cancelled before report finalization.")
        check = check_onnx_loadable(onnx_path)
        export_report = _read_optional_json(_experiment_dir(config) / "export" / "export.report.json")

        # 指标来源优先级：外部命令产出的实测指标 > 配置自报期望值 > baseline 默认值。
        metrics: dict[str, float]
        metrics_source: str
        produced_metrics, produced_error = _read_produced_metrics(evaluation_config)
        declared_produced = bool(evaluation_config.get("produced_metrics") or evaluation_config.get("metrics_file"))
        if declared_produced and produced_metrics is None:
            # 声明了 produced_metrics 但回读失败：这是评估契约破裂，必须失败而非静默回落。
            report = {
                "status": "failed",
                "adapter": self.name,
                "task": self.task,
                "onnx": str(onnx_path),
                "external": external_result,
                "message": f"Failed to read produced_metrics: {produced_error}",
            }
            write_json(report_path, report)
            return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result})
        if produced_metrics is not None:
            metrics = produced_metrics
            metrics_source = "measured"
            message = "Evaluation metrics were read from the external command's produced_metrics file."
        else:
            metrics = dict(self.default_metrics)
            declared = evaluation_config.get("expected_metrics", {}) or {}
            if isinstance(declared, dict) and declared:
                metrics.update({str(k): float(v) for k, v in declared.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
                metrics_source = "declared"
                message = "Evaluation metrics are DECLARED expected values from the config, not measured results. Configure evaluation.produced_metrics for real metrics."
            else:
                metrics_source = "baseline"
                message = "Evaluation is a deterministic local baseline until task-specific metric code is configured."

        report = {
            "status": "completed",
            "adapter": self.name,
            "task": self.task,
            "onnx": str(onnx_path),
            "check": check,
            "metrics": metrics,
            "metrics_source": metrics_source,
            "export": export_report,
            "message": message,
        }
        if external_result:
            report["external"] = external_result
        write_json(report_path, report)
        payload = {"report": str(report_path), "metrics": metrics, "metrics_source": metrics_source}
        if external_result:
            payload["external"] = external_result
        return AdapterResult(status="completed", path=report_path, payload=payload)


DETECTION_YOLO_BASELINE = LocalTaskAdapter(
    name="detection_yolo_baseline",
    task="detection",
    description="Local YOLO-compatible detection baseline with optional external trainer handoff.",
    output_format="yolo",
    default_metrics={"map50": 0.01, "precision": 0.01, "recall": 0.01},
    default_labels=["person"],
    output_shape=[1, 6],
)

REID_BASELINE = LocalTaskAdapter(
    name="reid_baseline",
    task="reid",
    description="Local ReID embedding baseline with optional external trainer handoff.",
    output_format="embedding",
    default_metrics={"map": 0.01, "rank1": 0.01},
    default_labels=["identity"],
    output_shape=[1, 128],
)

CLASSIFICATION_BASELINE = LocalTaskAdapter(
    name="classification_baseline",
    task="classification",
    description="Local classification baseline with optional external trainer handoff.",
    output_format="classification",
    default_metrics={"accuracy": 0.01, "f1": 0.01},
    default_labels=["negative", "positive"],
    output_shape=[1, 2],
)

SEGMENTATION_BASELINE = LocalTaskAdapter(
    name="segmentation_baseline",
    task="segmentation",
    description="Local segmentation baseline with optional external trainer handoff.",
    output_format="segmentation",
    default_metrics={"miou": 0.01, "dice": 0.01},
    default_labels=["background", "target"],
    output_shape=[1, 2, 640, 640],
)

ULTRALYTICS_YOLO_ADAPTER = LocalTaskAdapter(
    name="ultralytics_yolo",
    task="detection",
    description="Production YOLO adapter entrypoint; set training/export/evaluation command argv for Ultralytics in the deployment env.",
    output_format="yolo",
    default_metrics={"map50": 0.0, "precision": 0.0, "recall": 0.0},
    default_labels=["person"],
    output_shape=[1, 6],
)

TORCHREID_ADAPTER = LocalTaskAdapter(
    name="torchreid",
    task="reid",
    description="Production ReID adapter entrypoint; set command argv for TorchReID or an internal ReID trainer.",
    output_format="embedding",
    default_metrics={"map": 0.0, "rank1": 0.0},
    default_labels=["identity"],
    output_shape=[1, 128],
)

TORCHVISION_CLASSIFICATION_ADAPTER = LocalTaskAdapter(
    name="torchvision_classifier",
    task="classification",
    description="Production classification adapter entrypoint; set command argv for TorchVision, timm, or an internal classifier trainer.",
    output_format="classification",
    default_metrics={"accuracy": 0.0, "f1": 0.0},
    default_labels=["negative", "positive"],
    output_shape=[1, 2],
)

SEGMENTATION_FRAMEWORK_ADAPTER = LocalTaskAdapter(
    name="segmentation_framework",
    task="segmentation",
    description="Production segmentation adapter entrypoint; set command argv for MMSegmentation, SMP, or an internal segmenter trainer.",
    output_format="segmentation",
    default_metrics={"miou": 0.0, "dice": 0.0},
    default_labels=["background", "target"],
    output_shape=[1, 2, 640, 640],
)

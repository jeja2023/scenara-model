from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scenara_model.adapters.base import AdapterResult
from scenara_model.dataset_versions import reference_validation_issues
from scenara_model.datasets.manifest import validate_manifest
from scenara_model.export.onnx_checks import check_onnx_loadable
from scenara_model.utils import ensure_dir, read_yaml, sha256_file, write_json

LogLineSink = Callable[[str, str], None]
"""外部命令逐行日志回调：(stream, line)。"""


def _workspace_root() -> Path:
    return Path(os.environ.get("SCENARA_MODEL_WORKSPACE", Path.cwd())).resolve()


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
    model = helper.make_model(graph, producer_name="scenara-model-local-task", opset_imports=[helper.make_operatorsetid("", 17)])
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
_ENV_ALLOWLIST = {"SCENARA_MODEL_WORKSPACE"}
# 平台配置与凭证一律不进入子进程。SCENARA_MODEL_METADATA_DB 会携带 PostgreSQL DSN 中的
# 明文口令，SCENARA_MODEL_ADMIN_PASSWORD 是控制台管理员口令。
_ENV_BLOCKED_PREFIXES = ("SCENARA_MODEL_", "AWS_", "POSTGRES_", "MINIO_", "PG")
_ENV_BLOCKED_KEYWORDS = ("PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "ACCESS_KEY", "PRIVATE_KEY", "DSN")


def _env_passthrough() -> set[str]:
    """部署方显式放行的变量名（逗号分隔），用于训练确需的第三方凭证。"""
    raw = os.environ.get("SCENARA_MODEL_EXTERNAL_COMMAND_ENV_PASSTHROUGH", "")
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
    SCENARA_MODEL_METADATA_DB（含 PG 口令）与 SCENARA_MODEL_ADMIN_PASSWORD 就是这样漏掉的。
    训练确需的第三方凭证（如 HF_TOKEN）请通过
    SCENARA_MODEL_EXTERNAL_COMMAND_ENV_PASSTHROUGH 显式放行。
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
    timeout = _int_env("SCENARA_MODEL_EXTERNAL_COMMAND_TIMEOUT_SECONDS", 3600)
    log_limit = _int_env("SCENARA_MODEL_EXTERNAL_COMMAND_LOG_MAX_CHARS", 20000)
    allow_shell = _bool_env("SCENARA_MODEL_ALLOW_SHELL_COMMANDS", False)
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
                "stderr": "String shell commands are disabled; use an argv list or set SCENARA_MODEL_ALLOW_SHELL_COMMANDS=true.",
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


def _file_fingerprint(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path, use_cache=False),
    }


def _artifact_is_fresh(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    if after is None:
        return False
    return before is None or before.get("sha256") != after.get("sha256") or before.get("mtime_ns") != after.get("mtime_ns")


def _atomic_copy_file(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _failed_result(
    report_path: Path,
    *,
    adapter: str,
    task: str,
    stage: str,
    config_path: str | Path,
    message: str,
    detail: dict[str, Any] | None = None,
) -> AdapterResult:
    extra = detail or {}
    report = {
        "status": "failed",
        "adapter": adapter,
        "task": task,
        "stage": stage,
        "config": str(config_path),
        "message": message,
        **extra,
    }
    write_json(report_path, report)
    return AdapterResult("failed", report_path, {"report": str(report_path), "message": message, **extra})


def _training_manifest_issues(config: dict[str, Any]) -> list[str]:
    dataset = config.get("dataset", {})
    if not isinstance(dataset, dict):
        return ["dataset must be an object"]
    issues: list[str] = []
    package = config.get("package")
    if isinstance(package, dict) and str(package.get("profile") or "production").strip().lower() == "production":
        issues.extend(reference_validation_issues(config, _workspace_root()))
    for split, key in (("train", "train_manifest"), ("val", "val_manifest")):
        value = dataset.get(key)
        if not value:
            issues.append(f"dataset.{key} is required for a production training adapter")
            continue
        try:
            path = _resolve_workspace_file(str(value))
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not path.exists():
            issues.append(f"dataset.{key} was not found: {path}")
            continue
        validation = validate_manifest(path, min_split_counts={split: 1}, check_local_files=True)
        if not validation.ok:
            codes = ", ".join(sorted({issue.code for issue in validation.issues}))
            issues.append(f"dataset.{key} is invalid ({codes}): {path}")
    return issues


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


def _framework_provenance(training: dict[str, Any], expected_name: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate and fingerprint the immutable framework/runtime declaration."""
    raw = training.get("framework")
    if not isinstance(raw, dict):
        return None, ["training.framework is required for this adapter"]
    name = str(raw.get("name") or "").strip().lower()
    if name != expected_name:
        return None, [f"training.framework.name must be {expected_name}"]
    repository = str(raw.get("repository") or "").strip()
    if not repository.startswith(("https://", "ssh://", "git@")):
        return None, ["training.framework.repository must be a source repository URL"]
    revision = str(raw.get("revision") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        return None, ["training.framework.revision must be an immutable 40-64 character Git commit SHA"]
    lock_value = raw.get("environment_lock")
    if not lock_value:
        return None, ["training.framework.environment_lock is required"]
    try:
        lock_path = _resolve_workspace_file(str(lock_value))
    except ValueError as exc:
        return None, [str(exc)]
    if not lock_path.is_file():
        return None, [f"training.framework.environment_lock was not found: {lock_path}"]
    lock_sha256 = sha256_file(lock_path)
    declared_lock_sha256 = str(raw.get("environment_lock_sha256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", declared_lock_sha256) is None:
        return None, ["training.framework.environment_lock_sha256 must be a SHA-256 digest"]
    if not hmac.compare_digest(lock_sha256, declared_lock_sha256):
        return None, ["training.framework.environment_lock_sha256 does not match the lock file"]
    return {
        "name": name,
        "repository": repository,
        "revision": revision,
        "environment_lock": str(lock_path),
        "environment_lock_sha256": lock_sha256,
    }, []


def _required_metric_issues(metrics: dict[str, float], required: tuple[str, ...]) -> list[str]:
    issues = [f"measured metrics are missing: {name}" for name in required if name not in metrics]
    for name in required:
        value = metrics.get(name)
        if value is not None and (value < 0.0 or value > 1.0):
            issues.append(f"metric {name} must be between 0 and 1")
    if {"rank1", "rank5", "rank10"} <= set(required) and not issues:
        if not metrics["rank1"] <= metrics["rank5"] <= metrics["rank10"]:
            issues.append("ReID CMC metrics must satisfy rank1 <= rank5 <= rank10")
    return issues


@dataclass(frozen=True)
class LocalTaskAdapter:
    name: str
    task: str
    description: str
    output_format: str
    default_metrics: dict[str, float]
    default_labels: list[str]
    output_shape: list[int]
    requires_external_artifacts: bool = False
    framework_name: str | None = None
    required_evaluation_metrics: tuple[str, ...] = ()
    required_evaluation_protocol: str | None = None

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
        if not isinstance(training, dict):
            return _failed_result(
                report_path,
                adapter=self.name,
                task=self.task,
                stage="training",
                config_path=config_path,
                message="training must be an object.",
            )
        external_command = training.get("command")
        data_prepare_command = training.get("data_prepare_command")
        prepared_dataset_value = training.get("prepared_dataset_root")
        prepared_dataset_root: Path | None = None
        if prepared_dataset_value:
            try:
                prepared_dataset_root = _resolve_workspace_file(str(prepared_dataset_value))
            except ValueError as exc:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="training",
                    config_path=config_path,
                    message=str(exc),
                )
        checkpoint_value = training.get("produced_checkpoint") or training.get("checkpoint_file")
        checkpoint_path: Path | None = None
        checkpoint_before: dict[str, Any] | None = None
        framework: dict[str, Any] | None = None
        if checkpoint_value:
            try:
                checkpoint_path = _resolve_workspace_file(str(checkpoint_value))
            except ValueError as exc:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="training",
                    config_path=config_path,
                    message=str(exc),
                )
            checkpoint_before = _file_fingerprint(checkpoint_path)
        if self.requires_external_artifacts:
            preflight_issues = _training_manifest_issues(config)
            if not external_command:
                preflight_issues.append("training.command is required for a production training adapter")
            if checkpoint_path is None:
                preflight_issues.append("training.produced_checkpoint is required for a production training adapter")
            if self.framework_name:
                framework, framework_issues = _framework_provenance(training, self.framework_name)
                preflight_issues.extend(framework_issues)
                if not data_prepare_command:
                    preflight_issues.append("training.data_prepare_command is required for FastReID manifest materialization")
                if prepared_dataset_root is None:
                    preflight_issues.append("training.prepared_dataset_root is required for FastReID manifest materialization")
            if preflight_issues:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="training",
                    config_path=config_path,
                    message="Training preflight failed.",
                    detail={"preflight_issues": preflight_issues},
                )
        status = "completed"
        message = "Local baseline recorded a reproducible run; replace training.command for framework training."
        external_result: dict[str, Any] | None = None
        preflight_result: dict[str, Any] | None = None
        data_prepare_result: dict[str, Any] | None = None
        checkpoint: dict[str, Any] | None = None
        if external_command:
            if data_prepare_command:
                data_prepare_result = _run_external_command(
                    data_prepare_command,
                    cwd=_command_cwd(training),
                    stage="training_data_prepare",
                    should_cancel=should_cancel,
                    log_sink=log_sink,
                )
                if not data_prepare_result.get("ok"):
                    return _failed_result(
                        report_path,
                        adapter=self.name,
                        task=self.task,
                        stage="training",
                        config_path=config_path,
                        message="Training dataset preparation failed.",
                        detail={"data_preparation": data_prepare_result},
                    )
                if prepared_dataset_root is not None and not prepared_dataset_root.is_dir():
                    return _failed_result(
                        report_path,
                        adapter=self.name,
                        task=self.task,
                        stage="training",
                        config_path=config_path,
                        message=f"training.data_prepare_command did not produce prepared_dataset_root: {prepared_dataset_root}",
                        detail={"data_preparation": data_prepare_result},
                    )
            if preflight_command := training.get("preflight_command"):
                preflight_result = _run_external_command(
                    preflight_command,
                    cwd=_command_cwd(training),
                    stage="training_preflight",
                    should_cancel=should_cancel,
                    log_sink=log_sink,
                )
                if not preflight_result.get("ok"):
                    return _failed_result(
                        report_path,
                        adapter=self.name,
                        task=self.task,
                        stage="training",
                        config_path=config_path,
                        message="Training runtime preflight failed.",
                        detail={"preflight": preflight_result},
                    )
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
            if status == "completed" and checkpoint_path is not None:
                checkpoint = _file_fingerprint(checkpoint_path)
                if checkpoint is None:
                    status = "failed"
                    message = f"training.command completed but produced_checkpoint was not found: {checkpoint_path}"
                elif not _artifact_is_fresh(checkpoint_before, checkpoint):
                    status = "failed"
                    message = f"training.command did not produce a fresh checkpoint: {checkpoint_path}"
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
        if preflight_result:
            report["preflight"] = preflight_result
        if data_prepare_result:
            report["data_preparation"] = data_prepare_result
        if checkpoint:
            report["checkpoint"] = checkpoint
        if framework:
            report["framework"] = framework
        write_json(report_path, report)
        payload: dict[str, Any] = {"report": str(report_path), "message": message}
        if external_result:
            payload["external"] = external_result
        if preflight_result:
            payload["preflight"] = preflight_result
        if data_prepare_result:
            payload["data_preparation"] = data_prepare_result
        if checkpoint:
            payload["checkpoint"] = checkpoint
        if framework:
            payload["framework"] = framework
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
        produced = export_config.get("produced_onnx")
        produced_path: Path | None = None
        produced_before: dict[str, Any] | None = None
        if produced:
            try:
                produced_path = _resolve_workspace_file(str(produced))
            except ValueError as exc:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="export",
                    config_path=config_path,
                    message=str(exc),
                )
            produced_before = _file_fingerprint(produced_path)
        if self.requires_external_artifacts:
            preflight_issues: list[str] = []
            if not external_command:
                preflight_issues.append("export.command is required for a production export adapter")
            if produced_path is None:
                preflight_issues.append("export.produced_onnx is required for a production export adapter")
            if preflight_issues:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="export",
                    config_path=config_path,
                    message="Export preflight failed.",
                    detail={"preflight_issues": preflight_issues},
                )

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
            return AdapterResult(
                status="completed",
                path=output_path,
                payload={"onnx": str(output_path), "report": str(report_path), "onnx_source": "reused", "output_format": self.output_format},
            )

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
            produced_fingerprint = _file_fingerprint(produced_path)
            if produced_path is not None:
                if produced_fingerprint is None:
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
                if not _artifact_is_fresh(produced_before, produced_fingerprint):
                    report = {
                        "status": "failed",
                        "adapter": self.name,
                        "task": self.task,
                        "onnx": str(output_path),
                        "message": f"export.command did not produce a fresh ONNX artifact: {produced_path}",
                        "external": external_result,
                    }
                    write_json(report_path, report)
                    return AdapterResult("failed", report_path, {"report": str(report_path), "external": external_result, "message": report["message"]})
                if produced_path != output_path:
                    _atomic_copy_file(produced_path, output_path)
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
                "artifact": _file_fingerprint(output_path),
                "external": external_result,
            }
            write_json(report_path, report)
            return AdapterResult(
                status="completed",
                path=output_path,
                payload={
                    "onnx": str(output_path),
                    "report": str(report_path),
                    "onnx_source": "external_command",
                    "output_format": self.output_format,
                    "artifact": report["artifact"],
                    "external": external_result,
                },
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
        return AdapterResult(
            status="completed",
            path=output_path,
            payload={"onnx": str(output_path), "report": str(report_path), "onnx_source": "synthetic_baseline", "output_format": self.output_format},
        )

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
        metrics_value = evaluation_config.get("produced_metrics") or evaluation_config.get("metrics_file")
        metrics_path: Path | None = None
        metrics_before: dict[str, Any] | None = None
        if metrics_value:
            try:
                metrics_path = _resolve_workspace_file(str(metrics_value))
            except ValueError as exc:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="evaluation",
                    config_path=config_path,
                    message=str(exc),
                )
            metrics_before = _file_fingerprint(metrics_path)
        if self.requires_external_artifacts:
            preflight_issues: list[str] = []
            if not external_command:
                preflight_issues.append("evaluation.command is required for a production evaluation adapter")
            if metrics_path is None:
                preflight_issues.append("evaluation.produced_metrics is required for a production evaluation adapter")
            if self.required_evaluation_protocol:
                if evaluation_config.get("protocol") != self.required_evaluation_protocol:
                    preflight_issues.append(f"evaluation.protocol must be {self.required_evaluation_protocol}")
                dataset = config.get("dataset", {}) if isinstance(config.get("dataset"), dict) else {}
                if evaluation_config.get("manifest") != dataset.get("test_manifest"):
                    preflight_issues.append("evaluation.manifest must reference dataset.test_manifest for a fixed evaluation set")
            if preflight_issues:
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="evaluation",
                    config_path=config_path,
                    message="Evaluation preflight failed.",
                    detail={"preflight_issues": preflight_issues},
                )
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
            exported_onnx = export_result.payload.get("onnx")
            onnx_path = Path(exported_onnx) if exported_onnx else export_result.path
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
            if metrics_path is not None and not _artifact_is_fresh(metrics_before, _file_fingerprint(metrics_path)):
                return _failed_result(
                    report_path,
                    adapter=self.name,
                    task=self.task,
                    stage="evaluation",
                    config_path=config_path,
                    message=f"evaluation.command did not produce a fresh metrics artifact: {metrics_path}",
                    detail={"external": external_result},
                )
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

        metric_issues = _required_metric_issues(metrics, self.required_evaluation_metrics)
        if metric_issues:
            return _failed_result(
                report_path,
                adapter=self.name,
                task=self.task,
                stage="evaluation",
                config_path=config_path,
                message="Evaluation metric contract failed.",
                detail={"metric_issues": metric_issues, "metrics": metrics, "external": external_result},
            )

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
    requires_external_artifacts=True,
)

TORCHREID_ADAPTER = LocalTaskAdapter(
    name="torchreid",
    task="reid",
    description="Production ReID adapter entrypoint; set command argv for TorchReID or an internal ReID trainer.",
    output_format="embedding",
    default_metrics={"map": 0.0, "rank1": 0.0},
    default_labels=["identity"],
    output_shape=[1, 128],
    requires_external_artifacts=True,
)

TORCHVISION_CLASSIFICATION_ADAPTER = LocalTaskAdapter(
    name="torchvision_classifier",
    task="classification",
    description="Production classification adapter entrypoint; set command argv for TorchVision, timm, or an internal classifier trainer.",
    output_format="classification",
    default_metrics={"accuracy": 0.0, "f1": 0.0},
    default_labels=["negative", "positive"],
    output_shape=[1, 2],
    requires_external_artifacts=True,
)

SEGMENTATION_FRAMEWORK_ADAPTER = LocalTaskAdapter(
    name="segmentation_framework",
    task="segmentation",
    description="Production segmentation adapter entrypoint; set command argv for MMSegmentation, SMP, or an internal segmenter trainer.",
    output_format="segmentation",
    default_metrics={"miou": 0.0, "dice": 0.0},
    default_labels=["background", "target"],
    output_shape=[1, 2, 640, 640],
    requires_external_artifacts=True,
)

FASTREID_ADAPTER = LocalTaskAdapter(
    name="fastreid",
    task="reid",
    description="Production FastReID adapter with pinned framework/runtime provenance and fixed CMC+mAP evaluation contract.",
    output_format="embedding",
    default_metrics={"map": 0.0, "rank1": 0.0, "rank5": 0.0, "rank10": 0.0},
    default_labels=["identity"],
    output_shape=[1, 128],
    requires_external_artifacts=True,
    framework_name="fastreid",
    required_evaluation_metrics=("map", "rank1", "rank5", "rank10"),
    required_evaluation_protocol="fastreid-cmc-map-v1",
)

PADDLEOCR_ADAPTER = LocalTaskAdapter(
    name="paddleocr",
    task="ocr",
    description="Production OCR training/export/evaluation entrypoint; requires explicit PaddleOCR commands and measured artifacts.",
    output_format="ocr_bundle",
    default_metrics={"character_accuracy": 0.0, "text_accuracy": 0.0, "edit_distance": 0.0},
    default_labels=["text"],
    output_shape=[1, 1],
    requires_external_artifacts=True,
)

PADDLEVIDEO_ADAPTER = LocalTaskAdapter(
    name="paddlevideo",
    task="behavior",
    description="Production behavior recognition entrypoint; requires explicit PaddleVideo commands and measured temporal metrics.",
    output_format="behavior_bundle",
    default_metrics={"action_f1": 0.0, "temporal_iou": 0.0},
    default_labels=["action"],
    output_shape=[1, 1],
    requires_external_artifacts=True,
)

FASHION_MULTIHEAD_ADAPTER = LocalTaskAdapter(
    name="fashion_multihead",
    task="fashion",
    description="Production fashion multi-head entrypoint for character, style, and accessory models with measured metrics.",
    output_format="fashion_bundle",
    default_metrics={"macro_f1": 0.0, "map50": 0.0},
    default_labels=["fashion"],
    output_shape=[1, 1],
    requires_external_artifacts=True,
)

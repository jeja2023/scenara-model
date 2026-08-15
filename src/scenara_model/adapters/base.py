from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

LogLineSink = Callable[[str, str], None]
"""外部命令逐行日志回调：(stream, line)。"""


@dataclass(frozen=True)
class AdapterResult:
    status: str
    path: Path
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": str(self.path),
            **self.payload,
        }


class TaskAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def task(self) -> str: ...

    @property
    def description(self) -> str: ...

    def train(
        self,
        config_path: str | Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        ...

    def export(
        self,
        config_path: str | Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        ...

    def evaluate(
        self,
        config_path: str | Path,
        onnx_path: str | Path | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
        log_sink: LogLineSink | None = None,
    ) -> AdapterResult:
        ...

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml


def as_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def read_yaml(path: str | Path) -> dict[str, Any]:
    resolved = as_path(path)
    with resolved.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be an object: {resolved}")
    return data


def write_yaml(path: str | Path, data: dict[str, Any]) -> None:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=resolved.parent, prefix=f".{resolved.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    resolved = as_path(path)
    with resolved.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL line {line_number} must be an object")
            rows.append(row)
    return rows


def read_json(path: str | Path) -> Any:
    resolved = as_path(path)
    with resolved.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=resolved.parent, prefix=f".{resolved.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# 摘要缓存：键为路径，值为 (size, mtime_ns, digest)。
_DIGEST_CACHE: dict[str, tuple[int, int, str]] = {}
_DIGEST_CACHE_LOCK = threading.Lock()
_DIGEST_CACHE_MAX = 512


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024, *, use_cache: bool = True) -> str:
    """计算文件 sha256，按 (size, mtime_ns) 缓存。

    模型包校验对每个 ONNX 都要求摘要，而管理台首页每次刷新都会触发全量扫描；
    没有缓存时几百 MB 的模型会被反复整文件读取，扫描接口同步阻塞 HTTP worker。

    刚写入或复制出来的文件必须传 use_cache=False：shutil.copy2 会保留源文件
    mtime，同一路径先后放入 size 与 mtime 相同的两个文件时缓存会命中错误结果。
    """
    resolved = as_path(path)
    key = str(resolved)
    try:
        stat = resolved.stat()
        signature: tuple[int, int] | None = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        signature = None
    if use_cache and signature is not None:
        with _DIGEST_CACHE_LOCK:
            cached = _DIGEST_CACHE.get(key)
        if cached is not None and cached[:2] == signature:
            return cached[2]
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    if signature is not None:
        with _DIGEST_CACHE_LOCK:
            if len(_DIGEST_CACHE) >= _DIGEST_CACHE_MAX:
                _DIGEST_CACHE.clear()
            _DIGEST_CACHE[key] = (signature[0], signature[1], value)
    return value


def ensure_dir(path: str | Path) -> Path:
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def non_empty_lines(path: str | Path) -> list[str]:
    resolved = as_path(path)
    with resolved.open("r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


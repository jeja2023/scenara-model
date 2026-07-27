from __future__ import annotations

import os
import shutil
from pathlib import Path

from vision_model_lab.utils import sha256_file


def test_sha256_cache_is_bypassed_for_freshly_copied_files(workspace_tmp_path: Path) -> None:
    """回归：copy2 保留 mtime，打包路径必须绕过缓存，否则模型卡会写入错误摘要。"""
    source_a = workspace_tmp_path / "a.bin"
    source_b = workspace_tmp_path / "b.bin"
    source_a.write_bytes(b"AAAA")
    source_b.write_bytes(b"BBBB")  # 同样长度
    os.utime(source_a, (0, 0))
    os.utime(source_b, (0, 0))  # 同样 mtime

    target = workspace_tmp_path / "target.bin"
    shutil.copy2(source_a, target)
    first = sha256_file(target, use_cache=False)
    shutil.copy2(source_b, target)

    assert sha256_file(target, use_cache=False) != first


def test_sha256_cache_invalidates_on_content_change(workspace_tmp_path: Path) -> None:
    """缓存必须跟随 (size, mtime_ns) 失效，否则替换模型后会沿用旧摘要。"""
    target = workspace_tmp_path / "model.bin"
    target.write_bytes(b"first")
    first = sha256_file(target)

    assert sha256_file(target) == first  # 命中缓存

    target.write_bytes(b"second-content-differs")
    os.utime(target, None)

    assert sha256_file(target) != first

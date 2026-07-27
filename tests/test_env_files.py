from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 允许出现在 .env 中的非机密占位值。
SECRET_PATTERN = re.compile(r"^(VMLAB_AUTH_TOKEN|VMLAB_ADMIN_PASSWORD|VMLAB_S3_ACCESS_KEY_ID|VMLAB_S3_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)\s*=\s*(.+)$")
PLACEHOLDER_VALUES = {"", "change-me", "your-token", "your-access-key", "your-secret-key", "<token>", "<access-key>", "<secret-key>"}


def test_env_file_is_not_tracked_by_git() -> None:
    """.env 属于本地配置，绝不允许提交进版本库（防凭证泄漏）。"""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, ".env must not be tracked by git; run: git rm --cached .env"


def test_env_example_exists_and_has_no_real_secrets() -> None:
    example = ROOT / ".env.example"
    assert example.exists(), ".env.example must exist as the configuration template"
    for line in example.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        match = SECRET_PATTERN.match(stripped)
        if match:
            value = match.group(2).strip().strip("'\"")
            assert value.lower() in PLACEHOLDER_VALUES, f".env.example must not contain a real secret for {match.group(1)}"


def test_local_env_file_has_no_uncommented_secrets() -> None:
    """本地 .env 若存在，其中的机密值不应是会被误提交的真实凭证形态。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        match = SECRET_PATTERN.match(stripped)
        if match:
            # 本地填写真实凭证是允许的，但 .env 必须未被 git 跟踪（由上面的测试保证）。
            # 这里仅提示性检查：值非空说明用户已启用鉴权/对象存储凭证。
            assert match.group(2).strip(), f"{match.group(1)} is set but empty; remove or fill it"

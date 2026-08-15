"""用户密码与会话令牌的加密工具。

仅依赖标准库：密码使用 PBKDF2-HMAC-SHA256 加盐哈希，
会话令牌为高熵随机串，数据库只保存其 SHA-256 摘要（防止 DB 泄露即会话泄露）。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# OWASP 2023 建议 PBKDF2-HMAC-SHA256 迭代次数不低于 600k；
# 兼顾低配部署机的登录延迟，取 210k（与 Django 默认同量级）。
PBKDF2_ITERATIONS = 210_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32


def hash_password(password: str) -> tuple[str, str]:
    """返回 (salt_hex, hash_hex)。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest, expected)


def generate_session_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

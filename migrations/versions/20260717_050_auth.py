"""用户与登录会话表。

Revision ID: 20260717_050
Revises: 20260703_040
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql.elements import TextClause


revision = "20260717_050"
down_revision = "20260703_040"
branch_labels = None
depends_on = None


# 主键类型与运行时 DDL 对齐：PG 用 BIGSERIAL/BIGINT，SQLite 需要 INTEGER 才能启用 rowid 自增。
PK_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def _ts_type() -> sa.types.TypeEngine:
    # 与运行时 DDL 对齐：PG 用 TIMESTAMPTZ，SQLite 用 TEXT 存 ISO8601 字符串。
    if _is_postgres():
        return sa.DateTime(timezone=True)
    return sa.Text()


def _ts_default() -> TextClause:
    if _is_postgres():
        return sa.text("CURRENT_TIMESTAMP")
    return sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")


def _ts_column(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, _ts_type(), nullable=True)
    return sa.Column(name, _ts_type(), nullable=False, server_default=_ts_default())


def _has_table(name: str) -> bool:
    # 运行时内置 DDL（CREATE TABLE IF NOT EXISTS）可能先于本迁移建表
    # （例如服务先启动、后执行 storage migrate），schema 与此处一致，跳过即可。
    return sa.inspect(op.get_bind()).has_table(name)


def _has_index(table: str, index: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item.get("name") == index for item in inspector.get_indexes(table))


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", PK_TYPE, primary_key=True),
            sa.Column("username", sa.Text(), nullable=False, unique=True),
            sa.Column("password_salt", sa.Text(), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False, server_default="admin"),
            _ts_column("created_at"),
            _ts_column("updated_at"),
        )
    if not _has_table("auth_sessions"):
        op.create_table(
            "auth_sessions",
            sa.Column("id", PK_TYPE, primary_key=True),
            sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
            sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
            sa.Column("username", sa.Text(), nullable=False),
            _ts_column("created_at"),
            sa.Column("expires_at", _ts_type(), nullable=False),
            sa.Column("revoked", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_index("auth_sessions", "idx_auth_sessions_expires_at"):
        op.create_index("idx_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("idx_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("users")

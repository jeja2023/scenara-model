"""任务 worker 归属与心跳列。

Revision ID: 20260726_060
Revises: 20260717_050
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260726_060"
down_revision = "20260717_050"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def _ts_type() -> sa.types.TypeEngine:
    return sa.DateTime(timezone=True) if _is_postgres() else sa.Text()


def _existing_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("pipeline_jobs")}


def upgrade() -> None:
    existing = _existing_columns()
    if "worker_id" not in existing:
        op.add_column("pipeline_jobs", sa.Column("worker_id", sa.Text(), nullable=True))
    if "heartbeat_at" not in existing:
        op.add_column("pipeline_jobs", sa.Column("heartbeat_at", _ts_type(), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_jobs", "heartbeat_at")
    op.drop_column("pipeline_jobs", "worker_id")

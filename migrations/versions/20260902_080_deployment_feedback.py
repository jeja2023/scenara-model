"""Persist signed Core deployment-feedback events and replay state.

Revision ID: 20260902_080
Revises: 20260818_070
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260902_080"
down_revision = "20260818_070"
branch_labels = None
depends_on = None


PK_TEXT = sa.Text()
EPOCH_MS = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _received_at_default():
    return sa.text("CURRENT_TIMESTAMP") if _is_postgres() else sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")


def _runtime_timestamp_type() -> sa.types.TypeEngine:
    return sa.DateTime(timezone=True) if _is_postgres() else sa.Text()


def upgrade() -> None:
    if not _has_table("deployment_feedback_events"):
        op.create_table(
            "deployment_feedback_events",
            sa.Column("event_id", PK_TEXT, primary_key=True),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=False),
            sa.Column("capability", sa.Text(), nullable=False),
            sa.Column("model_id", sa.Text(), nullable=False),
            sa.Column("model_version", sa.Text(), nullable=False),
            sa.Column("package_sha256", sa.Text(), nullable=False),
            sa.Column("from_status", sa.Text(), nullable=True),
            sa.Column("to_status", sa.Text(), nullable=False),
            sa.Column("event_created_at", sa.Text(), nullable=False),
            sa.Column("event_created_at_ms", EPOCH_MS, nullable=False),
            sa.Column("body_sha256", sa.Text(), nullable=False),
            sa.Column("processing_status", sa.Text(), nullable=False),
            sa.Column("event_json", sa.Text(), nullable=False),
            sa.Column("received_at", _runtime_timestamp_type(), nullable=False, server_default=_received_at_default()),
        )
        op.create_index(
            "idx_deployment_feedback_events_scope",
            "deployment_feedback_events",
            ["tenant_id", "project_id", "capability", "event_created_at_ms"],
        )
    if not _has_table("deployment_feedback_state"):
        op.create_table(
            "deployment_feedback_state",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=False),
            sa.Column("capability", sa.Text(), nullable=False),
            sa.Column("model_id", sa.Text(), nullable=False),
            sa.Column("model_version", sa.Text(), nullable=False),
            sa.Column("package_sha256", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("event_id", sa.Text(), nullable=False),
            sa.Column("event_created_at", sa.Text(), nullable=False),
            sa.Column("event_created_at_ms", EPOCH_MS, nullable=False),
            sa.Column("updated_at", _runtime_timestamp_type(), nullable=False, server_default=_received_at_default()),
            sa.PrimaryKeyConstraint("tenant_id", "project_id", "capability"),
        )


def downgrade() -> None:
    if _has_table("deployment_feedback_state"):
        op.drop_table("deployment_feedback_state")
    if _has_table("deployment_feedback_events"):
        op.drop_index("idx_deployment_feedback_events_scope", table_name="deployment_feedback_events")
        op.drop_table("deployment_feedback_events")

"""Dataset version reference persistence.

Revision ID: 20260818_070
Revises: 20260726_060
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260818_070"
down_revision = "20260726_060"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def _existing_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("dataset_versions")}


def upgrade() -> None:
    if "reference_json" not in _existing_columns():
        if _is_postgres():
            op.add_column(
                "dataset_versions",
                sa.Column("reference_json", sa.Text(), nullable=False, server_default="{}"),
            )
        else:
            op.add_column(
                "dataset_versions",
                sa.Column("reference_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
            )


def downgrade() -> None:
    if "reference_json" in _existing_columns():
        op.drop_column("dataset_versions", "reference_json")

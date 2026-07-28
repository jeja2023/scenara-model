from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql.elements import TextClause


revision = "20260703_040"
down_revision = None
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


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("package", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        _ts_column("created_at"),
        _ts_column("updated_at"),
    )
    op.create_table(
        "package_validations",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("package_dir", sa.Text(), nullable=False),
        sa.Column("model_file", sa.Text(), nullable=True),
        sa.Column("ok", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("report_json", sa.Text(), nullable=False),
        _ts_column("created_at"),
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("config_path", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        _ts_column("created_at"),
    )
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("config_path", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts_column("created_at"),
        _ts_column("started_at", nullable=True),
        _ts_column("completed_at", nullable=True),
        _ts_column("cancelled_at", nullable=True),
        _ts_column("updated_at"),
    )
    op.create_index("idx_pipeline_jobs_status", "pipeline_jobs", ["status"])
    op.create_table(
        "audit_events",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        _ts_column("created_at"),
    )
    op.create_index("idx_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table(
        "pipeline_job_logs",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("job_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("stream", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        _ts_column("created_at"),
    )
    op.create_index("idx_pipeline_job_logs_job_id", "pipeline_job_logs", ["job_id"])
    op.create_table(
        "pipeline_artifacts",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("job_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("run_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        _ts_column("created_at"),
    )
    op.create_index("idx_pipeline_artifacts_job_id", "pipeline_artifacts", ["job_id"])
    op.create_index("idx_pipeline_artifacts_run_id", "pipeline_artifacts", ["run_id"])
    op.create_table(
        "dataset_versions",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("dataset_id", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("split_counts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("labels_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="registered"),
        _ts_column("created_at"),
        _ts_column("updated_at"),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("model_id", sa.Text(), nullable=False, unique=True),
        sa.Column("package_dir", sa.Text(), nullable=False),
        sa.Column("artifact_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("stage", sa.Text(), nullable=False, server_default="candidate"),
        _ts_column("created_at"),
        _ts_column("updated_at"),
    )
    op.create_index("idx_model_registry_stage", "model_registry", ["stage"])
    op.create_table(
        "release_approvals",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("decision_json", sa.Text(), nullable=False, server_default="{}"),
        _ts_column("created_at"),
        _ts_column("updated_at"),
    )
    op.create_index("idx_release_approvals_model_id", "release_approvals", ["model_id"])
    op.create_table(
        "deployment_rollouts",
        sa.Column("id", PK_TYPE, primary_key=True),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("traffic_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rollback_target", sa.Text(), nullable=True),
        _ts_column("created_at"),
        _ts_column("updated_at"),
    )
    op.create_index("idx_deployment_rollouts_model_id", "deployment_rollouts", ["model_id"])


def downgrade() -> None:
    op.drop_index("idx_deployment_rollouts_model_id", table_name="deployment_rollouts")
    op.drop_table("deployment_rollouts")
    op.drop_index("idx_release_approvals_model_id", table_name="release_approvals")
    op.drop_table("release_approvals")
    op.drop_index("idx_model_registry_stage", table_name="model_registry")
    op.drop_table("model_registry")
    op.drop_table("dataset_versions")
    op.drop_index("idx_pipeline_artifacts_run_id", table_name="pipeline_artifacts")
    op.drop_index("idx_pipeline_artifacts_job_id", table_name="pipeline_artifacts")
    op.drop_table("pipeline_artifacts")
    op.drop_index("idx_pipeline_job_logs_job_id", table_name="pipeline_job_logs")
    op.drop_table("pipeline_job_logs")
    op.drop_index("idx_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("idx_pipeline_jobs_status", table_name="pipeline_jobs")
    op.drop_table("pipeline_jobs")
    op.drop_table("pipeline_runs")
    op.drop_table("package_validations")
    op.drop_table("experiments")

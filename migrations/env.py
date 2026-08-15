from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _metadata_db_url() -> str:
    value = os.environ.get("SCENARA_MODEL_METADATA_DB", "sqlite:///artifacts/scenara_model.sqlite3")
    if value == ":memory:":
        return "sqlite:///:memory:"
    if "://" in value:
        return value
    workspace_root = Path(os.environ.get("SCENARA_MODEL_WORKSPACE", Path.cwd())).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = (workspace_root / path).resolve()
    return f"sqlite:///{path.as_posix()}"


def run_migrations_offline() -> None:
    url = _metadata_db_url()
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    url = _metadata_db_url()
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

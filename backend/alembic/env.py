"""Alembic environment for the Plum claims data layer.

The database URL is read from app.config.settings (single source of truth) rather
than hardcoded in alembic.ini, and target_metadata is Base.metadata so autogenerate
and `--sql` introspection stay in sync with the SQLAlchemy models.
"""
from __future__ import annotations

import pathlib
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the backend package importable when alembic runs from backend/.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.persistence import Base  # noqa: E402  (registers all tables)

config = context.config
# Inject the URL from settings, overriding any value in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Configure Python logging from alembic.ini ONLY for the standalone CLI. When the
# app runs migrations programmatically at startup (run_migrations sets
# configure_logger=False), skip this — fileConfig defaults to
# disable_existing_loggers=True and would silently clobber the app's / uvicorn's
# already-configured loggers, swallowing later log output.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

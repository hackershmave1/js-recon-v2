"""Alembic environment — URL comes from recon.config, metadata from the models."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from recon.config import get_settings
from recon.db.base import Base
from recon.db import models  # noqa: F401  (import registers tables on Base.metadata)

config = context.config
# Migrations run as the owning admin role, not the RLS-restricted app role.
config.set_main_option("sqlalchemy.url", get_settings().database_admin_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

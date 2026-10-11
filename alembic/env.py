from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.database.database import _make_engine
from app.database.models import Base

config = context.config
import logging

# Only configure logging when run from the alembic CLI; never clobber the app's JSON/redacting handler.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        connectable = engine_from_config(
            {"sqlalchemy.url": explicit}, prefix="sqlalchemy.", poolclass=pool.NullPool
        )
    else:
        connectable = _make_engine(get_settings().database_url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

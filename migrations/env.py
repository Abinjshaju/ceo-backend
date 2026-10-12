import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

import ceo_backend.models  # noqa: F401  (registers tables)
from ceo_backend.db import Base

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Search indexes are created in SQL by the initial migration; keep autogenerate from dropping them.
MANUAL_INDEXES = {"ix_document_sections_fts", "ix_messages_fts", "ix_document_sections_embedding"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "index" and name in MANUAL_INDEXES)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        raise RuntimeError("Set DATABASE_URL to a PostgreSQL URL to run migrations.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

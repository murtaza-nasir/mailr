import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    import logging

    # Only when run as the alembic CLI; inside mailr the app has configured logging already.
    if not logging.getLogger().handlers:
        fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
import mailr.models  # noqa: F401
from mailr.config import get_settings
from mailr.db import Base

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata

# Indexes and triggers written by hand in migrations (not in the models) are ignored by
# autogenerate, so it never proposes dropping them.
MANUAL_INDEXES = {"ix_msg_upstream_ref", "ix_outbox_sending", "ix_passage_embedding", "ix_msg_user_connector", "ix_msg_has_copies", "ix_msg_embedding", "ix_msg_inreplyto", "ix_msg_refs",
                  "ix_conv_user_order", "ix_msg_subject_trgm", "ix_msg_sender_trgm",
                  "ix_msg_groups", "ix_msg_sent_conv", "ix_conv_protected",
                  # People by address (c4d8e1f2a6b3): an expression index.
                  "ix_people_user_lower_email",
                  # The mail intel.backfill takes, newest first (e5b1c8d3a7f2; intel/tasks.py TODO_WHERE).
                  "ix_msg_intel_todo",
                  # The attachments page and substring searches (a8d4e2f6c9b3): expression indexes.
                  "ix_msg_attachments", "ix_msg_to_trgm", "ix_msg_cc_trgm", "ix_msg_bcc_trgm",
                  "ix_msg_attachments_trgm", "ix_msg_list_trgm", "ix_msg_from_lower"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "index" and name in MANUAL_INDEXES)


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      include_object=include_object)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

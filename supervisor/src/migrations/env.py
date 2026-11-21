"""Alembic's environment for unos.db (ADR-0439).

The supervisor's upgrade (src/database.py) passes its connection, inside the transaction that it
commits. The command line of supervisor/alembic.ini, for `alembic revision --autogenerate`, names a
database file with `-x db=PATH` (supervisor/AGENTS.md says how to add a migration).
"""
from alembic import context
from sqlalchemy import create_engine

from src.database import AUTOGENERATE, Base, import_models

import_models()


def _run(connection) -> None:
    # render_as_batch: SQLite alters a table only by copying it, which batch_alter_table does.
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True, **AUTOGENERATE)
    with context.begin_transaction():
        context.run_migrations()


def run() -> None:
    if context.is_offline_mode():
        raise SystemExit("UNOS's migrations run on a database: alembic -x db=PATH, without --sql")
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    path = context.get_x_argument(as_dictionary=True).get("db")
    if not path:
        raise SystemExit("name the database file: alembic -x db=PATH ...")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            _run(connection)
    finally:
        engine.dispose()


run()

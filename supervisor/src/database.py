import logging
import re
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, StaticPool
from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# How long a connection waits for SQLite's write lock before "database is locked"
# (sqlite3's default is 5 s). A share change holds the lock while testparm checks the
# new smb.conf, which can take longer when a pool's disk has to spin up.
BUSY_TIMEOUT_SECONDS = 15

# unos.db is on the system disk, an SD card on many boards (ADR-0214), and uses SQLite's
# write-ahead log (ADR-0306): a commit appends the pages it changed to unos.db-wal, and only
# the one that starts the log again (the first after a start or after a checkpoint) syncs it;
# a checkpoint, when the log reaches 1000 pages and when the supervisor stops
# (checkpoint_db), copies the pages into unos.db and syncs both files. With SQLite's default,
# the rollback journal and synchronous FULL, every commit created unos.db-journal, synced it
# twice, its folder once and unos.db once, and deleted it again.
JOURNAL_MODE = "wal"
SYNCHRONOUS = "NORMAL"


def in_memory(db_path: str) -> bool:
    """Whether ``db_path`` names SQLite's in-memory database (the tests'), not a file."""
    return db_path in ("", ":memory:")


def engine_options(db_path: str) -> dict:
    """The options of the app's engine for the database file ``db_path``.

    A file database gets a pool without a limit on extra connections: a
    request that holds smb_lock may need a second connection while every
    request waiting for the lock holds one (a limit would make the holder
    wait for a free connection, and fail after the pool timeout). An
    in-memory database (the tests) has a single shared connection, which
    takes no pool options.
    """
    # hide_parameters: an exception of SQLAlchemy names no bound value in its text, which a handler logs
    # (ADR-0398): the values of an UPDATE can be a password hash or a two-factor secret.
    options: dict = {"echo": False, "hide_parameters": True, "connect_args": {"timeout": BUSY_TIMEOUT_SECONDS}}
    if not in_memory(db_path):
        options["max_overflow"] = -1
    return options


def write_ahead(dbapi_connection, connection_record) -> None:
    """Every new connection of the app's engine to a database file: the write-ahead log and
    synchronous NORMAL (ADR-0306).

    The journal mode is kept in the file, so the first connection switches unos.db over and
    the others, the emergency command line's (src/emergency.py) included, find it so; the
    synchronous level holds for this connection only. A switch that SQLite refuses, such as
    on a read-only or full /data (it writes the new mode into the file), leaves the
    connection in the file's journal mode with synchronous FULL, which reads as before
    (ADR-0251): NORMAL is safe in the log, not with the rollback journal. A warning says why,
    and a later new connection, or the next start, tries again.
    """
    cursor = dbapi_connection.cursor()
    try:
        try:
            cursor.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}")
            mode = cursor.fetchone()[0]
            refused = None if mode == JOURNAL_MODE else f"SQLite keeps the journal mode {mode}"
        except sqlite3.Error as exc:
            refused = str(exc)
        if not refused:
            cursor.execute(f"PRAGMA synchronous = {SYNCHRONOUS}")
    finally:
        cursor.close()
    if refused:
        logger.warning("The database is not in the write-ahead log: %s", refused)


def create_app_engine(db_path: str) -> AsyncEngine:
    """The app's engine for ``db_path``: its options and, for a database file, write_ahead."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", **engine_options(db_path))
    if not in_memory(db_path):
        event.listen(engine.sync_engine, "connect", write_ahead)
    return engine


engine = create_app_engine(settings.db_path)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


# The schema of unos.db: the Alembic revisions of this folder (ADR-0444), shipped in the image with
# src/. Its first revision, BASELINE, is the schema that create_all made before the migrations.
MIGRATIONS = Path(__file__).with_name("migrations")
BASELINE = "0001"


def import_models() -> None:
    """Registers every table of the models with Base.metadata."""
    from .auth.models import User  # noqa: F401
    from .security.models import LoginAttempt  # noqa: F401
    from .shares.models import SmbAccount, SmbShare, SmbShareAccess, SmbState, SmbUid  # noqa: F401
    from .apps.models import AppResourceLimit  # noqa: F401


def server_default_differs(context, inspected_column, metadata_column, inspected_default, metadata_default,
                           rendered_metadata_default) -> bool | None:
    """Autogenerate's comparison of a column's server default: alembic's own for SQLite, which drops
    the parentheses and quotes around both defaults, but also for an empty default, which alembic
    takes for another one (('') against ''). Without both defaults, alembic's own decides."""
    if inspected_default is None or rendered_metadata_default is None:
        return None

    def bare(text: str) -> str:
        return re.sub(r"^\"?'(.*)'\"?$", r"\1", re.sub(r"^\((.*)\)$", r"\1", text))

    return bare(inspected_default) != bare(rendered_metadata_default)


# What autogenerate compares, in src/migrations/env.py and in tests/test_migrations.py, which fails
# for a model whose difference from the head of the migrations has no revision yet.
AUTOGENERATE = {"compare_type": True, "compare_server_default": server_default_differs}


def migration_config() -> Config:
    """Alembic's configuration of the supervisor, without a file: the scripts of MIGRATIONS."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def known_revision(revision: str) -> bool:
    """Whether ``revision`` is one of the revisions of MIGRATIONS (not one of a newer UNOS)."""
    script = ScriptDirectory.from_config(migration_config())
    return any(item.revision == revision for item in script.walk_revisions())


def stored_revisions(conn: sqlite3.Connection) -> list[str]:
    """The revisions that the database of ``conn`` records: one, or none for a new database or one
    that create_all made before the migrations. Alembic refuses a database that records more."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'").fetchone():
        return []
    return [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]


# The tables and indexes of a database, but Alembic's and SQLite's own, with their SQL.
LAYOUT_SQL = ("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
              "AND name != 'alembic_version'")


def layouts(rows) -> dict[str, frozenset[str]]:
    """The layout of each table and index of the rows of LAYOUT_SQL: the clauses of its SQL
    (columns, constraints, WITHOUT ROWID) as a set, without IF NOT EXISTS, the quotes around its
    name and the order that a revision's copy of a table changes."""
    found = {}
    for name, sql in rows:
        text = re.sub(r"\s+", " ", sql.replace("IF NOT EXISTS ", "")).strip()
        head, _, rest = text.partition("(")
        body, _, tail = rest.rpartition(")")
        clauses, depth, start = [], 0, 0
        for index, char in enumerate(body):
            depth += {"(": 1, ")": -1}.get(char, 0)
            if char == "," and depth == 0:
                clauses.append(body[start:index].strip())
                start = index + 1
        clauses.append(body[start:].strip())
        found[name] = frozenset([re.sub(r'"([^"]+)"', r"\1", head).strip(), *clauses, tail.strip()])
    return found


def _baseline_layouts() -> dict[str, frozenset[str]]:
    """The layouts of the baseline, on a database in memory."""
    reference = create_engine("sqlite://", poolclass=StaticPool)
    try:
        with reference.connect() as connection:
            upgrade(connection, BASELINE, log=False)
            return layouts(connection.exec_driver_sql(LAYOUT_SQL).fetchall())
    finally:
        reference.dispose()


def upgrade(connection: Connection, target: str = "head", name: str = "unos.db", log: bool = True) -> None:
    """Brings the database of ``connection`` to the revision ``target`` in one SQLite transaction,
    its DDL included: a failure part way leaves it as it was (ADR-0444).

    A database at the target is only read, without a transaction, so a start writes nothing
    (ADR-0214), also on a read-only or full /data. Otherwise BEGIN IMMEDIATE takes the write lock
    first, waiting out another writer for the busy timeout. A database without a revision gets the
    baseline first: a new one all of its tables, one that create_all made before the migrations (the
    dev device's, a backup made before ADR-0444) only the tables it lacks, its own staying as they
    are, and a warning names those whose layout is not the baseline's; the baseline's revision is
    then recorded. A database of a revision that MIGRATIONS lacks, a newer UNOS's, raises and is
    left unchanged.
    """
    config = migration_config()
    wanted = ScriptDirectory.from_config(config).get_revision(target).revision
    if MigrationContext.configure(connection).get_current_revision() == wanted:
        connection.rollback()
        return
    connection.rollback()
    # sqlite3 begins no transaction before DDL by itself; this BEGIN holds every statement until
    # the commit below, and a failure rolls all of them back.
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    before = MigrationContext.configure(connection).get_current_revision()
    adopted = layouts(connection.exec_driver_sql(LAYOUT_SQL).fetchall()) if before is None else {}
    config.attributes["connection"] = connection
    command.upgrade(config, target)
    after = MigrationContext.configure(connection).get_current_revision()
    connection.commit()
    if log and after != before:
        logger.info("%s was brought from the revision %s to %s", name, before or "none (no migrations yet)", after)
    if adopted:
        baseline = _baseline_layouts()
        differ = sorted(item for item, layout in adopted.items() if baseline.get(item) != layout)
        if differ:
            # Kept as they are, as create_all kept them (ADR-0319's smb_state of an older layout):
            # a revision that changes one of them copies the table it finds (batch_alter_table).
            logger.warning("%s was taken as the baseline %s, but these tables and indexes of it differ from the "
                           "baseline's and stay as they are: %s", name, BASELINE, ", ".join(differ))


def upgrade_copy(path: str, name: str) -> None:
    """upgrade on the database file ``path``, the extracted copy of a restore in its temporary
    directory in RAM: its rollback journal stays in memory, not beside it (ADR-0364), and its schema
    is not trusted (ADR-0144)."""
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def untrusted_in_memory_journal(dbapi_connection, connection_record) -> None:
        dbapi_connection.execute("PRAGMA trusted_schema = OFF")
        dbapi_connection.execute("PRAGMA journal_mode = MEMORY")

    try:
        with engine.connect() as connection:
            upgrade(connection, name=name)
    finally:
        engine.dispose()


async def init_db():
    """Brings unos.db to the head of the migrations (upgrade): in the lifespan of src/main.py before
    the first request, and after a restore's swap."""
    async with engine.connect() as conn:
        await conn.run_sync(upgrade)


async def checkpoint_db():
    """At the end of the lifespan: copy unos.db-wal into unos.db, sync both and empty the log
    (PRAGMA wal_checkpoint(TRUNCATE)), so a stopped supervisor leaves every commit in unos.db
    (ADR-0306).

    The pool stays open, as SQLite deletes unos.db-wal and unos.db-shm when the last
    connection closes: kept, the next start makes no file, also on a full /data, and SQLite
    reads the database on a read-only /data. A checkpoint that cannot finish, such as while
    another process reads, leaves the commits in the log, where the next start finds them.
    """
    try:
        async with engine.connect() as conn:
            busy, _, _ = (await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")).one()
    except SQLAlchemyError as exc:
        logger.warning("The database's last commits stay in unos.db-wal: the checkpoint failed (%s)", exc)
        return
    if busy:
        logger.warning("The database's last commits stay in unos.db-wal: another connection was reading")

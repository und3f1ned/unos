import logging
import sqlite3
from typing import TYPE_CHECKING, ClassVar, cast

from sqlalchemy import CursorResult, Result, Table, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
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
    if TYPE_CHECKING:
        # Every model is a declared table: DeclarativeBase types __table__ as any FromClause.
        __table__: ClassVar[Table]


def rowcount(result: Result) -> int:
    """The rows that an UPDATE or DELETE matched: Session.execute returns a CursorResult for them,
    typed as the Result of a query, which has no rowcount."""
    return cast(CursorResult, result).rowcount


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    # Import models to ensure they're registered with Base
    from .auth.models import User  # noqa: F401
    from .security.models import LoginAttempt  # noqa: F401
    from .shares.models import SmbAccount, SmbShare, SmbShareAccess, SmbState, SmbUid  # noqa: F401
    from .apps.models import AppResourceLimit  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
            busy = (await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")).one()[0]  # (busy, log, checkpointed)
    except SQLAlchemyError as exc:
        logger.warning("The database's last commits stay in unos.db-wal: the checkpoint failed (%s)", exc)
        return
    if busy:
        logger.warning("The database's last commits stay in unos.db-wal: another connection was reading")

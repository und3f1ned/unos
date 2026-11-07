from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from .config import get_settings

settings = get_settings()

# How long a connection waits for SQLite's write lock before "database is locked"
# (sqlite3's default is 5 s). A share change holds the lock while testparm checks the
# new smb.conf, which can take longer when a pool's disk has to spin up.
BUSY_TIMEOUT_SECONDS = 15


def engine_options(db_path: str) -> dict:
    """The options of the app's engine for the database file ``db_path``.

    A file database gets a pool without a limit on extra connections: a
    request that holds smb_lock may need a second connection while every
    request waiting for the lock holds one (a limit would make the holder
    wait for a free connection, and fail after the pool timeout). An
    in-memory database (the tests) has a single shared connection, which
    takes no pool options.
    """
    options: dict = {"echo": False, "connect_args": {"timeout": BUSY_TIMEOUT_SECONDS}}
    if db_path not in ("", ":memory:"):
        options["max_overflow"] = -1
    return options


engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", **engine_options(settings.db_path))

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    # Import models to ensure they're registered with Base
    from .auth.models import User  # noqa: F401
    from .security.models import LoginAttempt  # noqa: F401
    from .shares.models import SmbAccount, SmbShare, SmbShareAccess, SmbState  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

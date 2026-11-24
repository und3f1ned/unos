"""What a small commit writes to unos.db's files (feature architecture.sqlite-write-cost, ADR-0306).

unos.db is on the system disk, an SD card on many boards (ADR-0214). The app's engine keeps it in
SQLite's write-ahead log with synchronous NORMAL: a small commit, such as the login history's row of
a sign-in, appends its pages to unos.db-wal and leaves unos.db alone until a checkpoint; only the
commit that starts the log again (the first after a start or a checkpoint) syncs it. With the
rollback journal and synchronous FULL, the same commit created, synced and deleted unos.db-journal
and wrote and synced its pages in unos.db as well.

The program below runs in a new interpreter, as the supervisor does: src.database builds its engine
from UNOS_DB_PATH when it is imported, and the tests' own database is in memory (conftest.py).
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src import database

SUPERVISOR = Path(__file__).resolve().parents[1]
# A frame of the write-ahead log: a header of 24 bytes and one page (https://www.sqlite.org/fileformat2.html).
WAL_FRAME_HEADER = 24
# The pages a sign-in's row changes: the leaf of login_attempts and one leaf of each of its two indexes.
SIGN_IN_PAGES = 3

PROGRAM = r"""
import asyncio, json, os
from src import database
from src.security.models import LoginAttempt

AGENT = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
path = database.settings.db_path


async def sign_in():
    async with database.async_session() as db:  # the session of every request (get_db)
        db.add(LoginAttempt(username="admin", ip_address="192.0.2.21", user_agent=AGENT, success=True))
        await db.commit()


def pages(data, size):
    return [data[i:i + size] for i in range(0, len(data), size)]


def wal_size():
    return os.path.getsize(path + "-wal") if os.path.exists(path + "-wal") else None


async def main():
    await database.init_db()  # the supervisor's start
    await sign_in()  # the first sign-in: SQLite has made the files it keeps
    async with database.engine.connect() as first, database.engine.connect() as second:
        settings = [[(await c.exec_driver_sql(f"PRAGMA {name}")).scalar() for name in ("journal_mode", "synchronous")]
                    for c in (first, second)]
        page_size = (await first.exec_driver_sql("PRAGMA page_size")).scalar()
    with open(path, "rb") as f:
        before, wal_before = f.read(), wal_size()
    await sign_in()
    with open(path, "rb") as f:
        after, wal_after = f.read(), wal_size()
    await database.engine.dispose()
    old, new = pages(before, page_size), pages(after, page_size)
    print(json.dumps({
        "connections": settings,
        "page_size": page_size,
        "changed_pages": [i for i in range(max(len(old), len(new))) if old[i:i + 1] != new[i:i + 1]],
        "wal_growth": None if wal_after is None else wal_after - (wal_before or 0),
    }))


asyncio.run(main())
"""


@pytest.fixture(scope="module")
def sign_in(tmp_path_factory):
    """Two sign-ins' rows committed through src.database.engine on a database file, as the
    supervisor builds it from UNOS_DB_PATH; what the second commit wrote, and the settings of two
    connections of the engine's pool."""
    data = tmp_path_factory.mktemp("data")
    env = {**os.environ, "UNOS_DB_PATH": str(data / "unos.db"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", PROGRAM], cwd=SUPERVISOR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout)


def test_every_connection_of_the_apps_engine_writes_ahead_with_synchronous_normal(sign_in):
    # PRAGMA synchronous reads NORMAL back as 1 (FULL, SQLite's default, as 2).
    assert sign_in["connections"] == [["wal", 1], ["wal", 1]]


def test_a_sign_ins_commit_leaves_unos_db_alone(sign_in):
    # With the rollback journal, the commit wrote the row's pages and the change counter
    # (page 0) into unos.db itself, each time.
    assert sign_in["changed_pages"] == []


def test_a_sign_ins_commit_appends_its_three_pages_to_the_wal(sign_in):
    frame = WAL_FRAME_HEADER + sign_in["page_size"]
    assert sign_in["wal_growth"] == SIGN_IN_PAGES * frame  # 12,360 bytes with pages of 4 KiB


STOP = r"""
import asyncio
from src import database, main
from src.security.models import LoginAttempt


async def run():
    async with main.lifespan(main.app):  # a start and a clean stop of the supervisor
        async with database.async_session() as db:
            db.add(LoginAttempt(username="admin", ip_address="192.0.2.21", success=True))
            await db.commit()


asyncio.run(run())
"""


def test_a_stopped_supervisor_leaves_every_commit_in_unos_db_and_keeps_its_log_files(tmp_path):
    """Without the stop's checkpoint, a stopped supervisor left its last commits in unos.db-wal:
    a copy of unos.db alone, such as update.sh makes of a supervisor that is not running, lacked
    them. Closing the pool instead would delete unos.db-wal and unos.db-shm, which the next start
    would have to make again, and cannot on a full or read-only /data."""
    env = {**os.environ, "UNOS_DB_PATH": str(tmp_path / "unos.db"), "UNOS_DISABLE_BACKGROUND_TASKS": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", STOP], cwd=SUPERVISOR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]

    assert sorted(os.listdir(tmp_path)) == ["unos.db", "unos.db-shm", "unos.db-wal"]
    assert (tmp_path / "unos.db-wal").stat().st_size == 0  # the checkpoint emptied the log
    # immutable=1: SQLite reads unos.db only, as a copy of it alone is read.
    with contextlib.closing(sqlite3.connect(f"{(tmp_path / 'unos.db').as_uri()}?immutable=1", uri=True)) as conn:
        assert conn.execute("SELECT username FROM login_attempts").fetchall() == [("admin",)]


def test_a_connection_that_keeps_another_journal_mode_says_so(caplog):
    """SQLite answers PRAGMA journal_mode with the mode it keeps: an in-memory database keeps
    'memory'. The connection goes on with it, and the log names it."""
    with contextlib.closing(sqlite3.connect(":memory:")) as conn:
        database.write_ahead(conn, None)

        assert conn.execute("PRAGMA synchronous").fetchone() == (2,)  # FULL: NORMAL only in the log
    assert [r.getMessage() for r in caplog.records if r.name == "src.database"] == [
        "The database is not in the write-ahead log: SQLite keeps the journal mode memory"
    ]


def test_a_database_that_cannot_switch_to_the_log_is_still_read(tmp_path, caplog):
    """A read-only or full /data before the first switch (the first start after a deploy):
    SQLite refuses to write the new journal mode into the file. The connection keeps the
    rollback journal, with synchronous FULL, and reads as before (a sign-in still answers,
    ADR-0251), and the log says why; a later connection switches once it can."""
    path = tmp_path / "unos.db"
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE users (name TEXT)")
        conn.execute("INSERT INTO users VALUES ('admin')")
    with contextlib.closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        database.write_ahead(conn, None)

        assert conn.execute("SELECT name FROM users").fetchall() == [("admin",)]
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert conn.execute("PRAGMA synchronous").fetchone() == (2,)
    assert [r.getMessage() for r in caplog.records if r.name == "src.database"] == [
        "The database is not in the write-ahead log: attempt to write a readonly database"
    ]

"""Slice 2 foundation: the SMB tables (ADR-0077), the fixed values, the SMB state derived
for a user, the types shared by the shares modules, and the signatures of the functions that
other code is written against (SIGNATURES)."""
import asyncio
import inspect
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import asynccontextmanager, closing
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import APIRouter
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src import database
from src.auth.models import APIToken, User
from src.database import Base
from src.security.models import LoginAttempt
from src.shares import accounts, constants, interfaces, models, repair_router, service, smb
from src.shares.account_state import (
    ADMIN_REASON,
    SmbAccountState,
    SmbAccountStatus,
    derive_smb_state,
)
from src.shares.constants import (
    NAME_RULE,
    is_valid_linux_name,
    linux_name_problem,
    share_root_mode_ok,
    smb_password_problem,
)
from src.shares.models import SmbAccount, SmbShare, SmbShareAccess, SmbState

SUPERVISOR = Path(__file__).resolve().parents[1]
OLD_TABLES = {"users", "api_tokens", "login_attempts"}  # the database of the release before slice 2
SMB_TABLES = {"smb_shares", "smb_share_access", "smb_accounts", "smb_state"}
HASH = "$2b$12$" + "x" * 53  # bcrypt-shaped placeholder; nothing signs in with it
SET_AT = datetime(2026, 11, 6, 12, 30)


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _schema(path: Path, table: str) -> str:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]


def _rows(path: Path, table: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def _execute(path: Path, *statements: str) -> None:
    """Write with plain SQL, as the restore preparation and hand-made databases do."""
    with closing(sqlite3.connect(path)) as conn:
        for sql in statements:
            conn.execute(sql)
        conn.commit()


def _add(engine, *rows) -> None:
    """Insert ``rows`` in one transaction; IntegrityError when SQLite refuses one."""
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


@asynccontextmanager
async def _sessions(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _init_db_on(path: Path, monkeypatch) -> None:
    """database.init_db() (startup, and after a restore) on the database file at ``path``."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    monkeypatch.setattr(database, "engine", engine)
    try:
        await database.init_db()
    finally:
        await engine.dispose()


def _run_fresh(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run ``script`` in a new interpreter, where nothing has imported src.shares yet (as at startup)."""
    env = {**os.environ, "UNOS_DB_PATH": str(tmp_path / "fresh.db"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-c", script], cwd=SUPERVISOR, env=env,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture
def old_db(tmp_path) -> Path:
    """A database of the release before slice 2: users, api_tokens and login_attempts, one user."""
    path = tmp_path / "unos.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=[User.__table__, APIToken.__table__, LoginAttempt.__table__])
    with Session(engine) as session:
        session.add(User(username="alice", hashed_password=HASH, is_admin=False))
        session.commit()
    engine.dispose()
    return path


@pytest.fixture
def db_path(tmp_path) -> Path:
    """A database with every table and the users alice (id 1) and bob (id 2)."""
    path = tmp_path / "unos.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(username="alice", hashed_password=HASH), User(username="bob", hashed_password=HASH)])
        session.commit()
    engine.dispose()
    return path


@pytest.fixture
def db(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    yield engine
    engine.dispose()


class TestTablesOnAnOldDatabase:
    """init_db() runs create_all at startup and after a restore (ADR-0077)."""

    async def test_init_db_adds_the_four_tables_and_leaves_the_user_alone(self, old_db, monkeypatch):
        users = _rows(old_db, "users")
        users_schema = _schema(old_db, "users")
        assert _tables(old_db) == OLD_TABLES
        assert len(users) == 1

        await _init_db_on(old_db, monkeypatch)

        # And the sessions' table (ADR-0329), new beside them.
        assert _tables(old_db) == OLD_TABLES | SMB_TABLES | {"user_sessions"}
        assert _rows(old_db, "users") == users
        assert _schema(old_db, "users") == users_schema  # no column added to users
        # Nothing is written into the new tables: the SmbState row is made on first use.
        assert {table: _rows(old_db, table) for table in SMB_TABLES} == {table: [] for table in SMB_TABLES}

    async def test_init_db_again_keeps_the_smb_rows(self, old_db, monkeypatch):
        await _init_db_on(old_db, monkeypatch)
        _execute(
            old_db,
            "INSERT INTO smb_shares (name, path) VALUES ('media', '/mnt/pools/data/media')",
            "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 1, 'alice', 'write')",
            "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state)"
            " VALUES (1, 'alice', 70000, 1, 'set')",
            "INSERT INTO smb_state (id, next_uid) VALUES (1, 70001)",
        )
        rows = {table: _rows(old_db, table) for table in SMB_TABLES}

        await _init_db_on(old_db, monkeypatch)

        assert {table: _rows(old_db, table) for table in SMB_TABLES} == rows

    def test_init_db_registers_the_smb_models_itself(self, tmp_path):
        """At startup nothing else has imported shares.models: database.init_db() must (ADR-0077)."""
        result = _run_fresh(
            "import asyncio\n"
            "from src import database\n"
            "async def main():\n"
            "    await database.init_db()\n"
            "    await database.engine.dispose()\n"
            "asyncio.run(main())\n",
            tmp_path,
        )

        assert result.returncode == 0, result.stderr
        assert SMB_TABLES <= _tables(tmp_path / "fresh.db")

    def test_leaf_modules_import_no_other_shares_module(self, tmp_path):
        """constants, models, account_state and interfaces are leaf modules: they import only each other."""
        result = _run_fresh(
            "import sys\n"
            "import src.shares.constants, src.shares.models, src.shares.account_state, src.shares.interfaces\n"
            "print(*sorted(name for name in sys.modules if name.startswith('src.shares.')))\n",
            tmp_path,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == [
            "src.shares.account_state", "src.shares.constants", "src.shares.interfaces", "src.shares.models",
        ]


class TestConstraints:
    """The constraints hold in SQLite itself, also for rows written without the ORM."""

    def test_share_names_are_unique_whatever_their_case(self, db):
        _add(db, SmbShare(name="Media", path="/mnt/pools/data/media"))

        for name in ("media", "MEDIA"):
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed: smb_shares.name"):
                _add(db, SmbShare(name=name, path="/mnt/pools/data/other"))
        with Session(db) as session:
            assert session.execute(select(SmbShare).where(SmbShare.name == "MEDIA")).scalar_one().name == "Media"

    def test_share_defaults(self, db, db_path):
        _add(db, SmbShare(name="media", path="/mnt/pools/data/media"))
        _execute(db_path, "INSERT INTO smb_shares (name, path) VALUES ('docs', '/mnt/pools/data/docs')")

        with Session(db) as session:
            shares = session.execute(select(SmbShare).order_by(SmbShare.id)).scalars().all()
            assert [(s.name, s.version, s.guest_ok, s.browseable, s.comment, s.import_notes) for s in shares] == [
                ("media", 1, False, True, "", None),
                ("docs", 1, False, True, "", None),
            ]
            assert all(s.created_at is not None for s in shares)

    @pytest.mark.parametrize("level", ["read", "write"])
    def test_access_levels_read_and_write_are_accepted(self, db, level):
        _add(db, SmbShare(id=1, name="media", path="/mnt/pools/data/media"),
             SmbShareAccess(share_id=1, user_id=1, username="alice", level=level))

    @pytest.mark.parametrize("level", ["admin", "READ", "full", ""])
    def test_other_access_levels_are_refused(self, db, level):
        _add(db, SmbShare(id=1, name="media", path="/mnt/pools/data/media"))

        with pytest.raises(IntegrityError, match="ck_smb_share_access_level"):
            _add(db, SmbShareAccess(share_id=1, user_id=1, username="alice", level=level))

    def test_access_rows_need_the_username(self, db, db_path):
        _add(db, SmbShare(id=1, name="media", path="/mnt/pools/data/media"))

        with pytest.raises(IntegrityError, match="NOT NULL constraint failed: smb_share_access.username"):
            _add(db, SmbShareAccess(share_id=1, user_id=1, level="read"))
        with pytest.raises(sqlite3.IntegrityError, match="smb_share_access.username"):
            _execute(db_path, "INSERT INTO smb_share_access (share_id, user_id, level) VALUES (1, 1, 'read')")

    def test_one_access_row_per_user_and_share(self, db):
        _add(db, SmbShare(id=1, name="media", path="/mnt/pools/data/media"),
             SmbShareAccess(share_id=1, user_id=1, username="alice", level="read"))

        with pytest.raises(IntegrityError, match="smb_share_access.share_id, smb_share_access.user_id"):
            _add(db, SmbShareAccess(share_id=1, user_id=1, username="alice", level="write"))

    @pytest.mark.parametrize("uid", [None, 70000, 70001, 79999])
    def test_uids_in_the_range_are_accepted(self, db, uid):
        _add(db, SmbAccount(user_id=1, linux_name="alice", uid=uid))

    @pytest.mark.parametrize("uid", [0, 1000, 20001, 69999, 80000, 100000])
    def test_uids_outside_the_range_are_refused(self, db, uid):
        with pytest.raises(IntegrityError, match="ck_smb_accounts_uid_range"):
            _add(db, SmbAccount(user_id=1, linux_name="alice", uid=uid))

    def test_one_account_per_user_name_and_uid(self, db):
        _add(db, SmbAccount(user_id=1, linux_name="alice", uid=70000))

        with pytest.raises(IntegrityError, match="smb_accounts.user_id"):
            _add(db, SmbAccount(user_id=1, linux_name="alice2"))
        with pytest.raises(IntegrityError, match="smb_accounts.linux_name"):
            _add(db, SmbAccount(user_id=2, linux_name="alice"))
        with pytest.raises(IntegrityError, match="smb_accounts.uid"):
            _add(db, SmbAccount(user_id=2, linux_name="bob", uid=70000))

    def test_account_defaults(self, db, db_path):
        _add(db, SmbAccount(user_id=1, linux_name="alice"))
        _execute(db_path, "INSERT INTO smb_accounts (user_id, linux_name) VALUES (2, 'bob')")

        with Session(db) as session:
            accounts_ = session.execute(select(SmbAccount).order_by(SmbAccount.user_id)).scalars().all()
            assert [(a.linux_name, a.enabled, a.password_state, a.uid, a.password_set_at) for a in accounts_] == [
                ("alice", False, "needed", None, None),
                ("bob", False, "needed", None, None),
            ]

    @pytest.mark.parametrize("password_state", ["unknown", "SET", ""])
    def test_password_state_is_set_or_needed(self, db, password_state):
        _add(db, SmbAccount(user_id=1, linux_name="alice", password_state="set"),
             SmbAccount(user_id=2, linux_name="bob", password_state="needed"))

        with pytest.raises(IntegrityError, match="ck_smb_accounts_password_state"):
            _add(db, SmbAccount(user_id=3, linux_name="carol", password_state=password_state))

    def test_smb_state_has_only_the_row_1(self, db, db_path):
        with pytest.raises(IntegrityError, match="ck_smb_state_singleton"):
            _add(db, SmbState(id=2))

        _execute(db_path, "INSERT INTO smb_state (id) VALUES (1)")
        assert _rows(db_path, "smb_state") == [(1, 70000, None, None, None)]

    @pytest.mark.parametrize("source", ["live", None])
    def test_pending_import_sources_accepted(self, db, source):
        _add(db, SmbState(id=1, pending_import_source=source))

    # A restore imports from the live file alone (ADR-0319): no source names a backup's smb.conf.
    @pytest.mark.parametrize("source", ["backup", "tape", "LIVE", "file", ""])
    def test_other_pending_import_sources_are_refused(self, db, source):
        with pytest.raises(IntegrityError, match="ck_smb_state_pending_import_source"):
            _add(db, SmbState(id=1, pending_import_source=source))

    async def test_deleting_a_share_deletes_its_access_rows(self, db, db_path):
        """db.delete(share): the ORM cascade removes the access rows (no SQLite foreign keys)."""
        _add(db, SmbShare(id=1, name="media", path="/mnt/pools/data/media"),
             SmbShare(id=2, name="docs", path="/mnt/pools/data/docs"),
             SmbShareAccess(share_id=1, user_id=1, username="alice", level="write"),
             SmbShareAccess(share_id=1, user_id=2, username="bob", level="read"),
             SmbShareAccess(share_id=2, user_id=2, username="bob", level="write"))

        async with _sessions(db_path) as maker:
            async with maker() as session:
                share = (await session.execute(select(SmbShare).where(SmbShare.name == "MEDIA"))).scalar_one()
                await session.delete(share)
                await session.commit()

        assert [(row[1], row[2]) for row in _rows(db_path, "smb_share_access")] == [(2, 2)]
        assert [row[1] for row in _rows(db_path, "smb_shares")] == ["docs"]


class TestGetSmbState:
    async def test_made_on_first_use_and_written_only_by_the_callers_commit(self, db_path):
        async with _sessions(db_path) as maker:
            async with maker() as session:
                state = await models.get_smb_state(session)
                assert (state.id, state.next_uid, state.shares_imported_at, state.import_report,
                        state.pending_import_source) == (1, 70000, None, None, None)
                assert await models.get_smb_state(session) is state
                await session.rollback()
            assert _rows(db_path, "smb_state") == []  # flushed, not committed

            async with maker() as session:
                await models.get_smb_state(session)
                await session.commit()
            assert _rows(db_path, "smb_state") == [(1, 70000, None, None, None)]

    async def test_an_existing_row_is_returned_as_stored(self, db_path):
        _execute(db_path, "INSERT INTO smb_state (id, next_uid, import_report) VALUES (1, 70042, '[]')")

        async with _sessions(db_path) as maker:
            async with maker() as session:
                state = await models.get_smb_state(session)

                assert (state.next_uid, state.import_report) == (70042, "[]")
                assert not session.new and not session.dirty


def _user(username: str = "alice", *, is_admin: bool = False) -> User:
    return User(username=username, hashed_password=HASH, is_admin=is_admin)


def _account(**changes) -> SmbAccount:
    """The row of an account that is active for alice, unless ``changes`` say otherwise."""
    values = {"user_id": 1, "linux_name": "alice", "uid": 70001, "enabled": True,
              "password_state": "set", "password_set_at": SET_AT}
    return SmbAccount(**{**values, **changes})


class TestDeriveSmbState:
    def test_state_values(self):
        assert [state.value for state in SmbAccountState] == ["active", "password_needed", "off", "unavailable"]

    def test_an_administrator_is_unavailable_even_with_an_active_row(self):
        assert derive_smb_state(_user(is_admin=True), _account()) == SmbAccountStatus(
            state=SmbAccountState.UNAVAILABLE, allowed=False, reason=ADMIN_REASON
        )

    @pytest.mark.parametrize("username, reason", [
        ("Bob.Smith", "The username 'Bob.Smith' cannot be used for SMB. Use 1 to 32 characters: lowercase "
                      "letters, digits, '_' and '-', starting with a letter."),
        ("root", "The username 'root' cannot be used for SMB. 'root' is reserved for the system."),
        # An existing (or restored) user with a name Samba reads as a SID: unavailable, never rendered.
        ("s-1-1-0", "The username 's-1-1-0' cannot be used for SMB. Names that start with 's-' and a digit "
                    "are reserved: Samba reads them as security identifiers (SIDs)."),
    ])
    def test_a_username_smb_cannot_use_is_unavailable(self, username, reason):
        status = derive_smb_state(_user(username), _account(linux_name=username))

        assert status == SmbAccountStatus(state=SmbAccountState.UNAVAILABLE, allowed=False, reason=reason)

    def test_no_row_is_off(self):
        assert derive_smb_state(_user(), None) == SmbAccountStatus(state=SmbAccountState.OFF, allowed=False)

    def test_a_row_that_is_not_enabled_is_off(self):
        status = derive_smb_state(_user(), _account(enabled=False))

        assert status == SmbAccountStatus(state=SmbAccountState.OFF, allowed=False)

    def test_password_needed(self):
        status = derive_smb_state(_user(), _account(password_state="needed"))

        assert status == SmbAccountStatus(
            state=SmbAccountState.PASSWORD_NEEDED, allowed=True, linux_name="alice", uid=70001
        )

    @pytest.mark.parametrize("uid", [
        pytest.param(None, id="no-uid"),
        pytest.param(20001, id="20001"),
        pytest.param(69999, id="69999"),
        pytest.param(80000, id="80000"),
        pytest.param("70001", id="text"),
    ])
    def test_set_without_a_uid_in_the_range_needs_a_password(self, uid):
        status = derive_smb_state(_user(), _account(uid=uid))

        assert status == SmbAccountStatus(state=SmbAccountState.PASSWORD_NEEDED, allowed=True, linux_name="alice")

    def test_set_with_another_linux_name_needs_a_password(self):
        status = derive_smb_state(_user(), _account(linux_name="alicia"))

        assert status == SmbAccountStatus(
            state=SmbAccountState.PASSWORD_NEEDED, allowed=True, linux_name="alice", uid=70001
        )

    @pytest.mark.parametrize("uid", [70000, 70001, 79999])
    def test_active(self, uid):
        status = derive_smb_state(_user(), _account(uid=uid))

        assert status == SmbAccountStatus(
            state=SmbAccountState.ACTIVE, allowed=True, linux_name="alice", uid=uid, password_set_at=SET_AT
        )


class TestConstants:
    def test_ids_and_modes_are_fixed(self):
        """They end up in the ownership of files on the pools and in the host's account files."""
        assert (constants.SMB_GROUP_NAME, constants.SMB_GROUP_GID) == ("unos-smb", 70000)
        assert (constants.SMB_UID_MIN, constants.SMB_UID_MAX) == (70000, 79999)
        assert (constants.SMB_GECOS, constants.SMB_SHELL, constants.SMB_HOME) == (
            "UNOS SMB user", "/usr/sbin/nologin", "/nonexistent"
        )
        assert (constants.SHARE_DIR_MODE, constants.SHARE_FILE_MODE) == (0o2770, 0o660)
        assert share_root_mode_ok(stat.S_IFDIR | constants.SHARE_DIR_MODE, constants.SMB_GROUP_GID)

    def test_the_user_of_apps_in_the_group_is_no_one_elses(self):
        """The user of the apps that run in unos-smb (ADR-0274) owns what they write in share folders."""
        assert constants.SMB_APP_UID == 80000
        # No SMB account, and no account made with useradd (UID_MAX 60000 in
        # login.defs), below SUB_UID_MIN (100000), and in the block that
        # systemd never allocates (65536-524287), as the SMB ids (ADR-0107).
        assert not constants.SMB_UID_MIN <= constants.SMB_APP_UID <= constants.SMB_UID_MAX
        assert 65536 <= constants.SMB_APP_UID < 100000

    @pytest.mark.parametrize("name", ["alice", "a", "a" * 32, "e2e_user_abc", "bob-smith", "x9", "admin"])
    def test_valid_linux_names(self, name):
        assert linux_name_problem(name) is None
        assert is_valid_linux_name(name)

    @pytest.mark.parametrize("name", [
        "", "Alice", "1abc", "-o", "_alice", "a" * 33, "bob.smith", "bob smith", "alice\n", "x\ny",
        "machine$", "ålice", "alice\x00", None, 70001,
    ])
    def test_names_outside_the_pattern(self, name):
        assert linux_name_problem(name) == f"Use {NAME_RULE}"
        assert not is_valid_linux_name(name)

    @pytest.mark.parametrize("name", [
        "root", "nobody", "www-data", "docker", "sambashare", "unos", "unos-smb", "systemd-network", "systemd-x",
        # wsdd2.service runs as the dynamic user named after it (DynamicUser=true). A static
        # account of that name takes its place: an SMB host account wsdd2 would give the
        # network-facing daemon the group unos-smb and with it the share folders (ADR-0275).
        "wsdd2",
    ])
    def test_reserved_names(self, name):
        assert linux_name_problem(name) == f"'{name}' is reserved for the system"
        assert not is_valid_linux_name(name)

    @pytest.mark.parametrize("name", [
        "s-1-1-0",          # Everyone
        "s-1-0x1-0",        # the identifier authority in hexadecimal: also S-1-1-0
        "s-1-22-2-70000",   # Unix Group unos-smb: the primary group of every SMB account
        "s-1-5-32-545",     # BUILTIN\Users
        "s-1-5-11", "s-1", "s-0", "s-1-1-0abc",
    ])
    def test_names_samba_reads_as_sids(self, name):
        assert linux_name_problem(name) == (
            "Names that start with 's-' and a digit are reserved: Samba reads them as security identifiers (SIDs)"
        )
        assert linux_name_problem(name) == constants.SID_LIKE_RULE
        assert not is_valid_linux_name(name)

    @pytest.mark.parametrize("name", ["s1", "sam", "s-a", "s", "s_1-1-0", "s-x1", "bs-1-1-0", "sid-1-1-0"])
    def test_names_close_to_a_sid_are_valid(self, name):
        assert linux_name_problem(name) is None
        assert is_valid_linux_name(name)

    @pytest.mark.parametrize("password", [
        pytest.param("8 chars!", id="8-characters"),
        pytest.param("a" * 72, id="72-bytes"),
        pytest.param("ä" * 36, id="36-characters-72-bytes"),
        pytest.param("correct horse battery staple", id="spaces"),
    ])
    def test_smb_passwords_accepted(self, password):
        assert smb_password_problem(password) is None

    @pytest.mark.parametrize("password, problem", [
        pytest.param("seven77", "SMB passwords must have at least 8 characters", id="7-characters"),
        pytest.param("a" * 73, "SMB passwords must not be longer than 72 bytes (UTF-8)", id="73-bytes"),
        pytest.param("ä" * 37, "SMB passwords must not be longer than 72 bytes (UTF-8)",
                     id="37-characters-74-bytes"),
        pytest.param("tab\tpassword", "SMB passwords must not contain control characters such as tabs or line "
                                      "breaks", id="tab"),
        pytest.param("new\nline-pass", "SMB passwords must not contain control characters such as tabs or line "
                                       "breaks", id="newline"),
        pytest.param("carriage\rreturn", "SMB passwords must not contain control characters such as tabs or "
                                         "line breaks", id="carriage-return"),
        pytest.param("nul\x00password", "SMB passwords must not contain control characters such as tabs or line "
                                        "breaks", id="nul"),
        pytest.param("\ud800surrogate", "SMB passwords must be valid Unicode text", id="lone-surrogate"),
    ])
    def test_smb_passwords_refused(self, password, problem):
        assert smb_password_problem(password) == problem
        assert password not in problem  # the message never carries the password

    @pytest.mark.parametrize("password", [
        "password", "Password1", "PASSWORD123", "P@ssw0rd", "12345678", "1234567890", "11111111",
        "qwertyuiop", "1q2w3e4r", "Iloveyou", "sunshine", "Summer2024!", "Welcome@123", "abcd1234",
    ])
    def test_a_common_password_is_refused_with_the_reason_in_any_case(self, password):
        """shares.smb-login-protection, step 2 (ADR-0279)."""
        assert smb_password_problem(password) == (
            "SMB passwords must not be common passwords: this one is on the list of passwords that attackers "
            "try first"
        )
        assert smb_password_problem(password) == constants.COMMON_PASSWORD_RULE
        # The web UI shows its hint to change the password after every "SMB passwords must ..." answer.
        assert constants.COMMON_PASSWORD_RULE.startswith("SMB passwords must")

    @pytest.mark.parametrize("password", ["password-for-the-nas", "Sunshine on 7 hills", "qwertyuiop[]", " password"])
    def test_a_password_that_only_contains_a_common_one_is_taken(self, password):
        assert smb_password_problem(password) is None

    def test_the_other_rules_come_first(self):
        assert smb_password_problem("letmein") == "SMB passwords must have at least 8 characters"
        assert smb_password_problem("password\t") == (
            "SMB passwords must not contain control characters such as tabs or line breaks"
        )

    def test_a_password_that_goes_back_to_samba_is_not_checked_against_the_list(self):
        """``restoring``: the password Samba held before a change that failed goes back to it (ADR-0124),
        also a common one that an account got before the list; the other rules still apply (ADR-0279)."""
        assert smb_password_problem("Password123") == constants.COMMON_PASSWORD_RULE
        assert smb_password_problem("Password123", restoring=True) is None
        assert smb_password_problem("letmein", restoring=True) == "SMB passwords must have at least 8 characters"
        assert smb_password_problem("password\n", restoring=True) == (
            "SMB passwords must not contain control characters such as tabs or line breaks"
        )

    def test_the_list_of_common_passwords(self):
        """common_passwords.txt: a comment header, then one password per line, in lower case (casefolded),
        sorted and unique, each one that the other rules would take; it ships with the code (src/)."""
        path = SUPERVISOR / "src" / "shares" / "common_passwords.txt"
        lines = path.read_text(encoding="utf-8").split("\n")
        header = [line for line in lines if line.startswith("#")]
        entries = [line for line in lines if line and not line.startswith("#")]

        assert lines[: len(header)] == header and header, "the comment header comes first"
        assert lines[-1] == "" and "" not in lines[len(header):-1]
        assert "GNU General Public License" in " ".join(header) and "ADR-0279" in " ".join(header)
        assert entries == sorted(set(entries)), "sorted, without duplicates"
        assert [e for e in entries if e != e.casefold() or e != e.strip() or not e.isprintable()] == []
        assert [e for e in entries if len(e) < constants.SMB_PASSWORD_MIN_CHARS] == []
        assert len(entries) >= 500
        assert constants.common_passwords() == frozenset(entries)

    @pytest.mark.parametrize("mode, gid, ok", [
        pytest.param(stat.S_IFDIR | 0o2770, 70000, True, id="2770-unos-smb"),
        pytest.param(stat.S_IFDIR | 0o2770, 20000, False, id="other-group"),
        pytest.param(stat.S_IFDIR | 0o2770, 0, False, id="root-group"),
        pytest.param(stat.S_IFDIR | 0o0770, 70000, False, id="no-setgid"),
        pytest.param(stat.S_IFDIR | 0o2750, 70000, False, id="group-cannot-write"),
        pytest.param(stat.S_IFDIR | 0o2775, 70000, False, id="others-can-read"),
        pytest.param(stat.S_IFDIR | 0o2777, 70000, False, id="world-writable"),
        pytest.param(stat.S_IFREG | 0o2770, 70000, False, id="not-a-folder"),
    ])
    def test_share_root_mode_ok(self, mode, gid, ok):
        assert share_root_mode_ok(mode, gid) is ok


def _prev(**changes) -> interfaces.PrevAccount:
    values = {"user_id": 1, "username": "alice", "hashed_password": HASH, "is_admin": False,
              "linux_name": "alice", "uid": 70001, "enabled": True, "password_state": "set"}
    return interfaces.PrevAccount(**{**values, **changes})


class TestInterfaces:
    def test_prev_account_repr_hides_the_password_hash(self):
        prev = _prev()

        assert prev.hashed_password == HASH
        for text in (repr(prev), str(prev)):
            assert HASH not in text and "$2b$" not in text and "hashed_password" not in text
            assert "alice" in text

    @pytest.mark.parametrize("changes, active", [
        ({}, True),
        ({"enabled": False}, False),
        ({"password_state": "needed"}, False),
        ({"is_admin": True}, False),
        ({"uid": None}, False),
    ])
    def test_prev_account_active(self, changes, active):
        assert _prev(**changes).active is active

    def test_restore_snapshot(self):
        snapshot = interfaces.RestoreSnapshot()

        assert (snapshot.accounts, snapshot.next_uid) == ((), 70000)
        with pytest.raises(FrozenInstanceError):
            snapshot.next_uid = 1
        with pytest.raises(FrozenInstanceError):
            _prev().uid = 1

    def test_result_lists_are_independent(self):
        first, second = interfaces.ReconcileResult(), interfaces.ReconcileResult()
        first.changes.append("Removed the host account bob")
        first.problems.append("Samba has a user that UNOS did not create: nas")
        assert (second.changes, second.problems) == ([], [])

        applied = interfaces.ApplyResult()
        applied.closed_shares.append("media")
        applied.warnings.append("smbd did not reload")
        assert interfaces.ApplyResult() == interfaces.ApplyResult(
            changed=False, reloaded=None, closed_shares=[], warnings=[]
        )

        status = interfaces.AccountHostStatus()
        status.unmanaged_samba_users.append("nas")
        assert interfaces.AccountHostStatus() == interfaces.AccountHostStatus(
            tools_missing=None, group_problem=None, unmanaged_samba_users=[]
        )

    def test_smb_session_is_frozen(self):
        session = interfaces.SmbSession(pid=48211, username="alice", shares=("IPC$", "media"))

        with pytest.raises(FrozenInstanceError):
            session.pid = 1
        with pytest.raises(FrozenInstanceError):
            session.shares = ()
        assert {session, interfaces.SmbSession(48211, "alice", ("IPC$", "media"))} == {session}

    def test_restore_context_and_post_restore_do_not_exist(self):
        """Removed: the restore prepares the extracted database instead (ADR-0139)."""
        assert not hasattr(interfaces, "RestoreContext")
        assert not hasattr(interfaces, "PostRestore")


_POS = inspect.Parameter.POSITIONAL_OR_KEYWORD
_KW = inspect.Parameter.KEYWORD_ONLY

# The functions of the SMB service and accounts modules that other code is written against
# (slice 2 began them as stubs): async or not, parameters (name, kind, annotation), and the
# return annotation.
SIGNATURES = {
    "service.apply_smb_config": (True, [("db", _POS, "AsyncSession")], "interfaces.ApplyResult"),
    "service.reconcile_smb_on_startup": (True, [], "None"),
    "service.after_database_restore_locked": (
        True, [("db", _POS, "AsyncSession")], "tuple[list[str], bool]"
    ),
    "service.delete_access_of_user": (
        True, [("db", _POS, "AsyncSession"), ("user_id", _POS, "int")], "int"
    ),
    "service.share_problem": (
        True, [("db", _POS, "AsyncSession"), ("share_id", _POS, "int")], "str | None"
    ),
    "service.share_path_problem": (
        False,
        [("path", _POS, "object"), ("other_paths", _KW, "Mapping[str, str]"),
         ("pool_names", _KW, "Collection[str]")],
        "str | None"
    ),
    "accounts.snapshot_for_restore": (
        False, [("db_path", _POS, "pathlib.Path | None")], "interfaces.RestoreSnapshot"
    ),
    "accounts.prepare_restored_accounts": (
        False,
        [("conn", _POS, "sqlite3.Connection"), ("snapshot", _POS, "interfaces.RestoreSnapshot"),
         ("authoritative", _POS, "bool")],
        "list[str]"
    ),
    "accounts.reconcile_accounts": (True, [("db", _POS, "AsyncSession")], "interfaces.ReconcileResult"),
    "accounts.host_status": (False, [], "interfaces.AccountHostStatus"),
    "accounts.gid_problem": (False, [], "str | None"),
    "accounts.read_smb_sessions": (False, [], "list[interfaces.SmbSession] | None"),
    "accounts.end_smb_sessions": (False, [("names", _POS, "Iterable[str]")], "list[str]"),
}


def _function(name: str):
    module, function = name.split(".")
    return getattr({"service": service, "accounts": accounts}[module], function)


class TestSignatures:
    @pytest.mark.parametrize("name", list(SIGNATURES))
    def test_signature(self, name):
        is_async, parameters, returns = SIGNATURES[name]
        function = _function(name)
        signature = inspect.signature(function)

        assert [(p.name, p.kind, p.annotation, p.default) for p in signature.parameters.values()] == [
            (parameter, kind, annotation, inspect.Parameter.empty) for parameter, kind, annotation in parameters
        ]
        assert signature.return_annotation == returns
        assert inspect.iscoroutinefunction(function) is is_async

    def test_pool_list_unavailable_is_a_samba_config_error(self):
        assert issubclass(service.PoolListUnavailable, smb.SambaConfigError)

    def test_smb_lock_is_one_lock_per_event_loop(self):
        async def twice():
            return service.smb_lock(), service.smb_lock()

        first, again = asyncio.run(twice())
        other, _ = asyncio.run(twice())

        assert isinstance(first, asyncio.Lock)
        assert first is again
        assert other is not first
        assert str(inspect.signature(service.smb_lock)) == "() -> 'asyncio.Lock'"
        assert not inspect.iscoroutinefunction(service.smb_lock)

    def test_repair_router(self):
        assert isinstance(repair_router.router, APIRouter)
        assert repair_router.router.prefix == "/shares"

"""Rendering smb.conf from the database and applying it (ADR-0088, ADR-0089).

Each test has its own database file, pools base and smb.conf. Host commands
go to ``FakeSmbHost`` behind ``src.host.run_on_host``; the pool list is
``{"data"}`` (``storage.pools.managed_pool_names`` patched) unless a test says
otherwise. alice, bob and carol have active SMB accounts, dave has none and
admin is an administrator.
"""

import asyncio
import logging
import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import src.host
from src.audit import router as audit_router
from src.auth.models import User
from src.database import Base
from src.host import HostResult
from src.security import validators
from src.shares import service, smb, smbconf
from src.shares.models import SmbAccount, SmbShare, SmbShareAccess, SmbState
from src.storage import pools

HASH = "$2b$12$" + "x" * 53
ALICE, BOB, CAROL, DAVE, ADMIN = 1, 2, 3, 4, 5


class FakeSmbHost:
    """mkdir, testparm, smbcontrol and systemctl of the apply; ``fail`` maps an argv prefix
    to a HostResult, an exception or a function of the argv."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail: dict[tuple[str, ...], object] = {}
        self.tested: list[tuple[str, str]] = []

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        assert "close-denied-share" not in cmd, "close-denied-share is never used (ADR-0103)"
        for prefix, outcome in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome(cmd) if callable(outcome) else outcome
        if cmd[0] == "testparm":
            self.tested.append((cmd[-1], Path(cmd[-1]).read_text()))
            return HostResult(0, "", "Loaded services file OK.\n")
        if cmd[0] in ("mkdir", "smbcontrol", "systemctl"):
            return HostResult(0, "", "")
        raise AssertionError(f"unexpected host command: {cmd}")

    def closed(self) -> list[str]:
        return [cmd[3] for cmd in self.calls if cmd[:3] == ["smbcontrol", "smbd", "close-share"]]

    def reloads(self) -> int:
        return sum(cmd == ["smbcontrol", "all", "reload-config"] for cmd in self.calls)


def make_database(path: Path, *, imported: bool = True) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for user_id, name, admin in [(ALICE, "alice", False), (BOB, "bob", False), (CAROL, "carol", False),
                                     (DAVE, "dave", False), (ADMIN, "admin", True)]:
            session.add(User(id=user_id, username=name, hashed_password=HASH, is_admin=admin))
        for user_id, name in [(ALICE, "alice"), (BOB, "bob"), (CAROL, "carol")]:
            session.add(SmbAccount(user_id=user_id, linux_name=name, uid=70000 + user_id, enabled=True,
                                   password_state="set"))
        if imported:
            session.add(SmbState(id=1, next_uid=70004, shares_imported_at=datetime(2026, 11, 6, 12)))
        session.commit()
    engine.dispose()


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "pools"
    (base / "data").mkdir(parents=True)
    samba = tmp_path / "samba"
    samba.mkdir()
    conf = samba / "smb.conf"
    db_path = tmp_path / "unos.db"
    make_database(db_path)
    for settings in {id(smb.settings): smb.settings, id(validators.settings): validators.settings}.values():
        monkeypatch.setattr(settings, "smb_conf_path", str(conf))
        monkeypatch.setattr(settings, "pools_base_path", str(base))
        monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
    host = FakeSmbHost()
    monkeypatch.setattr(src.host, "run_on_host", host.run)
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    yield SimpleNamespace(base=base, pool=base / "data", conf=conf, samba=samba, db_path=db_path, host=host)


@asynccontextmanager
async def session(env):
    engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            yield db
    finally:
        await engine.dispose()


async def apply(env):
    async with session(env) as db:
        return await service.apply_smb_config(db)


async def render(env) -> service.RenderInput:
    async with session(env) as db:
        return await service.build_render_input(db, {"data"})


def sql(env, *statements, params=()):
    with closing(sqlite3.connect(env.db_path)) as conn:
        for statement in statements:
            conn.execute(statement, params) if params else conn.execute(statement)
        conn.commit()


def add_share(env, name, folder, *, access=None, guest_ok=False, comment="", browseable=True, path=None) -> int:
    with closing(sqlite3.connect(env.db_path)) as conn:
        cursor = conn.execute(
            "INSERT INTO smb_shares (name, path, comment, browseable, guest_ok, version) VALUES (?, ?, ?, ?, ?, 1)",
            (name, path if path is not None else f"{env.pool}/{folder}", comment, int(browseable), int(guest_ok)),
        )
        share_id = cursor.lastrowid
        names = {ALICE: "alice", BOB: "bob", CAROL: "carol", DAVE: "dave", ADMIN: "admin"}
        for user_id, level in (access or {}).items():
            conn.execute("INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (?, ?, ?, ?)",
                         (share_id, user_id, names.get(user_id, "nobody"), level))
        conn.commit()
    return share_id


# smb_share_access as a restored database may have it: without UNIQUE(share_id, user_id).
ACCESS_WITHOUT_CONSTRAINTS = (
    "CREATE TABLE smb_share_access (id INTEGER PRIMARY KEY, share_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
    "username VARCHAR(64) NOT NULL, level VARCHAR(8) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
)


def expected(env, *shares) -> str:
    return smbconf.render_config([service.ShareRender(name, f"{env.pool}/{folder}", comment, True, guest,
                                                      tuple(readers), tuple(writers))
                                  for name, folder, comment, guest, readers, writers in shares])


def temp_files(env) -> list[str]:
    return sorted(name for name in os.listdir(env.samba) if name.endswith(".unos-tmp"))


def copies(env, kind: str = "unos-replaced") -> list[str]:
    """The names of the copies <smb.conf>.<kind>-<UTC time> next to smb.conf, oldest first (ADR-0362)."""
    pattern = re.compile(rf"smb\.conf\.{re.escape(kind)}-\d{{8}}_\d{{6}}_\d{{6}}")
    return sorted(name for name in os.listdir(env.samba) if pattern.fullmatch(name))


def copy_time(name: str) -> datetime:
    """The UTC time in the name of a copy (ADR-0362)."""
    return datetime.strptime(name.rsplit("-", 1)[1], "%Y%m%d_%H%M%S_%f")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- the apply pipeline (ADR-0089) ----------------------------------------------------------------


class TestApply:
    async def test_changed_state_is_staged_checked_installed_and_reloaded(self, env, monkeypatch):
        add_share(env, "docs", "docs", access={ALICE: "write", BOB: "read"})
        synced = []
        real_fsync = os.fsync

        def fsync(fd):
            synced.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fsync)
        result = await apply(env)

        text = expected(env, ("docs", "docs", "", False, ["bob"], ["alice"]))
        assert env.conf.read_text() == text
        assert (result.changed, result.reloaded, result.closed_shares, result.warnings) == (True, True, [], [])
        assert env.host.calls == [
            ["mkdir", "-p", "-m", "0755", "/run/samba"],
            ["testparm", "-s", "--suppress-prompt", env.host.tested[0][0]],
            ["smbcontrol", "all", "reload-config"],
        ]
        staged, content = env.host.tested[0]
        assert os.path.dirname(staged) == str(env.samba)
        assert os.path.basename(staged).startswith(".smb.conf.") and staged.endswith(".unos-tmp")
        assert content == text
        assert synced == ["file", "dir"]  # the staged file, then the directory after the rename
        assert stat.S_IMODE(os.stat(env.conf).st_mode) == 0o644
        assert temp_files(env) == []
        assert (service.last_apply().ok, service.last_apply().reload_pending) == (True, False)

    async def test_unchanged_state_writes_nothing(self, env, monkeypatch):
        add_share(env, "docs", "docs", access={ALICE: "write"})
        await apply(env)
        env.host.calls.clear()
        staged = []
        monkeypatch.setattr(smb, "_stage", lambda content: staged.append(content))
        before = os.stat(env.conf).st_mtime_ns

        result = await apply(env)

        assert (result.changed, result.reloaded, result.closed_shares, result.warnings) == (False, None, [], [])
        assert env.host.calls == [] and staged == []
        assert os.stat(env.conf).st_mtime_ns == before

    async def test_mode_of_the_existing_file_is_kept(self, env):
        env.conf.write_text("[global]\n")
        os.chmod(env.conf, 0o640)
        await apply(env)
        assert stat.S_IMODE(os.stat(env.conf).st_mode) == 0o640

    async def test_testparm_rejection_keeps_the_old_file(self, env):
        env.conf.write_text("[global]\n   workgroup = OLD\n")
        add_share(env, "docs", "docs", access={ALICE: "read"})
        env.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")
        with pytest.raises(smb.SambaConfigError, match="testparm rejected the new smb.conf: Error loading services."):
            await apply(env)
        assert env.conf.read_text() == "[global]\n   workgroup = OLD\n"
        assert temp_files(env) == []
        assert env.host.reloads() == 0
        assert service.last_apply().ok is False
        assert "testparm rejected" in service.last_apply().message

    # Every unknown parameter refuses the file, except exactly the two [global] lines that
    # older Samba releases lack (smb.TOLERATED_UNKNOWN_PARAMETERS, test below).
    @pytest.mark.parametrize("output", ['Ignoring unknown parameter "hosts alow"',
                                        'Unknown parameter encountered: "logging"',
                                        'Ignoring unknown parameter "Server SMB Encrypt"',
                                        'Ignoring unknown parameter "server smb encrypts"',
                                        'Unknown parameter encountered: "smb3 unix extension"',
                                        'Ignoring unknown parameter "smb3 unix extensions" "logging"',
                                        'Ignoring unknown parameter "server smb encrypt" '
                                        'Ignoring unknown parameter "logging"',
                                        # A tolerated name at the end must not hide one before it.
                                        'Ignoring unknown parameter "logging" '
                                        'Ignoring unknown parameter "server smb encrypt"',
                                        'Unknown parameter encountered: "logging" '
                                        'Unknown parameter encountered: "smb3 unix extensions"',
                                        'Ignoring unknown parameter'])
    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    async def test_unknown_parameters_are_rejected(self, env, output, stream):
        env.conf.write_text("[global]\n")
        text = f"Load smb config files from x\n{output}\nLoaded services file OK.\n"
        env.host.fail[("testparm",)] = HostResult(0, text, "") if stream == "stdout" else HostResult(0, "", text)
        with pytest.raises(smb.SambaConfigError) as raised:
            await apply(env)
        assert str(raised.value) == f"testparm does not know a parameter: {output}"
        assert env.conf.read_text() == "[global]\n"
        assert temp_files(env) == []
        assert env.host.reloads() == 0

    @pytest.mark.parametrize("output", ['Ignoring unknown parameter "server smb encrypt"',
                                        'Ignoring unknown parameter "smb3 unix extensions"',
                                        'Unknown parameter encountered: "server smb encrypt"',
                                        '[2026/11/07 10:00:00.000000,  0] lpcfg_do_global_parameter: '
                                        'Unknown parameter encountered: "smb3 unix extensions"'])
    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    async def test_the_two_parameters_older_samba_lacks_are_tolerated(self, env, output, stream, caplog):
        """Older Samba releases lack server smb encrypt and smb3 unix extensions, which
        render_global writes with their defaults; smbd ignores such a line (device-2)."""
        env.conf.write_text("[global]\n")
        text = f"Load smb config files from x\n{output}\nLoaded services file OK.\n"
        env.host.fail[("testparm",)] = HostResult(0, text, "") if stream == "stdout" else HostResult(0, "", text)
        with caplog.at_level(logging.INFO, logger="src.shares.smb"):
            result = await apply(env)
        assert (result.changed, result.warnings) == (True, [])
        assert env.conf.read_text() == smbconf.render_config([])
        assert output in caplog.text
        assert smb.TOLERATED_UNKNOWN_PARAMETERS == ("server smb encrypt", "smb3 unix extensions")

    async def test_a_tolerated_line_does_not_hide_another_unknown_parameter(self, env):
        env.conf.write_text("[global]\n")
        text = ('Ignoring unknown parameter "server smb encrypt"\nIgnoring unknown parameter "smb3 unix extensions"\n'
                'Ignoring unknown parameter "map to gest"\n')
        env.host.fail[("testparm",)] = HostResult(0, "", text)
        with pytest.raises(smb.SambaConfigError) as raised:
            await apply(env)
        assert str(raised.value) == 'testparm does not know a parameter: Ignoring unknown parameter "map to gest"'
        assert env.conf.read_text() == "[global]\n"

    @pytest.mark.parametrize(
        "missing", [FileNotFoundError("nsenter"), HostResult(127, "", "nsenter: failed to execute testparm")]
    )
    async def test_missing_testparm_installs_with_a_warning(self, env, caplog, missing):
        env.host.fail[("testparm",)] = missing
        with caplog.at_level(logging.WARNING, logger="src.shares.smb"):
            result = await apply(env)
        assert result.changed is True
        assert env.conf.read_text() == smbconf.render_config([])
        assert "testparm" in caplog.text

    async def test_cache_directory_failure_is_logged_and_testparm_decides(self, env, caplog):
        env.host.fail[("mkdir",)] = HostResult(1, "", "mkdir: Read-only file system")
        with caplog.at_level(logging.WARNING, logger="src.shares.smb"):
            result = await apply(env)
        assert result.changed is True
        assert "/run/samba" in caplog.text

    async def test_install_failure_is_a_warning(self, env, monkeypatch):
        env.conf.write_text("[global]\n")
        real_replace = os.replace

        def replace(src, dst, **kwargs):
            if str(dst) == str(env.conf):
                raise PermissionError(13, "Permission denied")
            return real_replace(src, dst, **kwargs)

        monkeypatch.setattr(os, "replace", replace)
        result = await apply(env)
        assert result.changed is False
        assert result.warnings and result.warnings[0].startswith("smb.conf could not be installed: ")
        assert env.conf.read_text() == "[global]\n"
        assert temp_files(env) == []
        assert service.last_apply().ok is False

    async def test_a_database_error_is_recorded_without_its_text(self, env, monkeypatch):
        """Account flows show the error to regular users: a database error carries its SQL statement."""
        env.conf.write_text("[global]\n")

        async def locked(*args, **kwargs):
            raise OperationalError("SELECT smb_shares.id FROM smb_shares", {}, sqlite3.OperationalError("locked"))

        monkeypatch.setattr(service, "_read_render_input", locked)
        message = ("The new smb.conf could not be prepared: unexpected OperationalError "
                   "(details are in the supervisor log)")
        with pytest.raises(smb.SambaConfigError) as raised:
            await apply(env)
        assert str(raised.value) == message and isinstance(raised.value.__cause__, OperationalError)
        assert (service.last_apply().ok, service.last_apply().message) == (False, message)
        assert env.conf.read_text() == "[global]\n" and env.host.calls == []

    async def test_an_unreadable_smb_conf_is_not_replaced(self, env, monkeypatch):
        env.conf.write_text("[global]\n   workgroup = OLD\n")
        add_share(env, "docs", "docs", access={ALICE: "read"})

        def unreadable():
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(smb, "read_conf_bytes", unreadable)
        with pytest.raises(smb.SambaConfigError, match=r"^smb.conf could not be read: \[Errno 13\] Permission denied$"):
            await apply(env)
        assert env.conf.read_text() == "[global]\n   workgroup = OLD\n"
        assert env.host.calls == [] and temp_files(env) == []
        assert service.last_apply().ok is False

    async def test_a_failed_staging_keeps_smb_conf(self, env, monkeypatch):
        env.conf.write_text("[global]\n   workgroup = OLD\n")
        add_share(env, "docs", "docs", access={ALICE: "read"})

        def fsync(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "fsync", fsync)
        with pytest.raises(smb.SambaConfigError,
                           match=r"^The new smb.conf could not be written: \[Errno 28\] No space left on device$"):
            await apply(env)
        assert env.conf.read_text() == "[global]\n   workgroup = OLD\n"
        assert env.host.calls == [] and temp_files(env) == []
        assert service.last_apply().ok is False

    async def test_testparm_that_cannot_run_is_a_samba_config_error(self, env):
        env.conf.write_text("[global]\n")
        env.host.fail[("testparm",)] = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with pytest.raises(smb.SambaConfigError,
                           match="^testparm could not check the new smb.conf: 'utf-8' codec can't decode byte 0xff"):
            await apply(env)
        assert env.conf.read_text() == "[global]\n" and temp_files(env) == []
        assert service.last_apply().ok is False and env.host.reloads() == 0

    async def test_stage_then_install(self, env):
        """The two halves used by share mutations: stage, commit, install."""
        async with session(env) as db:
            db.add(SmbShare(name="new", path=f"{env.pool}/new", access=[
                SmbShareAccess(user_id=ALICE, username="alice", level="write")]))
            await db.flush()
            staged = await service.stage_smb_config(db)
            assert env.conf.exists() is False and len(temp_files(env)) == 1
            await db.commit()
            result = await service.install_smb_config(staged)
        assert result.changed is True
        assert env.conf.read_text() == expected(env, ("new", "new", "", False, [], ["alice"]))
        assert temp_files(env) == []

    async def test_discard_after_a_failed_commit(self, env):
        async with session(env) as db:
            db.add(SmbShare(name="new", path=f"{env.pool}/new"))
            await db.flush()
            staged = await service.stage_smb_config(db)
            await db.rollback()
            staged.discard()
        assert temp_files(env) == [] and not env.conf.exists()


class TestReloadAndClose:
    async def setup_docs(self, env):
        add_share(env, "docs", "docs", access={ALICE: "write"})
        await apply(env)
        env.host.calls.clear()

    async def test_reload_failure_is_retried_before_the_pending_close(self, env):
        await self.setup_docs(env)
        sql(env, "DELETE FROM smb_share_access", "DELETE FROM smb_shares")
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "Can't find pid for destination 'all'")
        env.host.fail[("systemctl",)] = HostResult(1, "", "smbd.service is not active")

        result = await apply(env)

        assert (result.changed, result.reloaded, result.closed_shares) == (True, False, [])
        assert len(result.warnings) == 1 and result.warnings[0].startswith("Samba did not reload smb.conf")
        assert env.host.closed() == []  # never before a reload that this run needs
        assert (service.last_apply().reload_pending, service.last_apply().pending_close) == (True, {"docs"})

        env.host.fail.clear()
        env.host.calls.clear()
        result = await apply(env)  # the text is unchanged now

        assert (result.changed, result.reloaded, result.closed_shares, result.warnings) == (False, True, ["docs"], [])
        assert env.host.calls == [["smbcontrol", "all", "reload-config"], ["smbcontrol", "smbd", "close-share", "docs"]]
        assert (service.last_apply().reload_pending, service.last_apply().pending_close) == (False, set())

    async def test_a_reload_with_systemctl_only_closes_now_and_again_after_the_full_reload(self, env):
        """systemctl reload smbd signals the main smbd only: the processes serving open
        connections keep the old file until their periodic check. A process with
        no connection to docs did not reload at the close, so a client could connect to docs
        through it with the old access, which its tree connect keeps (uid.c vuid_cache): docs
        is closed again after the next full reload."""
        await self.setup_docs(env)
        sql(env, "UPDATE smb_share_access SET level = 'read'")  # write -> read: docs shrinks
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "Can't find pid for destination 'all'")

        result = await apply(env)

        assert env.host.calls[-3:] == [["smbcontrol", "all", "reload-config"], ["systemctl", "reload", "smbd"],
                                       ["smbcontrol", "smbd", "close-share", "docs"]]
        # The main smbd reloaded: closing is safe, and the closed connections reload.
        assert (result.changed, result.reloaded, result.closed_shares) == (True, True, ["docs"])
        assert result.warnings == [
            "Samba was reloaded with systemctl only (smbcontrol: Can't find pid for destination 'all'); open "
            "connections apply the change within a few minutes; the full reload is tried again at the next change, "
            "and then the open connections to docs are closed again"]
        assert (service.last_apply().reload_pending, service.last_apply().reload_partial) == (True, True)
        assert (service.last_apply().pending_close, service.last_apply().close_after_full_reload) == (set(), {"docs"})

        env.host.fail.clear()
        env.host.calls.clear()
        result = await apply(env)  # the text is unchanged: the full reload, then docs is closed again

        assert env.host.calls == [["smbcontrol", "all", "reload-config"], ["smbcontrol", "smbd", "close-share", "docs"]]
        assert (result.changed, result.reloaded, result.closed_shares, result.warnings) == (False, True, ["docs"], [])
        assert (service.last_apply().reload_pending, service.last_apply().reload_partial) == (False, False)
        assert (service.last_apply().pending_close, service.last_apply().close_after_full_reload) == (set(), set())

        env.host.calls.clear()
        assert (await apply(env)).closed_shares == [] and env.host.calls == []  # nothing is left to do

    async def test_the_shares_wait_for_a_full_reload(self, env):
        await self.setup_docs(env)
        sql(env, "UPDATE smb_share_access SET level = 'read'")
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "no smbd")
        await apply(env)  # systemctl only: docs closed once
        env.host.fail[("systemctl",)] = HostResult(1, "", "not active")
        env.host.calls.clear()

        result = await apply(env)  # no reload at all: nothing is closed

        assert (result.reloaded, result.closed_shares) == (False, [])
        assert result.warnings == [
            "Samba did not reload smb.conf (smbcontrol: no smbd; systemctl: not active); the reload is tried again "
            "at the next change, and then the open connections to docs are closed again"]
        assert service.last_apply().close_after_full_reload == {"docs"}

        del env.host.fail[("systemctl",)]
        env.host.calls.clear()
        result = await apply(env)  # systemctl only again: docs still waits for the full reload

        assert (result.reloaded, result.closed_shares) == (True, [])
        assert env.host.closed() == []
        assert service.last_apply().close_after_full_reload == {"docs"}

        env.host.fail.clear()
        env.host.fail[("smbcontrol", "smbd", "close-share")] = HostResult(1, "", "smbd not running")
        result = await apply(env)  # the full reload: the close fails, so docs stays pending

        assert (result.reloaded, result.closed_shares) == (True, [])
        assert (service.last_apply().pending_close, service.last_apply().close_after_full_reload) == ({"docs"}, set())

    async def test_a_full_reload_after_a_partial_one_closes_the_shrunk_shares_of_both(self, env):
        await self.setup_docs(env)
        add_share(env, "media", "media", access={BOB: "write"})
        await apply(env)
        sql(env, "UPDATE smb_share_access SET level = 'read' WHERE share_id = 1")  # docs shrinks
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "no smbd")
        await apply(env)
        env.host.fail.clear()
        env.host.calls.clear()
        sql(env, "UPDATE smb_share_access SET level = 'read' WHERE share_id = 2")  # media shrinks

        result = await apply(env)

        assert (result.changed, result.reloaded, result.closed_shares) == (True, True, ["docs", "media"])
        assert env.host.closed() == ["docs", "media"]

    def test_reload_with_systemctl_only_is_reported(self, env):
        env.host.fail[("smbcontrol", "all")] = subprocess.TimeoutExpired(["smbcontrol"], 30)
        ok, note = smb._reload_samba()
        assert ok is True and note.startswith("Samba was reloaded with systemctl only (smbcontrol: ")
        env.host.fail.clear()
        assert smb._reload_samba() == (True, None)

    async def test_close_failure_stays_pending_and_is_retried_without_a_reload(self, env):
        await self.setup_docs(env)
        sql(env, "UPDATE smb_share_access SET level = 'read'")
        env.host.fail[("smbcontrol", "smbd", "close-share")] = HostResult(1, "", "smbd not running")

        result = await apply(env)

        assert (result.reloaded, result.closed_shares) == (True, [])
        assert result.warnings == ["Open connections to docs could not be closed: smbd not running"]
        assert service.last_apply().pending_close == {"docs"}

        env.host.fail.clear()
        env.host.calls.clear()
        result = await apply(env)

        assert (result.changed, result.reloaded, result.closed_shares) == (False, None, ["docs"])
        assert env.host.calls == [["smbcontrol", "smbd", "close-share", "docs"]]
        assert service.last_apply().pending_close == set()


class TestClosedShares:
    """close-share after every shrink (ADR-0103), after the reload; nothing after additions."""

    async def start(self, env, **share):
        add_share(env, "docs", "docs", **share)
        add_share(env, "other", "other", access={CAROL: "read"})
        await apply(env)
        env.host.calls.clear()

    async def test_removal(self, env):
        await self.start(env, access={ALICE: "write"})
        sql(env, "DELETE FROM smb_share_access WHERE share_id = 1", "DELETE FROM smb_shares WHERE id = 1")
        assert (await apply(env)).closed_shares == ["docs"]

    async def test_rename(self, env):
        await self.start(env, access={ALICE: "write"})
        sql(env, "UPDATE smb_shares SET name = 'papers' WHERE id = 1")
        assert (await apply(env)).closed_shares == ["docs"]

    async def test_path_change(self, env):
        await self.start(env, access={ALICE: "write"})
        sql(env, "UPDATE smb_shares SET path = ? WHERE id = 1", params=(f"{env.pool}/papers",))
        assert (await apply(env)).closed_shares == ["docs"]

    async def test_write_to_read(self, env):
        await self.start(env, access={ALICE: "write"})
        sql(env, "UPDATE smb_share_access SET level = 'read' WHERE share_id = 1")
        assert (await apply(env)).closed_shares == ["docs"]

    async def test_last_principal_removed(self, env):
        await self.start(env, access={ALICE: "read"})
        sql(env, "DELETE FROM smb_share_access WHERE share_id = 1")
        result = await apply(env)
        assert result.closed_shares == ["docs"]
        assert "   valid users = root\n   write list =\n   available = no\n" in env.conf.read_text()

    async def test_account_no_longer_active(self, env):
        await self.start(env, access={ALICE: "write"})
        sql(env, "UPDATE smb_accounts SET password_state = 'needed' WHERE user_id = 1")
        assert (await apply(env)).closed_shares == ["docs"]

    async def test_guest_turned_off_without_principals(self, env):
        await self.start(env, guest_ok=True)
        sql(env, "UPDATE smb_shares SET guest_ok = 0 WHERE id = 1")
        result = await apply(env)
        assert result.closed_shares == ["docs"]
        assert "map to guest = Never" in env.conf.read_text()

    async def test_additions_close_nothing(self, env):
        await self.start(env, access={ALICE: "read"})
        sql(env, "UPDATE smb_share_access SET level = 'write' WHERE share_id = 1")
        sql(env, "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 2, 'bob', 'read')")
        add_share(env, "new", "new", access={CAROL: "write"})
        result = await apply(env)
        assert (result.changed, result.closed_shares) == (True, [])
        assert env.host.closed() == []

    async def test_guest_turned_on_closes_nothing(self, env):
        await self.start(env, access={ALICE: "read"})
        sql(env, "UPDATE smb_shares SET guest_ok = 1 WHERE id = 1")
        result = await apply(env)
        assert result.closed_shares == []
        assert "map to guest = Bad User" in env.conf.read_text()

    async def test_invalid_name_from_a_hand_edited_file_is_skipped(self, env):
        await self.start(env, access={ALICE: "read"})
        rendered = smbconf.render_share(service.ShareRender("bad", f"{env.pool}/bad", "", True, False, ("alice",), ()))
        env.conf.write_text(env.conf.read_text() + "\n" + rendered.replace("[bad]", "[bad.name]"))
        result = await apply(env)
        assert result.closed_shares == []
        assert result.warnings == ["Open connections to bad.name were not closed: not a valid share name"]
        assert env.host.closed() == []
        assert service.last_apply().pending_close == set()
        assert copies(env) == []  # it has the fixed lines of a UNOS section


class TestImportBeforeApply:
    @pytest.fixture
    def not_imported(self, env):
        env.db_path.unlink()
        make_database(env.db_path, imported=False)
        return env

    async def test_apply_imports_first(self, not_imported):
        env = not_imported
        env.conf.write_text(f"[global]\n workgroup = HOME\n[docs]\n path = {env.pool}/docs\n valid users = alice\n"
                            " read only = no\n")
        result = await apply(env)
        assert result.changed is True
        assert env.conf.read_text() == expected(env, ("docs", "docs", "", False, [], ["alice"]))
        (backup,) = copies(env, "unos-import-backup")
        assert (env.samba / backup).read_text().startswith("[global]\n workgroup = HOME")

    async def test_a_failed_import_blocks_the_apply(self, not_imported, monkeypatch):
        env = not_imported
        env.conf.write_text(f"[docs]\n path = {env.pool}/docs\n valid users = alice\n")
        before = env.conf.read_bytes()

        def broken(*args, **kwargs):
            raise RuntimeError("import exploded")

        monkeypatch.setattr(service, "_plan_import", broken)
        with pytest.raises(smb.SambaConfigError, match=(
                r"^Shares were not imported from smb.conf yet: unexpected RuntimeError \(details are in the "
                r"supervisor log\)$")):
            await apply(env)
        assert service.last_import_problem() == "Shares were not imported from smb.conf: import exploded"
        assert env.conf.read_bytes() == before
        assert env.host.calls == []
        assert service.last_apply().ok is False

    async def test_stage_never_imports(self, not_imported):
        env = not_imported
        env.conf.write_text(f"[docs]\n path = {env.pool}/docs\n valid users = alice\n")
        async with session(env) as db:
            db.add(SmbShare(name="new", path=f"{env.pool}/new"))
            await db.flush()
            with pytest.raises(smb.SambaConfigError, match="^Shares were not imported from smb.conf yet: "):
                await service.stage_smb_config(db)
            await db.rollback()
        with closing(sqlite3.connect(env.db_path)) as conn:
            assert conn.execute("SELECT name FROM smb_shares").fetchall() == []
            assert conn.execute("SELECT * FROM smb_state").fetchall() == []
        assert copies(env, "unos-import-backup") == []
        assert env.host.calls == []


class TestPoolList:
    async def test_unreadable_pool_list(self, env, monkeypatch):
        env.conf.write_text("[global]\n   workgroup = OLD\n")

        def broken():
            raise RuntimeError("cat: /etc/fstab: No such file or directory")

        monkeypatch.setattr(pools, "managed_pool_names", broken)
        with pytest.raises(service.PoolListUnavailable, match="^The pool list could not be read: "):
            await apply(env)
        assert env.conf.read_text() == "[global]\n   workgroup = OLD\n"
        assert env.host.calls == []

    def test_managed_pool_names_reads_the_fstab_markers(self, monkeypatch):
        fstab = ("UUID=1 / ext4 defaults 0 1\n# unos-pool:data (managed by UNOS)\n"
                 "UUID=3a1c9f6e-1234-4c3b-9b0a-8f3c2a1b4c5d /mnt/pools/data btrfs defaults 0 0\n"
                 "# unos-pool:media (managed by UNOS)\n")
        monkeypatch.setattr(pools, "_read_fstab", lambda: fstab)
        assert pools.managed_pool_names() == {"data", "media"}

        def unreadable():
            raise src.host.HostCommandError(["cat", "--", "/etc/fstab"], 1, "Permission denied")

        monkeypatch.setattr(pools, "_read_fstab", unreadable)
        with pytest.raises(src.host.HostCommandError):
            pools.managed_pool_names()


class TestReplaced:
    OLD_RELEASE = "\n[legacy]\n   path = {pool}/legacy\n   browseable = yes\n   read only = no\n   guest ok = no\n"

    async def test_every_copy_is_kept_with_its_time(self, env):
        """ADR-0362: each file with share sections that UNOS did not render gets a copy of its own,
        named with the UTC time of the copy; an earlier copy is never replaced."""
        add_share(env, "docs", "docs", access={ALICE: "read"})
        await apply(env)
        rendered = env.conf.read_text()
        old = rendered + self.OLD_RELEASE.format(pool=env.pool)
        env.conf.write_text(old)

        before = utc_now()
        result = await apply(env)
        after = utc_now()

        (first,) = copies(env)
        assert before <= copy_time(first) <= after
        assert (env.samba / first).read_text() == old
        assert stat.S_IMODE((env.samba / first).stat().st_mode) == 0o600
        assert result.changed is True and "[legacy]" not in env.conf.read_text()
        assert result.closed_shares == ["legacy"]
        assert service.last_apply().replaced_problem == (
            f"smb.conf had shares that UNOS did not create (legacy); they are kept in {first}: "
            "create them again on the Shares page")
        assert temp_files(env) == []

        env.conf.write_text(old)  # the same file again: the newest copy has these bytes, nothing is written
        await apply(env)
        assert copies(env) == [first]

        # Another foreign section later (a rollback-era release, a hand edit): a second copy,
        # and the first keeps its text.
        later = rendered + f"\n[later]\n   path = {env.pool}/later\n   read only = no\n"
        env.conf.write_text(later)
        result = await apply(env)
        assert result.changed is True and "[later]" not in env.conf.read_text()
        kept, second = copies(env)
        assert kept == first and (env.samba / first).read_text() == old
        assert (env.samba / second).read_text() == later
        assert service.last_apply().replaced_problem == (
            f"smb.conf had shares that UNOS did not create (later); they are kept in {second}: "
            f"create them again on the Shares page; earlier copies (UNOS keeps the newest 10): {first}")

    async def test_the_newest_ten_copies_are_kept(self, env):
        """ADR-0362: a new copy deletes the oldest beyond 10; nothing is refused, and no other file
        of /etc/samba is touched."""
        add_share(env, "docs", "docs", access={ALICE: "read"})
        await apply(env)
        rendered = env.conf.read_text()
        earlier = [f"smb.conf.unos-replaced-202611{day:02d}_120000_000000" for day in range(1, 11)]
        others = ["smb.conf.unos-replaced", "smb.conf.unos-replaced-notes", "smb.conf.unos-replaced-20261101_1200",
                  "smb.conf.unos-import-backup-20261031_120000_000000"]
        for name in earlier + others:
            (env.samba / name).write_text(name)
        env.conf.write_text(rendered + self.OLD_RELEASE.format(pool=env.pool))

        result = await apply(env)

        assert result.changed is True
        kept = copies(env)
        assert kept[:9] == earlier[1:] and len(kept) == 10 and kept[9] not in earlier
        assert not (env.samba / earlier[0]).exists()
        assert all((env.samba / name).read_text() == name for name in others)
        assert service.last_apply().replaced_problem == (
            f"smb.conf had shares that UNOS did not create (legacy); they are kept in {kept[9]}: create them again "
            f"on the Shares page; earlier copies (UNOS keeps the newest 10): {', '.join(reversed(earlier[1:]))}")
        assert temp_files(env) == []

    async def test_a_copy_named_before_the_others_is_not_the_one_deleted(self, env):
        """A clock set back (a board without a clock of its own, before NTP) names the new copy
        before the others: the oldest of the others goes, and the new copy stays (ADR-0362)."""
        add_share(env, "docs", "docs", access={ALICE: "read"})
        await apply(env)
        rendered = env.conf.read_text()
        future = [f"smb.conf.unos-replaced-209911{day:02d}_120000_000000" for day in range(1, 11)]
        for name in future:
            (env.samba / name).write_text(name)
        old = rendered + self.OLD_RELEASE.format(pool=env.pool)
        env.conf.write_text(old)

        await apply(env)

        new, *rest = copies(env)
        assert (env.samba / new).read_text() == old
        assert rest == future[1:]

    async def test_a_file_copied_while_the_clock_was_set_back_is_not_copied_again(self, env):
        """The copy that holds the file is named before a newer copy of another file, as after a
        clock set back: the apply writes no copy and names that one (ADR-0362)."""
        add_share(env, "docs", "docs", access={ALICE: "read"})
        await apply(env)
        rendered = env.conf.read_text()
        old = rendered + self.OLD_RELEASE.format(pool=env.pool)
        set_back = "smb.conf.unos-replaced-20261101_120000_000000"
        newer = "smb.conf.unos-replaced-20261110_120000_000000"
        (env.samba / set_back).write_text(old)
        (env.samba / newer).write_text(rendered + "\n[later]\n   path = /srv/later\n")
        env.conf.write_text(old)

        result = await apply(env)

        assert result.changed is True and "[legacy]" not in env.conf.read_text()
        assert copies(env) == [set_back, newer]
        assert service.last_apply().replaced_problem == (
            f"smb.conf had shares that UNOS did not create (legacy); they are kept in {set_back}: create them again "
            f"on the Shares page; earlier copies (UNOS keeps the newest 10): {newer}")

    async def test_a_failed_copy_keeps_smb_conf(self, env, monkeypatch):
        """ADR-0362: sections that UNOS did not render are copied before they are
        overwritten; without the copy, smb.conf is not replaced."""
        add_share(env, "docs", "docs", access={ALICE: "read"})
        old = f"[global]\n   workgroup = OLD\n[legacy]\n   path = {env.pool}/legacy\n   read only = no\n"
        env.conf.write_text(old)
        real_link = os.link

        def link(src, dst, **kwargs):
            if os.path.basename(dst).startswith("smb.conf.unos-replaced-"):
                raise PermissionError(13, "Permission denied")
            return real_link(src, dst, **kwargs)

        monkeypatch.setattr(os, "link", link)
        with pytest.raises(smb.SambaConfigError) as raised:
            await apply(env)

        assert str(raised.value) == (
            "smb.conf has shares that UNOS did not create (legacy), and they could not be copied to "
            f"{env.conf}.unos-replaced-<UTC time>: [Errno 13] Permission denied")
        assert env.conf.read_text() == old
        assert copies(env) == [] and temp_files(env) == []
        assert env.host.calls == []  # no testparm, no reload, no close
        assert (service.last_apply().ok, service.last_apply().message) == (False, str(raised.value))
        assert service.last_apply().replaced_problem is None

    async def test_unos_section_of_a_deleted_row_is_not_copied(self, env):
        add_share(env, "docs", "docs", access={ALICE: "read"})
        add_share(env, "gone", "gone", access={ALICE: "read"})
        await apply(env)
        sql(env, "DELETE FROM smb_share_access WHERE share_id = 2", "DELETE FROM smb_shares WHERE id = 2")
        result = await apply(env)
        assert result.closed_shares == ["gone"]
        assert copies(env) == []
        assert service.last_apply().replaced_problem is None

    async def test_debian_default_sections_are_not_copied(self, env):
        env.conf.write_text("[global]\n workgroup = WORKGROUP\n[homes]\n browseable = no\n"
                            "[printers]\n path = /var/spool\n[print$]\n path = /var/lib/samba/printers\n")
        result = await apply(env)
        assert copies(env) == []
        assert sorted(result.closed_shares) == ["homes", "printers"]
        assert result.warnings == ["Open connections to print$ were not closed: not a valid share name"]


# --- the render input -----------------------------------------------------------------


class TestRenderInput:
    async def test_shares_that_are_not_served(self, env):
        ids = {
            "root": add_share(env, "root", "", path=str(env.pool), access={ALICE: "write"}),
            "apps": add_share(env, "apps", "apps/x", access={ALICE: "write"}),
            "other": add_share(env, "other", "", path=f"{env.base}/other/x", access={ALICE: "write"}),
            "snap": add_share(env, "snap", ".snapshots/x", access={ALICE: "write"}),
            "outer": add_share(env, "outer", "media", access={ALICE: "write"}),
            "inner": add_share(env, "inner", "media/films", access={BOB: "read"}),
            "fine": add_share(env, "fine", "fine", access={CAROL: "read"}),
        }
        result = await render(env)
        assert [s.name for s in result.shares] == ["fine"]
        assert set(result.unserved) == {ids[n] for n in ("root", "apps", "other", "snap", "outer", "inner")}
        assert result.unserved[ids["root"]] == service.POOL_ROOT_PROBLEM
        assert result.problems == [
            f"Share root is not served: {service.POOL_ROOT_PROBLEM}",
            "Share apps is not served: App data (data/apps) cannot be shared: apps keep their configuration there",
            "Share other is not served: Share path must be on a storage pool that UNOS manages: 'other' is not one",
            "Share snap is not served: Snapshot folders cannot be shared",
            "Share outer is not served: The folder overlaps share inner (one is inside the other). With one shared "
            "group, everyone who can open the outer share can reach the inner one: use folders side by side.",
            "Share inner is not served: The folder overlaps share outer (one is inside the other). With one shared "
            "group, everyone who can open the outer share can reach the inner one: use folders side by side.",
        ]
        await apply(env)
        assert [line for line in env.conf.read_text().splitlines() if line.startswith("[")] == ["[global]", "[fine]"]

    @pytest.mark.parametrize("written, normal", [("{pool}/", "{pool}"), ("{pool}/.", "{pool}"),
                                                 ("{pool}//apps", "{pool}/apps"), ("{pool}/./apps/x", "{pool}/apps/x")])
    async def test_stored_paths_not_in_normal_form(self, env, written, normal):
        written, normal = written.format(pool=env.pool), normal.format(pool=env.pool)
        share_id = add_share(env, "x", "", path=written, access={ALICE: "write"})
        result = await render(env)
        assert result.shares == []
        assert result.unserved == {share_id: f"Write the folder as {normal}"}
        assert result.problems == [f"Share x is not served: Write the folder as {normal}"]

    async def test_hostile_paths_leave_only_their_share_out(self, env, tmp_path, monkeypatch):
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, env.pool / "link")
        add_share(env, "etc", "", path="/etc", access={ALICE: "write"})
        add_share(env, "link", "link", access={ALICE: "write"})
        add_share(env, "fine", "fine", access={ALICE: "write"})
        threads = []
        real = service.share_path_problem

        def recording(*args, **kwargs):
            threads.append(threading.get_ident())
            return real(*args, **kwargs)

        monkeypatch.setattr(service, "share_path_problem", recording)
        result = await apply(env)
        assert result.changed is True
        assert [line for line in env.conf.read_text().splitlines() if line.startswith("[")] == ["[global]", "[fine]"]
        assert len(set(threads)) == 1 and threading.main_thread().ident not in threads
        problems = (await render(env)).problems
        assert problems[0] == f"Share etc is not served: Share path must be within {env.base}"
        assert problems[1].startswith("Share link is not served: Share path must not contain symbolic links")

    async def test_principals_must_be_active_with_their_current_name(self, env):
        sql(env, "UPDATE smb_accounts SET password_state = 'needed' WHERE user_id = 2")
        add_share(env, "docs", "docs", access={ALICE: "write", BOB: "write", DAVE: "read", ADMIN: "write"})
        add_share(env, "renamed", "renamed", access={CAROL: "read"})
        sql(env, "UPDATE smb_share_access SET username = 'carola' WHERE user_id = 3")
        add_share(env, "ghost", "ghost")
        sql(env, "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (3, 99, 'ghost', 'read')")
        (docs, renamed, ghost) = (await render(env)).shares
        assert (docs.readers, docs.writers) == ((), ("alice",))
        assert (renamed.readers, renamed.writers, renamed.available) == ((), (), False)
        assert (ghost.readers, ghost.writers) == ((), ())

    async def test_account_rows_that_are_not_active(self, env):
        sql(env, "UPDATE smb_accounts SET enabled = 0 WHERE user_id = 1",
            "UPDATE smb_accounts SET linux_name = 'bobby' WHERE user_id = 2",
            "UPDATE smb_accounts SET uid = NULL WHERE user_id = 3")
        add_share(env, "docs", "docs", access={ALICE: "write", BOB: "write", CAROL: "read"})
        (docs,) = (await render(env)).shares
        assert (docs.readers, docs.writers) == ((), ())

    async def test_invalid_levels_are_ignored(self, env):
        add_share(env, "docs", "docs")
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("INSERT INTO smb_share_access (share_id, user_id, username, level) "
                         "VALUES (1, 1, 'alice', 'admin')")
            conn.commit()
        (docs,) = (await render(env)).shares
        assert (docs.readers, docs.writers) == ((), ())

    async def test_invalid_and_duplicate_names(self, env):
        with closing(sqlite3.connect(env.db_path)) as conn:  # a restored database without the constraints
            conn.execute("DROP TABLE smb_share_access")
            conn.execute("DROP TABLE smb_shares")
            conn.execute("CREATE TABLE smb_shares (id INTEGER PRIMARY KEY, name TEXT, path TEXT, comment TEXT, "
                         "browseable BOOLEAN, guest_ok BOOLEAN, import_notes TEXT, version INTEGER, "
                         "created_at DATETIME, updated_at DATETIME)")
            conn.execute("CREATE TABLE smb_share_access (id INTEGER PRIMARY KEY, share_id INTEGER, user_id INTEGER, "
                         "username TEXT, level TEXT, created_at DATETIME)")
            for name, folder in [("Docs", "a"), ("docs", "b"), ("x\n[evil]", "c"), ("global", "d")]:
                conn.execute("INSERT INTO smb_shares (name, path, comment, browseable, guest_ok, version) "
                             "VALUES (?, ?, '', 1, 0, 1)", (name, f"{env.pool}/{folder}"))
            conn.commit()
        result = await render(env)
        assert [s.name for s in result.shares] == ["Docs"]
        assert result.problems[0] == ("Share docs is not served: share Docs has the same name in another letter case "
                                      "(share names are not case-sensitive)")
        assert result.problems[1].startswith("Share x?[evil] is not served: Invalid share name")
        assert result.problems[2] == "Share global is not served: 'global' is a reserved share name"
        await apply(env)
        assert "[evil]" not in env.conf.read_text()

    @pytest.mark.parametrize("name", ["globals", "Globals", "GLOBALS"])
    async def test_a_stored_share_named_like_the_global_section_is_not_served(self, env, name):
        """A restored row named globals (Samba's [global]) is listed with its problem, never
        renamed: the administrator renames it. Rendered, it would make its lines the
        defaults of every share (and of IPC$) instead of being a share."""
        share_id = add_share(env, name, "team", access={ALICE: "write"})
        add_share(env, "zeta", "zeta", guest_ok=True)
        result = await render(env)
        assert [s.name for s in result.shares] == ["zeta"]
        assert result.unserved == {share_id: f"'{name}' is a reserved share name"}
        assert result.problems == [f"Share {name} is not served: '{name}' is a reserved share name"]
        await apply(env)
        text = env.conf.read_text()
        assert [line for line in text.splitlines() if line.startswith("[")] == ["[global]", "[zeta]"]
        assert [s.name for s in smbconf.parse_smb_conf(text).sections] == ["zeta"]
        assert rows(env, "SELECT id, name FROM smb_shares ORDER BY id") == [(share_id, name), (share_id + 1, "zeta")]
        assert rows(env, "SELECT user_id, level FROM smb_share_access") == [(ALICE, "write")]

    @pytest.mark.parametrize("order", [("read", "write"), ("write", "read")])
    async def test_two_rows_of_one_user_render_the_lower_level(self, env, order):
        """ADR-0144: a restored smb_share_access without UNIQUE(share_id, user_id) passes
        check_smb_tables (it checks columns) and can hold two rows for one user. Neither the
        first nor the last row wins: the lower level does."""
        share_id = add_share(env, "docs", "docs")
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.executescript(f"DROP TABLE smb_share_access; {ACCESS_WITHOUT_CONSTRAINTS};")
            for level in order:
                conn.execute("INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (?, ?, ?, ?)",
                             (share_id, ALICE, "alice", level))
            conn.commit()
        [share] = (await render(env)).shares
        assert (share.readers, share.writers) == (("alice",), ())
        await apply(env)
        assert "   valid users = alice\n   write list =\n" in env.conf.read_text()

    async def test_invalid_comment_is_left_out(self, env):
        add_share(env, "docs", "docs", comment="line\nroot preexec = id", access={ALICE: "read"})
        result = await render(env)
        assert result.shares[0].comment == ""
        assert result.problems == ["Share docs: its comment is not written to smb.conf: Share comment must not "
                                   "contain line breaks or control characters"]

    async def test_stale_identity(self, env):
        """A user loaded before another session promoted it is not rendered (populate_existing)."""
        add_share(env, "docs", "docs", access={BOB: "write"})
        engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as first, maker() as second:
                bob = await first.get(User, BOB)
                assert bob.is_admin is False
                (await second.get(User, BOB)).is_admin = True
                await second.commit()
                result = await service.build_render_input(first, {"data"})
        finally:
            await engine.dispose()
        assert result.shares[0].writers == ()

    async def test_flushed_changes_of_the_session_are_rendered(self, env):
        async with session(env) as db:
            db.add(SmbShare(name="new", path=f"{env.pool}/new",
                            access=[SmbShareAccess(user_id=CAROL, username="carol", level="read")]))
            await db.flush()
            result = await service.build_render_input(db, {"data"})
        assert [(s.name, s.readers) for s in result.shares] == [("new", ("carol",))]


# --- the path rules (ADR-0145) ---------------------------------------------------------------------


class TestSharePathProblem:
    def problem(self, path, others=None, pools_=("data",)):
        return service.share_path_problem(path, other_paths=others or {}, pool_names=set(pools_))

    def test_a_valid_folder_and_a_volume_root(self, env):
        assert self.problem(f"{env.pool}/docs") is None
        assert self.problem(f"{env.pool}/docs/deep/folder") is None

    @pytest.mark.parametrize("path", ["/etc", "relative/x", "", None, 42, "/mnt/pools/../etc"])
    def test_rule_1_invalid(self, env, path):
        assert isinstance(self.problem(path), str)

    def test_rule_1_symlink(self, env, tmp_path):
        os.symlink(tmp_path, env.pool / "escape")
        assert "symbolic link" in self.problem(f"{env.pool}/escape/x")

    @pytest.mark.parametrize("written, normal", [("{pool}/docs/", "{pool}/docs"), ("{pool}//docs", "{pool}/docs"),
                                                 ("{pool}/./docs", "{pool}/docs")])
    def test_rule_1_normal_form(self, env, written, normal):
        assert self.problem(written.format(pool=env.pool)) == f"Write the folder as {normal.format(pool=env.pool)}"

    def test_rule_1_unsafe_characters(self, env):
        assert "invalid characters" in self.problem(f"{env.pool}/per-user-%U")

    def test_rule_2_snapshots(self, env):
        assert self.problem(f"{env.pool}/vol/.snapshots/s1") == "Snapshot folders cannot be shared"

    def test_rule_3_pool_root(self, env):
        assert self.problem(str(env.pool)) == service.POOL_ROOT_PROBLEM

    def test_rule_4_unmanaged_pool(self, env):
        assert self.problem(f"{env.base}/default/x") == (
            "Share path must be on a storage pool that UNOS manages: 'default' is not one")

    @pytest.mark.parametrize("folder", ["apps", "apps/immich", "apps/x/y"])
    def test_rule_5_apps(self, env, folder):
        assert self.problem(f"{env.pool}/{folder}") == (
            "App data (data/apps) cannot be shared: apps keep their configuration there")

    @pytest.mark.parametrize("other", ["docs", "docs/sub", "", "docs/sub/deeper"])
    def test_rule_6_overlap(self, env, other):
        mine = f"{env.pool}/docs" if other != "" else f"{env.pool}/docs/x"
        theirs = f"{env.pool}/{other}" if other else f"{env.pool}/docs"
        problem = self.problem(mine, {"team": theirs})
        assert problem.startswith("The folder overlaps share team (one is inside the other).")

    def test_rule_6_side_by_side(self, env):
        assert self.problem(f"{env.pool}/docs", {"a": f"{env.pool}/docs-old", "b": f"{env.pool}/doc"}) is None

    def test_never_raises(self, env, monkeypatch):
        def broken(path):
            raise RuntimeError("stat failed")

        monkeypatch.setattr(service, "validate_share_path", broken)
        assert self.problem(f"{env.pool}/docs") == "The folder could not be checked: stat failed"


class TestShareProblem:
    async def test_served_and_not_served(self, env):
        fine = add_share(env, "fine", "fine")
        root = add_share(env, "root", "", path=str(env.pool))
        async with session(env) as db:
            assert await service.share_problem(db, fine) is None
            assert await service.share_problem(db, root) == service.POOL_ROOT_PROBLEM
            assert await service.share_problem(db, 999) == "The share does not exist"

    async def test_pool_list_unavailable(self, env, monkeypatch):
        fine = add_share(env, "fine", "fine")

        def broken():
            raise OSError("fstab")

        monkeypatch.setattr(pools, "managed_pool_names", broken)
        async with session(env) as db:
            with pytest.raises(service.PoolListUnavailable, match="^The pool list could not be read: fstab"):
                await service.share_problem(db, fine)


# --- rows -------------------------------------------------------------------------------


def rows(env, query):
    with closing(sqlite3.connect(env.db_path)) as conn:
        return conn.execute(query).fetchall()


class TestRows:
    async def test_delete_access_of_user(self, env):
        add_share(env, "a", "a", access={ALICE: "write", BOB: "read"})
        add_share(env, "b", "b", access={ALICE: "read"})
        add_share(env, "c", "c", access={BOB: "read"})
        async with session(env) as db:
            assert await service.delete_access_of_user(db, ALICE) == 2
            # Flushed, not committed.
            assert rows(env, "SELECT count(*) FROM smb_share_access WHERE user_id = 1") == [(2,)]
            await db.commit()
        assert rows(env, "SELECT share_id, username FROM smb_share_access ORDER BY id") == [(1, "bob"), (3, "bob")]
        assert rows(env, "SELECT name, version FROM smb_shares ORDER BY id") == [("a", 2), ("b", 2), ("c", 1)]

    async def test_delete_access_of_a_user_without_rows(self, env):
        add_share(env, "a", "a", access={BOB: "read"})
        async with session(env) as db:
            assert await service.delete_access_of_user(db, ALICE) == 0
            assert not (db.new or db.dirty or db.deleted)

    async def test_purge_orphans(self, env):
        add_share(env, "a", "a", access={ALICE: "write", BOB: "read", ADMIN: "read"})
        add_share(env, "b", "b", access={CAROL: "read"})
        sql(env, "UPDATE smb_share_access SET username = 'carola' WHERE user_id = 3",
            "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (77, 1, 'alice', 'read')",
            "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 88, 'ghost', 'read')")
        async with session(env) as db:
            assert await service.purge_orphans(db) == 4
            await db.commit()
        assert rows(env, "SELECT share_id, username FROM smb_share_access ORDER BY id") == [(1, "alice"), (1, "bob")]
        assert rows(env, "SELECT name, version FROM smb_shares ORDER BY id") == [("a", 2), ("b", 2)]

    async def test_purge_without_orphans_writes_nothing(self, env):
        add_share(env, "a", "a", access={ALICE: "write"})
        async with session(env) as db:
            assert await service.purge_orphans(db) == 0
            assert not (db.new or db.dirty or db.deleted)
        assert rows(env, "SELECT version FROM smb_shares") == [(1,)]


# --- the lock, stale files and the share list -------------------------------------------------------


class TestEnsureImported:
    async def test_no_lock_when_the_marker_is_set(self, env):
        async with session(env) as db:
            async with service.smb_lock():
                await asyncio.wait_for(service.ensure_imported(db), timeout=5)

    async def test_waits_for_the_lock_when_the_marker_is_null(self, env):
        env.db_path.unlink()
        make_database(env.db_path, imported=False)
        async with session(env) as db:
            lock = service.smb_lock()
            await lock.acquire()
            task = asyncio.create_task(service.ensure_imported(db))
            await asyncio.sleep(0.05)
            assert not task.done()
            lock.release()
            await asyncio.wait_for(task, timeout=10)
        assert rows(env, "SELECT shares_imported_at IS NOT NULL FROM smb_state") == [(1,)]


class TestStaleTempFiles:
    def test_only_staged_files_are_removed(self, env):
        for name in (".smb.conf.abc123.unos-tmp", ".smb.conf.x.unos-tmp", "smb.conf", ".smb.conf.keep",
                     "notes.unos-tmp", "smb.conf.unos-import-backup-20261112_101530_123456"):
            (env.samba / name).write_text("x")
        assert smb.remove_stale_temp_files() == [".smb.conf.abc123.unos-tmp", ".smb.conf.x.unos-tmp"]
        assert sorted(os.listdir(env.samba)) == [".smb.conf.keep", "notes.unos-tmp", "smb.conf",
                                                 "smb.conf.unos-import-backup-20261112_101530_123456"]
        assert smb.remove_stale_temp_files() == []

    def test_missing_directory(self, env, monkeypatch):
        monkeypatch.setattr(smb.settings, "smb_conf_path", str(env.samba / "gone" / "smb.conf"))
        assert smb.remove_stale_temp_files() == []


class TestListShares:
    def write_file(self, env, *sections):
        env.conf.write_text("[global]\n workgroup = X\n" + "".join(
            f"[{name}]\n path = {path}\n" for name, path in sections))

    def test_database_only(self, env):
        add_share(env, "docs", "docs")
        add_share(env, "root", "", path=str(env.pool))  # not served, still listed
        assert smb.list_shares() == [smb.SharePathInfo("docs", f"{env.pool}/docs"),
                                     smb.SharePathInfo("root", str(env.pool))]

    def test_file_only(self, env):
        self.write_file(env, ("docs", "/mnt/pools/data/docs"))
        assert smb.list_shares() == [smb.SharePathInfo("docs", "/mnt/pools/data/docs")]

    def test_a_global_path_applies_to_the_sections_after_it(self, env):
        # As in Samba: [early] started before the path was set, so it has none.
        env.conf.write_text("[early]\n valid users = alice\n[global]\n path = /mnt/pools/data/g\n"
                            "[late]\n valid users = alice\n")
        assert smb.list_shares() == [smb.SharePathInfo("late", "/mnt/pools/data/g")]

    def test_both(self, env):
        add_share(env, "docs", "docs")
        add_share(env, "media", "media")
        self.write_file(env, ("DOCS", f"{env.pool}/docs"), ("media", f"{env.pool}/old-media"), ("legacy", "/x"))
        assert smb.list_shares() == [
            smb.SharePathInfo("docs", f"{env.pool}/docs"),
            smb.SharePathInfo("media", f"{env.pool}/media"),
            smb.SharePathInfo("media", f"{env.pool}/old-media"),  # a stale file entry is not missed
            smb.SharePathInfo("legacy", "/x"),
        ]

    @pytest.mark.parametrize("db_path", [":memory:", "", None])
    def test_no_database(self, env, monkeypatch, db_path):
        monkeypatch.setattr(smb.settings, "db_path", db_path)
        self.write_file(env, ("docs", "/d"))
        assert smb.list_shares() == [smb.SharePathInfo("docs", "/d")]

    def test_missing_database_file_or_table(self, env, monkeypatch, tmp_path):
        self.write_file(env, ("docs", "/d"))
        monkeypatch.setattr(smb.settings, "db_path", str(tmp_path / "missing.db"))
        assert smb.list_shares() == [smb.SharePathInfo("docs", "/d")]
        old = tmp_path / "old.db"
        with closing(sqlite3.connect(old)) as conn:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            conn.commit()
        monkeypatch.setattr(smb.settings, "db_path", str(old))
        assert smb.list_shares() == [smb.SharePathInfo("docs", "/d")]
        assert not (tmp_path / "missing.db").exists()  # opened read-only: nothing created

    def test_missing_file(self, env):
        add_share(env, "docs", "docs")
        assert not env.conf.exists()
        assert smb.list_shares() == [smb.SharePathInfo("docs", f"{env.pool}/docs")]

    def test_unreadable_file_raises(self, env):
        env.conf.mkdir()
        with pytest.raises(OSError):
            smb.list_shares()

    def test_other_database_errors_raise(self, env, monkeypatch, tmp_path):
        broken = tmp_path / "broken.db"
        broken.write_bytes(b"this is not a database" * 100)
        monkeypatch.setattr(smb.settings, "db_path", str(broken))
        with pytest.raises(sqlite3.DatabaseError):
            smb.list_shares()


class TestSmbHelpers:
    def test_close_share_validates_the_name(self, env):
        assert smb._close_share("docs") is None
        assert env.host.calls == [["smbcontrol", "smbd", "close-share", "docs"]]
        for name in ("print$", "a b", "-x", "x\ny", 7):
            assert smb._close_share(name).endswith("were not closed: not a valid share name")
        assert len(env.host.calls) == 1

    def test_close_share_failure(self, env):
        env.host.fail[("smbcontrol",)] = subprocess.TimeoutExpired(["smbcontrol"], 15)
        assert smb._close_share("docs").startswith("Open connections to docs could not be closed: ")

    def test_reload_never_raises(self, env):
        env.host.fail[("smbcontrol",)] = FileNotFoundError("nsenter")
        env.host.fail[("systemctl",)] = HostResult(1, "", "not active")
        ok, why = smb._reload_samba()
        assert ok is False and "nsenter" in why and "not active" in why

    def test_keep_copy_writes_a_new_file_named_with_its_utc_time(self, env):
        before = utc_now()
        kept = smb.keep_copy(smb.IMPORT_BACKUP, b"\xff first")
        after = utc_now()
        (name,) = copies(env, "unos-import-backup")
        assert kept == smb.KeptCopy(path=str(env.samba / name), earlier=())
        assert before <= copy_time(name) <= after
        assert (env.samba / name).read_bytes() == b"\xff first"
        assert stat.S_IMODE((env.samba / name).stat().st_mode) == 0o600
        assert temp_files(env) == []

        assert smb.keep_copy(smb.IMPORT_BACKUP, b"\xff first") == kept  # the newest copy has these bytes
        second = smb.keep_copy(smb.IMPORT_BACKUP, b"second")
        assert second.earlier == (kept.path,) and Path(second.path).read_bytes() == b"second"
        assert copies(env, "unos-import-backup") == [name, os.path.basename(second.path)]
        assert copies(env, "unos-replaced") == []

    def test_keep_copy_never_replaces_a_file(self, env, monkeypatch):
        """Two copies in one microsecond, or a clock set back onto an earlier copy's time: the
        link fails, the earlier file keeps its bytes, and no temporary file is left (ADR-0362)."""
        (env.samba / "smb.conf.unos-replaced-20261112_101530_123456").write_bytes(b"earlier")

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 12, 10, 15, 30, 123456, tzinfo=tz)

        monkeypatch.setattr(smb, "datetime", Frozen)
        with pytest.raises(FileExistsError):
            smb.keep_copy(smb.REPLACED, b"new")
        assert (env.samba / "smb.conf.unos-replaced-20261112_101530_123456").read_bytes() == b"earlier"
        assert temp_files(env) == []

    def test_a_copy_that_cannot_be_deleted_is_logged(self, env, monkeypatch, caplog):
        for day in range(1, 11):
            (env.samba / f"smb.conf.unos-replaced-202611{day:02d}_120000_000000").write_bytes(b"x")
        real_unlink = os.unlink

        def unlink(path, *args, **kwargs):
            if os.path.basename(path) == "smb.conf.unos-replaced-20261101_120000_000000":
                raise PermissionError(13, "Permission denied")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", unlink)
        with caplog.at_level(logging.WARNING, logger="src.shares.smb"):
            kept = smb.keep_copy(smb.REPLACED, b"new")
        assert Path(kept.path).read_bytes() == b"new"
        assert len(copies(env)) == 11
        assert "Could not delete the old copy smb.conf.unos-replaced-20261101_120000_000000" in caplog.text

    def test_only_regular_files_are_copies(self, env):
        """A directory, a symbolic link or a FIFO with the name of a copy is neither listed, read
        nor deleted (ADR-0362)."""
        (env.samba / "notes").write_bytes(b"same")
        directory = env.samba / "smb.conf.unos-replaced-20261101_120000_000000"
        directory.mkdir()
        link = env.samba / "smb.conf.unos-replaced-20261102_120000_000000"
        link.symlink_to("notes")
        fifo = env.samba / "smb.conf.unos-replaced-20261103_120000_000000"
        os.mkfifo(fifo)
        regular = env.samba / "smb.conf.unos-replaced-20261104_120000_000000"
        regular.write_bytes(b"other")

        assert smb.kept_copies(smb.REPLACED) == [str(regular)]
        kept = smb.keep_copy(smb.REPLACED, b"same")  # the link's target holds these bytes: no copy
        assert kept.path not in (str(link), str(regular)) and Path(kept.path).read_bytes() == b"same"
        assert kept.earlier == (str(regular),)
        assert directory.is_dir() and link.is_symlink() and stat.S_ISFIFO(os.lstat(fifo).st_mode)
        assert smb.kept_copies(smb.REPLACED) == [str(regular), kept.path]

    @staticmethod
    def listed_once(monkeypatch, raced: Path) -> None:
        """kept_copies lists RACED first once, as if a copy had been replaced after the listing."""
        real = smb.kept_copies
        calls: list[str] = []

        def listing(kind: str) -> list[str]:
            calls.append(kind)
            return [str(raced), *real(kind)] if len(calls) == 1 else real(kind)

        monkeypatch.setattr(smb, "kept_copies", listing)

    @pytest.mark.parametrize("entry", ["link", "directory"])
    def test_an_entry_that_replaced_a_copy_after_the_listing_is_not_read(self, env, monkeypatch, entry):
        """The read opens a copy without following a symbolic link and checks with fstat that it
        is a regular file: a link to a file with these bytes, or a directory, is no copy, and a new
        copy is written (ADR-0362)."""
        raced = env.samba / "smb.conf.unos-replaced-20261101_120000_000000"
        if entry == "link":
            (env.samba / "notes").write_bytes(b"same")
            raced.symlink_to("notes")
        else:
            raced.mkdir()
        self.listed_once(monkeypatch, raced)
        kept = smb.keep_copy(smb.REPLACED, b"same")
        assert kept.path != str(raced) and Path(kept.path).read_bytes() == b"same"
        assert os.path.lexists(raced) and temp_files(env) == []

    def test_a_fifo_that_replaced_a_copy_is_not_waited_on(self, env, monkeypatch):
        """A FIFO opened for a blocking read would hold the apply until something writes to it;
        here a writer puts these bytes into it once a reader waits (ADR-0362)."""
        fifo = env.samba / "smb.conf.unos-replaced-20261101_120000_000000"
        os.mkfifo(fifo)
        done = threading.Event()

        def writer() -> None:
            while not done.wait(0.01):
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:  # nobody reads yet
                    continue
                try:
                    os.write(fd, b"same")
                except OSError:
                    pass
                finally:
                    os.close(fd)
                return

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        self.listed_once(monkeypatch, fifo)
        try:
            kept = smb.keep_copy(smb.REPLACED, b"same")
        finally:
            done.set()
            thread.join(5)
        assert kept.path != str(fifo) and Path(kept.path).read_bytes() == b"same"

    def test_no_copy_when_any_copy_holds_these_bytes(self, env):
        """A copy written while the clock was set back is named before a newer one: it still holds
        these bytes, so nothing is written (ADR-0362)."""
        newer = env.samba / "smb.conf.unos-replaced-20261110_120000_000000"
        newer.write_bytes(b"newer")
        set_back = env.samba / "smb.conf.unos-replaced-20261101_120000_000000"
        set_back.write_bytes(b"same")
        (env.samba / "smb.conf.unos-replaced-20261105_120000_000000").write_bytes(b"sam")  # shorter: not read

        kept = smb.keep_copy(smb.REPLACED, b"same")

        assert kept == smb.KeptCopy(path=str(set_back), earlier=(
            str(newer), str(env.samba / "smb.conf.unos-replaced-20261105_120000_000000")))
        assert copies(env) == [set_back.name, "smb.conf.unos-replaced-20261105_120000_000000", newer.name]
        assert temp_files(env) == []


# --- share mutations and the startup -----------------

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import src.database  # noqa: E402
from src.auth.utils import verify_token  # noqa: E402
from src.database import get_db  # noqa: E402
from src.shares import accounts, constants, interfaces, schemas  # noqa: E402
from src.storage import disks  # noqa: E402


@pytest.fixture
def mutations(env, monkeypatch):
    """Share folders can be opened and created on the pool "data" (tmp), as the runner's group."""
    monkeypatch.setattr(pools, "_mounted_pool_target", lambda name, *, write=False: str(env.base / name))
    monkeypatch.setattr(disks, "host_mounts", lambda: [SimpleNamespace(target=str(env.pool))])
    monkeypatch.setattr(accounts, "gid_problem", lambda: None)
    monkeypatch.setattr(constants, "SMB_GROUP_GID", os.getgid())
    return env


@pytest.fixture
def client(mutations):
    from src.main import app

    env = mutations
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool),
                               expire_on_commit=False)

    async def override_get_db():
        async with maker() as db:
            yield db

    from src.shares import router as shares_router

    shares_router.limiter.reset()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=ADMIN, username="admin", is_admin=True)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(verify_token, None)


class TestShareMutations:
    def test_testparm_rejection_rolls_the_mutation_back(self, env, client):
        add_share(env, "docs", "docs", access={ALICE: "write"})
        asyncio.run(apply(env))
        before = env.conf.read_bytes()
        env.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")

        created = client.post("/api/v1/shares/smb", json={"name": "new", "path": f"{env.pool}/new"})
        changed = client.patch("/api/v1/shares/smb/docs", json={"browseable": False})
        deleted = client.delete("/api/v1/shares/smb/docs")

        for response in (created, changed, deleted):
            assert response.status_code == 500
            assert response.json()["detail"] == "testparm rejected the new smb.conf: Error loading services."
        assert rows(env, "SELECT name, browseable, version FROM smb_shares") == [("docs", 1, 1)]
        assert rows(env, "SELECT username FROM smb_share_access") == [("alice",)]
        assert env.conf.read_bytes() == before
        assert temp_files(env) == []

    def test_a_failed_unos_replaced_copy_rolls_the_mutation_back(self, env, client, monkeypatch):
        add_share(env, "docs", "docs", access={ALICE: "write"})
        old = f"[global]\n   workgroup = OLD\n[legacy]\n   path = {env.pool}/legacy\n   read only = no\n"
        env.conf.write_text(old)
        real_link = os.link

        def link(src, dst, **kwargs):
            if os.path.basename(dst).startswith("smb.conf.unos-replaced-"):
                raise PermissionError(13, "Permission denied")
            return real_link(src, dst, **kwargs)

        monkeypatch.setattr(os, "link", link)
        created = client.post("/api/v1/shares/smb", json={"name": "new", "path": f"{env.pool}/new"})
        changed = client.patch("/api/v1/shares/smb/docs", json={"browseable": False})
        deleted = client.delete("/api/v1/shares/smb/docs")

        for response in (created, changed, deleted):
            assert response.status_code == 500
            assert response.json()["detail"].startswith(
                "smb.conf has shares that UNOS did not create (legacy), and they could not be copied to ")
        assert rows(env, "SELECT name, browseable, version FROM smb_shares") == [("docs", 1, 1)]
        assert rows(env, "SELECT username, level FROM smb_share_access") == [("alice", "write")]
        assert env.conf.read_text() == old
        assert temp_files(env) == [] and copies(env) == []
        assert env.host.calls == []

    async def test_a_mutation_imports_first_and_the_import_commits_none_of_its_rows(self, mutations, monkeypatch):
        env = mutations
        env.conf.write_text(f"[legacy]\n path = {env.pool}/legacy\n valid users = alice\n")
        committed_by_the_import = []
        real_run_import = service._run_import

        async def run_import(db):
            plan = await real_run_import(db)
            committed_by_the_import.append(rows(env, "SELECT name FROM smb_shares ORDER BY id"))
            return plan

        monkeypatch.setattr(service, "_run_import", run_import)
        data = schemas.SmbShareCreate(name="new", path=f"{env.pool}/new",
                                      access=[{"user_id": BOB, "username": "bob", "level": "write"}])
        async with session(env) as db:
            lock = service.smb_lock()
            await lock.acquire()
            task = asyncio.create_task(service.create_share(db, data))
            await asyncio.sleep(0.05)
            assert not task.done()
            # A restore whose import failed leaves the marker NULL while the mutation waits.
            sql(env, "UPDATE smb_state SET shares_imported_at = NULL")
            lock.release()
            change = await asyncio.wait_for(task, timeout=10)

        assert committed_by_the_import == [[("legacy",)]]
        assert rows(env, "SELECT name FROM smb_shares ORDER BY id") == [("legacy",), ("new",)]
        assert change.info.name == "new" and change.info.served is True
        assert env.conf.read_text() == expected(env, ("legacy", "legacy", "", False, ["alice"], []),
                                                ("new", "new", "", False, [], ["bob"]))

    @staticmethod
    async def _while_smb_lock_is_held(monkeypatch, start):
        """Start a share mutation while this test holds smb_lock (in the test's own event
        loop: smb_lock is one lock per loop). Returns what it did before the lock was
        released (import checks, finished) and its result."""
        seen: list[str] = []
        real = service._import_if_needed_locked

        async def recording(db):
            seen.append("import check")
            return await real(db)

        monkeypatch.setattr(service, "_import_if_needed_locked", recording)
        lock = service.smb_lock()
        await lock.acquire()
        task = asyncio.create_task(start())
        try:
            for _ in range(20):
                await asyncio.sleep(0.01)
            early = (list(seen), task.done())
        finally:
            lock.release()
        result = await asyncio.wait_for(task, timeout=10)
        return early, seen, result

    async def test_a_patch_waits_for_smb_lock(self, mutations, monkeypatch):
        """ADR-0080: a PATCH must not read or stage while another SMB change (a restore, the startup
        reconcile, an account flow, another share change) holds smb_lock."""
        env = mutations
        add_share(env, "docs", "docs", access={ALICE: "read"})
        await apply(env)
        async with session(env) as db:
            early, seen, change = await self._while_smb_lock_is_held(
                monkeypatch, lambda: service.update_share(db, "docs", schemas.SmbShareUpdate(browseable=False)))
        assert early == ([], False), "the PATCH ran while smb_lock was held"
        assert seen == ["import check"]
        assert change.details == "Changed share docs: browseable no"
        assert rows(env, "SELECT browseable, version FROM smb_shares") == [(0, 2)]

    async def test_a_delete_waits_for_smb_lock(self, mutations, monkeypatch):
        env = mutations
        add_share(env, "docs", "docs", access={ALICE: "read"})
        await apply(env)
        async with session(env) as db:
            early, seen, change = await self._while_smb_lock_is_held(
                monkeypatch, lambda: service.delete_share(db, "docs"))
        assert early == ([], False), "the DELETE ran while smb_lock was held"
        assert seen == ["import check"]
        assert change.details.startswith("Deleted share docs")
        assert rows(env, "SELECT name FROM smb_shares") == []
        assert "[docs]" not in env.conf.read_text()

    @pytest.mark.parametrize("mutation", ["patch", "delete"])
    async def test_a_patch_or_delete_imports_first_and_the_import_commits_none_of_its_changes(
        self, mutations, monkeypatch, mutation
    ):
        """ADR-0084: the marker became NULL while the mutation waited on smb_lock (a
        restore whose import failed): the mutation imports first, and that commit holds only
        the import."""
        env = mutations
        add_share(env, "docs", "docs", access={ALICE: "write"})
        env.conf.write_text(f"[legacy]\n path = {env.pool}/legacy\n valid users = alice\n")
        committed_by_the_import = []
        real_run_import = service._run_import

        async def run_import(db):
            plan = await real_run_import(db)
            committed_by_the_import.append(rows(env, "SELECT name, browseable, version FROM smb_shares ORDER BY id"))
            return plan

        monkeypatch.setattr(service, "_run_import", run_import)
        async with session(env) as db:
            lock = service.smb_lock()
            await lock.acquire()
            if mutation == "patch":
                task = asyncio.create_task(service.update_share(db, "docs", schemas.SmbShareUpdate(browseable=False)))
            else:
                task = asyncio.create_task(service.delete_share(db, "docs"))
            await asyncio.sleep(0.05)
            assert not task.done()
            sql(env, "UPDATE smb_state SET shares_imported_at = NULL")
            lock.release()
            await asyncio.wait_for(task, timeout=10)

        assert committed_by_the_import == [[("docs", 1, 1), ("legacy", 1, 1)]]
        names = [s.name for s in smbconf.parse_smb_conf(env.conf.read_text()).sections]
        if mutation == "patch":
            assert rows(env, "SELECT name, browseable, version FROM smb_shares ORDER BY id") == [
                ("docs", 0, 2), ("legacy", 1, 1)]
            assert names == ["docs", "legacy"]
            assert "   browseable = no\n" in env.conf.read_text().split("[legacy]")[0]
        else:
            assert rows(env, "SELECT name, browseable, version FROM smb_shares ORDER BY id") == [("legacy", 1, 1)]
            assert names == ["legacy"]

    async def test_a_mutation_whose_import_fails_changes_nothing(self, mutations, monkeypatch):
        env = mutations
        sql(env, "UPDATE smb_state SET shares_imported_at = NULL")
        env.conf.write_text(f"[legacy]\n path = {env.pool}/legacy\n")
        monkeypatch.setattr(service, "_plan_import", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        async with session(env) as db:
            with pytest.raises(smb.SambaConfigError,
                               match=r"^Shares were not imported from smb.conf yet: unexpected RuntimeError \("):
                await service.create_share(db, schemas.SmbShareCreate(name="new", path=f"{env.pool}/new"))
        assert rows(env, "SELECT name FROM smb_shares") == []
        assert not (env.pool / "new").exists()


class TestStartupStaleFiles:
    async def test_stale_temp_files_are_removed_at_startup(self, env, monkeypatch):
        maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool),
                                   expire_on_commit=False)
        monkeypatch.setattr(src.database, "async_session", maker)
        monkeypatch.setattr(accounts, "reconcile_accounts", AsyncMock(return_value=interfaces.ReconcileResult()))
        monkeypatch.setattr(accounts, "read_smb_sessions", MagicMock(return_value=[]))
        monkeypatch.setattr(accounts, "end_smb_sessions", MagicMock(return_value=[]))
        monkeypatch.setattr(service, "_last_reconcile", service.LastReconcile())
        for name in (".smb.conf.abc123.unos-tmp", ".smb.conf.x.unos-tmp"):
            (env.samba / name).write_text("half-written")

        await service.reconcile_smb_on_startup()

        assert temp_files(env) == []
        assert sorted(os.listdir(env.samba)) == ["smb.conf"]


class TestDatabaseEngine:
    """The app's engine (database.engine_options): share mutations hold SQLite's write lock
    while testparm runs, and smb_lock holders may need a second connection while requests
    waiting for the lock hold one each (ADR-0079)."""

    def test_options(self):
        options = src.database.engine_options("/data/unos.db")
        assert options == {"echo": False, "connect_args": {"timeout": 15}, "max_overflow": -1}
        assert "max_overflow" not in src.database.engine_options(":memory:")  # one shared connection

    async def test_other_writers_wait_15_seconds_and_connections_never_run_out(self, tmp_path):
        path = str(tmp_path / "unos.db")
        options = src.database.engine_options(path)
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}", pool_timeout=1, **options)
        connections = []
        try:
            for _ in range(20):  # pool_size 5: without max_overflow=-1 the 16th waits, then fails
                connections.append(await engine.connect())
            busy = await connections[-1].exec_driver_sql("PRAGMA busy_timeout")
            assert busy.scalar() == 15000
        finally:
            for connection in connections:
                await connection.close()
            await engine.dispose()

    async def test_the_app_uses_these_options(self):
        """src.database.engine, the engine of every request (the tests' database is in memory)."""
        async with src.database.engine.connect() as connection:
            busy = await connection.exec_driver_sql("PRAGMA busy_timeout")
            assert busy.scalar() == 15000

    def test_the_app_uses_these_options_for_a_database_file(self, tmp_path):
        """src.database.engine as the supervisor builds it from UNOS_DB_PATH, in a new interpreter."""
        script = (
            "import asyncio\n"
            "from src import database\n"
            "async def main():\n"
            "    # pool_size 5: without max_overflow=-1 the 16th connection waits 30 s for a free one\n"
            "    connections = [await asyncio.wait_for(database.engine.connect(), 5) for _ in range(20)]\n"
            "    busy = (await connections[-1].exec_driver_sql('PRAGMA busy_timeout')).scalar()\n"
            "    for connection in connections:\n"
            "        await connection.close()\n"
            "    await database.engine.dispose()\n"
            "    print('busy_timeout', busy)\n"
            "asyncio.run(main())\n"
        )
        env = {**os.environ, "UNOS_DB_PATH": str(tmp_path / "unos.db"), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.split() == ["busy_timeout", "15000"]

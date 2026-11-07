"""The SMB startup reconcile (ADR-0138): service.reconcile_smb_on_startup.

The functions of shares/accounts.py are mocked: accounts.reconcile_accounts (an AsyncMock returning
a ReconcileResult), accounts.read_smb_sessions (returning SmbSession values)
and accounts.end_smb_sessions. The real ``smbcontrol <pid> shutdown`` is
asserted in test_smb_integration.py.

Each test has its own database file (alice, bob and carol have active SMB
accounts, dave has none), pools base, smb.conf and audit log; host commands
go to ``FakeSmbHost`` and the pool list is ``{"data"}``.
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import src.database
import src.host
from src import main
from src.audit import router as audit_router
from src.auth.models import User
from src.auth.utils import verify_token
from src.database import Base, get_db
from src.host import HostResult
from src.security import validators
from src.shares import accounts, interfaces, service, smb, smbconf
from src.shares.models import SmbAccount, SmbState
from src.storage import disks, pools

HASH = "$2b$12$" + "x" * 53
ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4
NAMES = {ALICE: "alice", BOB: "bob", CAROL: "carol", DAVE: "dave"}
RELOAD = ["smbcontrol", "all", "reload-config"]


class FakeSmbHost:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail: dict[tuple[str, ...], object] = {}

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        assert "close-denied-share" not in cmd
        for prefix, outcome in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
        if cmd[0] in ("mkdir", "testparm", "smbcontrol", "systemctl"):
            return HostResult(0, "", "")
        raise AssertionError(f"unexpected host command: {cmd}")

    def closed(self) -> list[str]:
        return [cmd[3] for cmd in self.calls if cmd[:3] == ["smbcontrol", "smbd", "close-share"]]

    def tested(self) -> int:
        return sum(cmd[0] == "testparm" for cmd in self.calls)


def make_database(path: Path, *, imported: bool = True) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for user_id, name in NAMES.items():
            session.add(User(id=user_id, username=name, hashed_password=HASH, is_admin=False))
        session.add(User(id=9, username="admin", hashed_password=HASH, is_admin=True))
        for user_id in (ALICE, BOB, CAROL):
            session.add(SmbAccount(user_id=user_id, linux_name=NAMES[user_id], uid=70000 + user_id,
                                   enabled=True, password_state="set"))
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
    db_path = tmp_path / "unos.db"
    make_database(db_path)
    for settings in {id(smb.settings): smb.settings, id(validators.settings): validators.settings}.values():
        monkeypatch.setattr(settings, "smb_conf_path", str(samba / "smb.conf"))
        monkeypatch.setattr(settings, "pools_base_path", str(base))
        monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
    monkeypatch.setattr(disks, "host_mounts", lambda: [SimpleNamespace(target=str(base / "data"))])
    host = FakeSmbHost()
    monkeypatch.setattr(src.host, "run_on_host", host.run)
    audit = tmp_path / "audit_log.json"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(audit))
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    monkeypatch.setattr(service, "_last_import_problem", None)
    monkeypatch.setattr(service, "_last_reconcile", service.LastReconcile())
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool),
                               expire_on_commit=False)
    monkeypatch.setattr(src.database, "async_session", maker)
    reconcile = AsyncMock(return_value=interfaces.ReconcileResult())
    sessions = MagicMock(return_value=[])
    end = MagicMock(return_value=[])
    monkeypatch.setattr(accounts, "reconcile_accounts", reconcile)
    monkeypatch.setattr(accounts, "read_smb_sessions", sessions)
    monkeypatch.setattr(accounts, "end_smb_sessions", end)
    monkeypatch.setattr(accounts, "gid_problem", lambda: None)
    monkeypatch.setattr(accounts, "host_status",
                        lambda: interfaces.AccountHostStatus(tools_missing=[], group_problem=None))
    yield SimpleNamespace(base=base, pool=base / "data", samba=samba, conf=samba / "smb.conf", db_path=db_path,
                          host=host, audit=audit, maker=maker, reconcile=reconcile, sessions=sessions, end=end)


def sql(env, *statements):
    with closing(sqlite3.connect(env.db_path)) as conn:
        for statement in statements:
            conn.execute(statement)
        conn.commit()


def add_share(env, name, folder, access=(), guest_ok=False, path=None):
    with closing(sqlite3.connect(env.db_path)) as conn:
        share_id = conn.execute(
            "INSERT INTO smb_shares (name, path, comment, browseable, guest_ok, version) VALUES (?, ?, '', 1, ?, 1)",
            (name, path or f"{env.pool}/{folder}", int(guest_ok)),
        ).lastrowid
        for user_id, level in access:
            conn.execute("INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (?, ?, ?, ?)",
                         (share_id, user_id, NAMES[user_id], level))
        conn.commit()


def session(pid, username, *shares):
    return interfaces.SmbSession(pid=pid, username=username, shares=tuple(sorted(shares)))


async def start():
    await service.reconcile_smb_on_startup()


async def smb_status(env) -> dict:
    """GET /api/v1/shares/smb-status as an administrator, on the test database."""

    async def override_get_db():
        async with env.maker() as db:
            yield db

    main.app.dependency_overrides[get_db] = override_get_db
    main.app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=9, username="admin", is_admin=True)
    try:
        return await asyncio.to_thread(lambda: TestClient(main.app).get("/api/v1/shares/smb-status").json())
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(verify_token, None)


class TestConsistentState:
    async def test_a_second_start_writes_nothing(self, env, monkeypatch, caplog):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        await start()  # the first start renders smb.conf
        assert "[docs]" in env.conf.read_text()
        env.host.calls.clear()
        env.audit.unlink()
        mtime = os.stat(env.conf).st_mtime_ns
        commits = []
        real_commit = AsyncSession.commit

        async def counting_commit(self):
            commits.append(1)
            return await real_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", counting_commit)
        with caplog.at_level(logging.INFO, logger="src.shares.service"):
            await start()

        assert env.host.calls == [RELOAD]  # no testparm, no write: only the reload message
        assert os.stat(env.conf).st_mtime_ns == mtime
        assert sorted(os.listdir(env.samba)) == ["smb.conf"]
        assert commits == []
        assert not env.audit.exists()
        assert "SMB state is consistent; nothing changed" in caplog.text
        env.reconcile.assert_awaited()
        assert service.last_reconcile().problems == []

    async def test_the_first_start_is_audited(self, env):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        await start()
        (entry,) = json.loads(env.audit.read_text())
        assert (entry["action"], entry["username"], entry["resource_type"]) == ("other", "system", "smb")
        assert entry["details"] == "SMB startup reconcile: smb.conf was rewritten"


class TestSessionSweep:
    async def test_shares_not_served_or_not_available_are_closed_and_other_users_ended(self, env):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        add_share(env, "media", "media")                       # no principal: not available
        add_share(env, "DATA", "", path=str(env.pool))          # the pool root: not served
        add_share(env, "public", "public", guest_ok=True)      # guests: available
        env.sessions.return_value = [
            session(101, "alice", "IPC$", "docs"),
            session(102, "nobody", "IPC$", "public"),
            session(103, "70004", "old"),                        # a deleted account
            session(104, "nas", "IPC$", "DATA"),                 # a foreign account
            session(105, "bob", "media"),
        ]

        await start()

        assert env.host.closed() == ["DATA", "media", "old"]
        assert "IPC$" not in env.host.closed()
        env.end.assert_called_once_with(["70004", "nas", "nobody"])
        entries = json.loads(env.audit.read_text())
        assert "Closed the open connections to DATA, media, old (not served or without access)" in entries[0]["details"]
        assert "Ended the SMB sessions of 70004, nas, nobody" in entries[0]["details"]

    async def test_close_after_the_reload(self, env):
        add_share(env, "media", "media")
        env.sessions.return_value = [session(105, "bob", "media")]
        await start()
        reload = env.host.calls.index(RELOAD)
        close = env.host.calls.index(["smbcontrol", "smbd", "close-share", "media"])
        assert reload < close

    async def test_a_failed_close_is_retried_at_the_next_apply(self, env):
        add_share(env, "media", "media")
        env.sessions.return_value = [session(105, "bob", "media")]
        env.host.fail[("smbcontrol", "smbd", "close-share")] = HostResult(1, "", "smbd not running")
        await start()
        assert service.last_apply().pending_close == {"media"}

    async def test_sessions_that_cannot_be_listed(self, env):
        env.sessions.return_value = None
        await start()
        env.end.assert_not_called()
        assert service.last_reconcile().problems == [
            "Open SMB connections could not be listed; connections of accounts that are not active end when "
            "their clients disconnect"]


class TestFailedImport:
    @pytest.fixture
    def not_imported(self, env, monkeypatch):
        env.db_path.unlink()
        make_database(env.db_path, imported=False)
        env.conf.write_text(f"[global]\n   workgroup = HOME\n[docs]\n   path = {env.pool}/docs\n   valid users = alice\n")

        def broken(*args, **kwargs):
            raise RuntimeError("import exploded")

        monkeypatch.setattr(service, "_plan_import", broken)
        env.sessions.return_value = [session(101, "alice", "docs"), session(102, "nobody", "docs")]
        return env

    async def test_no_apply_and_no_close_but_the_other_steps(self, not_imported):
        env = not_imported
        before = env.conf.read_bytes()

        await start()

        assert env.conf.read_bytes() == before
        assert env.host.calls == [RELOAD]  # no testparm, no close-share
        env.reconcile.assert_awaited_once()
        env.end.assert_called_once_with(["nobody"])
        assert service.last_import_problem() == "Shares were not imported from smb.conf: import exploded"

    async def test_the_problem_is_in_the_status(self, not_imported):
        await start()
        from src.main import app

        async def override_get_db():
            async with not_imported.maker() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=9, username="admin", is_admin=True)
        try:
            status = await asyncio.to_thread(lambda: TestClient(app).get("/api/v1/shares/smb-status").json())
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(verify_token, None)
        assert "Shares were not imported from smb.conf: import exploded" in status["problems"]


class TestSteps:
    @pytest.mark.parametrize("step", ["stale files", "purge", "accounts", "apply", "render"])
    async def test_a_step_that_raises_does_not_stop_the_others(self, env, monkeypatch, step):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        env.sessions.return_value = [session(102, "nobody", "IPC$")]

        def boom(*args, **kwargs):
            raise RuntimeError(f"{step} exploded")

        async def async_boom(*args, **kwargs):
            raise RuntimeError(f"{step} exploded")

        if step == "stale files":
            monkeypatch.setattr(smb, "remove_stale_temp_files", boom)
        elif step == "purge":
            monkeypatch.setattr(service, "purge_orphans", async_boom)
        elif step == "accounts":
            env.reconcile.side_effect = RuntimeError("accounts exploded")
        elif step == "apply":
            monkeypatch.setattr(service, "apply_smb_config", async_boom)
        else:
            monkeypatch.setattr(service, "build_render_input", async_boom)

        await start()

        problems = service.last_reconcile().problems
        assert len(problems) == 1 and f"{step} exploded" in problems[0]
        env.reconcile.assert_awaited_once()
        assert RELOAD in env.host.calls
        env.end.assert_called_once_with(["nobody"])
        if step not in ("apply",):
            assert "[docs]" in env.conf.read_text()

    async def test_a_failed_apply_is_kept_in_last_apply(self, env):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        env.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")
        await start()
        assert not env.conf.exists()
        assert service.last_apply().ok is False
        assert "testparm rejected the new smb.conf" in service.last_apply().message
        assert env.host.calls[-1] == RELOAD  # the reload message is sent anyway

    async def test_a_failed_reload_is_pending(self, env):
        env.host.fail[("smbcontrol",)] = HostResult(1, "", "no smbd")
        env.host.fail[("systemctl",)] = HostResult(1, "", "not active")
        env.conf.write_text(smbconf.render_config([]))  # consistent: no share
        await start()
        assert service.last_apply().reload_pending is True

    async def test_a_reload_with_systemctl_only_stays_pending(self, env, caplog):
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "no smbd")
        env.conf.write_text(smbconf.render_config([]))  # consistent: no share
        with caplog.at_level(logging.WARNING, logger="src.shares.service"):
            await start()
        assert env.host.calls == [RELOAD, ["systemctl", "reload", "smbd"]]
        assert (service.last_apply().reload_pending, service.last_apply().reload_partial) == (True, True)
        assert "Samba was reloaded with systemctl only (smbcontrol: no smbd)" in caplog.text

    @pytest.mark.parametrize("systemctl", ["works", "fails"])
    async def test_a_close_before_the_full_reload_is_repeated_after_it(self, env, systemctl):
        """The sweep closes media while smbd processes may still run the old file (the main
        smbd alone reloaded, or nothing did): a process with no connection to media did not
        reload, and a client could connect to media through it with the old access, which its
        tree connect keeps. The next full reload closes media again."""
        add_share(env, "media", "media")  # no principal: not available
        env.conf.write_text(smbconf.render_config([service.ShareRender("media", f"{env.pool}/media", "", True, False,
                                                                       (), ())]))  # consistent
        env.sessions.return_value = [session(105, "bob", "media")]
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "no smbd")
        if systemctl == "fails":
            env.host.fail[("systemctl",)] = HostResult(1, "", "not active")

        await start()

        assert env.host.calls == [RELOAD, ["systemctl", "reload", "smbd"],
                                  ["smbcontrol", "smbd", "close-share", "media"]]
        assert service.last_apply().close_after_full_reload == {"media"}

        env.host.fail.clear()
        env.host.calls.clear()
        async with env.maker() as db:
            result = await service.apply_smb_config(db)  # the text is unchanged: the full reload, then the close

        assert env.host.calls == [RELOAD, ["smbcontrol", "smbd", "close-share", "media"]]
        assert (result.reloaded, result.closed_shares) == (True, ["media"])
        assert (service.last_apply().reload_pending, service.last_apply().close_after_full_reload) == (False, set())

    async def test_the_startup_reload_closes_pending_shares_again_after_the_full_reload(self, env):
        """Step 5 of _reconcile (_reload_now) closes the pending shares after its reload; after a
        reload with systemctl only they are closed again after the next full reload."""
        service.last_apply().pending_close = {"docs"}
        env.host.fail[("smbcontrol", "all")] = HostResult(1, "", "no smbd")

        await service._reload_now(service._Run(), True)

        assert env.host.calls == [RELOAD, ["systemctl", "reload", "smbd"],
                                  ["smbcontrol", "smbd", "close-share", "docs"]]
        assert (service.last_apply().pending_close, service.last_apply().close_after_full_reload) == (set(), {"docs"})

        env.host.fail.clear()
        env.host.calls.clear()
        run = service._Run()
        await service._reload_now(run, True)

        assert env.host.calls == [RELOAD, ["smbcontrol", "smbd", "close-share", "docs"]]
        assert run.changes == ["Closed the open connections to docs"]
        assert (service.last_apply().pending_close, service.last_apply().close_after_full_reload) == (set(), set())

    async def test_without_the_import_marker_a_full_reload_leaves_the_shares_pending(self, env):
        env.conf.write_text(smbconf.render_config([]))  # consistent: no share
        service.last_apply().close_after_full_reload = {"docs"}
        service.last_apply().reload_pending = service.last_apply().reload_partial = True

        await service._reload_now(service._Run(), False)  # after a failed import: no close-share

        assert env.host.calls == [RELOAD]
        assert (service.last_apply().pending_close, service.last_apply().close_after_full_reload) == ({"docs"}, set())
        env.host.calls.clear()
        async with env.maker() as db:
            result = await service.apply_smb_config(db)  # nothing to reload: the pending close runs right away
        assert (result.reloaded, result.closed_shares) == (None, ["docs"])
        assert env.host.calls == [["smbcontrol", "smbd", "close-share", "docs"]]

    async def test_stale_temporary_files_are_removed(self, env):
        (env.samba / ".smb.conf.abc.unos-tmp").write_text("x")
        await start()
        assert sorted(os.listdir(env.samba)) == ["smb.conf"]

    async def test_stale_access_rows_are_purged(self, env):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        sql(env, "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 77, 'ghost', 'read')")
        await start()
        with closing(sqlite3.connect(env.db_path)) as conn:
            assert conn.execute("SELECT username FROM smb_share_access").fetchall() == [("alice",)]
        assert "Removed 1 stale share access row(s)" in json.loads(env.audit.read_text())[0]["details"]


class TestProblemsOnly:
    async def test_no_audit_entry_and_one_warning_per_start(self, env, caplog):
        env.conf.write_text(smbconf.render_config([]))  # consistent: no share
        env.reconcile.return_value = interfaces.ReconcileResult(problems=["SMB tools missing on the host: smbpasswd"])
        for _ in range(2):
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                await start()
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and r.name == "src.shares.service"]
            assert len(warnings) == 1
            assert "SMB tools missing on the host: smbpasswd" in warnings[0].getMessage()
        assert not env.audit.exists()
        assert service.last_reconcile().problems == ["SMB tools missing on the host: smbpasswd"]
        assert (await smb_status(env))["problems"] == ["SMB tools missing on the host: smbpasswd"]


# --- the startup task and the first request (ADR-0138) ----------------------------------------------


def _quiet_lifespan(monkeypatch):
    """The other background loops and startup checks of the lifespan do nothing."""

    async def noop(*args, **kwargs):
        return None

    for name in ("start_scheduler", "stop_scheduler", "start_health_monitors", "stop_health_monitors",
                 "start_ups_monitor", "stop_ups_monitor", "check_host_tools"):
        monkeypatch.setattr(main, name, noop)
    monkeypatch.setattr(main, "reconcile_pools_on_startup", AsyncMock(return_value=[]))
    monkeypatch.delenv("UNOS_DISABLE_BACKGROUND_TASKS", raising=False)


class TestFirstRequest:
    @pytest.fixture
    def app_client(self, env, monkeypatch):
        _quiet_lifespan(monkeypatch)
        release = threading.Event()
        started = threading.Event()
        real_remove = smb.remove_stale_temp_files

        def slow_first_step():
            # Step 1, before the import: the reconcile holds smb_lock from its first statement.
            started.set()
            release.wait(10)
            return real_remove()

        monkeypatch.setattr(smb, "remove_stale_temp_files", slow_first_step)

        async def override_get_db():
            async with env.maker() as db:
                yield db

        main.app.dependency_overrides[get_db] = override_get_db
        main.app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=9, username="admin", is_admin=True)
        try:
            yield SimpleNamespace(release=release, started=started)
        finally:
            release.set()
            main.app.dependency_overrides.pop(get_db, None)
            main.app.dependency_overrides.pop(verify_token, None)

    def _first_get(self, client) -> tuple[threading.Thread, dict]:
        answer: dict = {}

        def get():
            answer["response"] = client.get("/api/v1/shares/smb")
            answer["at"] = time.monotonic()

        thread = threading.Thread(target=get)
        thread.start()
        return thread, answer

    def test_before_the_import_the_first_get_waits_for_the_reconcile(self, env, app_client):
        env.db_path.unlink()
        make_database(env.db_path, imported=False)
        env.conf.write_text(f"[docs]\n   path = {env.pool}/docs\n   valid users = alice\n")

        with TestClient(main.app) as client:
            assert app_client.started.wait(5)  # the reconcile runs and holds smb_lock
            thread, answer = self._first_get(client)
            thread.join(0.5)
            assert thread.is_alive()  # waiting for smb_lock
            released = time.monotonic()
            app_client.release.set()
            thread.join(10)
            assert answer["at"] >= released
            assert answer["response"].status_code == 200
            assert [share["name"] for share in answer["response"].json()] == ["docs"]

    def test_after_the_import_the_first_get_does_not_wait(self, env, app_client):
        add_share(env, "docs", "docs", access=[(ALICE, "write")])
        with TestClient(main.app) as client:
            assert app_client.started.wait(5)
            thread, answer = self._first_get(client)
            thread.join(10)
            assert not thread.is_alive()  # ensure_imported took no lock
            assert answer["response"].status_code == 200
            app_client.release.set()

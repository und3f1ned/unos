"""The share API: /shares/smb, /shares/smb-status.

Each test has its own database file, pools base (with the managed pool
"data"), smb.conf and audit log. Host commands go to ``FakeSmbHost`` behind
``src.host.run_on_host``; ``pools.managed_pool_names``,
``pools._mounted_pool_target`` and ``disks.host_mounts`` are patched, and so
are ``accounts.gid_problem`` and ``accounts.host_status`` (shares/accounts.py).
``constants.SMB_GROUP_GID`` is the runner's own gid, so that new folders can
get it without root.

Users: alice, bob and carol have active SMB accounts; dave has none (off);
erin is allowed but needs a password; admin is an administrator; Bob.Smith
has a name SMB cannot use.
"""

import asyncio
import errno
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import src.host
from src.audit import router as audit_router
from src.auth.models import User
from src.auth.utils import verify_token
from src.database import Base, get_db
from src.host import HostResult
from src.main import app
from src.security import validators
from src.shares import accounts, constants, interfaces, repair, service, smb, smbconf
from src.shares import router as shares_router
from src.shares.models import SmbAccount, SmbState
from src.storage import disks, pools

from .samba_fake import FakeSambaHost
from .audit_helpers import audit_entries

# The real functions, before any fixture replaces them (tests/conftest.py replaces the
# mount check where /proc/self/mountinfo exists: the tests' pools are no mount points).
REAL_POOL_ROOT_MOUNT_PROBLEM = service._pool_root_mount_problem
REAL_GID_PROBLEM = accounts.gid_problem
REAL_HOST_STATUS = accounts.host_status

API = "/api/v1/shares"
HASH = "$2b$12$" + "x" * 53
ALICE, BOB, CAROL, DAVE, ERIN, ADMIN, LEGACY = 1, 2, 3, 4, 5, 6, 7
NAMES = {ALICE: "alice", BOB: "bob", CAROL: "carol", DAVE: "dave", ERIN: "erin", ADMIN: "admin", LEGACY: "Bob.Smith"}
POOL_ROOT = service.POOL_ROOT_PROBLEM
APPS = "App data (data/apps) cannot be shared: apps keep their configuration there"
REPLACED = (
    "smb.conf was replaced or removed after UNOS wrote it; it is written again at the next change or "
    "restart, and SMB cannot be turned on until then"
)


class FakeSmbHost:
    """mkdir, testparm, smbcontrol and systemctl; ``fail`` maps an argv prefix to a HostResult or exception."""

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


def make_database(path: Path, *, imported: bool = True) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for user_id, name in NAMES.items():
            session.add(User(id=user_id, username=name, hashed_password=HASH, is_admin=user_id == ADMIN))
        for user_id in (ALICE, BOB, CAROL):
            session.add(SmbAccount(user_id=user_id, linux_name=NAMES[user_id], uid=70000 + user_id,
                                   enabled=True, password_state="set"))
        session.add(SmbAccount(user_id=ERIN, linux_name="erin", uid=None, enabled=True, password_state="needed"))
        session.add(SmbState(id=1, next_uid=70010,
                             shares_imported_at=datetime(2026, 11, 6, 12) if imported else None))
        session.commit()
    engine.dispose()


class Api:
    """The test client and the paths of one test."""

    def __init__(self, client, tmp_path, host, mounted):
        self.client = client
        self.tmp = tmp_path
        self.base = tmp_path / "pools"
        self.pool = self.base / "data"
        self.samba = tmp_path / "samba"
        self.conf = self.samba / "smb.conf"
        self.db_path = tmp_path / "unos.db"
        self.audit = tmp_path / "audit.jsonl"
        self.host = host
        self.mounted = mounted  # calls of pools._mounted_pool_target: (pool, write)

    def create(self, name="docs", folder="docs", **fields):
        body = {"name": name, "path": f"{self.pool}/{folder}" if folder is not None else str(self.pool)}
        body.update(fields)
        return self.client.post(f"{API}/smb", json=body)

    def patch(self, share, **fields):
        return self.client.patch(f"{API}/smb/{share}", json=fields)

    def get(self, share):
        return self.client.get(f"{API}/smb/{share}")

    def rows(self, query, params=()):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(query, params).fetchall()

    def sql(self, *statements):
        with closing(sqlite3.connect(self.db_path)) as conn:
            for statement in statements:
                conn.execute(statement)
            conn.commit()

    def audit_entries(self):
        return audit_entries(self.audit)

    def temp_files(self):
        return sorted(name for name in os.listdir(self.samba) if name.endswith(".unos-tmp"))


def access(*entries):
    return [{"user_id": user_id, "username": NAMES[user_id], "level": level} for user_id, level in entries]


async def apply_smb_config(api) -> interfaces.ApplyResult:
    """service.apply_smb_config in a session of its own, as an account flow runs it after its commit."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{api.db_path}", poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await service.apply_smb_config(db)
    finally:
        await engine.dispose()


@pytest.fixture
def api(tmp_path, monkeypatch):
    base = tmp_path / "pools"
    (base / "data").mkdir(parents=True)
    (tmp_path / "samba").mkdir()
    db_path = tmp_path / "unos.db"
    make_database(db_path)
    for settings in {id(smb.settings): smb.settings, id(validators.settings): validators.settings}.values():
        monkeypatch.setattr(settings, "smb_conf_path", str(tmp_path / "samba" / "smb.conf"))
        monkeypatch.setattr(settings, "pools_base_path", str(base))
        monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data", "ghost"})
    mounted: list[tuple[str, bool]] = []

    def mounted_pool_target(name, *, write=False):
        mounted.append((name, write))
        if name == "ghost":
            raise pools.PoolNotFoundError(f"Pool '{name}' not found")
        return str(base / name)

    monkeypatch.setattr(pools, "_mounted_pool_target", mounted_pool_target)
    monkeypatch.setattr(disks, "host_mounts", lambda: [SimpleNamespace(target=str(base / "data"))])
    monkeypatch.setattr(accounts, "gid_problem", lambda: None)
    monkeypatch.setattr(accounts, "host_status",
                        lambda: interfaces.AccountHostStatus(tools_missing=[], group_problem=None))
    monkeypatch.setattr(constants, "SMB_GROUP_GID", os.getgid())
    host = FakeSmbHost()
    monkeypatch.setattr(src.host, "run_on_host", host.run)
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    monkeypatch.setattr(service, "_last_import_problem", None)
    monkeypatch.setattr(service, "_last_reconcile", service.LastReconcile())
    shares_router.limiter.reset()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=ADMIN, username="admin", is_admin=True)
    try:
        yield Api(TestClient(app), tmp_path, host, mounted)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(verify_token, None)
        shares_router.limiter.reset()


def section(api, name) -> str:
    """The rendered section of ``name`` in smb.conf ('' when it has none)."""
    text = api.conf.read_text() if api.conf.exists() else ""
    for part in text.split("\n\n"):
        if part.startswith(f"[{name}]\n"):
            return part.rstrip("\n")
    return ""


# --- create --------------------------------------------------------------------------


class TestCreate:
    def test_a_missing_folder_is_created_for_the_smb_group(self, api):
        response = api.create(access=access((ALICE, "write"), (BOB, "read")), comment="Team documents")

        assert response.status_code == 200, response.text
        body = response.json()
        folder = api.pool / "docs"
        st = os.stat(folder)
        assert stat.S_IMODE(st.st_mode) == 0o2770 and st.st_gid == os.getgid()
        assert body["name"] == "docs" and body["path"] == str(folder)
        assert (body["served"], body["path_problem"], body["available"]) == (True, None, True)
        assert (body["pool"], body["pool_mounted"], body["permissions_ok"]) == ("data", True, True)
        assert (body["version"], body["import_notes"], body["warnings"]) == (1, [], [])
        assert body["access"] == [
            {"user_id": ALICE, "username": "alice", "level": "write", "smb_state": "active", "effective": True},
            {"user_id": BOB, "username": "bob", "level": "read", "smb_state": "active", "effective": True},
        ]
        assert section(api, "docs") == smbconf.render_share(
            service.ShareRender("docs", str(folder), "Team documents", True, False, ("bob",), ("alice",))
        ).rstrip("\n")
        assert api.mounted == [("data", True)]
        assert api.rows("SELECT name, path, version FROM smb_shares") == [("docs", str(folder), 1)]
        assert api.rows("SELECT user_id, username, level FROM smb_share_access ORDER BY user_id") == [
            (ALICE, "alice", "write"), (BOB, "bob", "read")]

    def test_an_existing_folder_is_never_changed(self, api):
        folder = api.pool / "media"
        folder.mkdir()
        os.chmod(folder, 0o755)
        before = os.stat(folder)

        response = api.create("media", "media")

        assert response.status_code == 200, response.text
        after = os.stat(folder)
        assert (stat.S_IMODE(after.st_mode), after.st_gid) == (stat.S_IMODE(before.st_mode), before.st_gid)
        assert response.json()["permissions_ok"] is False
        assert response.json()["warnings"] == [service.PERMISSIONS_WARNING]
        assert api.mounted == [("data", False)]

    def test_unmounted_pool(self, api, monkeypatch):
        def not_mounted(name, *, write=False):
            raise pools.PoolConflictError(f"Pool '{name}' is not mounted at {api.pool}. Mount it on the Storage page first.")

        monkeypatch.setattr(pools, "_mounted_pool_target", not_mounted)
        response = api.create()
        assert response.status_code == 409
        assert "is not mounted" in response.json()["detail"]
        assert not (api.pool / "docs").exists()
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_unknown_pool(self, api):
        (api.base / "ghost").mkdir()
        response = api.client.post(f"{API}/smb", json={"name": "docs", "path": f"{api.base}/ghost/docs"})
        assert (response.status_code, response.json()["detail"]) == (
            400, "Share path must be on a storage pool: Pool 'ghost' not found")
        assert not (api.base / "ghost" / "docs").exists()

    def test_a_pool_that_unos_does_not_manage(self, api):
        (api.base / "default").mkdir()
        response = api.client.post(f"{API}/smb", json={"name": "docs", "path": f"{api.base}/default/docs"})
        assert (response.status_code, response.json()["detail"]) == (
            400, "Share path must be on a storage pool that UNOS manages: 'default' is not one")

    @pytest.mark.parametrize("folder", [".snapshots", "x/.snapshots", ".snapshots/old"])
    def test_snapshots(self, api, folder):
        response = api.create(folder=folder)
        assert (response.status_code, response.json()["detail"]) == (400, "Snapshot folders cannot be shared")

    @pytest.mark.parametrize("folder", [None, "apps", "apps/x"])
    def test_pool_root_and_app_data(self, api, folder):
        response = api.create(folder=folder)
        assert response.status_code == 400
        assert response.json()["detail"] == (POOL_ROOT if folder is None else APPS)
        assert not (api.pool / "apps").exists()

    def test_a_volume_root_is_accepted(self, api):
        (api.pool / "media").mkdir()
        response = api.create("media", "media")
        assert response.status_code == 200, response.text
        assert response.json()["served"] is True

    @pytest.mark.parametrize(
        "written, detail",
        [("{pool}/", POOL_ROOT), ("{pool}/.", POOL_ROOT), ("{pool}//apps", APPS), ("{pool}/./apps/x", APPS)],
    )
    def test_paths_not_in_normal_form_are_checked_normalized(self, api, written, detail):
        response = api.client.post(f"{API}/smb", json={"name": "docs", "path": written.format(pool=api.pool)})
        assert (response.status_code, response.json()["detail"]) == (400, detail)

    def test_a_path_is_stored_normalized(self, api):
        response = api.client.post(f"{API}/smb", json={"name": "docs", "path": f"{api.pool}//shared/"})
        assert response.status_code == 200, response.text
        assert response.json()["path"] == f"{api.pool}/shared"
        assert api.rows("SELECT path FROM smb_shares") == [(f"{api.pool}/shared",)]
        assert f"   path = {api.pool}/shared\n" in api.conf.read_text()

    @pytest.mark.parametrize("folder", ["docs", "docs/inner", "docs/a/b"])
    def test_overlapping_folders(self, api, folder):
        assert api.create().status_code == 200
        response = api.create("other", folder)
        assert response.status_code == 409
        assert response.json()["detail"].startswith("The folder overlaps share docs (one is inside the other).")

    def test_a_folder_containing_a_share(self, api):
        assert api.create("inner", "x/inner").status_code == 200
        response = api.create("outer", "x")
        assert response.status_code == 409
        assert "overlaps share inner" in response.json()["detail"]

    def test_a_pool_root_share_does_not_block_folders_of_its_pool(self, api):
        api.sql(f"INSERT INTO smb_shares (name, path, version) VALUES ('DATA', '{api.pool}', 1)")
        assert api.create().status_code == 200

    def test_gid_problem_creates_nothing(self, api, monkeypatch):
        problem = "gid 70000 belongs to the host group media, not unos-smb: share folders would be opened to media's members"
        monkeypatch.setattr(accounts, "gid_problem", lambda: problem)
        response = api.create()
        assert (response.status_code, response.json()["detail"]) == (409, problem)
        assert not (api.pool / "docs").exists()
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_a_folder_created_concurrently_is_an_existing_folder(self, api, monkeypatch):
        real_mkdir, changes = os.mkdir, []

        def mkdir(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            if path == "docs":
                raise FileExistsError(path)

        monkeypatch.setattr(os, "mkdir", mkdir)
        monkeypatch.setattr(os, "fchown", lambda *args: changes.append(("fchown", args)))
        monkeypatch.setattr(os, "fchmod", lambda *args: changes.append(("fchmod", args)))
        response = api.create()
        assert response.status_code == 200, response.text
        assert changes == []

    def test_name_taken_case_insensitively(self, api):
        assert api.create().status_code == 200
        response = api.create("DOCS", "other")
        assert (response.status_code, response.json()["detail"]) == (409, "Share DOCS already exists")

    def test_old_clients_get_422(self, api):
        for extra in ({"readonly": False}, {"valid_users": ["alice"]}, {"guest_write": True}):
            response = api.create(**extra)
            assert response.status_code == 422, extra
        assert api.rows("SELECT * FROM smb_shares") == []


# --- injection (moved from test_shares.py: the legacy functions are gone) ---------------------------------


class TestInjection:
    """Hostile share definitions never reach smb.conf (ADR-0037); they are refused with 400."""

    @pytest.mark.parametrize(
        "comment",
        [
            "x\n[evil]\n   path = /\n   root preexec = /bin/sh -c id",
            "x\r\nroot preexec = id",
            "line\x0bbreak",
            "unicode line separator",
            "nul\x00byte",
            "[bracket",
            "bracket]",
            "trailing backslash \\",
            "tab\tcharacter",
            "c" * 257,
        ],
    )
    def test_hostile_comment(self, api, comment):
        response = api.create(comment=comment)
        assert response.status_code == 400
        assert api.rows("SELECT * FROM smb_shares") == [] and not api.conf.exists()
        assert not (api.pool / "docs").exists()

    @pytest.mark.parametrize(
        "name",
        ["docs\n", "docs]", "do[cs", "docs\nroot preexec = id", "-docs", "a" * 33, "", "global", "GLOBAL",
         "globals", "Globals", "GLOBALS", "homes", "printers", "print$", "IPC$", "ipc$"],
    )
    def test_hostile_name(self, api, name):
        response = api.create(name=name)
        assert response.status_code == 400
        assert api.rows("SELECT * FROM smb_shares") == [] and not api.conf.exists()

    @pytest.mark.parametrize("name", ["globals", "Globals", "GLOBALS"])
    def test_a_rename_to_a_name_samba_reads_as_the_global_section(self, api, name):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        before = api.conf.read_text()
        response = api.patch("docs", name=name)
        assert (response.status_code, response.json()["detail"]) == (400, f"'{name}' is a reserved share name")
        assert api.rows("SELECT name, version FROM smb_shares") == [("docs", 1)]
        assert api.conf.read_text() == before

    @pytest.mark.parametrize("name", ["docs", "my-share", "_hidden", "A1", "a" * 32])
    def test_valid_names(self, api, name):
        response = api.create(name=name, folder="x")
        assert response.status_code == 200, response.text
        assert response.json()["name"] == name

    @pytest.mark.parametrize(
        "path",
        [
            "/etc",
            "{tmp}/outside",
            "{base}/../outside",
            "{base}-old/share",
            "{base}X/share",
            "{base}/data/x\n   root preexec = id",
            "{base}/data/per-user-%U",
            "{base}/data/trailing\\",
            '{base}/data/quoted"',
            "{base}/data/trailing-space ",
            "{base}/data/two  spaces",
            "data/relative",
            "",
        ],
    )
    def test_hostile_path(self, api, path):
        response = api.client.post(f"{API}/smb", json={"name": "docs", "path": path.format(base=api.base, tmp=api.tmp)})
        assert response.status_code == 400
        assert api.rows("SELECT * FROM smb_shares") == [] and not api.conf.exists()
        assert [call for call in api.host.calls if call[0] != "mkdir"] == []

    def test_symlink_escaping_the_pools(self, api):
        outside = api.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, api.pool / "escape")
        response = api.create(folder="escape")
        assert response.status_code == 400
        assert "symbolic link" in response.json()["detail"]

    def test_symlink_to_a_percent_directory(self, api):
        """A link planted by an app must not smuggle '%U' into the path of smb.conf."""
        (api.pool / "media").mkdir()
        os.symlink(api.pool / "%U" / "%U", api.pool / "media" / "downloads")
        response = api.create(folder="media/downloads", guest_ok=True)
        assert response.status_code == 400
        assert not (api.pool / "%U").exists() and not api.conf.exists()

    def test_symlink_to_another_pool_is_neither_shared_nor_changed(self, api):
        private = api.base / "backup" / "hr-private"
        private.mkdir(parents=True)
        os.chmod(private, 0o700)
        (api.pool / "media").mkdir()
        os.symlink(private, api.pool / "media" / "downloads")
        response = api.create(folder="media/downloads", guest_ok=True)
        assert response.status_code == 400
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o700

    def test_patch_refuses_a_folder_swapped_for_a_symlink(self, api):
        private = api.base / "backup" / "hr-private"
        private.mkdir(parents=True)
        os.chmod(private, 0o700)
        assert api.create().status_code == 200
        before = api.conf.read_text()
        os.rmdir(api.pool / "docs")
        os.symlink(private, api.pool / "docs")

        response = api.patch("docs", path=f"{api.pool}/docs", guest_ok=True)

        assert response.status_code == 400
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o700
        assert api.conf.read_text() == before
        assert api.rows("SELECT guest_ok, version FROM smb_shares") == [(0, 1)]

    def test_a_path_with_spaces_is_stored_as_given(self, api):
        response = api.create(folder="Team Docs")
        assert response.status_code == 200, response.text
        assert response.json()["path"] == f"{api.pool}/Team Docs"
        assert f"   path = {api.pool}/Team Docs\n" in api.conf.read_text()


# --- PATCH (ADR-0134) ----------------------------------------------------------------------------------


class TestPatch:
    @pytest.fixture
    def docs(self, api):
        response = api.create(comment="Docs", access=access((ALICE, "write"), (BOB, "read")))
        assert response.status_code == 200, response.text
        api.host.calls.clear()
        return response.json()

    def kept(self, api, before, **changed):
        after = api.get("docs").json()
        for key in ("name", "path", "comment", "browseable", "guest_ok", "access", "import_notes"):
            if key not in changed:
                assert after[key] == before[key], key
        for key, value in changed.items():
            assert after[key] == value, key
        return after

    def test_browseable_only(self, api, docs):
        response = api.patch("docs", browseable=False)
        assert response.status_code == 200, response.text
        after = self.kept(api, docs, browseable=False)
        assert after["version"] == 2
        assert "   browseable = no\n" in api.conf.read_text()

    def test_guest_only(self, api, docs):
        response = api.patch("docs", guest_ok=True)
        assert response.status_code == 200, response.text
        self.kept(api, docs, guest_ok=True)
        assert response.json()["warnings"] == [service.GUEST_WARNING]

    def test_access_only(self, api, docs):
        new = access((CAROL, "read"), (BOB, "write"))
        response = api.patch("docs", access=new)
        assert response.status_code == 200, response.text
        after = self.kept(api, docs, access=[
            {"user_id": BOB, "username": "bob", "level": "write", "smb_state": "active", "effective": True},
            {"user_id": CAROL, "username": "carol", "level": "read", "smb_state": "active", "effective": True},
        ])
        assert after["version"] == 2
        assert api.host.closed() == ["docs"]  # alice lost write: a shrink

    def test_comment_only(self, api, docs):
        response = api.patch("docs", comment="Changed")
        assert response.status_code == 200, response.text
        self.kept(api, docs, comment="Changed")

    def test_empty_patch_writes_nothing(self, api, docs):
        before = api.conf.read_bytes()
        mtime = os.stat(api.conf).st_mtime_ns
        response = api.patch("docs")
        assert response.status_code == 200
        assert response.json()["version"] == 1
        assert api.conf.read_bytes() == before and os.stat(api.conf).st_mtime_ns == mtime
        assert api.rows("SELECT version FROM smb_shares") == [(1,)]
        assert api.host.calls == []
        assert [e["action"] for e in api.audit_entries()] == ["share_created"]

    def test_unknown_share(self, api):
        assert api.patch("nope", comment="x").status_code == 404

    @pytest.mark.parametrize("field", ["name", "path", "comment", "browseable", "guest_ok", "access",
                                       "import_notes", "expected_version"])
    def test_null_field(self, api, docs, field):
        response = api.client.patch(f"{API}/smb/docs", json={field: None})
        assert response.status_code == 422
        assert f"{field} must not be null" in response.text

    def test_extra_field(self, api, docs):
        assert api.patch("docs", readonly=True).status_code == 422
        assert api.rows("SELECT version FROM smb_shares") == [(1,)]

    def test_import_notes_can_only_be_dismissed(self, api, docs):
        assert api.patch("docs", import_notes=["x"]).status_code == 422

    def test_put_is_gone(self, api, docs):
        response = api.client.put(f"{API}/smb/docs", json={"name": "docs", "path": docs["path"]})
        assert response.status_code == 405

    def test_expected_version(self, api, docs):
        response = api.patch("docs", comment="One", expected_version=1)
        assert (response.status_code, response.json()["version"]) == (200, 2)

        stale = api.patch("docs", comment="Two", expected_version=1)
        assert (stale.status_code, stale.json()["detail"]) == (409, service.VERSION_CONFLICT)
        assert api.rows("SELECT comment, version FROM smb_shares") == [("One", 2)]

    def test_version_after_an_access_change_and_dismissed_notes(self, api, docs):
        api.patch("docs", access=access((ALICE, "read")))
        assert api.rows("SELECT version FROM smb_shares") == [(2,)]
        api.sql("UPDATE smb_shares SET import_notes = '[\"note\"]'")
        assert api.get("docs").json()["import_notes"] == ["note"]
        response = api.patch("docs", import_notes=[], expected_version=2)
        assert response.status_code == 200, response.text
        assert (response.json()["version"], response.json()["import_notes"]) == (3, [])
        assert api.rows("SELECT import_notes, version FROM smb_shares") == [(None, 3)]

    def test_rename(self, api, docs):
        response = api.patch("docs", name="papers")
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "papers"
        assert "[papers]" in api.conf.read_text() and "[docs]" not in api.conf.read_text()
        assert api.host.closed() == ["docs"]
        assert api.get("DOCS").status_code == 404 and api.get("PAPERS").status_code == 200

    def test_rename_collision(self, api, docs):
        assert api.create("other", "other").status_code == 200
        response = api.patch("docs", name="OTHER")
        assert (response.status_code, response.json()["detail"]) == (409, "Share OTHER already exists")

    def test_path_change(self, api, docs):
        response = api.patch("docs", path=f"{api.pool}//papers/")
        assert response.status_code == 200, response.text
        assert response.json()["path"] == f"{api.pool}/papers"
        assert stat.S_IMODE(os.stat(api.pool / "papers").st_mode) == 0o2770
        assert (api.pool / "docs").is_dir()  # the old folder stays
        assert api.host.closed() == ["docs"]

    @pytest.mark.parametrize(
        "written, status, detail",
        [("{pool}", 400, POOL_ROOT), ("{pool}/", 400, POOL_ROOT), ("{pool}/.", 400, POOL_ROOT),
         ("{pool}/apps", 400, APPS), ("{pool}//apps", 400, APPS), ("{pool}/./apps/x", 400, APPS),
         ("{base}/default/x", 400, "Share path must be on a storage pool that UNOS manages: 'default' is not one")],
    )
    def test_path_rules(self, api, docs, written, status, detail):
        response = api.patch("docs", path=written.format(pool=api.pool, base=api.base))
        assert (response.status_code, response.json()["detail"]) == (status, detail)
        assert api.rows("SELECT path, version FROM smb_shares") == [(docs["path"], 1)]

    def test_a_volume_root_is_accepted(self, api, docs):
        (api.pool / "media").mkdir()
        response = api.patch("docs", path=f"{api.pool}/media")
        assert response.status_code == 200, response.text
        assert (response.json()["path"], response.json()["served"]) == (f"{api.pool}/media", True)
        assert api.mounted[-1] == ("data", False)  # an existing folder: opened, never created

    def test_path_overlap(self, api, docs):
        assert api.create("other", "other").status_code == 200
        response = api.patch("other", path=f"{api.pool}/docs/sub")
        assert response.status_code == 409

    def test_moving_a_share_into_its_own_folder_is_not_an_overlap(self, api, docs):
        # docs was made root:unos-smb 2770, which every SMB account can change: a new folder
        # is never created in it (TestFoldersAboveAShare). Made 0755, it may hold one.
        os.chmod(api.pool / "docs", 0o755)
        response = api.patch("docs", path=f"{api.pool}/docs/sub")
        assert response.status_code == 200, response.text

    def test_moving_a_share_into_a_new_folder_of_its_old_group_writable_folder(self, api, docs):
        response = api.patch("docs", path=f"{api.pool}/docs/sub")
        assert response.status_code == 409
        # 'chmod 755' would open the share's files to every account (2755): go-w only takes write away.
        assert response.json()["detail"] == (
            f"Share folders must be inside folders that only root can change: {api.pool}/docs can be changed by "
            f"other accounts (mode 2770). Run 'chmod go-w {api.pool}/docs' on the NAS, then try again.")
        assert not (api.pool / "docs" / "sub").exists()
        assert api.rows("SELECT path, version FROM smb_shares") == [(docs["path"], 1)]


class TestAccessEntries:
    def test_unknown_user(self, api):
        response = api.create(access=[{"user_id": 42, "username": "ghost", "level": "read"}])
        assert (response.status_code, response.json()["detail"]) == (400, "User 42 does not exist")

    def test_user_id_reused(self, api):
        """bob was deleted and the id now belongs to someone else: the request is stale."""
        api.sql("UPDATE users SET username = 'mallory' WHERE id = 2")
        response = api.create(access=access((BOB, "write")))
        assert (response.status_code, response.json()["detail"]) == (409, "User 2 is now 'mallory'; reload the page")
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_administrator(self, api):
        response = api.create(access=access((ADMIN, "read")))
        assert (response.status_code, response.json()["detail"]) == (400, "Administrators cannot use SMB")

    def test_unusable_name(self, api):
        response = api.create(access=access((LEGACY, "read")))
        assert response.status_code == 400
        assert response.json()["detail"].startswith("The username 'Bob.Smith' cannot be used for SMB.")

    def test_duplicate(self, api):
        response = api.create(access=access((ALICE, "read"), (ALICE, "write")))
        assert response.status_code == 422
        assert "Each user can appear only once in the access list" in response.text

    def test_users_without_an_active_account_are_stored_not_effective(self, api):
        response = api.create(access=access((DAVE, "read"), (ERIN, "write")))
        assert response.status_code == 200, response.text
        assert response.json()["access"] == [
            {"user_id": DAVE, "username": "dave", "level": "read", "smb_state": "off", "effective": False},
            {"user_id": ERIN, "username": "erin", "level": "write", "smb_state": "password_needed",
             "effective": False},
        ]
        assert response.json()["available"] is False
        assert "   valid users = root\n   write list =\n   available = no\n" in section(api, "docs") + "\n"

    def test_stale_rows_are_not_listed_and_a_replacement_removes_them(self, api):
        assert api.create(access=access((ALICE, "read"))).status_code == 200
        api.sql("INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 2, 'robert', 'write')",
                "INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 6, 'admin', 'read')")
        assert [entry["username"] for entry in api.get("docs").json()["access"]] == ["alice"]
        response = api.patch("docs", access=access((ALICE, "read")))
        assert response.status_code == 200
        assert api.rows("SELECT user_id, username FROM smb_share_access") == [(ALICE, "alice")]

    def test_a_row_written_for_an_earlier_user_with_the_same_id_is_replaced(self, api):
        """The row of user 2 was written for 'robert'; granting bob (now id 2) replaces it in place."""
        assert api.create(access=access((ALICE, "read"))).status_code == 200
        api.sql("INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, 2, 'robert', 'read')")
        response = api.patch("docs", access=access((ALICE, "read"), (BOB, "write")))
        assert response.status_code == 200, response.text
        assert api.rows("SELECT user_id, username, level FROM smb_share_access ORDER BY user_id") == [
            (ALICE, "alice", "read"), (BOB, "bob", "write")]
        assert api.audit_entries()[-1]["details"] == "Changed share docs: access: +bob=write, -robert"


class TestNotServed:
    @pytest.fixture
    def data(self, api):
        """The device's imported DATA: the pool root, not served, without access."""
        api.sql(f"INSERT INTO smb_shares (name, path, version, import_notes) VALUES "
                f"('DATA', '{api.pool}', 1, '[\"Not served: {POOL_ROOT}\"]')")
        return api

    def test_listed_with_its_problem(self, data):
        info = data.get("data").json()
        assert (info["served"], info["path_problem"], info["available"]) == (False, POOL_ROOT, False)
        assert (info["pool"], info["pool_mounted"], info["permissions_ok"]) == ("data", True, None)

    def test_comment_can_change(self, data):
        response = data.patch("DATA", comment="Old pool root")
        assert response.status_code == 200, response.text
        assert response.json()["served"] is False
        assert data.rows("SELECT comment, version FROM smb_shares") == [("Old pool root", 2)]

    def test_access_needs_a_valid_folder(self, data):
        response = data.patch("DATA", access=access((ALICE, "write")))
        assert (response.status_code, response.json()["detail"]) == (400, POOL_ROOT)
        response = data.patch("DATA", guest_ok=True)
        assert (response.status_code, response.json()["detail"]) == (400, POOL_ROOT)
        assert data.rows("SELECT * FROM smb_share_access") == []

    def test_path_and_access_together(self, data):
        response = data.patch("DATA", path=f"{data.pool}/shared", access=access((ALICE, "write")))
        assert response.status_code == 200, response.text
        info = response.json()
        assert (info["served"], info["path_problem"], info["available"]) == (True, None, True)
        assert "[DATA]\n" in data.conf.read_text()

    def test_import_notes_can_be_dismissed(self, data):
        response = data.patch("DATA", import_notes=[])
        assert response.status_code == 200, response.text
        assert response.json()["import_notes"] == []

    @pytest.fixture
    def granting(self, api, monkeypatch):
        """docs was served with alice=write and guest access; then its pool stopped being one
        that UNOS manages (forgotten on the Storage page): not served, and it still grants."""
        assert api.create(access=access((ALICE, "write")), guest_ok=True).status_code == 200
        api.sql("UPDATE smb_shares SET import_notes = '[\"note\"]'")
        monkeypatch.setattr(pools, "managed_pool_names", lambda: {"ghost"})
        info = api.get("docs").json()
        assert (info["served"], info["guest_ok"], [a["username"] for a in info["access"]]) == (False, True, ["alice"])
        return api

    def test_import_notes_of_a_share_that_still_grants_can_be_dismissed(self, granting):
        """Dismissing grants nothing new (ADR-0136): it skips PATCH step 4."""
        api = granting
        response = api.patch("docs", import_notes=[], expected_version=1)
        assert response.status_code == 200, response.text
        assert (response.json()["import_notes"], response.json()["version"]) == ([], 2)
        assert api.rows("SELECT import_notes, guest_ok, version FROM smb_shares") == [(None, 1, 2)]
        assert api.rows("SELECT username, level FROM smb_share_access") == [("alice", "write")]

    @pytest.mark.parametrize("fields", [{"comment": "x"}, {"import_notes": [], "comment": "x"},
                                        {"import_notes": [], "browseable": True},
                                        {"import_notes": [], "guest_ok": True}])
    def test_no_other_change_skips_step_4(self, granting, fields):
        api = granting
        response = api.patch("docs", **fields)
        assert (response.status_code, response.json()["detail"]) == (
            400, "Share path must be on a storage pool that UNOS manages: 'data' is not one")
        assert api.rows("SELECT import_notes, comment, version FROM smb_shares") == [('["note"]', "", 1)]


class TestGuest:
    def globals_of(self, api):
        text = api.conf.read_text()
        return [line for line in text.split("\n")
                if line.startswith(("   map to guest", "   restrict anonymous", "   server signing"))]

    def test_guest_flips_the_globals(self, api):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        assert self.globals_of(api) == [
            "   map to guest = Never", "   restrict anonymous = 2", "   server signing = mandatory"]

        assert api.patch("docs", guest_ok=True).status_code == 200
        assert self.globals_of(api) == [
            "   map to guest = Bad User", "   restrict anonymous = 1", "   server signing = default"]
        assert "valid users" not in section(api, "docs")
        assert "   read only = yes\n" in section(api, "docs") + "\n"

        assert api.patch("docs", guest_ok=False).status_code == 200
        assert self.globals_of(api) == [
            "   map to guest = Never", "   restrict anonymous = 2", "   server signing = mandatory"]

    def test_guest_share_created(self, api):
        response = api.create(guest_ok=True)
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [service.GUEST_WARNING]
        assert response.json()["available"] is True


class TestDelete:
    def test_delete(self, api):
        assert api.create(access=access((ALICE, "read"))).status_code == 200
        api.host.calls.clear()
        response = api.client.delete(f"{API}/smb/DOCS")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted", "warnings": []}
        assert (api.pool / "docs").is_dir()  # the folder and its data stay
        assert api.rows("SELECT * FROM smb_shares") == [] and api.rows("SELECT * FROM smb_share_access") == []
        assert "[docs]" not in api.conf.read_text()
        assert api.host.closed() == ["docs"]

    def test_unknown(self, api):
        assert api.client.delete(f"{API}/smb/nope").status_code == 404


class TestFailures:
    def test_a_commit_that_fails_after_staging_changes_nothing(self, api, monkeypatch):
        real_commit = AsyncSession.commit
        attempts = []

        async def commit(self):
            if not attempts:
                attempts.append(1)
                raise sqlite3.OperationalError("database is locked")
            return await real_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", commit)
        response = api.create(access=access((ALICE, "write")))

        assert (response.status_code, response.json()["detail"]) == (
            503, "Nothing was changed: the database was busy; try again")
        assert api.temp_files() == []
        assert not api.conf.exists()
        assert api.rows("SELECT * FROM smb_shares") == []
        assert [call[0] for call in api.host.calls] == ["mkdir", "testparm"]  # staged, never installed

    def test_testparm_rejection_is_500_and_changes_nothing(self, api):
        assert api.create().status_code == 200
        before = api.conf.read_bytes()
        api.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")
        response = api.patch("docs", comment="New")
        assert response.status_code == 500
        assert "testparm rejected the new smb.conf: Error loading services." in response.json()["detail"]
        assert api.conf.read_bytes() == before and api.temp_files() == []
        assert api.rows("SELECT comment, version FROM smb_shares") == [("", 1)]

    def test_pool_list_unavailable_is_503(self, api, monkeypatch):
        def broken():
            raise OSError("cat: /etc/fstab: Input/output error")

        monkeypatch.setattr(pools, "managed_pool_names", broken)
        for response in (api.create(), api.client.get(f"{API}/smb")):
            assert response.status_code == 503
            assert response.json()["detail"].startswith("The pool list could not be read: ")


# --- reading -------------------------------------------------------------------------------------------


class TestRead:
    def test_list(self, api):
        assert api.create("media", "media", access=access((DAVE, "read"))).status_code == 200
        assert api.create("Docs", "docs", access=access((ALICE, "write"))).status_code == 200
        api.sql(f"INSERT INTO smb_shares (name, path, version) VALUES ('apps', '{api.pool}/apps', 4)")

        listed = api.client.get(f"{API}/smb").json()

        assert [(s["name"], s["served"], s["available"], s["version"]) for s in listed] == [
            ("apps", False, False, 4), ("Docs", True, True, 1), ("media", True, False, 1)]
        assert listed[0]["path_problem"] == APPS
        assert listed[0]["permissions_ok"] is None

    def test_get_is_case_insensitive(self, api):
        assert api.create("Docs").status_code == 200
        assert api.get("DOCS").json()["name"] == "Docs"
        assert (api.get("nope").status_code, api.get("nope").json()["detail"]) == (404, "Share not found")

    def test_pool_mount_state(self, api, monkeypatch):
        assert api.create().status_code == 200
        monkeypatch.setattr(disks, "host_mounts", lambda: [])
        info = api.get("docs").json()
        assert (info["pool_mounted"], info["permissions_ok"]) == (False, None)

        def broken():
            raise RuntimeError("findmnt failed")

        monkeypatch.setattr(disks, "host_mounts", broken)
        info = api.get("docs").json()
        assert (info["pool_mounted"], info["permissions_ok"]) == (None, None)

    @pytest.mark.parametrize("order", [("read", "write"), ("write", "read")])
    def test_two_rows_of_one_user_show_the_lower_level(self, api, order):
        """ADR-0144: a restored smb_share_access without its UNIQUE constraint can hold two rows for
        one user; the answer shows the level that smb.conf renders: the lower one."""
        assert api.create().status_code == 200
        api.sql("DROP TABLE smb_share_access",
                "CREATE TABLE smb_share_access (id INTEGER PRIMARY KEY, share_id INTEGER NOT NULL, user_id INTEGER "
                "NOT NULL, username VARCHAR(64) NOT NULL, level VARCHAR(8) NOT NULL, created_at DATETIME NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP)",
                *(f"INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, {ALICE}, 'alice', "
                  f"'{level}')" for level in order))
        for info in (api.get("docs").json(), api.client.get(f"{API}/smb").json()[0]):
            assert info["access"] == [
                {"user_id": ALICE, "username": "alice", "level": "read", "smb_state": "active", "effective": True}]

    def test_restored_values_are_shown_defensively(self, api):
        api.sql(f"INSERT INTO smb_shares (name, path, comment, version, import_notes) VALUES "
                f"('odd', '{api.pool}/odd', 'x', 'text', '{{\"not\": \"a list\"}}')")
        info = api.get("odd").json()
        assert (info["version"], info["import_notes"]) == (0, [])
        response = api.patch("odd", comment="y", expected_version=0)
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 1


class TestPaths:
    """GET /shares/paths: the share form offers only folders of pools that UNOS manages (ADR-0148)."""

    @pytest.fixture
    def listed(self, api, monkeypatch):
        mounted = [SimpleNamespace(name="data", mountpoint="/mnt/pools/data"),
                   SimpleNamespace(name="other", mountpoint="/mnt/pools/other"),
                   SimpleNamespace(name="gone", mountpoint=None)]
        volumes = {"data": [SimpleNamespace(name="media", path="/mnt/pools/data/media")],
                   "other": [SimpleNamespace(name="films", path="/mnt/pools/other/films")]}
        monkeypatch.setattr(pools, "list_pools", lambda: mounted)
        monkeypatch.setattr(pools, "list_volumes", lambda name: volumes[name])
        return api

    def test_every_path_says_whether_its_pool_is_managed(self, listed, monkeypatch):
        monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data", "gone"})
        response = listed.client.get(f"{API}/paths")
        assert response.status_code == 200, response.text
        assert response.json() == [
            {"path": "/mnt/pools/data", "label": "data (Pool)", "type": "pool", "managed": True},
            {"path": "/mnt/pools/data/media", "label": "data/media (Volume)", "type": "volume", "managed": True},
            {"path": "/mnt/pools/other", "label": "other (Pool)", "type": "pool", "managed": False},
            {"path": "/mnt/pools/other/films", "label": "other/films (Volume)", "type": "volume", "managed": False},
        ]

    def test_none_is_managed_when_the_pool_list_cannot_be_read(self, listed, monkeypatch):
        def broken():
            raise OSError("cat: /etc/fstab: Input/output error")

        monkeypatch.setattr(pools, "managed_pool_names", broken)
        response = listed.client.get(f"{API}/paths")
        assert response.status_code == 200, response.text
        assert [(p["path"], p["managed"]) for p in response.json()] == [
            ("/mnt/pools/data", False), ("/mnt/pools/data/media", False),
            ("/mnt/pools/other", False), ("/mnt/pools/other/films", False)]


# --- status --------------------------------------------------------------------------------------------


class TestStatus:
    def test_fields(self, api, monkeypatch):
        api.sql(f"INSERT INTO smb_shares (name, path, version) VALUES ('DATA', '{api.pool}', 1)",
                "UPDATE smb_state SET import_report = '[\"Imported 1 share(s) from /etc/samba/smb.conf\"]'")
        assert api.create("media", "media", guest_ok=True).status_code == 200
        monkeypatch.setattr(accounts, "host_status", lambda: interfaces.AccountHostStatus(
            tools_missing=[], group_problem=None, unmanaged_samba_users=["nas"]))

        status = api.client.get(f"{API}/smb-status").json()

        assert status == {
            "samba_tools_missing": [],
            "group_ok": True,
            "problems": [f"Share DATA is not served: {POOL_ROOT}"],
            "guest_shares": ["media"],
            "import_report": ["Imported 1 share(s) from /etc/samba/smb.conf"],
            "unmanaged_samba_users": ["nas"],
            "active_smb_users": 3,
        }

    def test_pending_reload_and_closes(self, api):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.fail[("smbcontrol",)] = HostResult(1, "", "no smbd")
        api.host.fail[("systemctl",)] = HostResult(1, "", "not active")
        response = api.patch("docs", access=access((ALICE, "read")))
        assert response.status_code == 200
        assert response.json()["warnings"][0].startswith("Samba did not reload smb.conf")

        problems = api.client.get(f"{API}/smb-status").json()["problems"]
        assert problems == [
            "Samba has not reloaded smb.conf yet; the reload is tried again at the next change",
            "Open connections to docs were not closed yet; this is tried again at the next change",
        ]

    def test_a_reload_with_systemctl_only(self, api):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.fail[("smbcontrol", "all")] = HostResult(1, "", "no smbd")
        response = api.patch("docs", access=access((ALICE, "read")))
        assert response.status_code == 200
        assert response.json()["warnings"] == [
            "Samba was reloaded with systemctl only (smbcontrol: no smbd); open connections apply the change "
            "within a few minutes; the full reload is tried again at the next change, and then the open "
            "connections to docs are closed again"]
        assert api.host.closed() == ["docs"]

        problems = api.client.get(f"{API}/smb-status").json()["problems"]
        assert problems == [
            "Samba was reloaded with systemctl only: open connections apply the last change within a few "
            "minutes; the full reload is tried again at the next change, and then the open connections to docs "
            "are closed again"]

        del api.host.fail[("smbcontrol", "all")]
        response = api.patch("docs", comment="Team")  # the next change: the full reload, then docs again
        assert (response.status_code, response.json()["warnings"]) == (200, [])
        assert api.host.closed() == ["docs", "docs"]
        assert api.client.get(f"{API}/smb-status").json()["problems"] == []

    def test_shares_that_unos_did_not_create(self, api):
        assert api.create(access=access((ALICE, "read"))).status_code == 200
        api.conf.write_text(api.conf.read_text() + f"\n[legacy]\n   path = {api.pool}/legacy\n   read only = no\n")
        assert api.patch("docs", comment="Team").status_code == 200
        assert (api.samba / "smb.conf.unos-replaced").exists()

        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            "smb.conf had shares that UNOS did not create (legacy); they are kept in smb.conf.unos-replaced: "
            "create them again on the Shares page"]

    def test_a_failed_apply(self, api):
        """An apply after an account change that testparm rejects."""
        api.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")
        with pytest.raises(smb.SambaConfigError):
            asyncio.run(apply_smb_config(api))
        assert not api.conf.exists()

        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            "smb.conf was not updated: testparm rejected the new smb.conf: Error loading services."]

        api.host.fail.clear()
        asyncio.run(apply_smb_config(api))
        assert api.client.get(f"{API}/smb-status").json()["problems"] == []

    def test_an_smb_conf_replaced_after_the_last_apply(self, api):
        """ADR-0248: after an apply that left UNOS's file in place, a file without UNOS's header,
        or none, is a problem until the next change writes UNOS's file again."""
        assert api.create(access=access((ALICE, "read"))).status_code == 200
        assert api.client.get(f"{API}/smb-status").json()["problems"] == []

        api.conf.write_text("[global]\n   workgroup = WORKGROUP\n\n[docs]\n   path = /srv/docs\n")
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [REPLACED]
        api.conf.unlink()
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [REPLACED]

        assert api.patch("docs", comment="Team").status_code == 200
        assert api.conf.read_text().startswith(smbconf.HEADER)
        assert api.client.get(f"{API}/smb-status").json()["problems"] == []

    def test_an_smb_conf_that_cannot_be_read_after_the_last_apply(self, api, monkeypatch):
        assert api.create().status_code == 200

        def unreadable():
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(smb, "read_conf_bytes", unreadable)
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            "smb.conf could not be read: [Errno 5] Input/output error"]

    def test_group_problems(self, api, monkeypatch):
        monkeypatch.setattr(accounts, "gid_problem", lambda: "gid 70000 belongs to the host group media, not unos-smb")
        status = api.client.get(f"{API}/smb-status").json()
        assert status["group_ok"] is False
        assert status["problems"] == ["gid 70000 belongs to the host group media, not unos-smb",
                                      service.GROUP_PROBLEM_NOTE]

        monkeypatch.setattr(accounts, "gid_problem", lambda: None)
        monkeypatch.setattr(accounts, "host_status", lambda: interfaces.AccountHostStatus(
            tools_missing=[], group_problem="The host group unos-smb exists with gid 1234"))
        status = api.client.get(f"{API}/smb-status").json()
        assert status["group_ok"] is False
        assert status["problems"] == ["The host group unos-smb exists with gid 1234", service.GROUP_PROBLEM_NOTE]

    def test_host_checks_that_cannot_run(self, api, monkeypatch):
        def broken():
            raise RuntimeError("nsenter failed")

        monkeypatch.setattr(accounts, "host_status", broken)
        status = api.client.get(f"{API}/smb-status").json()
        assert (status["samba_tools_missing"], status["group_ok"], status["unmanaged_samba_users"]) == (None, None, [])
        assert status["problems"] == ["The SMB host checks could not run: nsenter failed"]

    def test_uids_exhausted(self, api):
        api.sql("UPDATE smb_state SET next_uid = 80000")
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            "No SMB user ids left (70000-79999): SMB access cannot be turned on for more users"]

    def test_import_report_delete(self, api):
        api.sql("UPDATE smb_state SET import_report = '[\"line\"]'")
        assert api.client.get(f"{API}/smb-status").json()["import_report"] == ["line"]
        response = api.client.delete(f"{API}/smb-status/import-report")
        assert (response.status_code, response.json()) == (200, {"status": "ok"})
        assert api.rows("SELECT import_report FROM smb_state") == [(None,)]
        assert api.client.get(f"{API}/smb-status").json()["import_report"] == []

    def test_a_share_named_status_is_not_shadowed(self, api):
        assert api.create("status", "status").status_code == 200
        assert api.get("status").json()["name"] == "status"
        assert "problems" in api.client.get(f"{API}/smb-status").json()


def test_the_e2e_fakes_answer_with_the_servers_texts(api, monkeypatch):
    """webui/tests/shares-fakes.ts answers with these texts word for word (ADR-0189): why a share
    of a pool that UNOS does not manage, or no longer manages after an unmount, is not served
    (ADR-0247), the status problem that names such a share, and the status problem of a replaced
    smb.conf (ADR-0248)."""
    fakes = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "shares-fakes.ts").read_text(encoding="utf-8")
    unmanaged = service.share_path_problem(
        str(validators.pools_base() / "e2e_data" / "docs"), other_paths={}, pool_names={"data"}
    )
    assert unmanaged == "Share path must be on a storage pool that UNOS manages: 'e2e_data' is not one"
    assert f"`{unmanaged.replace('e2e_data', '${pool}')}`" in fakes
    assert f"'{service.SMB_CONF_REPLACED}'" in fakes

    # The status once the pool of two shares is no longer one that UNOS manages, as setPoolMounted
    # changes it: each share is named, in the order of the shares, and none is a guest share.
    assert api.create().status_code == 200
    assert api.create("public", "public", guest_ok=True).status_code == 200
    assert api.client.get(f"{API}/smb-status").json()["guest_shares"] == ["public"]
    monkeypatch.setattr(pools, "managed_pool_names", lambda: {"ghost"})
    problem = unmanaged.replace("e2e_data", "data")
    status = api.client.get(f"{API}/smb-status").json()
    assert (status["problems"], status["guest_shares"]) == (
        [f"Share docs is not served: {problem}", f"Share public is not served: {problem}"], [])
    assert "`Share ${share} is not served: ${problem}`" in fakes


# --- audit (ADR-0157) ------------------------------------------------------------------------------------


class TestAudit:
    def test_entries(self, api):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        assert api.patch("docs", access=access((CAROL, "read"), (ALICE, "read"))).status_code == 200
        assert api.patch("docs", guest_ok=True).status_code == 200
        assert api.patch("docs").status_code == 200  # nothing changed: no entry
        assert api.client.delete(f"{API}/smb/docs").status_code == 200

        entries = api.audit_entries()
        assert [(e["action"], e["severity"], e["username"], e["resource_type"], e["resource_id"])
                for e in entries] == [
            ("share_created", "info", "admin", "share", "docs"),
            ("share_updated", "info", "admin", "share", "docs"),
            ("share_updated", "warning", "admin", "share", "docs"),
            ("share_deleted", "warning", "admin", "share", "docs"),
        ]
        assert entries[0]["details"] == f"Created share docs at {api.pool}/docs; access: alice=write"
        assert entries[1]["details"] == "Changed share docs: access: +carol=read, alice write→read"
        assert entries[2]["details"] == "Changed share docs: guest access on"
        assert entries[3]["details"].startswith("Deleted share docs; its folder")

    def test_guest_share_created_is_a_warning(self, api):
        assert api.create(guest_ok=True).status_code == 200
        (entry,) = api.audit_entries()
        assert (entry["severity"], entry["details"]) == (
            "warning", f"Created share docs at {api.pool}/docs, guests can read it")


# --- the folders above a share folder (ADR-0153, ADR-0154) ----------------------------------------


def refusal(folder, detail="mode 0777", commands=None) -> str:
    commands = commands or f"'chmod 755 {folder}'"
    return (f"Share folders must be inside folders that only root can change: {folder} can be changed by other "
            f"accounts ({detail}). Run {commands} on the NAS, then try again.")


def unsafe_warning(folder, detail="mode 0777") -> str:
    return (f"Share folders must be inside folders that only root can change: {folder} can be changed by other "
            f"accounts ({detail}), so they can replace this share's folder. Run 'chmod 755 {folder}' on the NAS.")


def directory(uid, gid, mode) -> os.stat_result:
    return os.stat_result((stat.S_IFDIR | mode, 1, 1, 2, uid, gid, 0, 0, 0, 0))


STRANGER = 1000 if os.geteuid() != 1000 else 1001  # an account other than root and the runner


@pytest.fixture
def owners(monkeypatch):
    """``own(path, uid=..., gid=...)``: os.stat and os.fstat report that owner or group for the
    folder at ``path`` (the tests cannot chown without root). Everything else is unchanged."""
    faked: dict[tuple[int, int], tuple[int | None, int | None]] = {}
    real_stat, real_fstat = os.stat, os.fstat

    def fake(st):
        uid, gid = faked.get((st.st_dev, st.st_ino), (None, None))
        if uid is None and gid is None:
            return st
        return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                               st.st_uid if uid is None else uid, st.st_gid if gid is None else gid,
                               st.st_size, int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))

    monkeypatch.setattr(os, "stat", lambda *args, **kwargs: fake(real_stat(*args, **kwargs)))
    monkeypatch.setattr(os, "fstat", lambda *args, **kwargs: fake(real_fstat(*args, **kwargs)))

    def own(path, *, uid=None, gid=None):
        st = real_stat(path)
        faked[(st.st_dev, st.st_ino)] = (uid, gid)

    return own


class TestFoldersAboveAShare:
    """Whoever can change a folder between the pool root and a share folder can rename the
    share folder away and put another folder or a symlink in its place: smbd and Repair
    permissions follow the path (ADR-0153). The check reads through descriptors and
    changes no mode (ADR-0154)."""

    @pytest.mark.parametrize("uid, gid, mode, detail, commands", [
        (0, 0, 0o755, None, None),
        ("euid", 5, 0o755, None, None),
        (0, 0, 0o775, None, None),                      # writable by the group root only
        (0, 0, 0o1777, None, None),                     # sticky: only an entry's owner can rename it
        (0, 5, 0o1775, None, None),
        (0, 5, 0o775, "mode 0775", "'chmod 755 /p/x'"),
        (0, 5, 0o2770, "mode 2770", "'chmod go-w /p/x'"),   # chmod 755 would open it to everyone
        (0, 5, 0o770, "mode 0770", "'chmod go-w /p/x'"),
        (0, 0, 0o757, "mode 0757", "'chmod 755 /p/x'"),
        (0, 0, 0o777, "mode 0777", "'chmod 755 /p/x'"),
        (0, 0, 0o2777, "mode 2777", "'chmod 755 /p/x'"),
        (0, 0, 0o703, "mode 0703", "'chmod go-w /p/x'"),
        (1000, 0, 0o755, "owner uid 1000, mode 0755", "'chown root:root /p/x'"),
        (1000, 0, 0o1777, "owner uid 1000, mode 1777", "'chown root:root /p/x'"),
        (1000, 1000, 0o777, "owner uid 1000, mode 0777", "'chown root:root /p/x' and 'chmod 755 /p/x'"),
        (1000, 1000, 0o2770, "owner uid 1000, mode 2770", "'chown root:root /p/x' and 'chmod go-w /p/x'"),
    ])
    def test_which_folders_other_accounts_can_change(self, uid, gid, mode, detail, commands):
        uid = os.geteuid() if uid == "euid" else uid
        if uid == 1000 and os.geteuid() == 1000:
            pytest.skip("the runner's own uid")
        unsafe = service._unsafe_folder("/p/x", directory(uid, gid, mode))
        if detail is None:
            assert unsafe is None
        else:
            assert (unsafe.detail, unsafe.commands) == (detail, commands)
            assert unsafe.refusal() == refusal("/p/x", detail, commands)

    def test_the_suggested_chmod_takes_write_access_away_and_adds_none(self):
        """For every mode, what GNU chmod makes of the directory ('755' keeps its setuid and
        setgid bits): a 2770 folder must not become 2755, open to every account."""
        checked = 0
        for mode in range(0o10000):
            for gid in (0, 5):
                unsafe = service._unsafe_folder("/p/x", directory(0, gid, mode))
                if unsafe is None:
                    continue
                checked += 1
                after = {"'chmod 755 /p/x'": (mode & 0o6000) | 0o755,
                         "'chmod go-w /p/x'": mode & ~0o022}[unsafe.commands]
                assert after & ~mode & 0o777 == 0, f"{mode:04o} -> {after:04o}"  # no access added
                assert service._unsafe_folder("/p/x", directory(0, gid, after)) is None, f"{mode:04o}"
        assert checked > 1000

    def test_a_path_with_a_space_is_quoted_in_the_command(self):
        unsafe = service._unsafe_folder("/p/Team Docs", directory(0, 0, 0o777))
        assert unsafe.commands == "\"chmod 755 '/p/Team Docs'\""

    @pytest.mark.parametrize("mode, entry, detail", [
        (0o1777, STRANGER, f"mode 1777, the share folder belongs to uid {STRANGER}"),
        (0o1757, STRANGER, f"mode 1757, the share folder belongs to uid {STRANGER}"),
        (0o1777, 0, None),
        (0o1777, "euid", None),
        (0o1777, None, None),                           # no share folder yet
        (0o1755, STRANGER, None),                       # nobody else can write the parent: no rename
        (0o1775, STRANGER, None),                       # only the group root can
        (0o0777, 0, "mode 0777"),                       # without the sticky bit anyone can rename it
    ])
    def test_in_a_sticky_folder_the_owner_of_the_share_folder_counts(self, mode, entry, detail):
        """The sticky bit lets an entry's owner rename the entry: another account that owns the
        share folder could rename it away and put another folder or a symlink in its place."""
        entry = os.geteuid() if entry == "euid" else entry
        unsafe = service._unsafe_folder("/p/x", directory(0, 0, mode), entry_uid=entry)
        if detail is None:
            assert unsafe is None
        else:
            assert (unsafe.detail, unsafe.commands) == (detail, "'chmod 755 /p/x'")

    @pytest.mark.parametrize("mode, gid, writers, detail", [
        (0o775, 0, ["uid 1000"], "mode 0775, ACL entries for uid 1000"),
        (0o775, 0, ["uid 1000", "gid 1001"], "mode 0775, ACL entries for uid 1000, gid 1001"),
        (0o775, 0, [], None),
        (0o755, 0, ["uid 1000"], None),                 # no group bits: the mask takes write away
        (0o1775, 0, ["uid 1000"], None),                # sticky: only an entry's owner can rename it
    ])
    def test_named_acl_entries_count_as_other_accounts(self, monkeypatch, mode, gid, writers, detail):
        """With a POSIX ACL the group bits are the mask of its named entries: a folder of
        root:root 0775 can then be writable by uid 1000 (through an ACL, ADR-0193)."""
        read = []
        monkeypatch.setattr(service, "_acl_writers", lambda fd: read.append(fd) or writers)
        unsafe = service._unsafe_folder("/p/x", directory(0, gid, mode), fd=7)
        if detail is None:
            assert unsafe is None
        else:
            assert (unsafe.detail, unsafe.commands) == (detail, "'chmod 755 /p/x'")
        assert read == ([7] if mode & 0o020 else [])  # read only when the group bits allow writing
        assert service._unsafe_folder("/p/x", directory(0, gid, mode)) is None  # no descriptor: mode only

    @staticmethod
    def acl(*entries) -> bytes:
        """A POSIX access ACL as Linux returns it: version 2, then (tag, permissions, id) entries."""
        return (2).to_bytes(4, "little") + b"".join(
            tag.to_bytes(2, "little") + perm.to_bytes(2, "little") + entry_id.to_bytes(4, "little")
            for tag, perm, entry_id in entries)

    def test_the_acl_entries_that_let_others_write(self):
        user_obj, user, group_obj, group, mask, other, undefined = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0xFFFFFFFF
        base = [(user_obj, 7, undefined), (group_obj, 7, undefined), (other, 5, undefined)]
        acl = self.acl(*base, (user, 7, 1000), (user, 5, 1002), (user, 7, 0), (user, 7, os.geteuid()),
                       (group, 6, 1001), (group, 7, 0), (mask, 7, undefined))
        assert service._acl_writers_in(acl) == (["uid 1000", "gid 1001"] if os.geteuid() != 1000 else ["gid 1001"])
        # The mask takes write access from every named entry.
        assert service._acl_writers_in(self.acl(*base, (user, 7, 1000), (mask, 5, undefined))) == []
        for unreadable in (b"", self.acl()[:3], self.acl(*base)[:-1], (1).to_bytes(4, "little")):
            with pytest.raises(OSError, match="^a folder's ACL could not be read$"):
                service._acl_writers_in(unreadable)

    @pytest.mark.skipif(not hasattr(os, "setxattr"), reason="POSIX ACLs need Linux (the Linux test runner)")
    def test_a_real_acl(self, tmp_path):
        user_obj, user, group_obj, mask, other, undefined = 0x01, 0x02, 0x04, 0x10, 0x20, 0xFFFFFFFF
        folder = tmp_path / "x"
        folder.mkdir()
        os.chmod(folder, 0o755)
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert service._acl_writers(fd) == []  # no ACL
            try:
                os.setxattr(folder, "system.posix_acl_access", self.acl(
                    (user_obj, 7, undefined), (user, 7, STRANGER), (group_obj, 5, undefined), (mask, 7, undefined),
                    (other, 5, undefined)))
            except OSError as exc:
                if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                    pytest.skip(f"no POSIX ACLs on this file system: {exc}")
                raise
            assert stat.S_IMODE(os.stat(folder).st_mode) == 0o775  # the group bits are the mask
            assert service._acl_writers(fd) == [f"uid {STRANGER}"]
            # As root:root (the runner cannot chown): the group root may write, uid STRANGER too.
            unsafe = service._unsafe_folder(str(folder), directory(0, 0, 0o775), fd=fd)
            assert (unsafe.detail, unsafe.commands) == (f"mode 0775, ACL entries for uid {STRANGER}",
                                                        f"'chmod 755 {folder}'")
            os.chmod(folder, 0o755)  # sets the mask: the entry can no longer write
            assert service._acl_writers(fd) == []
        finally:
            os.close(fd)

    def test_without_os_getxattr_there_is_no_acl(self, monkeypatch):
        monkeypatch.delattr(os, "getxattr", raising=False)  # macOS (the tests, the local stand-in)
        assert service._acl_writers(0) == []

    @pytest.mark.parametrize("mode", [0o777, 0o757, 0o775])
    def test_no_folder_is_created_below_a_pool_root_that_others_can_change(self, api, mode):
        os.chmod(api.pool, mode)
        response = api.create(access=access((ALICE, "write")))
        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool, f"mode {mode:04o}"))
        assert os.listdir(api.pool) == []
        assert api.rows("SELECT * FROM smb_shares") == [] and not api.conf.exists()
        assert stat.S_IMODE(os.stat(api.pool).st_mode) == mode  # never changed

    def test_no_folder_is_created_below_a_parent_that_others_can_change(self, api):
        (api.pool / "downloads").mkdir()
        os.chmod(api.pool / "downloads", 0o777)
        response = api.create(folder="downloads/new/docs")
        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool / "downloads"))
        assert os.listdir(api.pool / "downloads") == []

    def test_a_sticky_folder_may_hold_a_new_share_folder(self, api):
        os.chmod(api.pool, 0o1777)
        response = api.create()
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []

    def test_a_patch_to_a_new_folder_below_such_a_folder_is_refused(self, api):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        before = api.conf.read_text()
        os.chmod(api.pool, 0o777)
        response = api.patch("docs", path=f"{api.pool}/papers")
        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool))
        assert not (api.pool / "papers").exists()
        assert api.rows("SELECT path, version FROM smb_shares") == [(f"{api.pool}/docs", 1)]
        assert api.conf.read_text() == before

    def test_an_existing_folder_below_such_a_folder_is_accepted_with_a_warning(self, api):
        (api.pool / "media").mkdir()
        os.chmod(api.pool / "media", 0o755)
        os.chmod(api.pool, 0o777)
        response = api.create("media", "media")
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [service.PERMISSIONS_WARNING, unsafe_warning(api.pool)]
        assert stat.S_IMODE(os.stat(api.pool / "media").st_mode) == 0o755

        (api.pool / "films").mkdir()
        os.chmod(api.pool / "films", 0o2770)
        response = api.patch("media", path=f"{api.pool}/films")
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [unsafe_warning(api.pool)]

    def test_served_shares_below_such_folders_are_status_problems(self, api):
        """One problem line per served share, naming the first such folder from the pool root."""
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        assert api.create("media", "media").status_code == 200
        (api.pool / "inner").mkdir()
        os.chmod(api.pool / "inner", 0o755)
        api.sql(f"INSERT INTO smb_shares (name, path, version) VALUES ('films', '{api.pool}/inner/films', 1)",
                f"INSERT INTO smb_shares (name, path, version) VALUES ('DATA', '{api.pool}', 1)")  # not served
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [f"Share DATA is not served: {POOL_ROOT}"]

        def problem(name, folder, detail="mode 0777"):
            return (f"Share {name}: {folder} can be changed by other accounts ({detail}), so they can replace the "
                    f"share's folder. Run 'chmod 755 {folder}' on the NAS.")

        os.chmod(api.pool, 0o777)
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            f"Share DATA is not served: {POOL_ROOT}",
            problem("docs", api.pool), problem("media", api.pool), problem("films", api.pool)]
        os.chmod(api.pool, 0o755)
        os.chmod(api.pool / "inner", 0o757)
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            f"Share DATA is not served: {POOL_ROOT}", problem("films", api.pool / "inner", "mode 0757")]

    def test_repair_permissions_is_refused(self, api, monkeypatch):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        os.chmod(api.pool, 0o777)
        walks = []
        monkeypatch.setattr(repair, "repair_tree", lambda *args, **kwargs: walks.append(args))
        response = api.client.post(f"{API}/smb/docs/repair-permissions")
        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool))
        assert walks == []

    def test_a_folder_renamed_in_after_the_mkdir_can_no_longer_be_changed(self, api, monkeypatch):
        """ADR-0153: a writer of the parent could remove the new folder and rename a folder of
        its choice into its place between the mkdir and the open, and UNOS then gave that
        folder (an app's data) the SMB group and 2770. Nothing is created below a parent that
        others can change any more, so only root could race."""
        victim = api.pool / "victim"
        victim.mkdir()
        os.chmod(victim, 0o755)
        (victim / "config.xml").write_text("app settings")
        os.chmod(api.pool, 0o777)
        real_mkdir = os.mkdir

        def racing_mkdir(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            if path == "newshare":
                os.rmdir("newshare", dir_fd=dir_fd)
                os.rename(victim, api.pool / "newshare")

        monkeypatch.setattr(os, "mkdir", racing_mkdir)
        response = api.create("newshare", "newshare")

        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool))
        assert stat.S_IMODE(os.stat(victim).st_mode) == 0o755
        assert os.listdir(victim) == ["config.xml"]
        assert not (api.pool / "newshare").exists()
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_a_share_folder_of_another_account_in_a_sticky_folder(self, api, owners, monkeypatch):
        """In a sticky folder that others can write, the owner of the share folder can still
        rename it away and put another folder or a symlink in its place."""
        os.chmod(api.pool, 0o1777)
        for name in ("media", "films"):
            (api.pool / name).mkdir()
            os.chmod(api.pool / name, 0o2770)
        owners(api.pool / "media", uid=STRANGER)
        detail = f"mode 1777, the share folder belongs to uid {STRANGER}"

        response = api.create("media", "media")
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [unsafe_warning(api.pool, detail)]
        assert api.create("films", "films").json()["warnings"] == []  # root's folder: only root can rename it
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            f"Share media: {api.pool} can be changed by other accounts ({detail}), so they can replace the share's "
            f"folder. Run 'chmod 755 {api.pool}' on the NAS."]
        walks = []
        monkeypatch.setattr(repair, "repair_tree", lambda *args, **kwargs: walks.append(args))
        response = api.client.post(f"{API}/smb/media/repair-permissions")
        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool, detail))
        assert walks == []

    def test_a_folder_another_account_makes_first_in_a_sticky_folder_is_reported(self, api, owners, monkeypatch):
        """The creation in a sticky folder finds a folder of another account made a moment
        before its mkdir: an existing folder, never changed, with the warning."""
        os.chmod(api.pool, 0o1777)
        real_mkdir = os.mkdir

        def mkdir_after_another_account(path, mode=0o777, *, dir_fd=None):
            if path == "newshare":
                real_mkdir(path, 0o755, dir_fd=dir_fd)
                owners(api.pool / "newshare", uid=STRANGER)
            real_mkdir(path, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "mkdir", mkdir_after_another_account)
        response = api.create("newshare", "newshare")

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [
            service.PERMISSIONS_WARNING, unsafe_warning(api.pool, f"mode 1777, the share folder belongs to uid {STRANGER}")]
        assert stat.S_IMODE(os.stat(api.pool / "newshare").st_mode) == 0o755  # never changed

    def test_a_folder_with_acl_entries_for_other_accounts(self, api, owners, monkeypatch):
        """root:root 0775 lets only the group root write, unless the group bits are the mask of
        an ACL that names other accounts (read from the descriptor on Linux)."""
        os.chmod(api.pool, 0o775)
        owners(api.pool, gid=0)
        root = os.stat(api.pool)
        writers = [f"uid {STRANGER}"]
        monkeypatch.setattr(service, "_acl_writers",
                            lambda fd: writers if os.fstat(fd).st_ino == root.st_ino else [])

        detail = f"mode 0775, ACL entries for uid {STRANGER}"
        response = api.create(access=access((ALICE, "write")))
        assert (response.status_code, response.json()["detail"]) == (409, refusal(api.pool, detail))
        assert os.listdir(api.pool) == []
        (api.pool / "media").mkdir()
        os.chmod(api.pool / "media", 0o2770)
        response = api.create("media", "media")
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [unsafe_warning(api.pool, detail)]
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            f"Share media: {api.pool} can be changed by other accounts ({detail}), so they can replace the share's "
            f"folder. Run 'chmod 755 {api.pool}' on the NAS."]
        writers = []
        response = api.create(access=access((ALICE, "write")))
        assert response.status_code == 200, response.text
        assert api.client.get(f"{API}/smb-status").json()["problems"] == []

    def test_an_acl_that_cannot_be_read_is_a_status_problem(self, api, owners, monkeypatch):
        (api.pool / "media").mkdir()
        os.chmod(api.pool / "media", 0o2770)
        assert api.create("media", "media").status_code == 200
        os.chmod(api.pool, 0o775)
        owners(api.pool, gid=0)

        def unreadable(fd):
            raise OSError("a folder's ACL could not be read")

        monkeypatch.setattr(service, "_acl_writers", unreadable)
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [
            "Share media: the folders above its folder could not be checked: a folder's ACL could not be read"]


# --- folders are created on the pool's mount (ADR-0152) --------------------------------


def not_mounted(api) -> str:
    return f"Pool 'data' is not mounted at {api.pool}. Mount it on the Storage page first."


class TestFolderCreationOnThePoolMount:
    """The pool can be unmounted, or its mountpoint removed, between the findmnt check and the
    creation (ADR-0152): the folder is created only through a descriptor
    of the pool root that is on the pool's mount, and the mountpoint is never made again."""

    @pytest.fixture
    def mount(self, api, monkeypatch):
        """The mount check as the real one sees it: the pool's mount is the folder at the
        pool's path now; a folder put at that path later is the bare mountpoint."""
        mounted = os.stat(api.pool)
        checked = []

        def pool_root_mount_problem(root_fd, pool):
            st = os.fstat(root_fd)
            checked.append(pool)
            return None if (st.st_dev, st.st_ino) == (mounted.st_dev, mounted.st_ino) else not_mounted(api)

        monkeypatch.setattr(service, "_pool_root_mount_problem", pool_root_mount_problem)
        return checked

    def unmount(self, api):
        os.rename(api.pool, api.base / "data-fs")
        os.mkdir(api.pool)

    def test_nothing_is_created_when_the_pool_left_its_mountpoint(self, api, monkeypatch, mount):
        monkeypatch.setattr(accounts, "gid_problem", lambda: self.unmount(api))  # after the findmnt check
        response = api.create(folder="sub/docs", access=access((ALICE, "write")))
        assert (response.status_code, response.json()["detail"]) == (409, not_mounted(api))
        assert mount == ["data"]
        assert os.listdir(api.pool) == [] and os.listdir(api.base / "data-fs") == []
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_the_folder_is_created_through_the_checked_descriptor(self, api, monkeypatch, mount):
        check = service._pool_root_mount_problem

        def check_then_unmount(root_fd, pool):
            problem = check(root_fd, pool)
            self.unmount(api)
            return problem

        monkeypatch.setattr(service, "_pool_root_mount_problem", check_then_unmount)
        response = api.create(folder="sub/docs")
        assert response.status_code == 200, response.text
        assert os.listdir(api.pool) == []  # nothing at the bare mountpoint
        assert stat.S_IMODE(os.stat(api.base / "data-fs" / "sub" / "docs").st_mode) == 0o2770

    def test_the_mountpoint_of_a_pool_is_never_made_again(self, api, monkeypatch):
        monkeypatch.setattr(accounts, "gid_problem", lambda: os.rmdir(api.pool))  # delete_pool removed it
        response = api.create(folder="sub/docs")
        assert (response.status_code, response.json()["detail"]) == (409, not_mounted(api))
        assert not api.pool.exists()


class TestPoolRootMountCheck:
    """service._pool_root_mount_problem, the check that tests/conftest.py replaces on Linux."""

    @pytest.fixture
    def root(self, api):
        fd = os.open(api.pool, os.O_RDONLY | os.O_DIRECTORY)
        yield fd
        os.close(fd)

    @pytest.fixture
    def table(self, api, monkeypatch):
        path = api.tmp / "mountinfo"
        monkeypatch.setattr(repair, "MOUNTINFO_PATH", str(path))
        return path

    def test_without_a_mount_table_there_is_nothing_to_check(self, api, root, monkeypatch):
        monkeypatch.setattr(repair, "MOUNTINFO_PATH", str(api.tmp / "no-such-file"))
        assert REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data") is None

    def test_the_pool_root_must_be_on_a_mount_at_the_pools_mountpoint(self, api, root, table, monkeypatch):
        monkeypatch.setattr(repair, "_mount_id", lambda fd: 41)
        table.write_text(f"40 1 0:30 / / rw - ext4 /dev/mmcblk1p2 rw\n41 40 0:40 / {api.pool} rw - btrfs /dev/sda rw\n")
        assert REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data") is None
        # Unmounted: the pool root's folder is on the system disk's mount.
        table.write_text(f"41 1 0:30 / / rw - ext4 /dev/mmcblk1p2 rw\n42 41 0:40 / {api.pool} rw - btrfs /dev/sda rw\n")
        assert REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data") == not_mounted(api)

    @pytest.mark.parametrize("unreadable", ["fdinfo", "mountinfo"])
    def test_procfs_that_cannot_be_read_fails_closed(self, api, root, table, monkeypatch, unreadable):
        table.write_text(f"41 1 0:40 / {api.pool} rw - btrfs /dev/sda rw\n")
        monkeypatch.setattr(repair, "_mount_id", lambda fd: 41)
        if unreadable == "fdinfo":
            def no_fdinfo(fd):
                raise OSError(errno.ENOENT, "No such file or directory")

            monkeypatch.setattr(repair, "_mount_id", no_fdinfo)
        else:
            table.unlink()
            table.mkdir()  # exists, cannot be read as a file
        problem = REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data")
        assert problem.startswith("The mount of pool 'data' could not be checked: ")
        assert problem.endswith(". Nothing was created.")

    @pytest.mark.skipif(not os.path.exists("/proc/self/fdinfo"), reason="needs Linux procfs (the Linux test runner)")
    def test_the_mount_id_of_the_descriptor(self, api, root, table, monkeypatch):
        info = open(f"/proc/self/fdinfo/{root}").read()
        mount_id = int(re.search(r"^mnt_id:\s*(\d+)$", info, re.M).group(1))
        table.write_text(f"{mount_id} 1 0:40 / {api.pool} rw - btrfs /dev/sda rw\n")
        assert REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data") is None
        table.write_text(f"{mount_id} 1 0:40 / {api.base} rw - tmpfs tmpfs rw\n")
        assert REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data") == not_mounted(api)
        # The real mount table: the test's pool is a folder, no mount point.
        monkeypatch.setattr(repair, "MOUNTINFO_PATH", "/proc/self/mountinfo")
        assert REAL_POOL_ROOT_MOUNT_PROBLEM(root, "data") == not_mounted(api)


class TestGidConflictInTheStatus:
    @pytest.mark.parametrize("unos_smb_gid", [None, 1234])
    def test_another_group_with_the_smb_gid_is_listed_once(self, api, monkeypatch, unos_smb_gid):
        """host_status() and gid_problem() both see another host group with gid 70000; when
        the group unos-smb is missing too, they say the same thing: one line."""
        fake = FakeSambaHost()
        fake.add_group("media", 70000)
        if unos_smb_gid is not None:
            fake.add_group("unos-smb", unos_smb_gid)
        monkeypatch.setattr(src.host, "run_on_host", fake.run)
        monkeypatch.setattr(accounts, "gid_problem", REAL_GID_PROBLEM)
        monkeypatch.setattr(accounts, "host_status", REAL_HOST_STATUS)
        gid_line, group_line = REAL_GID_PROBLEM(), REAL_HOST_STATUS().group_problem
        assert gid_line and group_line and gid_line != group_line  # both see a conflict

        status = api.client.get(f"{API}/smb-status").json()

        assert status["group_ok"] is False
        if unos_smb_gid is None:
            assert status["problems"] == [gid_line, service.GROUP_PROBLEM_NOTE]
        else:
            assert status["problems"] == [group_line, gid_line, service.GROUP_PROBLEM_NOTE]

    @pytest.mark.parametrize("gid_line", [
        "The owner of gid 70000 on the host could not be checked: getent timed out",
        # Another group took the gid between the two reads.
        "gid 70000 belongs to the host group video, not unos-smb: share folders would be opened to video's "
        "members. Remove or renumber video on the host",
    ])
    def test_a_gid_line_about_something_else_keeps_the_conflict(self, api, monkeypatch, gid_line):
        """Only the same conflict is listed once: when gid_problem() could not check the gid, or
        names another group, the line of host_status() is the only sign of this conflict."""
        fake = FakeSambaHost()
        fake.add_group("media", 70000)
        monkeypatch.setattr(src.host, "run_on_host", fake.run)
        monkeypatch.setattr(accounts, "host_status", REAL_HOST_STATUS)
        monkeypatch.setattr(accounts, "gid_problem", lambda: gid_line)
        group_line = REAL_HOST_STATUS().group_problem
        assert group_line == "gid 70000 is used by group media"

        status = api.client.get(f"{API}/smb-status").json()

        assert status["problems"] == [group_line, gid_line, service.GROUP_PROBLEM_NOTE]

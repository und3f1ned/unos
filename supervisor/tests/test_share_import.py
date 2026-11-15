"""The one-time import of the existing smb.conf (ADR-0084, ADR-0086).

Each test has its own database file, pools base, smb.conf and audit log; host
commands go to a fake behind ``src.host.run_on_host`` and the pool list is
``{"data"}`` (``storage.pools.managed_pool_names`` patched).
"""

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import src.host
from src.audit import router as audit_router
from src.auth.models import User
from src.database import Base
from src.host import HostResult
from src.security import validators
from src.shares import service, smb, smbconf
from src.storage import pools
from .audit_helpers import audit_entries

HASH = "$2b$12$" + "x" * 53
# Users: alice, bob, carol and dave (regular), admin (an administrator) and the
# legacy Bob.Smith, whose name SMB cannot use.
USERS = [("alice", False), ("bob", False), ("carol", False), ("dave", False), ("admin", True), ("Bob.Smith", False)]

# The device's smb.conf (s2u/device-state/out_a.txt, A4); the path is {pool}, the pool "data".
DEVICE_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no

[DATA]
   path = {pool}
   browseable = yes
   read only = no
   guest ok = no
"""

# The file written before phase 0 (A5): a guest share that guests could write.
PRE_PHASE0_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no


[DATA]
   path = {path}
   browseable = yes
   read only = no
   guest ok = yes
   force user = nobody
   force group = nogroup
   create mask = 0666
   directory mask = 0777
"""

NOT_EVERYONE = "It was open to every Samba user; UNOS shares need per-user access: grant it on the Shares page"
APPS_PROBLEM = "App data (data/apps) cannot be shared: apps keep their configuration there"


class FakeHost:
    """Host commands of the apply after an import: mkdir, testparm and smbcontrol succeed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        assert "close-denied-share" not in cmd
        if cmd[0] in ("mkdir", "testparm", "smbcontrol"):
            return HostResult(0, "", "")
        raise AssertionError(f"unexpected host command: {cmd}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "pools"
    (base / "data").mkdir(parents=True)
    samba = tmp_path / "samba"
    samba.mkdir()
    conf = samba / "smb.conf"
    db_path = tmp_path / "unos.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(User(username=name, hashed_password=HASH, is_admin=admin) for name, admin in USERS)
        session.commit()
    engine.dispose()

    for settings in {id(smb.settings): smb.settings, id(validators.settings): validators.settings}.values():
        monkeypatch.setattr(settings, "smb_conf_path", str(conf))
        monkeypatch.setattr(settings, "pools_base_path", str(base))
        monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
    host = FakeHost()
    monkeypatch.setattr(src.host, "run_on_host", host.run)
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(audit))
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    monkeypatch.setattr(service, "_last_import_problem", None)
    yield SimpleNamespace(base=base, pool=base / "data", conf=conf, samba=samba, db_path=db_path, host=host,
                          audit=audit)


async def run_import(env) -> bool:
    engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await service._import_if_needed_locked(db)
    finally:
        await engine.dispose()


def backups(env) -> list[Path]:
    """The import backups <smb.conf>.unos-import-backup-<UTC time>, oldest first (ADR-0362)."""
    pattern = re.compile(r"smb\.conf\.unos-import-backup-\d{8}_\d{6}_\d{6}")
    return sorted(env.samba / name for name in os.listdir(env.samba) if pattern.fullmatch(name))


def backup(env) -> Path | None:
    """The one import backup, or None."""
    found = backups(env)
    assert len(found) <= 1, found
    return found[0] if found else None


def write_conf(env, text: str) -> None:
    env.conf.write_text(text.format(pool=env.pool, base=env.base))


def shares(env) -> dict[str, SimpleNamespace]:
    with closing(sqlite3.connect(env.db_path)) as conn:
        rows = conn.execute(
            "SELECT id, name, path, comment, browseable, guest_ok, import_notes, version FROM smb_shares ORDER BY id"
        ).fetchall()
        access = conn.execute("SELECT share_id, username, level FROM smb_share_access ORDER BY id").fetchall()
    result = {}
    for share_id, name, path, comment, browseable, guest_ok, notes, version in rows:
        result[name] = SimpleNamespace(
            path=path, comment=comment, browseable=bool(browseable), guest_ok=bool(guest_ok),
            notes=json.loads(notes) if notes is not None else None, version=version,
            access={user: level for sid, user, level in access if sid == share_id},
        )
    return result


def state(env) -> SimpleNamespace | None:
    with closing(sqlite3.connect(env.db_path)) as conn:
        row = conn.execute(
            "SELECT shares_imported_at, import_report, pending_import_source FROM smb_state "
            "WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    return SimpleNamespace(imported_at=row[0], report=json.loads(row[1]) if row[1] is not None else None,
                           source=row[2])


def options(env) -> dict[str, tuple[int, int, int]]:
    """The protocol options of the shares that have a row (ADR-0377): name -> (encryption required,
    Time Machine, Previous Versions)."""
    with closing(sqlite3.connect(env.db_path)) as conn:
        return {name: (encryption, time_machine, previous) for name, encryption, time_machine, previous in conn.execute(
            "SELECT s.name, o.encryption_required, o.time_machine, o.previous_versions "
            "FROM smb_share_options o JOIN smb_shares s ON s.id = o.share_id")}


def one(env, name="x") -> SimpleNamespace:
    found = shares(env)
    assert name in found, found
    return found[name]


def served_folder(env, name="x"):
    return f"{env.pool}/{name}"


# --- mapping a section to a share (ADR-0086) ---------------------------------------------------------------------


class TestMapping:
    async def test_printer_share_is_skipped(self, env):
        write_conf(env, "[lp]\n path = /var/spool/samba\n printable = yes\n[pr2]\n path = /x\n print ok = yes\n")
        await run_import(env)
        assert shares(env) == {}
        assert state(env).report[1:3] == ["Printer share lp was not imported", "Printer share pr2 was not imported"]

    async def test_global_printable_applies_to_every_section(self, env):
        write_conf(env, "[global]\n printable = yes\n[x]\n path = {pool}/x\n valid users = alice\n")
        await run_import(env)
        assert shares(env) == {}
        assert "Printer share x was not imported" in state(env).report

    @pytest.mark.parametrize("name", ["bad name!", "a.b", "-x", "IPC$", "z" * 33])
    async def test_invalid_names_are_skipped(self, env, name):
        write_conf(env, f"[{name}]\n path = {{pool}}/x\n valid users = alice\n")
        await run_import(env)
        assert shares(env) == {}
        (line,) = [line for line in state(env).report if line.startswith("Share ")]
        assert line.startswith(f"Share {name} was not imported: ")

    async def test_homes_printers_and_print_dollar_are_not_imported(self, env):
        write_conf(env, "[homes]\n browseable = no\n[printers]\n path = /var/spool\n[print$]\n path = /p\n"
                        "[x]\n path = {pool}/x\n")
        await run_import(env)
        assert list(shares(env)) == ["x"]
        assert state(env).report[1:4] == ["[homes] was not imported (not supported)",
                                          "[printers] was not imported (not supported)",
                                          "[print$] was not imported (not supported)"]

    async def test_name_of_an_existing_row_is_skipped(self, env):
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO smb_shares (name, path) VALUES ('docs', '/x')")
            conn.commit()
        write_conf(env, "[DOCS]\n path = {pool}/docs\n")
        await run_import(env)
        assert list(shares(env)) == ["docs"]
        assert any(line.startswith("Share DOCS was not imported: another share has this name")
                   for line in state(env).report)

    async def test_no_path_or_a_path_too_long_is_skipped(self, env):
        write_conf(env, f"[a]\n comment = no path\n[b]\n path =\n[c]\n path = {env.pool}/{'p' * 4100}\n")
        await run_import(env)
        assert shares(env) == {}
        assert state(env).report[1:] == [
            "Share a was not imported: it has no folder (path)",
            "Share b was not imported: it has no folder (path)",
            "Share c was not imported: its folder (path) is longer than 4096 characters",
        ]

    async def test_invalid_comment_is_dropped_with_a_note(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n comment = Old [docs]\n valid users = alice\n")
        await run_import(env)
        share = one(env)
        assert share.comment == ""
        assert share.notes == ["Its comment was not kept: Share comment must not contain '[', ']' or '\\'"]
        assert share.access == {"alice": "read"}

    async def test_comment_is_kept(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n comment = Team files\n valid users = alice\n")
        await run_import(env)
        assert one(env).comment == "Team files"
        assert one(env).notes is None

    async def test_available_no(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice\n available = no\n")
        await run_import(env)
        assert one(env).access == {}
        assert one(env).notes == ["It was switched off (available = no) and stays without access"]

    @pytest.mark.parametrize("line", ["hosts allow = 192.168.1.0/24", "allow hosts = 10.0.0.1",
                                      "hosts deny = 10.0.0.5"])
    async def test_section_restrictions_remove_access(self, env, line):
        write_conf(env, f"[x]\n path = {{pool}}/x\n guest ok = yes\n write list = alice\n {line}\n")
        await run_import(env)
        share = one(env)
        assert (share.access, share.guest_ok) == ({}, False)
        assert share.notes == [
            f"It had {line}, which UNOS does not support; access was removed: review it and grant access"]

    @pytest.mark.parametrize("line", ["smb encrypt = required", "server smb encrypt = mandatory", "smb encrypt = Force",
                                      "smb encrypt = forced", "server smb encrypt = enforced",
                                      "smb encrypt = Forced", "server smb encrypt = ENFORCED"])
    async def test_required_encryption_in_a_section_is_kept_as_the_share_s_option(self, env, line):
        """The share requires encryption (ADR-0377), which keeps the restriction: the access stays,
        and guest access, which smbd refuses there, goes."""
        write_conf(env, f"[x]\n path = {{pool}}/x\n guest ok = yes\n write list = alice\n {line}\n")
        await run_import(env)
        share = one(env)
        assert (share.access, share.guest_ok) == ({"alice": "write"}, False)
        assert share.notes == [f"Guest access was removed: {service.GUEST_ENCRYPTION}"]
        assert options(env) == {"x": (1, 0, 0)}

    @pytest.mark.parametrize("value", ["desired", "if_required", "off", "default"])
    async def test_encryption_that_does_not_require_is_dropped(self, env, value):
        write_conf(env, f"[x]\n path = {{pool}}/x\n valid users = alice\n smb encrypt = {value}\n")
        await run_import(env)
        assert one(env).access == {"alice": "read"}
        assert one(env).notes == [f"Option 'smb encrypt = {value}' was not kept"]

    @pytest.mark.parametrize("command, close", [
        ("root preexec = /usr/local/bin/check %U", "root preexec close = yes"),
        ("preexec = /usr/local/bin/check", "preexec close = true"),
        ("exec = /usr/local/bin/check", "preexec close = 1"),
    ])
    async def test_a_script_that_refuses_connections_removes_access(self, env, command, close):
        """smbd refuses a tree connect when the script fails and its close option is true: UNOS
        cannot run the script, so the share keeps no access (the import only narrows)."""
        write_conf(env, f"[x]\n path = {{pool}}/x\n guest ok = yes\n write list = alice\n {command}\n {close}\n"
                        "[y]\n path = {pool}/y\n valid users = bob\n")
        await run_import(env)
        share = one(env)
        assert (share.access, share.guest_ok) == ({}, False)
        assert share.notes == [f"It had {command} with {close}, which UNOS does not support; access was removed: "
                               "review it and grant access"]
        assert (one(env, "y").access, one(env, "y").notes) == ({"bob": "read"}, None)
        # A restriction gets a report line too, in a section as in [global] (ADR-0086).
        assert [line for line in state(env).report if line.startswith("Share ")] == [
            f"Share x had {command} with {close}, which UNOS does not support: its access was removed; "
            "review it and grant access"]

    async def test_a_script_with_its_close_option_from_global_removes_the_access_of_that_share(self, env):
        write_conf(env, "[global]\n root preexec close = yes\n"
                        "[x]\n path = {pool}/x\n valid users = alice\n root preexec = /bin/check\n"
                        "[y]\n path = {pool}/y\n valid users = bob\n")
        await run_import(env)
        assert one(env).access == {}
        assert one(env).notes == ["It had root preexec = /bin/check with root preexec close = yes, which UNOS does "
                                  "not support; access was removed: review it and grant access"]
        assert one(env, "y").access == {"bob": "read"}
        assert [line for line in state(env).report if line.startswith("Share ")] == [
            "Share x had root preexec = /bin/check with root preexec close = yes, which UNOS does not support: its "
            "access was removed; review it and grant access"]

    @pytest.mark.parametrize("lines", ["root preexec = /bin/check", "root preexec = /bin/check\n preexec close = yes",
                                       "root preexec = /bin/check\n root preexec close = no"])
    async def test_a_script_that_refuses_no_connection_is_dropped(self, env, lines):
        write_conf(env, f"[x]\n path = {{pool}}/x\n valid users = alice\n {lines}\n")
        await run_import(env)
        assert one(env).access == {"alice": "read"}
        assert one(env).notes == [f"Option '{line.strip()}' was not kept" for line in lines.split("\n")]
        assert not any("preexec" in line for line in state(env).report)

    async def test_copy_removes_access(self, env):
        write_conf(env, "[other]\n path = {pool}/other\n valid users = alice\n"
                        "[x]\n path = {pool}/x\n copy = other\n valid users = alice\n")
        await run_import(env)
        assert one(env, "other").access == {"alice": "read"}
        assert one(env).access == {}
        assert one(env).notes == ["It copied the settings of [other], which UNOS does not follow; access was removed"]

    async def test_invalid_users_that_cannot_be_mapped(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice, bob\n invalid users = @staff, mallory\n")
        await run_import(env)
        assert one(env).access == {}
        assert one(env).notes == [
            "invalid users had entries that UNOS cannot map to its users (@staff, mallory); access was removed: "
            "review it and grant access"
        ]

    async def test_invalid_users_that_map_exclude_the_user(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice, bob\n invalid users = BOB\n read only = no\n")
        await run_import(env)
        assert one(env).access == {"alice": "write"}
        assert one(env).notes is None

    @pytest.mark.parametrize("source", [None, "live"])
    async def test_invalid_users_exclude_writers_of_a_guest_share(self, env, source):
        """Samba refuses an invalid users entry before it looks at write list
        (share_access.c user_ok_token): bob must not get write, also not after a restore."""
        text = "[x]\n path = {pool}/x\n guest ok = yes\n write list = carol, bob\n invalid users = bob\n"
        write_conf(env, text)
        if source is not None:
            with closing(sqlite3.connect(env.db_path)) as conn:
                conn.execute("INSERT INTO smb_state (id, next_uid, pending_import_source) VALUES (1, 70000, ?)",
                             (source,))
                conn.commit()
        await run_import(env)
        share = one(env)
        assert (share.guest_ok, share.access) == (source is None, {"carol": "write"})

    async def test_guest_share(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n guest ok = yes\n write list = carol\n")
        await run_import(env)
        share = one(env)
        assert (share.guest_ok, share.access, share.notes) == (True, {"carol": "write"}, None)

    async def test_guest_share_that_guests_could_write(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n public = yes\n writable = yes\n guest only = yes\n")
        await run_import(env)
        share = one(env)
        assert (share.guest_ok, share.access) == (True, {})
        assert share.notes == ["Guest write access was removed: guests can only read this share now",
                               "guest only was removed: people with access now sign in with their own account"]

    async def test_guest_share_from_a_restore_is_not_guest(self, env):
        text = "[x]\n path = {pool}/x\n guest ok = yes\n write list = carol\n".format(pool=env.pool)
        write_conf(env, text)
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO smb_state (id, next_uid, pending_import_source) VALUES (1, 70000, 'live')")
            conn.commit()
        await run_import(env)
        share = one(env)
        assert (share.guest_ok, share.access) == (False, {"carol": "write"})
        assert share.notes == ["Guest access was not restored"]
        assert state(env).source is None

    async def test_guest_ok_with_valid_users(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n guest ok = yes\n valid users = alice\n")
        await run_import(env)
        share = one(env)
        assert (share.guest_ok, share.access) == (False, {"alice": "read"})
        assert share.notes == ["Guests could not use it (valid users); guest access is off"]

    async def test_open_to_every_samba_user(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n read only = no\n write list = alice\n")
        await run_import(env)
        assert (one(env).access, one(env).notes) == ({}, [NOT_EVERYONE])

    async def test_levels(self, env):
        write_conf(env, "[ro]\n path = {pool}/ro\n valid users = alice, bob\n write list = bob\n"
                        "[rw]\n path = {pool}/rw\n valid users = alice, bob, carol\n read only = no\n"
                        " read list = carol\n write list = carol\n"
                        "[w]\n path = {pool}/w\n valid users = alice\n writeable = yes\n")
        await run_import(env)
        found = shares(env)
        assert found["ro"].access == {"alice": "read", "bob": "write"}
        assert found["rw"].access == {"alice": "write", "bob": "write", "carol": "write"}
        assert found["w"].access == {"alice": "write"}
        assert all(share.notes is None for share in found.values())

    async def test_groups_in_read_list_downgrade_write(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice, bob\n read only = no\n read list = @staff\n"
                        " write list = bob\n")
        await run_import(env)
        assert one(env).access == {"alice": "read", "bob": "write"}
        assert one(env).notes == ["read list had groups or variables (@staff): users who could write through "
                                  "read only = no can only read now"]

    async def test_entries_that_do_not_map_are_dropped(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice, mallory, admin, Bob.Smith, @staff, %U, Carol\n"
                        " write list = +writers, &w, dave\n")
        await run_import(env)
        assert one(env).access == {"alice": "read", "carol": "read"}
        assert one(env).notes == [
            "'mallory' in valid users is not a UNOS user and was dropped",
            "'admin' in valid users is an administrator, who cannot use SMB, and was dropped",
            "'Bob.Smith' in valid users is a UNOS user whose name SMB cannot use, and was dropped",
            "'Carol' was mapped to the UNOS user 'carol'",
            "Entries of valid users for groups or variables (@staff, %U) were dropped",
            "Entries of write list for groups or variables (+writers, &w) were dropped",
        ]

    async def test_a_case_insensitive_match_must_be_unique(self, env):
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('Eve', 'x', 0)")
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('eVe', 'x', 0)")
            conn.commit()
        write_conf(env, "[x]\n path = {pool}/x\n valid users = EVE, alice\n")
        await run_import(env)
        assert one(env).access == {"alice": "read"}
        assert one(env).notes == ["'EVE' in valid users matches several UNOS users and was dropped"]

    async def test_admin_users_ownership_and_other_options(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice\n admin users = alice\n group = staff\n"
                        " create mode = 0666\n vfs objects = recycle\n root preexec = /bin/sh -c id\n")
        await run_import(env)
        assert one(env).access == {"alice": "read"}
        assert one(env).notes == [
            "admin users removed (they act as root)",
            "Ownership settings (group = staff, create mode = 0666) were replaced by the UNOS model: group "
            "unos-smb, folders 2770; run Repair permissions",
            "Option 'vfs objects = recycle' was not kept",
            "Option 'root preexec = /bin/sh -c id' was not kept",
        ]

    async def test_invalid_booleans_are_read_fail_safe(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice\n read only = sometimes\n guest ok = maybe\n")
        await run_import(env)
        assert one(env).access == {"alice": "read"}
        assert one(env).guest_ok is False
        assert len(one(env).notes) == 2

    async def test_browseable(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n browsable = no\n[y]\n path = {pool}/y\n")
        await run_import(env)
        assert (one(env, "x").browseable, one(env, "y").browseable) == (False, True)


class TestEffectiveValues:
    async def test_global_values_apply_when_the_section_has_none(self, env):
        write_conf(env, "[global]\n read only = no\n valid users = alice, bob\n"
                        "[a]\n path = {pool}/a\n[b]\n path = {pool}/b\n read only = yes\n valid users = carol\n")
        await run_import(env)
        assert one(env, "a").access == {"alice": "write", "bob": "write"}
        assert one(env, "b").access == {"carol": "read"}

    async def test_samba_defaults_apply_when_neither_has_one(self, env):
        write_conf(env, "[global]\n workgroup = HOME\n[a]\n path = {pool}/a\n valid users = alice\n")
        await run_import(env)
        assert (one(env, "a").access, one(env, "a").guest_ok, one(env, "a").browseable) == (
            {"alice": "read"}, False, True)

    async def test_global_guest_ok(self, env):
        write_conf(env, "[global]\n guest ok = yes\n[a]\n path = {pool}/a\n")
        await run_import(env)
        assert one(env, "a").guest_ok is True

    async def test_global_available_no(self, env):
        write_conf(env, "[global]\n available = no\n[a]\n path = {pool}/a\n valid users = alice\n")
        await run_import(env)
        assert one(env, "a").access == {}

    async def test_global_ownership_settings(self, env):
        write_conf(env, "[global]\n force user = nobody\n[a]\n path = {pool}/a\n valid users = alice\n")
        await run_import(env)
        assert one(env, "a").notes == ["Ownership settings (force user = nobody) were replaced by the UNOS model: "
                                       "group unos-smb, folders 2770; run Repair permissions"]

    async def test_a_global_block_after_a_share_does_not_widen_it(self, env):
        """Samba copies the share defaults when a section starts (loadparm.c lp_do_section):
        a later [global] block changes only the shares after it."""
        write_conf(env, "[global]\n workgroup = WORKGROUP\n"
                        "[media]\n path = {pool}/media\n read only = yes\n"
                        "[docs]\n path = {pool}/docs\n valid users = alice\n"
                        "[global]\n guest ok = yes\n read only = no\n write list = bob\n"
                        "[late]\n path = {pool}/late\n valid users = alice, bob\n")
        await run_import(env)
        found = shares(env)
        # Neither a guest share nor writable in Samba: imported as such.
        assert (found["media"].guest_ok, found["media"].access, found["media"].notes) == (False, {}, [NOT_EVERYONE])
        assert (found["docs"].guest_ok, found["docs"].access, found["docs"].notes) == (False, {"alice": "read"}, None)
        # A share after the block has its values, as in Samba.
        assert (found["late"].guest_ok, found["late"].access) == (False, {"alice": "write", "bob": "write"})
        assert found["late"].notes == ["Guests could not use it (valid users); guest access is off"]


class TestRestrictions:
    @pytest.mark.parametrize("line", ["hosts allow = 192.168.1.0/24", "hosts deny = 10.0.0.5",
                                      "invalid users = bob",
                                      "root preexec = /usr/local/bin/check %U with root preexec close = yes",
                                      "exec = /bin/check with preexec close = on"])
    async def test_global_restrictions_remove_all_access(self, env, line):
        written = line.replace(" with ", "\n ")  # a script restriction is two lines
        write_conf(env, f"[global]\n {written}\n[x]\n path = {{pool}}/x\n valid users = alice, bob\n"
                        "[g]\n path = {pool}/g\n guest ok = yes\n write list = carol\n")
        await run_import(env)
        note = (f"The old [global] had {line}, which UNOS does not keep per share; access was removed: "
                "review it and grant access")
        for name in ("x", "g"):
            share = one(env, name)
            assert (share.access, share.guest_ok, share.notes) == ({}, False, [note])
        report = state(env).report
        assert [r for r in report if r.startswith("The old [global] had")] == [
            f"The old [global] had {line}, which UNOS does not keep per share: access to every imported share was "
            "removed; review the shares and grant access"
        ]
        assert [r for r in report if r.startswith("Share ")] == []  # one line for [global], none per share

    @pytest.mark.parametrize("line", ["hosts allow = 192.168.1.0/24", "invalid users = bob"])
    async def test_a_global_restriction_after_the_shares_still_removes_all_access(self, env, line):
        # smbd checks the final [global] hosts allow for every connection: the rule stays conservative.
        write_conf(env, f"[x]\n path = {{pool}}/x\n valid users = alice, bob\n[global]\n {line}\n")
        await run_import(env)
        assert (one(env).access, one(env).notes) == ({}, [
            f"The old [global] had {line}, which UNOS does not keep per share; access was removed: "
            "review it and grant access"])

    @pytest.mark.parametrize("line, lifted", [
        ("invalid users = alice", "invalid users ="),
        ("hosts allow = 192.168.1.5", "hosts allow ="),
        ("hosts deny = 10.0.0.5", "deny hosts ="),
    ])
    async def test_a_global_restriction_that_a_later_block_lifts_still_removes_all_access(self, env, line, lifted):
        """Samba copies the share defaults when a section starts (loadparm.c lp_do_section), so
        the shares between the two [global] blocks keep the restriction (alice was refused on
        media). Every [global] block counts, and the rule covers every imported share."""
        write_conf(env, f"[global]\n {line}\n"
                        "[media]\n path = {pool}/media\n valid users = alice, bob\n"
                        "[g]\n path = {pool}/g\n guest ok = yes\n write list = carol\n"
                        f"[global]\n {lifted}\n"
                        "[late]\n path = {pool}/late\n valid users = alice\n")
        await run_import(env)
        note = (f"The old [global] had {line}, which UNOS does not keep per share; access was removed: "
                "review it and grant access")
        for name in ("media", "g", "late"):
            share = one(env, name)
            assert (share.access, share.guest_ok, share.notes) == ({}, False, [note])
        assert [r for r in state(env).report if r.startswith("The old [global] had")] == [
            f"The old [global] had {line}, which UNOS does not keep per share: access to every imported share was "
            "removed; review the shares and grant access"
        ]

    @pytest.mark.parametrize("line", ["server smb encrypt = required", "server smb encrypt = forced",
                                      "smb encrypt = enforced"])
    async def test_required_encryption_in_global_is_kept_by_every_share(self, env, line):
        """Every imported share requires encryption (ADR-0377): the shares keep their access, and a
        guest share, whose guests smbd would refuse, loses guest access."""
        write_conf(env, f"[global]\n {line}\n[x]\n path = {{pool}}/x\n valid users = alice, bob\n"
                        "[g]\n path = {pool}/g\n guest ok = yes\n write list = carol\n")
        await run_import(env)
        x, g = one(env, "x"), one(env, "g")
        assert (x.access, x.guest_ok, x.notes) == ({"alice": "read", "bob": "read"}, False, None)
        assert (g.access, g.guest_ok, g.notes) == (
            {"carol": "write"}, False, [f"Guest access was removed: {service.GUEST_ENCRYPTION}"])
        assert options(env) == {"x": (1, 0, 0), "g": (1, 0, 0)}
        report = state(env).report
        assert f"The old [global] had {line}: every imported share requires encryption" in report
        assert [r for r in report if "access to every imported share was removed" in r] == []

    async def test_required_encryption_that_a_later_block_lifts_is_still_kept_by_every_share(self, env):
        """As with the restrictions above, every [global] block counts, and the rule covers every share."""
        write_conf(env, "[global]\n server smb encrypt = required\n"
                        "[media]\n path = {pool}/media\n valid users = alice, bob\n"
                        "[global]\n smb encrypt = default\n"
                        "[late]\n path = {pool}/late\n valid users = alice\n")
        await run_import(env)
        assert options(env) == {"media": (1, 0, 0), "late": (1, 0, 0)}
        assert (one(env, "late").access, one(env, "late").notes) == ({"alice": "read"}, None)

    async def test_every_global_restriction_is_named_once_in_file_order(self, env):
        write_conf(env, "[global]\n hosts deny = 10.0.0.5\n invalid users = bob\n"
                        "[x]\n path = {pool}/x\n valid users = alice\n"
                        "[global]\n hosts deny = 10.0.0.5\n hosts allow = 192.168.1.5\n")
        await run_import(env)
        assert one(env).notes == [
            f"The old [global] had {option}, which UNOS does not keep per share; access was removed: "
            "review it and grant access"
            for option in ("hosts deny = 10.0.0.5", "invalid users = bob", "hosts allow = 192.168.1.5")
        ]
        assert len([r for r in state(env).report if r.startswith("The old [global] had")]) == 3

    @pytest.mark.parametrize("line", ["include = /etc/samba/shares.conf", "config file = /etc/samba/%m.conf"])
    async def test_include_removes_all_access(self, env, line):
        value = line.split(" = ")[1]
        write_conf(env, f"[global]\n {line}\n[x]\n path = {{pool}}/x\n valid users = alice\n"
                        "[g]\n path = {pool}/g\n guest ok = yes\n")
        await run_import(env)
        for name in ("x", "g"):
            share = one(env, name)
            assert (share.access, share.guest_ok) == ({}, False)
            assert share.notes == [
                f"smb.conf included another file ({value}), which UNOS cannot read; access was removed"]
        assert any("was not followed" in line for line in state(env).report)


# --- the device and the old release -------------------------------------------------------------


class TestDeviceFiles:
    async def test_device_data_share(self, env):
        write_conf(env, DEVICE_CONF)
        original = env.conf.read_bytes()
        assert await run_import(env) is True
        assert shares(env) == {"DATA": SimpleNamespace(
            path=str(env.pool), comment="", browseable=True, guest_ok=False, version=1, access={},
            notes=[NOT_EVERYONE, f"Not served: {service.POOL_ROOT_PROBLEM}"],
        )}
        assert state(env).report == [
            f"Imported 1 share(s) from {env.conf}; the previous file is kept as {backup(env)}",
            "These settings of the old [global] were replaced: map to guest = Bad Password, "
            "log file = /var/log/samba/log.%m, max log size = 50",
        ]
        assert backup(env).read_bytes() == original
        assert env.conf.read_bytes() == original  # the import itself does not write smb.conf

    async def test_pre_phase0_guest_copy(self, env):
        # A folder of the pool instead of the pool root, so that the share is served.
        write_conf(env, PRE_PHASE0_CONF.format(path=served_folder(env, "shared")))
        await run_import(env)
        share = one(env, "DATA")
        assert (share.guest_ok, share.access) == (True, {})
        assert share.notes == [
            "Guest write access was removed: guests can only read this share now",
            "Ownership settings (force user = nobody, force group = nogroup, create mask = 0666, directory mask = "
            "0777) were replaced by the UNOS model: group unos-smb, folders 2770; run Repair permissions",
        ]

    async def test_pre_phase0_guest_copy_after_a_restore(self, env):
        write_conf(env, PRE_PHASE0_CONF.format(path=served_folder(env, "shared")))
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO smb_state (id, next_uid, pending_import_source) VALUES (1, 70000, 'live')")
            conn.commit()
        await run_import(env)
        share = one(env, "DATA")
        assert (share.guest_ok, share.access) == (False, {})
        assert share.notes[0] == "Guest access was not restored"
        assert share.notes[1].startswith("Ownership settings (force user = nobody")
        # The live file, which UNOS did not write, is kept before the import (ADR-0362).
        assert state(env).report[0] == (
            f"Imported 1 share(s) from {env.conf}; the previous file is kept as {backup(env)}")
        assert backup(env) is not None


# --- path rules (ADR-0145) -----------------------------------------------------------------


class TestPaths:
    async def test_paths_that_break_a_rule(self, env, tmp_path):
        write_conf(env, (
            "[root]\n path = {pool}\n valid users = alice\n"
            "[apps]\n path = {pool}/apps/x\n valid users = alice\n"
            "[other]\n path = {base}/other/x\n valid users = alice\n"
            "[outside]\n path = /srv/outside\n guest ok = yes\n"
            "[snap]\n path = {pool}/.snapshots/x\n valid users = alice\n"
            "[outer]\n path = {pool}/media\n valid users = alice\n"
            "[inner]\n path = {pool}/media/films\n guest ok = yes\n"
            "[fine]\n path = {pool}/docs\n valid users = alice\n"
        ))
        await run_import(env)
        found = shares(env)
        problems = {
            "root": service.POOL_ROOT_PROBLEM,
            "apps": "App data (data/apps) cannot be shared: apps keep their configuration there",
            "other": "Share path must be on a storage pool that UNOS manages: 'other' is not one",
            "outside": f"Share path must be within {env.base}",
            "snap": "Snapshot folders cannot be shared",
            "outer": "The folder overlaps share inner (one is inside the other). With one shared group, everyone "
                     "who can open the outer share can reach the inner one: use folders side by side.",
            "inner": "The folder overlaps share outer (one is inside the other). With one shared group, everyone "
                     "who can open the outer share can reach the inner one: use folders side by side.",
        }
        for name, problem in problems.items():
            assert (found[name].access, found[name].guest_ok) == ({}, False), name
            assert found[name].notes == [f"Not served: {problem}"], name
        assert (found["fine"].access, found["fine"].notes) == ({"alice": "read"}, None)
        assert found["outside"].path == "/srv/outside"  # refused paths are stored as written

    @pytest.mark.parametrize(
        "written, stored, problem",
        [
            ("{pool}/", "{pool}", service.POOL_ROOT_PROBLEM),
            ("{pool}/.", "{pool}", service.POOL_ROOT_PROBLEM),
            ("{pool}//apps", "{pool}/apps", APPS_PROBLEM),
            ("{pool}/./apps/x", "{pool}/apps/x", APPS_PROBLEM),
        ],
    )
    async def test_paths_not_in_normal_form(self, env, written, stored, problem):
        written, stored = written.format(pool=env.pool), stored.format(pool=env.pool)
        write_conf(env, f"[x]\n path = {written}\n valid users = alice\n read only = no\n guest ok = no\n")
        await run_import(env)
        share = one(env)
        assert (share.path, share.access, share.guest_ok) == (stored, {}, False)
        assert share.notes == [f"The folder was written as {written}; it is stored as {stored}",
                               f"Not served: {problem}"]

    async def test_a_folder_not_in_normal_form_is_stored_normalized_and_served(self, env):
        write_conf(env, "[x]\n path = {pool}//shared/\n valid users = alice\n read only = no\n")
        await run_import(env)
        share = one(env)
        assert (share.path, share.access) == (f"{env.pool}/shared", {"alice": "write"})
        assert share.notes == [f"The folder was written as {env.pool}//shared/; it is stored as {env.pool}/shared"]

    async def test_path_checks_run_in_a_worker_thread(self, env, monkeypatch):
        import threading

        seen = []
        real = service.share_path_problem

        def recording(*args, **kwargs):
            seen.append(threading.get_ident())
            return real(*args, **kwargs)

        monkeypatch.setattr(service, "share_path_problem", recording)
        write_conf(env, "[a]\n path = {pool}/a\n[b]\n path = {pool}/b\n")
        await run_import(env)
        assert seen and threading.main_thread().ident not in seen


# --- UNOS-rendered sources ------------------------------------------------------------------------


def rendered(env, *shares_):
    return smbconf.render_config(list(shares_))


class TestUnosRenderedSource:
    async def test_reimport_without_notes(self, env):
        text = rendered(
            env,
            service.ShareRender("empty", f"{env.pool}/empty", "", True, False, (), ()),
            service.ShareRender("team", f"{env.pool}/team", "Team", False, False, ("bob",), ("alice",)),
            service.ShareRender("guests", f"{env.pool}/guests", "", True, True, (), ("carol",)),
        )
        assert "   hosts allow = " in text and "   invalid users = root\n" in text
        env.conf.write_text(text)
        await run_import(env)
        found = shares(env)
        assert {name: (s.access, s.guest_ok, s.notes) for name, s in found.items()} == {
            "empty": ({}, False, None),
            "team": ({"alice": "write", "bob": "read"}, False, None),
            "guests": ({"carol": "write"}, True, None),
        }
        assert (found["team"].comment, found["team"].browseable) == ("Team", False)
        assert state(env).report == [f"Imported 3 share(s) from {env.conf}"]
        assert backup(env) is None

    async def test_unos_file_without_shares_gives_no_report(self, env):
        env.conf.write_text(smbconf.render_config([]))
        assert await run_import(env) is True
        assert shares(env) == {}
        assert state(env).report is None
        assert state(env).imported_at is not None
        assert backup(env) is None

    async def test_a_hand_edit_of_a_unos_file_still_counts(self, env):
        text = rendered(env, service.ShareRender("team", f"{env.pool}/team", "", True, False, ("bob",), ()))
        edited = text.replace("   workgroup = WORKGROUP\n", "   workgroup = WORKGROUP\n   hosts deny = 10.0.0.9\n")
        env.conf.write_text(edited)
        await run_import(env)
        assert one(env, "team").access == {}
        assert any(line.startswith("The old [global] had hosts deny = 10.0.0.9") for line in state(env).report)


# --- bytes that are not UTF-8 -----------------------------------------------------------------------


class TestNotUtf8:
    async def test_latin1_values(self, env):
        env.conf.write_bytes(
            f"[docs]\n path = {env.pool}/docs\n comment = Caf\xe9\n valid users = alice\n"
            f"[media]\n path = {env.pool}/caf\xe9\n valid users = alice\n"
            f"[caf\xe9]\n path = {env.pool}/c\n".encode("latin-1")
        )
        await run_import(env)
        assert list(shares(env)) == ["docs"]
        docs = one(env, "docs")
        assert (docs.comment, docs.access) == ("", {"alice": "read"})
        assert docs.notes == [
            "Its comment was not kept: Share comment must not contain line breaks or control characters"]
        report = state(env).report
        assert f"Share media was not imported: its folder ({env.pool}/caf�) is not valid UTF-8" in report
        assert any(line.startswith("Share caf� was not imported: ") for line in report)
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.text_factory = bytes
            for (raw,) in conn.execute("SELECT import_notes FROM smb_shares UNION ALL "
                                       "SELECT import_report FROM smb_state"):
                raw.decode("utf-8")  # strict
        assert backup(env).read_bytes() == env.conf.read_bytes()


# --- the marker, failures and the copy --------------------------------------------------------------


class TestMarker:
    async def test_the_import_runs_once(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice\n")
        assert await run_import(env) is True
        assert await run_import(env) is False
        assert list(shares(env)) == ["x"]

    async def test_deleted_shares_never_come_back(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice\n")
        await run_import(env)
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("DELETE FROM smb_share_access")
            conn.execute("DELETE FROM smb_shares")
            conn.commit()
        assert await run_import(env) is False
        assert shares(env) == {}

    async def test_missing_file_imports_nothing(self, env):
        assert await run_import(env) is True
        assert shares(env) == {}
        assert state(env).report == [f"Imported 0 share(s) from {env.conf}"]
        assert backup(env) is None

    async def test_a_failure_halfway_commits_nothing(self, env, monkeypatch):
        write_conf(env, "[x]\n path = {pool}/x\n valid users = alice\n[y]\n path = {pool}/y\n")
        real_commit = AsyncSession.commit

        async def failing_commit(self):
            await self.flush()  # the rows reach the database inside the transaction
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(AsyncSession, "commit", failing_commit)
        with pytest.raises(smb.SambaConfigError, match=(
                r"^Shares were not imported from smb.conf yet: unexpected RuntimeError \(details are in the "
                r"supervisor log\)$")):
            await run_import(env)
        assert shares(env) == {}
        assert state(env) is None
        assert service.last_import_problem() == "Shares were not imported from smb.conf: disk I/O error"
        # The copy was written before the import transaction and stays.
        assert backup(env).read_bytes() == env.conf.read_bytes()
        assert not env.audit.exists()

        monkeypatch.setattr(AsyncSession, "commit", real_commit)
        assert await run_import(env) is True
        assert sorted(shares(env)) == ["x", "y"]
        # The newest import backup has these bytes already: this import names it and writes none.
        assert len(backups(env)) == 1
        assert state(env).report[0] == f"Imported 2 share(s) from {env.conf}; the previous file is kept as {backup(env)}"
        assert service.last_import_problem() is None

    async def test_pool_list_unavailable_imports_nothing(self, env, monkeypatch):
        def broken():
            raise RuntimeError("cat: /etc/fstab: Permission denied")

        monkeypatch.setattr(pools, "managed_pool_names", broken)
        write_conf(env, "[x]\n path = {pool}/x\n")
        with pytest.raises(smb.SambaConfigError, match=(
                "^Shares were not imported from smb.conf yet: The pool list could not be read: "
                "cat: /etc/fstab: Permission denied$")):
            await run_import(env)
        assert state(env) is None and shares(env) == {}
        assert backup(env) is None

    async def test_an_unreadable_smb_conf_keeps_its_reason(self, env, monkeypatch):
        def unreadable():
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(smb, "read_conf_bytes", unreadable)
        with pytest.raises(smb.SambaConfigError, match=(
                r"^Shares were not imported from smb.conf yet: \[Errno 13\] Permission denied$")):
            await run_import(env)
        assert state(env) is None
        assert service.last_import_problem() == "Shares were not imported from smb.conf: [Errno 13] Permission denied"

    async def test_a_database_error_names_only_its_type(self, env, monkeypatch):
        """Account flows show this text to regular users (shares/accounts.py turns it into their warning), and a
        database error carries its SQL statement and parameters: share names, folders, comments
        and user names. The log and the administrator's status keep the details."""
        write_conf(env, "[docs]\n path = {pool}/docs\n comment = Team\n valid users = alice\n")

        async def locked(db):
            raise OperationalError("INSERT INTO smb_shares (name, path, comment) VALUES (?, ?, ?)",
                                   ("docs", f"{env.pool}/docs", "Team"), sqlite3.OperationalError("database is locked"))

        monkeypatch.setattr(service, "_run_import", locked)
        message = ("Shares were not imported from smb.conf yet: unexpected OperationalError "
                   "(details are in the supervisor log)")
        engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                with pytest.raises(smb.SambaConfigError) as raised:
                    await service.apply_smb_config(db)
        finally:
            await engine.dispose()
        assert str(raised.value) == message
        assert isinstance(raised.value.__cause__, OperationalError)
        assert (service.last_apply().ok, service.last_apply().message) == (False, message)
        assert service.last_import_problem().startswith(
            "Shares were not imported from smb.conf: (sqlite3.OperationalError) database is locked")
        assert state(env) is None and shares(env) == {}
        assert env.host.calls == []

    async def test_the_import_commits_nothing_of_the_callers_session(self, env):
        write_conf(env, "[x]\n path = {pool}/x\n")
        engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = await db.get(User, 1)
                assert await service._import_if_needed_locked(db) is True
                assert user.username == "alice"  # not expired by the import
        finally:
            await engine.dispose()

    async def test_a_failed_import_leaves_the_callers_objects_usable(self, env, monkeypatch):
        """An account flow commits, then applies; when the import fails it still answers with its objects."""
        write_conf(env, "[x]\n path = {pool}/x\n")
        monkeypatch.setattr(service, "_plan_import", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = await db.get(User, 2)
                user.is_admin = False
                await db.commit()
                with pytest.raises(smb.SambaConfigError, match=r"yet: unexpected RuntimeError \(details are in"):
                    await service.apply_smb_config(db)
                assert (user.username, user.is_admin) == ("bob", False)  # loaded, not expired
        finally:
            await engine.dispose()
        assert state(env) is None and shares(env) == {}


class TestCopy:
    async def test_lazy_trigger_writes_the_copy(self, env):
        write_conf(env, DEVICE_CONF)
        engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await service.ensure_imported(db)
                await service.ensure_imported(db)
        finally:
            await engine.dispose()
        assert backup(env).read_bytes() == env.conf.read_bytes()
        assert "the previous file is kept as" in state(env).report[0]

    async def test_apply_trigger_writes_the_copy_before_installing(self, env):
        write_conf(env, DEVICE_CONF)
        original = env.conf.read_bytes()
        engine = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                result = await service.apply_smb_config(db)
        finally:
            await engine.dispose()
        assert backup(env).read_bytes() == original
        assert result.changed is True
        assert env.conf.read_text() == smbconf.render_config([])  # DATA is a pool root: not served
        assert result.closed_shares == ["DATA"]

    async def test_every_import_backup_is_kept_with_its_date(self, env):
        """ADR-0362: an import of another file that UNOS did not write keeps it in a backup of its
        own, named with the UTC time of the copy; the earlier backup keeps its bytes."""
        write_conf(env, DEVICE_CONF)
        earlier = env.samba / "smb.conf.unos-import-backup-20261101_120000_000000"
        earlier.write_bytes(b"older copy")
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        await run_import(env)
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        first, new = backups(env)
        assert first == earlier and earlier.read_bytes() == b"older copy"
        assert new.read_bytes() == env.conf.read_bytes()
        assert before <= datetime.strptime(new.name.rsplit("-", 1)[1], "%Y%m%d_%H%M%S_%f") <= after
        assert state(env).report[0] == (
            f"Imported 1 share(s) from {env.conf}; the previous file is kept as {new}; earlier import backups "
            f"(UNOS keeps the newest 10): {earlier}")

    async def test_the_newest_ten_import_backups_are_kept(self, env):
        write_conf(env, DEVICE_CONF)
        earlier = [env.samba / f"smb.conf.unos-import-backup-202610{day:02d}_120000_000000" for day in range(1, 11)]
        for path in earlier:
            path.write_bytes(path.name.encode())
        (env.samba / "smb.conf.unos-replaced-20261001_120000_000000").write_bytes(b"replaced")
        await run_import(env)
        kept = backups(env)
        assert kept[:9] == earlier[1:] and len(kept) == 10 and kept[9].read_bytes() == env.conf.read_bytes()
        assert not earlier[0].exists()
        assert (env.samba / "smb.conf.unos-replaced-20261001_120000_000000").read_bytes() == b"replaced"

    async def test_an_import_backup_written_while_the_clock_was_set_back_is_not_written_again(self, env):
        """The backup that holds the file is named before a newer backup of another file, as after
        a clock set back: the import writes none and names that one (ADR-0362)."""
        write_conf(env, DEVICE_CONF)
        set_back = env.samba / "smb.conf.unos-import-backup-20261001_120000_000000"
        set_back.write_bytes(env.conf.read_bytes())
        newer = env.samba / "smb.conf.unos-import-backup-20261101_120000_000000"
        newer.write_bytes(b"another file")
        await run_import(env)
        assert backups(env) == [set_back, newer]
        assert state(env).report[0] == (
            f"Imported 1 share(s) from {env.conf}; the previous file is kept as {set_back}; earlier import backups "
            f"(UNOS keeps the newest 10): {newer}")

    async def test_no_copy_of_a_unos_file(self, env):
        env.conf.write_text(rendered(env, service.ShareRender("a", f"{env.pool}/a", "", True, False, (), ())))
        await run_import(env)
        assert backup(env) is None
        assert sorted(os.listdir(env.samba)) == ["smb.conf"]


class TestAudit:
    async def test_one_audit_entry(self, env):
        write_conf(env, "[a]\n path = {pool}/a\n[b]\n path = {pool}/b\n")
        await run_import(env)
        (entry,) = audit_entries(env.audit)
        assert (entry["action"], entry["username"], entry["resource_type"], entry["details"]) == (
            "smb_shares_imported", "system", "share", "Imported 2 shares from smb.conf")

    @pytest.mark.parametrize("text", [None, "[global]\n workgroup = HOME\n[homes]\n browseable = no\n"])
    async def test_an_import_of_no_share_writes_no_audit_entry(self, env, text):
        """Each audit entry rewrites the whole audit log on the system disk (device-5)."""
        if text is not None:
            write_conf(env, text)
        assert await run_import(env) is True
        assert state(env).imported_at is not None and shares(env) == {}
        assert not env.audit.exists()

    async def test_notes_and_report_are_logged(self, env, caplog):
        write_conf(env, DEVICE_CONF)
        with caplog.at_level("INFO", logger="src.shares.service"):
            await run_import(env)
        assert NOT_EVERYONE in caplog.text
        assert "These settings of the old [global] were replaced" in caplog.text


class TestJsonLines:
    @pytest.mark.parametrize(
        "value, expected",
        [(None, []), ("", []), ("not json", []), ('{"a": 1}', []), ('"text"', []), ("[1, 2]", []),
         ('["a", 2, null, "b"]', ["a", "b"]), ('["one"]', ["one"]), (b'["x"]', []), (7, [])],
    )
    def test_read_defensively(self, value, expected):
        assert service.json_lines(value) == expected


# --- the other times the import runs: the startup and the first read (ADR-0084) ---------------------------------

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import src.database  # noqa: E402
from src.auth.utils import verify_token  # noqa: E402
from src.database import get_db  # noqa: E402
from src.shares import accounts, interfaces  # noqa: E402
from src.storage import disks  # noqa: E402


@pytest.fixture
def startup(env, monkeypatch):
    """reconcile_smb_on_startup on the test database; the functions of shares/accounts.py are mocked."""
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool),
                               expire_on_commit=False)
    monkeypatch.setattr(src.database, "async_session", maker)
    monkeypatch.setattr(accounts, "reconcile_accounts", AsyncMock(return_value=interfaces.ReconcileResult()))
    monkeypatch.setattr(accounts, "read_smb_sessions", MagicMock(return_value=[]))
    monkeypatch.setattr(accounts, "end_smb_sessions", MagicMock(return_value=[]))
    monkeypatch.setattr(service, "_last_reconcile", service.LastReconcile())
    return env


@pytest.fixture
def client(env, monkeypatch):
    """The share API on the test database (marker NULL: nothing imported yet)."""
    from src.main import app

    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool),
                               expire_on_commit=False)

    async def override_get_db():
        async with maker() as db:
            yield db

    monkeypatch.setattr(disks, "host_mounts", lambda: [SimpleNamespace(target=str(env.pool))])
    monkeypatch.setattr(accounts, "gid_problem", lambda: None)
    monkeypatch.setattr(accounts, "host_status",
                        lambda: interfaces.AccountHostStatus(tools_missing=[], group_problem=None))
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=5, username="admin", is_admin=True)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(verify_token, None)


class TestStartupTrigger:
    async def test_the_startup_writes_the_copy_then_imports_then_applies(self, startup):
        env = startup
        write_conf(env, DEVICE_CONF)
        original = env.conf.read_bytes()

        await service.reconcile_smb_on_startup()

        assert backup(env).read_bytes() == original
        assert list(shares(env)) == ["DATA"]
        assert state(env).report[0] == (
            f"Imported 1 share(s) from {env.conf}; the previous file is kept as {backup(env)}")
        assert env.conf.read_text() == smbconf.render_config([])  # DATA is a pool root: not served
        details = [entry["details"] for entry in audit_entries(env.audit)]
        assert details[0] == "Imported 1 shares from smb.conf"
        assert details[1].startswith("SMB startup reconcile: Imported 1 share(s) from smb.conf; smb.conf was rewritten")

    async def test_the_startup_removes_the_options_of_deleted_shares(self, startup):
        """SQLite reuses the highest rowid: options left by a share deleted outside UNOS would attach
        to the next share (ADR-0377)."""
        env = startup
        write_conf(env, DEVICE_CONF)
        await service.reconcile_smb_on_startup()
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO smb_share_options (share_id, encryption_required, time_machine, "
                         "previous_versions) VALUES (99, 1, 0, 0)")
            conn.commit()

        await service.reconcile_smb_on_startup()

        with closing(sqlite3.connect(env.db_path)) as conn:
            assert conn.execute("SELECT * FROM smb_share_options").fetchall() == []
        details = [entry["details"] for entry in audit_entries(env.audit)]
        assert details[-1] == "SMB startup reconcile: Removed the options of 1 deleted share(s)"

    async def test_a_second_startup_imports_nothing(self, startup):
        env = startup
        write_conf(env, DEVICE_CONF)
        await service.reconcile_smb_on_startup()
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("DELETE FROM smb_share_access")
            conn.execute("DELETE FROM smb_shares")
            conn.commit()
        env.conf.write_text(DEVICE_CONF.format(pool=env.pool))  # a stale file does not bring DATA back

        await service.reconcile_smb_on_startup()

        assert shares(env) == {}
        imports = [e for e in audit_entries(env.audit) if e["details"].startswith("Imported")]
        assert len(imports) == 1
        assert env.conf.read_text() == smbconf.render_config([])


class TestLazyTrigger:
    def test_the_first_get_imports(self, env, client):
        write_conf(env, DEVICE_CONF)
        original = env.conf.read_bytes()

        response = client.get("/api/v1/shares/smb")

        assert response.status_code == 200, response.text
        (share,) = response.json()
        assert (share["name"], share["served"], share["access"]) == ("DATA", False, [])
        assert share["import_notes"] == [NOT_EVERYONE, f"Not served: {service.POOL_ROOT_PROBLEM}"]
        assert state(env).imported_at is not None
        assert backup(env).read_bytes() == original
        assert env.conf.read_bytes() == original  # a read imports, it does not apply
        assert env.host.calls == []

    def test_a_failed_import_answers_500_and_the_status_shows_it(self, env, client, monkeypatch):
        write_conf(env, DEVICE_CONF)
        monkeypatch.setattr(service, "_plan_import", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        response = client.get("/api/v1/shares/smb")
        assert (response.status_code, response.json()["detail"]) == (
            500, "Shares were not imported from smb.conf yet: unexpected RuntimeError "
                 "(details are in the supervisor log)")
        status = client.get("/api/v1/shares/smb-status")
        assert status.status_code == 200
        # The administrator's status names the cause.
        assert status.json()["problems"][0] == "Shares were not imported from smb.conf: boom"
        assert state(env) is None


class TestJsonColumnsInTheApi:
    @pytest.mark.parametrize("value", ["not json", '{"a": 1}', "[1, 2]", '"text"'])
    def test_invalid_values_read_as_empty(self, env, client, value):
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO smb_state (id, next_uid, shares_imported_at, import_report) "
                         "VALUES (1, 70000, '2026-11-06 12:00:00.000000', ?)", (value,))
            conn.execute("INSERT INTO smb_shares (name, path, comment, browseable, guest_ok, import_notes, version) "
                         "VALUES ('x', ?, '', 1, 0, ?, 1)", (f"{env.pool}/x", value))
            conn.commit()

        listed = client.get("/api/v1/shares/smb")
        status = client.get("/api/v1/shares/smb-status")

        assert listed.status_code == 200 and listed.json()[0]["import_notes"] == []
        assert status.status_code == 200 and status.json()["import_report"] == []

    def test_items_that_are_not_text_are_dropped(self, env, client):
        with closing(sqlite3.connect(env.db_path)) as conn:
            conn.execute("INSERT INTO smb_state (id, next_uid, shares_imported_at, import_report) "
                         "VALUES (1, 70000, '2026-11-06 12:00:00.000000', '[\"kept\", 1, null]')")
            conn.execute("INSERT INTO smb_shares (name, path, comment, browseable, guest_ok, import_notes, version) "
                         "VALUES ('x', ?, '', 1, 0, '[{\"a\": 1}, \"note\"]', 1)", (f"{env.pool}/x",))
            conn.commit()
        assert client.get("/api/v1/shares/smb").json()[0]["import_notes"] == ["note"]
        assert client.get("/api/v1/shares/smb-status").json()["import_report"] == ["kept"]

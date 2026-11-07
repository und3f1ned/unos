"""The whole SMB cycle with the real shares and accounts code (ADR-0189):
startup import, a share folder, an account, access changes, revocations and the
startup sweep, asserting the smb.conf that UNOS writes.

Host commands go to the FakeSambaHost (its fstab has the pool 'data'). It does
not emulate findmnt or btrfs, so pools._mounted_pool_target and
disks.host_mounts are patched; os.fchown is recorded, so that the test runs as
a regular user.
"""
from __future__ import annotations

import asyncio
import os
import stat
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import database
from src.config import get_settings
from src.security import validators
from src.shares import service, smb
from src.storage import disks, pools
from src.storage.disks import HostMount

from .samba_fake import DATA_UUID
from .test_smb_accounts import World

API = "/api/v1"
TEST_PW = "Test-pass-2026"
NEW_TEST_PW = "Test-new-pass-2026"

DEVICE_SMB_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no

[DATA]
   path = {base}/data
   browseable = yes
   read only = no
   guest ok = no
"""

ALL_SAMBA_TOOLS = {"getent", "groupadd", "useradd", "userdel", "smbpasswd", "pdbedit", "smbstatus",
                   "smbcontrol", "testparm"}

# The real functions of shares/service.py, recorded at import (before any fixture replaces them).
REAL_APPLY = service.apply_smb_config
REAL_DELETE_ACCESS = service.delete_access_of_user


def section(text: str, name: str) -> list[str]:
    """The lines of the section [name] of an smb.conf text."""
    lines, inside = [], False
    for line in text.split("\n"):
        if line.startswith("["):
            inside = line == f"[{name}]"
            continue
        if inside and line:
            lines.append(line)
    return lines


@pytest.fixture
def cycle(tmp_path, monkeypatch, caplog):
    """The World of test_smb_accounts (admin and the regular user 'test') with the real
    service.apply_smb_config and delete_access_of_user, a temporary smb.conf and pools base."""
    world = World(tmp_path, monkeypatch, caplog)
    # The real service functions, not the AsyncMocks of the unit tests.
    monkeypatch.setattr(service, "apply_smb_config", REAL_APPLY)
    monkeypatch.setattr(service, "delete_access_of_user", REAL_DELETE_ACCESS)
    world.add_user("test", TEST_PW)

    base = tmp_path / "pools"
    (base / "data").mkdir(parents=True)
    conf = tmp_path / "etc-samba" / "smb.conf"
    conf.parent.mkdir()
    conf.write_text(DEVICE_SMB_CONF.format(base=base))
    # smb, validators and pools keep the Settings object of their import; another test
    # may have rebuilt get_settings()'s cache since (test_secret_bootstrap.py does).
    for settings in {id(s): s for s in (get_settings(), smb.settings, validators.settings, pools.settings)}.values():
        monkeypatch.setattr(settings, "pools_base_path", str(base))
        monkeypatch.setattr(settings, "smb_conf_path", str(conf))
    monkeypatch.setattr(database, "async_session", world.maker)
    monkeypatch.setattr(service, "async_session", world.maker, raising=False)

    monkeypatch.setattr(pools, "_mounted_pool_target", lambda name, write=False: str(base / name))
    mount = HostMount(target=str(base / "data"), source="/dev/sda", fstype="btrfs", uuid=DATA_UUID,
                      majmin="0:44", vfs_options="rw,noatime", fs_options="rw")
    monkeypatch.setattr(disks, "host_mounts", lambda: [mount])
    chowns, chmods = [], []
    real_fchmod = os.fchmod
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: chowns.append((uid, gid)))

    def fchmod(fd, mode):
        chmods.append(mode)
        real_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", fchmod)
    try:
        yield SimpleNamespace(world=world, fake=world.fake, client=world.client, base=base, conf=conf,
                              chowns=chowns, chmods=chmods)
    finally:
        world.close()


def startup() -> None:
    asyncio.run(service.reconcile_smb_on_startup())


def since(fake, mark: int) -> list[list[str]]:
    return [argv for argv, _ in fake.calls[mark:]]


def patch_share(c, body: dict) -> dict:
    response = c.client.request("PATCH", f"{API}/shares/smb/DATA", json=body, headers=c.world.headers("admin"))
    assert response.status_code == 200, response.text
    return response.json()


def test_whole_cycle(cycle):
    c, fake, world = cycle, cycle.fake, cycle.world
    test_id = world.ids["test"]

    # 1. Startup imports the device's [DATA]: the pool root is not served, so
    # smb.conf is the header and render_global(False) only.
    startup()
    from src.shares import smbconf  # imported after startup(), which uses it too

    text = c.conf.read_text()
    assert text == smbconf.render_config([])
    assert "[DATA]" not in text
    shares = c.client.get(f"{API}/shares/smb", headers=world.headers("admin")).json()
    assert [(s["name"], s["served"], s["access"]) for s in shares] == [("DATA", False, [])]

    # 2. PATCH DATA's path to a folder of the pool: created with mode 2770 and gid 70000.
    info = patch_share(c, {"path": f"{c.base}/data/shared"})
    assert info["served"] is True
    folder = c.base / "data" / "shared"
    assert folder.is_dir()
    assert stat.S_IMODE(folder.stat().st_mode) & 0o777 == 0o770
    # 2770 was asked for (a runner that is not in the folder's group loses the setgid bit).
    assert 0o2770 in c.chmods
    assert c.chowns and {gid for _, gid in c.chowns} == {70000}
    assert section(c.conf.read_text(), "DATA")[3:7] == [
        "   guest ok = no", "   valid users = root", "   write list =", "   available = no",
    ]

    # 3. Allow 'test', then self-activation.
    assert c.client.post(f"{API}/auth/users/{test_id}/smb/enable", json={"username": "test"},
                         headers=world.headers("admin")).status_code == 200
    enabled = c.client.post(f"{API}/auth/me/smb/enable", json={"current_password": TEST_PW},
                            headers=world.headers("test"))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["account"]["state"] == "active"
    assert fake.passwd["test"].ours and "test" in fake.passdb

    # 4. Grant write.
    patch_share(c, {"access": [{"user_id": test_id, "username": "test", "level": "write"}]})
    data = section(c.conf.read_text(), "DATA")
    assert "   valid users = test" in data
    assert "   write list = test" in data
    assert "   available = yes" in data

    # 5. With a session of 'test' open on DATA: a downgrade to read closes the share.
    pid = fake.add_session("test", ["IPC$", "DATA"])
    mark = len(fake.calls)
    patch_share(c, {"access": [{"user_id": test_id, "username": "test", "level": "read"}]})
    assert ["smbcontrol", "smbd", "close-share", "DATA"] in since(fake, mark)
    assert "   write list =" in section(c.conf.read_text(), "DATA")

    # 6. The last principal removed: closed, and nobody can connect.
    mark = len(fake.calls)
    patch_share(c, {"access": []})
    assert ["smbcontrol", "smbd", "close-share", "DATA"] in since(fake, mark)
    data = section(c.conf.read_text(), "DATA")
    assert "   valid users = root" in data
    assert "   write list =" in data
    assert "   available = no" in data

    # 7. Guest on (an addition: nothing closed), then off with no principal (closed).
    mark = len(fake.calls)
    patch_share(c, {"guest_ok": True})
    assert ["smbcontrol", "smbd", "close-share", "DATA"] not in since(fake, mark)
    assert "   map to guest = Bad User" in c.conf.read_text().split("\n")
    mark = len(fake.calls)
    patch_share(c, {"guest_ok": False})
    assert ["smbcontrol", "smbd", "close-share", "DATA"] in since(fake, mark)
    assert "   map to guest = Never" in c.conf.read_text().split("\n")

    # 8. A password change of 'test' ends its session.
    mark = len(fake.calls)
    changed = c.client.post(f"{API}/auth/change-password",
                            json={"current_password": TEST_PW, "new_password": NEW_TEST_PW},
                            headers=world.headers("test"))
    assert changed.status_code == 200, changed.text
    assert ["smbcontrol", str(pid), "shutdown"] in since(fake, mark)
    assert fake.passdb["test"]["password"] == NEW_TEST_PW

    # 9. A startup while 'test' (active) and a foreign 'nas' have sessions: the
    # sweep ends the session of nas only.
    fake.add_account("nas", 1000, gid=1000, gecos="nas", home="/home/nas", shell="/bin/bash")
    test_pid = fake.add_session("test", ["IPC$"])
    nas_pid = fake.add_session("nas", ["IPC$"])
    mark = len(fake.calls)
    startup()
    shutdowns = [argv for argv in since(fake, mark) if argv[0] == "smbcontrol" and argv[-1] == "shutdown"]
    assert shutdowns == [["smbcontrol", str(nas_pid), "shutdown"]]
    assert test_pid in fake.session_pids()

    # 10. Deleting the user: the session ends before userdel; passdb and account are gone.
    mark = len(fake.calls)
    deleted = c.client.delete(f"{API}/auth/users/{test_id}", params={"username": "test"},
                              headers=world.headers("admin"))
    assert deleted.status_code == 200, deleted.text
    calls = since(fake, mark)
    assert calls.index(["smbcontrol", str(test_pid), "shutdown"]) < calls.index(["userdel", "test"])
    assert "test" not in fake.passdb and "test" not in fake.passwd

    # 11. A second startup writes nothing and sends the reload message.
    before = (c.conf.read_bytes(), c.conf.stat().st_mtime_ns, world.audit_file.read_bytes())
    commits = []
    real_commit = AsyncSession.commit

    async def counting_commit(self):
        commits.append(1)
        return await real_commit(self)

    mark = len(fake.calls)
    with patch.object(AsyncSession, "commit", counting_commit):
        startup()
    calls = since(fake, mark)
    assert (c.conf.read_bytes(), c.conf.stat().st_mtime_ns, world.audit_file.read_bytes()) == before
    assert commits == []
    assert ["smbcontrol", "all", "reload-config"] in calls
    assert not [argv for argv in calls if argv[0] == "testparm"]

    # 12. Without the Samba tools, two starts leave the audit log untouched.
    fake.missing = set(ALL_SAMBA_TOOLS)
    audit = world.audit_file.read_bytes()
    startup()
    startup()
    assert world.audit_file.read_bytes() == audit


def test_a_host_without_samba_writes_the_audit_log_only_for_changes(cycle):
    """ADR-0157: problems alone (the stand-in) write no audit entry."""
    fake, world = cycle.fake, cycle.world
    fake.missing = set(ALL_SAMBA_TOOLS)

    startup()  # the import is a change: one audit entry
    audit = world.audit_file.read_bytes() if world.audit_file.exists() else b""
    startup()
    startup()

    assert (world.audit_file.read_bytes() if world.audit_file.exists() else b"") == audit
    # Nothing to revoke without Samba: no account command was even tried.
    assert not [argv for argv in fake.argvs()
                if argv[0] in ("getent", "groupadd", "useradd", "userdel", "smbpasswd", "pdbedit")]


def test_smb_is_turned_on_only_once_unos_wrote_smb_conf(cycle, monkeypatch):
    """ADR-0130: the first apply after the upgrade fails (here testparm refuses the file),
    so Samba still serves the device's old smb.conf, whose [DATA] gives write access to every
    Samba user. Turning SMB on is refused (no smbpasswd, no passdb entry) until an apply
    succeeded; revocations are not blocked."""
    c, fake, world = cycle, cycle.fake, cycle.world
    test_id = world.ids["test"]
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    monkeypatch.setattr(service, "_last_import_problem", None)
    fake.testparm_result = (1, "Error loading services.")
    old = c.conf.read_text()

    startup()
    assert c.conf.read_text() == old  # the import ran; the apply did not install anything

    admin = world.headers("admin")
    assert c.client.post(f"{API}/auth/users/{test_id}/smb/enable", json={"username": "test"},
                         headers=admin).status_code == 200
    refused = c.client.post(f"{API}/auth/me/smb/enable", json={"current_password": TEST_PW},
                            headers=world.headers("test"))
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "SMB cannot be turned on until UNOS has installed its own smb.conf; ask an administrator"
    )
    by_admin = c.client.post(f"{API}/auth/users/{test_id}/smb/enable",
                             json={"username": "test", "password": NEW_TEST_PW}, headers=admin)
    assert by_admin.status_code == 409
    assert by_admin.json()["detail"] == (
        "SMB cannot be turned on until UNOS has installed its own smb.conf: smb.conf was not updated: "
        "testparm rejected the new smb.conf: Error loading services."
    )
    assert fake.commands("smbpasswd") == []
    assert "test" not in fake.passdb and "test" not in fake.passwd
    assert c.client.post(f"{API}/auth/me/smb/disable", headers=world.headers("test")).status_code == 200

    # testparm accepts the file now: the next start installs UNOS's smb.conf.
    fake.testparm_result = (0, "")
    startup()
    from src.shares import smbconf

    assert c.conf.read_text().startswith(smbconf.HEADER)
    enabled = c.client.post(f"{API}/auth/me/smb/enable", json={"current_password": TEST_PW},
                            headers=world.headers("test"))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["account"]["state"] == "active"
    assert fake.passdb["test"]["password"] == TEST_PW


def test_smb_conf_replaced_after_unos_wrote_it(cycle, monkeypatch):
    """ADR-0130 after a successful apply: the device's old smb.conf is copied back while
    this release runs. Turning SMB on is refused, and neither answer claims what is not
    so: the regular user is told to ask an administrator (the Shares page lists no reason
    for this), the administrator that the file was replaced after UNOS wrote it."""
    c, fake, world = cycle, cycle.fake, cycle.world
    test_id = world.ids["test"]
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    monkeypatch.setattr(service, "_last_import_problem", None)
    from src.shares import smbconf

    startup()  # the import and the first apply: UNOS's smb.conf is installed
    assert c.conf.read_text().startswith(smbconf.HEADER)
    admin = world.headers("admin")
    assert c.client.post(f"{API}/auth/users/{test_id}/smb/enable", json={"username": "test"},
                         headers=admin).status_code == 200
    c.conf.write_text(DEVICE_SMB_CONF.format(base=c.base))

    refused = c.client.post(f"{API}/auth/me/smb/enable", json={"current_password": TEST_PW},
                            headers=world.headers("test"))
    assert (refused.status_code, refused.json()["detail"]) == (
        409, "SMB cannot be turned on until UNOS has installed its own smb.conf; ask an administrator"
    )
    by_admin = c.client.post(f"{API}/auth/users/{test_id}/smb/enable",
                             json={"username": "test", "password": NEW_TEST_PW}, headers=admin)
    assert (by_admin.status_code, by_admin.json()["detail"]) == (
        409,
        "SMB cannot be turned on until UNOS has installed its own smb.conf: it was replaced or removed "
        "after UNOS wrote it; it is written again at the next change or restart",
    )
    assert fake.commands("smbpasswd") == []
    assert "test" not in fake.passdb and "test" not in fake.passwd

    # As the reason says: the next start writes UNOS's smb.conf again, and SMB can be turned on.
    startup()
    assert c.conf.read_text().startswith(smbconf.HEADER)
    enabled = c.client.post(f"{API}/auth/me/smb/enable", json={"current_password": TEST_PW},
                            headers=world.headers("test"))
    assert enabled.status_code == 200, enabled.text
    assert fake.passdb["test"]["password"] == TEST_PW


def test_a_regular_users_answers_name_no_other_share_and_no_host_output(cycle):
    """ADR-0131: an administrator removed alice's access to her private share
    while Samba's reload fails, so its close is pending. The regular user 'test' turns its own
    SMB off: the answer carries fixed warnings only, while the administrator's status keeps
    the details."""
    c, fake, world = cycle, cycle.fake, cycle.world
    startup()
    alice_id = world.ids["alice"]
    world.make_active("alice", 70005, "Alice-pass-2026")
    world.make_active("test", 70006, TEST_PW)
    admin = world.headers("admin")
    body = {"name": "alice_private", "path": f"{c.base}/data/alice_private",
            "access": [{"user_id": alice_id, "username": "alice", "level": "write"}]}
    assert c.client.post(f"{API}/shares/smb", json=body, headers=admin).status_code == 200
    fake.fail[("smbcontrol", "all", "reload-config")] = (
        1, "Failed to send message to all: NT_STATUS_ACCESS_DENIED\n"
           "debug: lock directory /run/samba, private dir /var/lib/samba/private\n")
    fake.fail[("systemctl", "reload", "smbd")] = (
        1, "Job for smbd.service failed.\nSee \"journalctl -xeu smbd.service\".\n")
    patched = c.client.request("PATCH", f"{API}/shares/smb/alice_private", json={"access": []}, headers=admin)
    assert patched.status_code == 200, patched.text
    assert service.last_apply().pending_close == {"alice_private"}

    off = c.client.post(f"{API}/auth/me/smb/disable", headers=world.headers("test"))
    assert off.status_code == 200, off.text
    assert off.json()["warnings"] == [
        "SMB access changed, but Samba has not applied every change yet; "
        "an administrator can see why on the Shares page"
    ]

    # The reload works now, but closing alice_private fails.
    del fake.fail[("smbcontrol", "all", "reload-config")]
    fake.fail[("smbcontrol", "smbd", "close-share", "alice_private")] = (1, "smbcontrol: Can't find pid for smbd\n")
    world.make_active("test", 70006, TEST_PW)
    off = c.client.post(f"{API}/auth/me/smb/disable", headers=world.headers("test"))
    assert off.status_code == 200, off.text
    for warning in off.json()["warnings"]:
        assert "alice_private" not in warning and "smbcontrol" not in warning and "\n" not in warning
        assert "NT_STATUS" not in warning and "/var/lib" not in warning
    assert off.json()["warnings"] == [
        "SMB access changed, but Samba has not applied every change yet; "
        "an administrator can see why on the Shares page"
    ]

    # The administrator still sees what is pending.
    status = c.client.get(f"{API}/shares/smb-status", headers=admin).json()
    assert any("alice_private" in problem for problem in status["problems"])

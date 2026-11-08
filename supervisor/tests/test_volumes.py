"""Volumes: btrfs subvolumes directly in a pool's top directory, through the API.

Host commands go to the in-memory ``FakeHost`` of test_pool_persistence,
extended here with the subvolumes and quota groups of its mounted btrfs
filesystems (``FakeSubvolumes``). ``subprocess.run`` is blocked there, so every
command must go through ``src.host.run_on_host``: the host's mount namespace,
where the pool's mount was checked.
"""

from __future__ import annotations

import os
import subprocess
import sys

import docker
import pytest
from fastapi import HTTPException

from src import host as host_module
from src.host import HostResult
from src.shares import nfs
from src.storage import pools
from src.storage.schemas import VolumeCreate

from .test_pool_persistence import (
    DATA_UUID,
    MEDIA_UUID,
    admin_client,
    install_fake_host,
    wait_for_app_starts,
)

API = "/api/v1/storage/pools"
TOP_LEVEL = 5  # id of the top-level subvolume of every btrfs filesystem
ARCHIVE_UUID = "7e5f3d92-4567-4f6e-8e3d-bc6f5e4d7f80"
OTHER_UUID = "8f6a4ea3-5678-4a7f-9f4e-cd7a6f5e8a91"
NOT_ENABLED = "ERROR: unable to limit requested quota group: Transport endpoint is not connected"
# What btrfs-progs 4.20 to 6.14 print for `btrfs qgroup show` without quotas (exit status 1).
QGROUPS_NOT_ENABLED = "ERROR: can't list qgroups: quotas not enabled"
INVALID = "ERROR: unable to limit requested quota group: Invalid argument"
GIB = 1 << 30
# A subvolume name that is not UTF-8 (b"x\xff"), as FakeHost output holds it (surrogate escape).
NOT_UTF8 = b"x\xff".decode("utf-8", "surrogateescape")
# Before any fixture replaces them (test_output_of_a_real_command).
REAL_SUBPROCESS_RUN = subprocess.run
REAL_RUN_ON_HOST = host_module.run_on_host


class FakeSubvolumes:
    """Subvolumes and quota groups of the FakeHost's mounted btrfs filesystems.

    Answers ``btrfs subvolume list|create|delete|show``, ``btrfs qgroup
    show|limit`` and ``btrfs inspect-internal rootid`` like btrfs-progs; every
    other btrfs command still goes to FakeHost. Created and deleted
    subvolumes appear in and disappear from ``FakeHost.dirs``, and the
    commands that change the host are recorded in ``FakeHost.changes``.
    Names may hold bytes that are not UTF-8 (NOT_UTF8): FakeHost.run decodes
    them like subprocess.run.
    """

    def __init__(self, fake) -> None:
        self.fake = fake
        # filesystem uuid -> subvolume id -> (parent subvolume id, path from the top level)
        self.subvolumes: dict[str, dict[int, tuple[int, str]]] = {}
        self.snapshot_of: dict[int, int] = {}  # snapshot id -> id of the subvolume it copies
        self.quotas: set[str] = set()  # uuids of the filesystems with quotas enabled
        # subvolume id -> limit in bytes of its quota group (0/<id>); ids are unique here
        self.limits: dict[int, int] = {}
        # mountpoint -> id of the subvolume mounted there (the top level if missing)
        self.mounted_subvolume: dict[str, int] = {}
        self._next_id = 256
        other_btrfs_commands = fake._btrfs

        def btrfs(cmd):
            if cmd[1] in ("subvolume", "qgroup", "inspect-internal"):
                return self._run(cmd)
            return other_btrfs_commands(cmd)

        fake._btrfs = btrfs

    def add(self, uuid: str, path: str, parent: int = TOP_LEVEL, snapshot_of: int | None = None) -> int:
        """An existing subvolume at ``path`` (relative to the top level); returns its id.

        Like btrfs, every new subvolume gets a higher id than all before it.
        """
        subvol_id = self._next_id
        self._next_id += 1
        self.subvolumes.setdefault(uuid, {})[subvol_id] = (parent, path)
        if snapshot_of is not None:
            self.snapshot_of[subvol_id] = snapshot_of
        return subvol_id

    def paths(self, uuid: str) -> list[str]:
        return sorted(path for _, path in self.subvolumes.get(uuid, {}).values())

    def _mounted_at(self, target: str) -> str | None:
        mount = next(
            (m for m in self.fake.mounts if m["target"] == target and m["fstype"] == "btrfs"), None
        )
        return mount["uuid"] if mount else None

    def _read_only(self, target: str) -> bool:
        return any(
            "ro" in f"{m['vfs-options']},{m['fs-options']}".split(",")
            for m in self.fake.mounts
            if m["target"] == target
        )

    # Commands that write: what btrfs-progs prints when they fail.
    _WRITES = {
        ("subvolume", "create"): "ERROR: cannot create subvolume",
        ("subvolume", "delete"): "ERROR: Could not destroy subvolume/snapshot",
        ("qgroup", "limit"): "ERROR: unable to limit requested quota group",
    }

    def _subvolume_at(self, path: str) -> tuple[str, int] | None:
        """(filesystem uuid, id) of the subvolume at ``path``, as it is named now, or None.

        Only for paths directly in a mountpoint of a filesystem whose top level is mounted.
        """
        target, name = os.path.split(path)
        uuid = self._mounted_at(target)
        if uuid is None or self.mounted_subvolume.get(target, TOP_LEVEL) != TOP_LEVEL:
            return None
        for subvol_id, (parent, subvol_path) in self.subvolumes.get(uuid, {}).items():
            if parent == TOP_LEVEL and subvol_path == name:
                return uuid, subvol_id
        return None

    def _qgroup_limit(self, cmd: list[str]) -> HostResult:
        """``btrfs qgroup limit <size> [<qgroupid>] <path>`` (btrfs-progs 4.20 to 6.14).

        Without a quota group id, the subvolume at ``<path>`` as it is named
        when the command runs; with one, that quota group of the filesystem
        that ``<path>`` is on (here: its mountpoint or a path directly in it).
        """
        assert len(cmd) in (5, 6), cmd
        size, path = cmd[3], cmd[-1]
        if len(cmd) == 5:
            found = self._subvolume_at(path)
            if found is None:
                return HostResult(1, "", f"ERROR: not a subvolume: {path}")
            uuid, subvol_id = found
        else:
            uuid = self._mounted_at(path) or self._mounted_at(os.path.dirname(path))
            if uuid is None:
                return HostResult(1, "", f"ERROR: not a btrfs filesystem: {path}")
            level, _, number = cmd[4].partition("/")
            assert level == "0" and number.isdigit(), cmd
            subvol_id = int(number)
        if uuid not in self.quotas:
            return HostResult(1, "", NOT_ENABLED)
        if subvol_id not in self.subvolumes.get(uuid, {}):
            return HostResult(1, "", "ERROR: unable to limit requested quota group: No such file or directory")
        self.fake.changes.append(cmd)
        self.limits[subvol_id] = int(size)
        return HostResult(0, "", "")

    def _run(self, cmd: list[str]) -> HostResult:
        action = cmd[1:3]
        failure = self._WRITES.get(tuple(action))
        if failure is not None:
            # The mountpoint: the last argument of delete and of a limit by
            # quota group id, the parent of the others'.
            by_id = action == ["subvolume", "delete"] or (action == ["qgroup", "limit"] and len(cmd) == 6)
            written = cmd[-1] if by_id else os.path.dirname(cmd[-1])
            if self._read_only(written):
                return HostResult(1, "", f"{failure}: Read-only file system")
        if action == ["subvolume", "list"]:
            assert len(cmd) == 4, cmd
            uuid = self._mounted_at(cmd[3])
            if uuid is None:
                return HostResult(1, "", f"ERROR: not a btrfs filesystem: {cmd[3]}")
            lines = [
                f"ID {subvol_id} gen 12 top level {parent} path {path}\n"
                for subvol_id, (parent, path) in sorted(self.subvolumes.get(uuid, {}).items())
            ]
            return HostResult(0, "".join(lines), "")
        if action == ["subvolume", "create"]:
            assert len(cmd) == 4, cmd
            path = cmd[3]
            target, name = os.path.split(path)
            uuid = self._mounted_at(target)
            if uuid is None:
                return HostResult(1, "", f"ERROR: not a btrfs filesystem: {target}")
            if path in self.fake.dirs or path in self.fake.files:
                return HostResult(1, "", f"ERROR: target path already exists: {path}")
            self.fake.changes.append(cmd)
            mounted = self.mounted_subvolume.get(target, TOP_LEVEL)
            if mounted == TOP_LEVEL:
                self.add(uuid, name)
            else:  # in the mounted subvolume, not in the top level
                self.add(uuid, f"{self.subvolumes[uuid][mounted][1]}/{name}", parent=mounted)
            self.fake.dirs.add(path)
            return HostResult(0, f"Create subvolume '{path}'\n", "")
        if action == ["subvolume", "delete"]:
            # Only by id: btrfs-progs resolves a path (realpath), symbolic links included.
            assert len(cmd) == 6 and cmd[3] == "--subvolid", cmd
            subvol_id, target = int(cmd[4]), cmd[5]
            uuid = self._mounted_at(target)
            if uuid is None:
                return HostResult(1, "", f"ERROR: not a btrfs filesystem: {target}")
            subvolumes = self.subvolumes.get(uuid, {})
            if subvol_id not in subvolumes:
                return HostResult(1, "", "ERROR: Could not destroy subvolume/snapshot: No such file or directory")
            if any(parent == subvol_id for parent, _ in subvolumes.values()):
                return HostResult(1, "", "ERROR: Could not destroy subvolume/snapshot: Directory not empty")
            self.fake.changes.append(cmd)
            _, path = subvolumes.pop(subvol_id)
            self.fake.dirs.discard(os.path.join(target, path))
            return HostResult(0, f"Delete subvolume (no-commit): '{target}/{path}'\n", "")
        if action == ["subvolume", "show"]:
            # By id only; the first line is the path from the top level (btrfs-progs >= 4.16).
            assert len(cmd) == 6 and cmd[3] == "-r", cmd
            subvol_id, target = int(cmd[4]), cmd[5]
            uuid = self._mounted_at(target)
            if uuid is None:
                return HostResult(1, "", f"ERROR: not a btrfs filesystem: {target}")
            if subvol_id not in self.subvolumes.get(uuid, {}):
                return HostResult(1, "", "ERROR: Failed to get subvol info: No such file or directory")
            parent, path = self.subvolumes[uuid][subvol_id]
            snapshots = [
                f"\t\t\t\t{self.subvolumes[uuid][snapshot][1]}\n"
                for snapshot, source in sorted(self.snapshot_of.items())
                if source == subvol_id and snapshot in self.subvolumes[uuid]
            ]
            return HostResult(0, (
                f"{path}\n"
                f"\tName: \t\t\t{os.path.basename(path)}\n"
                "\tParent UUID: \t\t-\n"
                f"\tSubvolume ID: \t\t{subvol_id}\n"
                "\tGeneration: \t\t12\n"
                f"\tParent ID: \t\t{parent}\n"
                f"\tTop level ID: \t\t{parent}\n"
                "\tFlags: \t\t\t-\n"
                "\tSnapshot(s):\n"
                + "".join(snapshots)
            ), "")
        if action == ["qgroup", "show"]:
            assert len(cmd) == 4, cmd
            uuid = self._mounted_at(cmd[3])
            if uuid is None:
                return HostResult(1, "", f"ERROR: not a btrfs filesystem: {cmd[3]}")
            if uuid not in self.quotas:
                return HostResult(1, "", QGROUPS_NOT_ENABLED)
            # btrfs-progs 6 adds the subvolume paths.
            lines = ["Qgroupid    Referenced    Exclusive   Path \n",
                     "--------    ----------    ---------   ---- \n",
                     "0/5           16.00KiB     16.00KiB   <toplevel>\n"]
            lines += [
                f"0/{subvol_id:<10}    16.00KiB     16.00KiB   {path}\n"
                for subvol_id, (_, path) in sorted(self.subvolumes.get(uuid, {}).items())
            ]
            return HostResult(0, "".join(lines), "")
        if action == ["qgroup", "limit"]:
            return self._qgroup_limit(cmd)
        if action == ["inspect-internal", "rootid"]:
            assert len(cmd) == 4, cmd
            target = cmd[3]
            if self._mounted_at(target) is None:
                return HostResult(1, "", f"ERROR: cannot open {target}: Not a btrfs filesystem")
            return HostResult(0, f"{self.mounted_subvolume.get(target, TOP_LEVEL)}\n", "")
        raise AssertionError(f"unexpected btrfs command: {cmd}")


@pytest.fixture
def host(monkeypatch, tmp_path):
    """Pool "data" mounted at /mnt/pools/data; pool "media" exists but is not mounted.

    No SMB shares or NFS exports: ``host.smb_conf`` is a file of this test, and the
    host's export files are files of the FakeHost (see add_share and add_export).
    """
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake.mount_pool("data")
    fake.add_disk("sdb", serial="WD-2")
    fake.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
    # The mountpoint of a pool that is not mounted: a directory on the boot disk.
    fake.dirs.add("/mnt/pools/media")
    fake.subvols = FakeSubvolumes(fake)
    fake.smb_conf = tmp_path / "smb.conf"
    fake.smb_conf.write_text("[global]\n   workgroup = WORKGROUP\n   security = user\n")
    fake.files["/etc/exports"] = ""
    # One settings object for pools, shares.smb and shares.nfs.
    monkeypatch.setattr(pools.settings, "smb_conf_path", str(fake.smb_conf))
    monkeypatch.setattr(pools.settings, "nfs_exports_path", "/etc/exports")
    yield fake
    wait_for_app_starts()


@pytest.fixture
def api(host):
    with admin_client() as client:
        yield client


def _volume(name: str, pool: str = "data", quota: int | None = None) -> dict:
    return {"name": name, "pool": pool, "path": f"/mnt/pools/{pool}/{name}", "used": None, "quota": quota}


def _btrfs_calls(host, *action: str) -> list[list[str]]:
    return [cmd for cmd in host.calls if cmd[:1] == ["btrfs"] and tuple(cmd[1:3]) == action]


def _subvolume_id(host, path: str, uuid: str = DATA_UUID) -> int:
    """The id of the subvolume at ``path`` (relative to the top level) now."""
    [subvol_id] = [i for i, (_, p) in host.subvols.subvolumes[uuid].items() if p == path]
    return subvol_id


def add_share(host, name: str, path: str) -> None:
    """An SMB share in smb.conf, as shares/smb.py writes it."""
    with open(host.smb_conf, "a") as conf:
        conf.write(f"\n[{name}]\n   path = {path}\n   browseable = yes\n   read only = no\n")


def add_export(host, path: str, file: str = "/etc/exports") -> None:
    """An NFS export in an export file of the host: /etc/exports, or the file of UNOS (ADR-0261)."""
    host.files[file] = host.files.get(file, "") + f"{path} 192.168.1.0/24(rw,sync,no_subtree_check)\n"


def list_with_a_real_command(monkeypatch, listing: bytes) -> None:
    """``btrfs subvolume list`` goes to the real run_on_host, which runs a process printing ``listing``.

    Every other host command still goes to the FakeHost.
    """
    fake_run = host_module.run_on_host  # FakeHost.run

    def run_on_host(cmd, **kwargs):
        if cmd[:3] != ["btrfs", "subvolume", "list"]:
            return fake_run(cmd, **kwargs)
        script = f"import sys; sys.stdout.buffer.write({listing!r})"
        with monkeypatch.context() as real:
            real.setattr(subprocess, "run", REAL_SUBPROCESS_RUN)
            real.setenv("UNOS_HOST_EXEC", "local")
            return REAL_RUN_ON_HOST([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(host_module, "run_on_host", run_on_host)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_body_of_the_web_interface_creates_the_volume_on_the_pool(self, api, host):
        # webui/src/lib/api/storage.ts createVolume(): the pool is only in the URL.
        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": None})

        assert response.status_code == 200, response.text
        assert response.json() == _volume("documents")
        # On the host, in the pool's mounted filesystem.
        assert host.changes == [["btrfs", "subvolume", "create", "/mnt/pools/data/documents"]]
        assert host.subvols.paths(DATA_UUID) == ["documents"]
        assert api.get(f"{API}/data/volumes").json() == [_volume("documents")]

    def test_name_only(self, api, host):
        response = api.post(f"{API}/data/volumes", json={"name": "photos"})
        assert response.status_code == 200, response.text
        assert response.json() == _volume("photos")

    def test_the_pool_is_the_one_in_the_url(self, api, host):
        response = api.post(f"{API}/data/volumes", json={"pool_name": "media", "name": "documents"})

        assert response.status_code == 200, response.text
        assert response.json()["path"] == "/mnt/pools/data/documents"
        assert host.changes == [["btrfs", "subvolume", "create", "/mnt/pools/data/documents"]]

    def test_pools_base_from_the_settings(self, api, host, monkeypatch):
        monkeypatch.setattr(pools.settings, "pools_base_path", "/srv/pools")
        host.mounts.clear()
        host.mount_pool("data", "/srv/pools/data")

        response = api.post(f"{API}/data/volumes", json={"name": "documents"})

        assert response.status_code == 200, response.text
        assert response.json()["path"] == "/srv/pools/data/documents"
        assert host.changes == [["btrfs", "subvolume", "create", "/srv/pools/data/documents"]]

    @pytest.mark.parametrize(
        "name",
        ["", ".", "..", "../etc", "a/b", "/abs", ".snapshots", "-rf", "_x", "two words", "name\n",
         "x" * 64, "na\x00me", "naïve"],
    )
    def test_invalid_names_are_refused(self, api, host, name):
        response = api.post(f"{API}/data/volumes", json={"name": name})

        assert response.status_code == 400, response.text
        assert host.changes == []
        assert _btrfs_calls(host, "subvolume", "create") == []

    # btrfs stores the limit as an unsigned 64-bit number and reads 2^64 - 1 as
    # "no limit", which the API would still report as the volume's quota.
    @pytest.mark.parametrize("quota", [0, -1, "lots", (1 << 64) - 1, 1 << 64, 10**30])
    def test_invalid_quota_is_refused(self, api, host, quota):
        # With the pool in the body as well (the old request shape): only the quota is wrong.
        response = api.post(
            f"{API}/data/volumes", json={"pool_name": "data", "name": "documents", "quota": quota}
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "quota"]
        assert host.changes == []

    def test_unmounted_pool_is_refused_and_nothing_lands_on_the_boot_disk(self, api, host):
        response = api.post(f"{API}/media/volumes", json={"name": "documents"})

        assert response.status_code == 409, response.text
        assert "not mounted" in response.json()["detail"]
        assert host.changes == []
        assert _btrfs_calls(host, "subvolume", "create") == []
        assert "/mnt/pools/media/documents" not in host.dirs

    def test_another_filesystem_on_the_mountpoint_is_refused(self, api, host):
        host.add_disk("sdc")
        host.add_partition("sdc", "sdc1", fstype="ext4", uuid=OTHER_UUID)
        host.add_mount("/mnt/pools/media", "/dev/sdc1", "ext4", OTHER_UUID)

        response = api.post(f"{API}/media/volumes", json={"name": "documents"})

        assert response.status_code == 409, response.text
        assert host.changes == []

    def test_pool_mounted_elsewhere_only_is_refused(self, api, host):
        host.mount_pool("media", "/srv/media")

        response = api.post(f"{API}/media/volumes", json={"name": "documents"})

        assert response.status_code == 409, response.text
        assert host.changes == []

    def test_unknown_pool(self, api, host):
        response = api.post(f"{API}/nope/volumes", json={"name": "documents"})
        assert response.status_code == 404
        assert host.changes == []

    @pytest.mark.parametrize("existing", ["dir", "file", "volume"])
    def test_existing_name_is_refused(self, api, host, existing):
        path = "/mnt/pools/data/documents"
        if existing == "dir":
            host.dirs.add(path)
        elif existing == "file":
            host.files[path] = "a file, or a symbolic link"
        else:
            host.subvols.add(DATA_UUID, "documents")
            host.dirs.add(path)

        response = api.post(f"{API}/data/volumes", json={"name": "documents"})

        assert response.status_code == 409, response.text
        assert "already exists" in response.json()["detail"]
        assert host.changes == []

    def test_btrfs_error_is_reported(self, api, host):
        host.fail[("btrfs", "subvolume", "create")] = (1, "ERROR: cannot create subvolume: No space left on device")

        response = api.post(f"{API}/data/volumes", json={"name": "documents"})

        assert response.status_code == 500
        assert "No space left on device" in response.json()["detail"]

    def test_quota_is_set_on_the_new_volume(self, api, host):
        host.subvols.quotas.add(DATA_UUID)
        host.subvols.add(DATA_UUID, "archive")

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": 1073741824})

        assert response.status_code == 200, response.text
        assert response.json() == _volume("documents", quota=1073741824)
        documents = _subvolume_id(host, "documents")
        # By the quota group of the new subvolume; the mountpoint only names the filesystem.
        assert host.changes == [
            ["btrfs", "subvolume", "create", "/mnt/pools/data/documents"],
            ["btrfs", "qgroup", "limit", "1073741824", f"0/{documents}", "/mnt/pools/data"],
        ]
        assert host.subvols.limits == {documents: 1073741824}
        # Quotas are checked, and the newest subvolume id read, before the create;
        # the new subvolume's id is read and checked after it.
        assert [cmd[1:3] for cmd in host.calls if cmd[:2] in (["btrfs", "qgroup"], ["btrfs", "subvolume"])] == [
            ["qgroup", "show"], ["subvolume", "list"], ["subvolume", "create"],
            ["subvolume", "list"], ["subvolume", "show"], ["qgroup", "limit"],
        ]

    def test_largest_quota_btrfs_can_store(self, api, host):
        host.subvols.quotas.add(DATA_UUID)

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": (1 << 64) - 2})

        assert response.status_code == 200, response.text
        assert response.json()["quota"] == (1 << 64) - 2
        assert host.subvols.limits == {_subvolume_id(host, "documents"): (1 << 64) - 2}

    def test_quota_without_quotas_on_the_pool_creates_nothing(self, api, host):
        # UNOS never enables quotas: before, every create with a quota made the
        # subvolume, failed to limit it and removed it again (by name).
        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Volume 'documents' was not created: quotas are not enabled on pool 'data'. Enable them "
            "with 'btrfs quota enable /mnt/pools/data' on the host and try again, or create the "
            "volume without a quota."
        )
        assert host.changes == []
        assert ["btrfs", "qgroup", "show", "/mnt/pools/data"] in host.calls
        assert _btrfs_calls(host, "subvolume", "create") == []
        assert host.subvols.paths(DATA_UUID) == []

    # Only btrfs-progs' own answer (exit status 1 with that text) means "not enabled".
    @pytest.mark.parametrize(
        "status,stderr",
        [(1, "ERROR: can't list qgroups: Operation not permitted"), (2, QGROUPS_NOT_ENABLED)],
    )
    def test_quotas_that_cannot_be_checked_create_nothing(self, api, host, status, stderr):
        host.fail[("btrfs", "qgroup", "show")] = (status, stderr)

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": GIB})

        assert response.status_code == 500, response.text
        assert response.json()["detail"] == (
            f"Could not find out whether quotas are enabled on /mnt/pools/data: {stderr}"
        )
        assert host.changes == []

    def test_no_quota_needs_no_quotas(self, api, host):
        response = api.post(f"{API}/data/volumes", json={"name": "documents"})

        assert response.status_code == 200, response.text
        assert _btrfs_calls(host, "qgroup", "show") == []

    # Quotas were disabled after the check. The kernel answers ENOTCONN; newer
    # btrfs-progs print "quota not enabled" instead of its text.
    @pytest.mark.parametrize(
        "stderr", [NOT_ENABLED, "ERROR: unable to limit requested quota group: quota not enabled"]
    )
    def test_quota_that_cannot_be_set_removes_the_new_volume(self, api, host, stderr):
        host.subvols.quotas.add(DATA_UUID)
        host.fail[("btrfs", "qgroup", "limit")] = (1, stderr)

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": 1073741824})

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        # btrfs's reason first, then what to do about it.
        assert detail.startswith(f"Could not set the quota of volume 'documents': {stderr}. ")
        assert "Quotas are not enabled on pool 'data'" in detail
        assert "'btrfs quota enable /mnt/pools/data'" in detail
        assert detail.endswith("The volume was removed again.")
        (create, delete) = host.changes
        assert create == ["btrfs", "subvolume", "create", "/mnt/pools/data/documents"]
        assert delete[:4] == ["btrfs", "subvolume", "delete", "--subvolid"]
        assert host.subvols.paths(DATA_UUID) == []
        assert api.get(f"{API}/data/volumes").json() == []

    def test_other_quota_errors_do_not_blame_disabled_quotas(self, api, host):
        host.subvols.quotas.add(DATA_UUID)
        host.fail[("btrfs", "qgroup", "limit")] = (1, INVALID)

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": 1073741824})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Could not set the quota of volume 'documents': {INVALID}. The volume was removed again."
        )
        assert host.subvols.paths(DATA_UUID) == []


class TestQuotaRollback:
    """The new volume is removed again when its quota cannot be set, and only it.

    Whatever can rename entries in the pool's top directory (an SMB guest of
    a share of the whole pool, an app with the pool's folder) can move the new
    subvolume away and give an existing one its name before the quota fails.
    """

    @staticmethod
    def swap_when_the_quota_is_set(host, replacement):
        """The new "photos" is renamed away and ``replacement`` gets its name; the quota fails."""
        original = host.subvols._run

        def run(cmd):
            if cmd[1:3] == ["qgroup", "limit"]:
                subvolumes = host.subvols.subvolumes[DATA_UUID]
                new = next(i for i, (_, path) in subvolumes.items() if path == "photos")
                subvolumes[new] = (TOP_LEVEL, "photos.mine")
                subvolumes[replacement] = (TOP_LEVEL, "photos")
                return HostResult(1, "", INVALID)
            return original(cmd)

        host.subvols._run = run

    def test_an_existing_volume_given_the_name_is_not_removed(self, api, host):
        host.subvols.quotas.add(DATA_UUID)
        documents = host.subvols.add(DATA_UUID, "documents")
        host.dirs.add(DOCUMENTS)
        add_share(host, "docs", DOCUMENTS)  # in use: a DELETE of it is refused
        self.swap_when_the_quota_is_set(host, documents)

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Could not set the quota of volume 'photos': {INVALID}. The new volume was not "
            f"removed: the subvolume named 'photos' now (id {documents}) existed before it was "
            "created."
        )
        assert _btrfs_calls(host, "subvolume", "delete") == []
        assert host.subvols.paths(DATA_UUID) == ["photos", "photos.mine"]
        assert host.subvols.subvolumes[DATA_UUID][documents] == (TOP_LEVEL, "photos")

    # The existing volume was renamed before the create so that its line of
    # `btrfs subvolume list` broke: str.splitlines() also splits at "\u2028",
    # "\x85", "\v" or "\x1c", and a name that starts with "\n" (or "\r", which
    # subprocess turns into "\n") leaves its line without a path. The list
    # before the create must still count it.
    @pytest.mark.parametrize("prefix", ["\u2028", "\x85", "\v", "\x1c", "\n", "\r"])
    def test_an_existing_volume_whose_name_breaks_its_line_is_not_removed(self, api, host, prefix):
        host.subvols.quotas.add(DATA_UUID)
        host.subvols.add(DATA_UUID, "archive")
        documents = host.subvols.add(DATA_UUID, f"{prefix}documents")  # the newest subvolume
        self.swap_when_the_quota_is_set(host, documents)

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Could not set the quota of volume 'photos': {INVALID}. The new volume was not "
            f"removed: the subvolume named 'photos' now (id {documents}) existed before it was "
            "created."
        )
        assert _btrfs_calls(host, "subvolume", "delete") == []
        assert host.subvols.subvolumes[DATA_UUID][documents] == (TOP_LEVEL, "photos")

    @pytest.mark.parametrize("prefix", [b"\r", b"\n", b"\v", b"\xc2\x85", b"\xe2\x80\xa8", b"\xff"])
    def test_every_line_of_a_real_listing_counts(self, host, monkeypatch, prefix):
        """Through the real run_on_host: subprocess turns "\\r" into "\\n" and replaces bad bytes."""
        list_with_a_real_command(monkeypatch, (
            b"ID 256 gen 7 top level 5 path archive\n"
            b"ID 300 gen 9 top level 5 path " + prefix + b"documents\n"
        ))

        assert pools._newest_subvolume_id("/mnt/pools/data") == 300
        assert pools._pool_volumes("/mnt/pools/data") == {"archive": 256}

    def test_the_top_level_is_older_than_any_new_volume(self, api, host):
        # An empty pool: the newest subvolume is the top level (5).
        host.subvols.quotas.add(DATA_UUID)
        host.fail[("btrfs", "qgroup", "limit")] = (1, INVALID)

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"].endswith("The volume was removed again.")
        assert host.subvols.paths(DATA_UUID) == []

    def test_a_subvolume_made_after_the_check_is_removed(self, api, host):
        # Only what existed before the create is protected: a subvolume made
        # since (here, while the quota was set) is not an existing volume.
        host.subvols.quotas.add(DATA_UUID)
        host.subvols.add(DATA_UUID, "documents")
        original = host.subvols._run

        def run(cmd):
            if cmd[1:3] == ["qgroup", "limit"]:
                subvolumes = host.subvols.subvolumes[DATA_UUID]
                new = next(i for i, (_, path) in subvolumes.items() if path == "photos")
                subvolumes[new] = (TOP_LEVEL, "photos.mine")
                host.subvols.add(DATA_UUID, "photos")
                return HostResult(1, "", INVALID)
            return original(cmd)

        host.subvols._run = run

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"].endswith("The volume was removed again.")
        assert host.subvols.paths(DATA_UUID) == ["documents", "photos.mine"]


class TestQuotaOfTheNewVolume:
    """The quota goes to the new subvolume, by its id, and never to another one.

    `btrfs qgroup limit <size> <path>` limited whatever had the new name when
    it ran. Whatever can rename entries in the pool's top directory (an SMB
    guest of a share of the whole pool, an app with the pool's folder) could
    swap an existing volume in under the new name right after the create.
    """

    @staticmethod
    def rename_new_volume(host, replacement: int | None = None, *, before: str = "create") -> None:
        """The new "photos" becomes "photos.mine", and ``replacement`` (an id) gets its name.

        Right after the create (``before="create"``), or right before the
        quota is set (``before="limit"``).
        """
        original = host.subvols._run

        def swap():
            subvolumes = host.subvols.subvolumes[DATA_UUID]
            new = next(i for i, (_, path) in subvolumes.items() if path == "photos")
            subvolumes[new] = (TOP_LEVEL, "photos.mine")
            if replacement is not None:
                subvolumes[replacement] = (TOP_LEVEL, "photos")

        def run(cmd):
            if before == "limit" and cmd[1:3] == ["qgroup", "limit"]:
                swap()
            result = original(cmd)
            if before == "create" and cmd[1:3] == ["subvolume", "create"] and result.ok:
                swap()
            return result

        host.subvols._run = run

    def test_a_volume_swapped_in_after_the_create_gets_no_quota(self, api, host):
        host.subvols.quotas.add(DATA_UUID)
        documents = host.subvols.add(DATA_UUID, "documents")
        self.rename_new_volume(host, documents)

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Could not set the quota of volume 'photos': it was renamed or replaced right after "
            "it was created. The new volume was not removed: the subvolume named 'photos' now "
            f"(id {documents}) existed before it was created."
        )
        assert host.subvols.limits == {}
        assert _btrfs_calls(host, "qgroup", "limit") == []
        assert _btrfs_calls(host, "subvolume", "delete") == []
        assert host.subvols.subvolumes[DATA_UUID][documents] == (TOP_LEVEL, "photos")
        assert host.subvols.paths(DATA_UUID) == ["photos", "photos.mine"]

    def test_a_new_volume_renamed_away_gets_no_quota(self, api, host):
        host.subvols.quotas.add(DATA_UUID)
        self.rename_new_volume(host)

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Could not set the quota of volume 'photos': it was renamed or replaced right after "
            "it was created. The new volume was not removed: nothing in pool 'data' is named "
            "'photos' now."
        )
        assert host.subvols.limits == {}
        assert _btrfs_calls(host, "qgroup", "limit") == []
        assert host.subvols.paths(DATA_UUID) == ["photos.mine"]

    def test_a_swap_right_before_the_limit_does_not_move_the_quota(self, api, host):
        host.subvols.quotas.add(DATA_UUID)
        documents = host.subvols.add(DATA_UUID, "documents")
        self.rename_new_volume(host, documents, before="limit")

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 200, response.text
        assert host.subvols.limits == {_subvolume_id(host, "photos.mine"): GIB}
        assert host.subvols.subvolumes[DATA_UUID][documents] == (TOP_LEVEL, "photos")

    def test_a_forged_list_line_gets_no_quota(self, api, host):
        # Right after the create, a subvolume whose name forges a line of the
        # list that gives "photos" its own, newer id.
        host.subvols.quotas.add(DATA_UUID)
        original = host.subvols._run
        forged = []

        def run(cmd):
            result = original(cmd)
            if cmd[1:3] == ["subvolume", "create"] and result.ok:
                evil = host.subvols._next_id
                forged.append(host.subvols.add(
                    DATA_UUID, f"apps/evil\nID {evil} gen 1 top level {TOP_LEVEL} path photos"
                ))
            return result

        host.subvols._run = run

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Volume 'photos' was created without its quota: the subvolume list of pool 'data' "
            f"gives it the id {forged[0]}, which belongs to another subvolume. A subvolume whose "
            "name contains a line break can cause this."
        )
        assert host.subvols.limits == {}
        assert _btrfs_calls(host, "qgroup", "limit") == []
        assert _btrfs_calls(host, "subvolume", "delete") == []
        assert "photos" in host.subvols.paths(DATA_UUID)

    def test_the_limit_names_a_quota_group_of_the_pool(self, api, host):
        # btrfs-progs: btrfs qgroup limit [options] <size>|none [<qgroupid>] <path>
        host.subvols.quotas.add(DATA_UUID)
        host.subvols.add(DATA_UUID, "documents")

        response = api.post(f"{API}/data/volumes", json={"name": "photos", "quota": GIB})

        assert response.status_code == 200, response.text
        photos = _subvolume_id(host, "photos")
        assert _btrfs_calls(host, "qgroup", "limit") == [
            ["btrfs", "qgroup", "limit", str(GIB), f"0/{photos}", "/mnt/pools/data"]
        ]
        assert host.subvols.limits == {photos: GIB}


class TestFakeQuotaLimit:
    """FakeSubvolumes answers both forms of `btrfs qgroup limit`, like btrfs-progs."""

    def test_by_path_limits_the_subvolume_named_so_now(self, host):
        host.subvols.quotas.add(DATA_UUID)
        documents = host.subvols.add(DATA_UUID, "documents")

        result = host_module.run_on_host(["btrfs", "qgroup", "limit", "100", "/mnt/pools/data/documents"])

        assert result.ok, result.stderr
        assert host.subvols.limits == {documents: 100}

    def test_by_quota_group_id_on_any_path_of_the_filesystem(self, host):
        host.subvols.quotas.add(DATA_UUID)
        documents = host.subvols.add(DATA_UUID, "documents")

        for path in ("/mnt/pools/data", "/mnt/pools/data/documents"):
            result = host_module.run_on_host(["btrfs", "qgroup", "limit", "100", f"0/{documents}", path])
            assert result.ok, result.stderr
        assert host.subvols.limits == {documents: 100}

    def test_unknown_quota_group_or_disabled_quotas(self, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        limit = ["btrfs", "qgroup", "limit", "100", f"0/{documents}", "/mnt/pools/data"]

        assert host_module.run_on_host(limit).stderr == NOT_ENABLED
        host.subvols.quotas.add(DATA_UUID)
        limit[4] = f"0/{documents + 1}"
        assert "No such file or directory" in host_module.run_on_host(limit).stderr
        assert host.subvols.limits == {}


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestList:
    def test_only_volumes_directly_in_the_pool(self, api, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, "photos")
        host.subvols.add(DATA_UUID, "documents/nested", parent=documents)
        host.subvols.add(DATA_UUID, ".snapshots/auto_20250101_000000")  # in a plain directory
        host.subvols.add(DATA_UUID, "Old Photos")  # not a name UNOS gives volumes
        host.subvols.add(DATA_UUID, ".hidden")

        response = api.get(f"{API}/data/volumes")

        assert response.status_code == 200, response.text
        assert response.json() == [_volume("documents"), _volume("photos")]
        assert host.changes == []

    def test_empty_pool(self, api, host):
        assert api.get(f"{API}/data/volumes").json() == []

    def test_unmounted_pool(self, api, host):
        response = api.get(f"{API}/media/volumes")
        assert response.status_code == 409
        assert "not mounted" in response.json()["detail"]
        assert _btrfs_calls(host, "subvolume", "list") == []

    def test_unknown_pool(self, api, host):
        assert api.get(f"{API}/nope/volumes").status_code == 404

    def test_invalid_pool_name(self, api, host):
        assert api.get(f"{API}/-x/volumes").status_code == 400
        assert host.calls == []

    def test_btrfs_error_is_reported(self, api, host):
        host.fail[("btrfs", "subvolume", "list")] = (1, "ERROR: can't perform the search: Permission denied")

        response = api.get(f"{API}/data/volumes")

        assert response.status_code == 500
        assert "Permission denied" in response.json()["detail"]

    def test_share_paths_offer_volumes_but_not_snapshots(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, ".snapshots/auto_20250101_000000")

        response = api.get("/api/v1/shares/paths")

        assert response.status_code == 200, response.text
        assert [p["path"] for p in response.json()] == ["/mnt/pools/data", "/mnt/pools/data/documents"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_deletes_the_volume_by_its_subvolume_id(self, api, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, "photos")
        host.dirs.add("/mnt/pools/data/documents")

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted"}
        assert host.changes == [["btrfs", "subvolume", "delete", "--subvolid", str(documents), "/mnt/pools/data"]]
        assert api.get(f"{API}/data/volumes").json() == [_volume("photos")]

    def test_link_planted_under_a_volume_name_is_not_followed(self, api, host):
        # Pool contents are writable by SMB users and apps. btrfs-progs would
        # resolve /mnt/pools/data/documents to the other pool's subvolume.
        host.add_disk("sdc", serial="WD-3")
        host.add_btrfs("archive", ARCHIVE_UUID, ["/dev/sdc"])
        host.mount_pool("archive")
        host.subvols.add(ARCHIVE_UUID, "documents")
        host.files["/mnt/pools/data/documents"] = "symbolic link to /mnt/pools/archive/documents"

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 404, response.text
        assert host.changes == []
        assert host.subvols.paths(ARCHIVE_UUID) == ["documents"]

    # `btrfs subvolume list` prints names as they are. Whatever can write in
    # the pool (an app's container, for one) can create a subvolume whose name
    # holds a line break and a forged line of the list.
    def test_forged_list_line_cannot_redirect_the_delete(self, api, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        # A phantom volume "trash" with the id of "documents".
        host.subvols.add(DATA_UUID, f"apps/evil\nID {documents} gen 1 top level {TOP_LEVEL} path trash")
        assert _volume("trash") in api.get(f"{API}/data/volumes").json()

        response = api.delete(f"{API}/data/volumes/trash")

        assert response.status_code == 409, response.text
        assert host.changes == []
        assert "documents" in host.subvols.paths(DATA_UUID)

    def test_forged_list_line_cannot_swap_volumes(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        photos = host.subvols.add(DATA_UUID, "photos")
        # Listed after the real "documents": gives "documents" the id of "photos".
        host.subvols.add(DATA_UUID, f"apps/evil\nID {photos} gen 1 top level {TOP_LEVEL} path documents")

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert host.changes == []
        assert {"documents", "photos"} <= set(host.subvols.paths(DATA_UUID))

    # btrfs-progs ends its lines with "\n" only. str.splitlines() also splits at
    # these: the newer subvolume became a phantom "documents" that took the
    # volume's id in the list, and deleting the volume was refused (409).
    @pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\v", "\f", "\x1c", "\x1d", "\x1e"])
    def test_a_name_with_another_line_separator_is_one_name(self, api, host, separator):
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, f"documents{separator}old")

        assert api.get(f"{API}/data/volumes").json() == [_volume("documents")]
        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert host.changes == [["btrfs", "subvolume", "delete", "--subvolid", str(documents), "/mnt/pools/data"]]

    def test_volume_that_cannot_be_checked_is_kept(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        host.fail[("btrfs", "subvolume", "show")] = (1, "ERROR: Failed to get subvol info: Operation not permitted")

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 500
        assert "Operation not permitted" in response.json()["detail"]
        assert host.changes == []
        assert host.subvols.paths(DATA_UUID) == ["documents"]

    def test_unknown_volume(self, api, host):
        host.subvols.add(DATA_UUID, ".snapshots/documents")
        host.dirs.add("/mnt/pools/data/documents")  # a plain directory is no volume

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 404, response.text
        assert host.changes == []

    def test_volume_with_snapshots_inside_is_kept(self, api, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, "documents/.snapshots/daily", parent=documents)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert "snapshots" in response.json()["detail"]
        assert host.changes == []
        assert host.subvols.paths(DATA_UUID) == ["documents", "documents/.snapshots/daily"]

    def test_busy_volume_is_kept(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        host.fail[("btrfs", "subvolume", "delete")] = (
            1, "ERROR: Could not destroy subvolume/snapshot: Device or resource busy"
        )

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert "in use" in response.json()["detail"]
        assert host.subvols.paths(DATA_UUID) == ["documents"]

    def test_btrfs_error_is_reported(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        host.fail[("btrfs", "subvolume", "delete")] = (1, "ERROR: Could not destroy subvolume/snapshot: Read-only file system")

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 500
        assert "Read-only file system" in response.json()["detail"]

    def test_unmounted_pool(self, api, host):
        host.subvols.add(MEDIA_UUID, "documents")

        response = api.delete(f"{API}/media/volumes/documents")

        assert response.status_code == 409, response.text
        assert host.changes == []

    def test_unknown_pool(self, api, host):
        assert api.delete(f"{API}/nope/volumes/documents").status_code == 404

    @pytest.mark.parametrize("name", ["-rf", ".snapshots", "two%20words", "x" * 64])
    def test_invalid_names_are_refused(self, api, host, name):
        host.subvols.add(DATA_UUID, ".snapshots")

        response = api.delete(f"{API}/data/volumes/{name}")

        assert response.status_code == 400, response.text
        assert host.calls == []


# ---------------------------------------------------------------------------
# Subvolume names that are not UTF-8
# ---------------------------------------------------------------------------


class TestNamesThatAreNotUtf8:
    """Subvolume names are bytes, chosen by whatever can write in the pool.

    One that is not UTF-8, anywhere in the pool (an app's container can make
    it in its folder), made listing and deleting volumes fail with HTTP 400:
    run_on_host decodes strictly unless told otherwise. Such names are no
    volume names, so they are skipped.
    """

    def test_list(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, NOT_UTF8)
        host.subvols.add(DATA_UUID, f"apps/myapp/{NOT_UTF8}")

        response = api.get(f"{API}/data/volumes")

        assert response.status_code == 200, response.text
        assert response.json() == [_volume("documents")]

    def test_delete(self, api, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, f"apps/myapp/{NOT_UTF8}")

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert host.changes == [["btrfs", "subvolume", "delete", "--subvolid", str(documents), "/mnt/pools/data"]]

    def test_delete_a_volume_with_such_a_snapshot(self, api, host):
        # `btrfs subvolume show` lists the snapshots of the volume by path.
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, f".snapshots/{NOT_UTF8}", snapshot_of=documents)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert host.subvols.paths(DATA_UUID) == [f".snapshots/{NOT_UTF8}"]

    def test_delete_of_a_volume_renamed_right_before(self, api, host):
        # It is deleted by id; btrfs prints the path it has by then.
        documents = host.subvols.add(DATA_UUID, "documents")
        original = host.subvols._run

        def run(cmd):
            if cmd[1:3] == ["subvolume", "delete"]:
                host.subvols.subvolumes[DATA_UUID][documents] = (TOP_LEVEL, NOT_UTF8)
            return original(cmd)

        host.subvols._run = run

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert host.subvols.paths(DATA_UUID) == []

    # findmnt prints what a mount on the host mounts as it is named now: the
    # folder in SOURCE ("/dev/sda[/documents/media]") and, for btrfs, the
    # subvolume in subvol=. Here a Docker volume of the local driver binds a
    # folder of "documents" that was renamed afterwards. Every volume operation
    # reads the mount table first.
    def test_folder_mounted_on_the_host(self, api, host):
        documents = host.subvols.add(DATA_UUID, "documents")
        host.subvols.add(DATA_UUID, "photos")
        host.add_mount(
            "/var/lib/docker/volumes/paperless_media/_data", f"/dev/sda[/documents/{NOT_UTF8}]",
            "btrfs", DATA_UUID, fs_options=f"rw,subvolid={documents},subvol=/documents",
        )

        response = api.get(f"{API}/data/volumes")
        assert response.status_code == 200, response.text
        assert response.json() == [_volume("documents"), _volume("photos")]

        response = api.post(f"{API}/data/volumes", json={"name": "music"})
        assert response.status_code == 200, response.text

        response = api.delete(f"{API}/data/volumes/photos")
        assert response.status_code == 200, response.text
        assert host.subvols.paths(DATA_UUID) == ["documents", "music"]

    def test_create_with_a_quota(self, api, host):
        # `btrfs qgroup show` (btrfs-progs 6) lists the subvolume paths too.
        host.subvols.quotas.add(DATA_UUID)
        host.subvols.add(DATA_UUID, f"apps/myapp/{NOT_UTF8}")

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": GIB})

        assert response.status_code == 200, response.text
        assert host.subvols.limits == {_subvolume_id(host, "documents"): GIB}

    @pytest.mark.parametrize("operation", ["list", "delete"])
    def test_output_of_a_real_command(self, api, host, monkeypatch, operation):
        """The real run_on_host runs a process that prints the bytes btrfs-progs would."""
        documents = host.subvols.add(DATA_UUID, "documents")
        listing = (
            f"ID {documents} gen 12 top level 5 path documents\n".encode()
            + b"ID 300 gen 12 top level 290 path apps/myapp/x\xff\n"
        )
        list_with_a_real_command(monkeypatch, listing)

        if operation == "list":
            response = api.get(f"{API}/data/volumes")
            assert response.status_code == 200, response.text
            assert response.json() == [_volume("documents")]
        else:
            response = api.delete(f"{API}/data/volumes/documents")
            assert response.status_code == 200, response.text
            assert host.subvols.paths(DATA_UUID) == []


# ---------------------------------------------------------------------------
# Volumes in use: containers, SMB shares and NFS exports
# ---------------------------------------------------------------------------

DOCUMENTS = "/mnt/pools/data/documents"


class TestVolumeInUse:
    """Deleting a volume that something uses would leave it a dead directory."""

    @pytest.fixture
    def documents(self, host):
        host.subvols.add(DATA_UUID, "documents")
        host.dirs.add(DOCUMENTS)
        return host

    @staticmethod
    def assert_kept(host):
        assert host.changes == []
        assert _btrfs_calls(host, "subvolume", "delete") == []
        assert host.subvols.paths(DATA_UUID) == ["documents"]

    @pytest.mark.parametrize("source", [DOCUMENTS, f"{DOCUMENTS}/", f"{DOCUMENTS}/config/app"])
    def test_running_container_with_a_mount_from_the_volume(self, api, documents, source):
        documents.docker.add("jellyfin", sources=[source])

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Volume 'documents' is used by running containers (jellyfin). Stop or remove them and try again."
        )
        self.assert_kept(documents)

    # smb.conf and /etc/exports can be edited by hand: Linux reads "//mnt" as "/mnt".
    @pytest.mark.parametrize("path", [DOCUMENTS, f"{DOCUMENTS}/scans", f"/{DOCUMENTS}", f"/{DOCUMENTS}/./scans"])
    def test_smb_share_of_the_volume(self, api, documents, path):
        add_share(documents, "docs", path)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Volume 'documents' is used by SMB shares (docs). Stop or remove them and try again."
        )
        self.assert_kept(documents)

    @pytest.mark.parametrize(
        "path, file",
        [
            (DOCUMENTS, "/etc/exports"),
            (f"{DOCUMENTS}/scans", "/etc/exports"),
            (f"/{DOCUMENTS}", "/etc/exports"),
            (DOCUMENTS, "/etc/exports.d/unos.exports"),
            (DOCUMENTS, "/etc/exports.d/backup.exports"),
        ],
    )
    def test_nfs_export_of_the_volume(self, api, documents, path, file):
        add_export(documents, path, file)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Volume 'documents' is used by NFS exports ({path}). Stop or remove them and try again."
        )
        self.assert_kept(documents)

    def test_every_user_is_named(self, api, documents):
        documents.docker.add("paperless", sources=[f"{DOCUMENTS}/consume"])
        documents.docker.add("jellyfin", sources=["/srv/media", DOCUMENTS])
        add_share(documents, "scans", f"{DOCUMENTS}/scans")
        add_share(documents, "docs", DOCUMENTS)
        add_export(documents, DOCUMENTS)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Volume 'documents' is used by running containers (jellyfin, paperless), SMB shares "
            f"(docs, scans) and NFS exports ({DOCUMENTS}). Stop or remove them and try again."
        )
        self.assert_kept(documents)

    def test_users_of_a_parent_folder_or_of_other_volumes_do_not_count(self, api, documents):
        # They keep working without the volume.
        documents.docker.add("unos-supervisor", sources=["/mnt", "/var/run/docker.sock"])
        documents.docker.add("backup", sources=["/mnt/pools/data"])
        documents.docker.add("old-docs", sources=[f"{DOCUMENTS}-old"])  # same prefix, other folder
        add_share(documents, "data", "/mnt/pools/data")
        add_share(documents, "old", f"{DOCUMENTS}-old")
        add_share(documents, "pool", "//mnt/pools/data")
        add_export(documents, "/mnt/pools/data")
        add_export(documents, "/mnt/pools/media/documents")

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert documents.subvols.paths(DATA_UUID) == []

    def test_stopped_containers_and_app_labels_do_not_count(self, api, documents):
        documents.docker.add("stopped", sources=[DOCUMENTS], running=False)
        # The installer's label names the pool, so it is on every app of the pool.
        documents.docker.add("immich", labels={"unos.app": "immich", "unos.app.pool": "data"},
                             sources=["/mnt/pools/data/apps/immich"])

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert documents.subvols.paths(DATA_UUID) == []

    def test_without_docker_nothing_is_deleted(self, api, documents, caplog):
        # An unmount is checked again afterwards and undone; a deleted subvolume cannot be.
        documents.docker.unavailable = True

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail.startswith(
            "Volume 'documents' was not deleted: Docker did not answer, so it is not known whether "
            "a running container uses it. Try again when Docker is running"
        )
        self.assert_kept(documents)
        assert "Could not list the running containers" in caplog.text

    def test_unreadable_shares_keep_the_volume(self, api, documents):
        documents.smb_conf.unlink()
        documents.smb_conf.mkdir()  # reading it fails

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 500, response.text
        assert "the SMB shares and NFS exports could not be read" in response.json()["detail"]
        self.assert_kept(documents)

    def test_users_are_checked_under_the_pool_lock_right_before_the_delete(
        self, api, documents, monkeypatch
    ):
        asked = []
        real_containers = documents.docker.containers

        def containers(all=False, filters=None):
            asked.append((pools._lock._is_owned(), len(documents.calls)))
            return real_containers(all=all, filters=filters)

        monkeypatch.setattr(documents.docker, "containers", containers)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        [(locked, calls_before)] = asked
        assert locked
        # After the volume's id was checked; in between, only the rest of the check ran on the
        # host: the NFS export files are read there (ADR-0261).
        assert documents.calls[calls_before - 1][:4] == ["btrfs", "subvolume", "show", "-r"]
        assert documents.calls[calls_before] == [
            "/bin/sh", "-c", nfs._FILES_SCRIPT, "unos-nfs-files", "/etc/exports", "/etc/exports.d"]
        assert documents.calls[calls_before + 1][:3] == ["btrfs", "subvolume", "delete"]

    def test_a_new_volume_whose_quota_fails_is_removed_without_asking_docker(self, api, host):
        host.docker.unavailable = True
        host.subvols.quotas.add(DATA_UUID)
        host.fail[("btrfs", "qgroup", "limit")] = (1, INVALID)

        response = api.post(f"{API}/data/volumes", json={"name": "documents", "quota": 1073741824})

        assert response.status_code == 409, response.text
        assert "The volume was removed again" in response.json()["detail"]
        assert host.subvols.paths(DATA_UUID) == []

    def test_the_users_are_checked_unless_the_caller_says_otherwise(self, documents):
        # _delete_volume checks by default; only the rollback of a new volume opts out.
        documents.docker.add("jellyfin", sources=[DOCUMENTS])

        with pytest.raises(pools.PoolConflictError, match=r"used by running containers \(jellyfin\)"):
            pools._delete_volume("data", "/mnt/pools/data", "documents")

        self.assert_kept(documents)

    # A Docker volume of the local driver that binds a folder (driver_opts
    # type: none, o: bind, device: <folder> in a compose file). Docker reports
    # the volume's own folder below /var/lib/docker/volumes as the Source.
    @pytest.mark.parametrize(
        "options",
        [
            {"type": "none", "o": "bind", "device": DOCUMENTS},
            {"type": "none", "o": "bind", "device": f"{DOCUMENTS}/media"},
            {"type": "none", "o": "ro,bind", "device": f"/{DOCUMENTS}/./media"},
            {"type": "none", "o": "rbind", "device": f"{DOCUMENTS}/media"},
        ],
    )
    def test_running_container_with_a_docker_volume_that_binds_a_folder_of_it(
        self, api, documents, options
    ):
        mount = documents.docker.add_volume("paperless_media", options=options)
        documents.docker.add("paperless", mounts=[mount])

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Volume 'documents' is used by running containers (paperless). Stop or remove them and try again."
        )
        self.assert_kept(documents)

    @pytest.mark.parametrize(
        "driver,options",
        [
            ("local", None),  # a plain named volume: its data is below /var/lib/docker
            ("local", {"type": "none", "o": "bind", "device": "/mnt/pools/data"}),  # a parent folder
            ("local", {"type": "none", "o": "bind", "device": f"{DOCUMENTS}-old"}),
            ("local", {"type": "nfs", "o": "addr=192.168.1.5,rw", "device": f":{DOCUMENTS}"}),
            ("rclone", {"o": "bind", "device": DOCUMENTS}),  # options of another driver
        ],
    )
    def test_docker_volumes_that_do_not_bind_a_folder_of_it_do_not_count(
        self, api, documents, driver, options
    ):
        documents.docker.add("app", mounts=[documents.docker.add_volume("data", driver=driver, options=options)])
        # Stopped: it does not count, whatever its volume binds.
        documents.docker.add("stopped", running=False, mounts=[
            documents.docker.add_volume("stopped_data", options={"type": "none", "o": "bind", "device": DOCUMENTS})
        ])

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 200, response.text
        assert documents.subvols.paths(DATA_UUID) == []

    def test_docker_volume_that_cannot_be_inspected_keeps_the_volume(self, api, documents, monkeypatch, caplog):
        documents.docker.add("paperless", mounts=[documents.docker.add_volume("paperless_media")])

        def inspect_volume(name):
            raise docker.errors.APIError("500 Server Error: Internal Server Error")

        monkeypatch.setattr(documents.docker, "inspect_volume", inspect_volume)

        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 503, response.text
        assert response.json()["detail"].startswith(
            "Volume 'documents' was not deleted: Docker did not answer, so it is not known whether "
            "a running container uses it. Try again when Docker is running"
        )
        self.assert_kept(documents)
        assert "Could not list the running containers or their volumes" in caplog.text


# ---------------------------------------------------------------------------
# Pools that are read-only or not mounted at their top level
# ---------------------------------------------------------------------------

READ_ONLY = "Pool 'data' is mounted read-only; volumes cannot be created or deleted."


class TestReadOnlyPool:
    """btrfs cannot create or delete subvolumes there; it used to fail with a 500."""

    @pytest.fixture(params=["mounted read-only", "forced read-only"])
    def read_only(self, request, host):
        host.subvols.add(DATA_UUID, "documents")
        host.dirs.add(DOCUMENTS)
        host.mounts[:] = [m for m in host.mounts if m["target"] != "/mnt/pools/data"]
        if request.param == "mounted read-only":
            host.mount_pool("data", vfs_options="ro,noatime", fs_options="ro")
        else:
            # btrfs stopped writing after an error: the mount itself still says rw.
            host.mount_pool("data", fs_options="ro")
        return request.param

    def expected(self, read_only: str) -> str:
        if read_only == "forced read-only":
            return READ_ONLY + " btrfs made it read-only after an error; check the kernel log (dmesg)."
        return READ_ONLY

    def test_create_is_refused_before_any_host_change(self, api, host, read_only):
        response = api.post(f"{API}/data/volumes", json={"name": "photos"})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == self.expected(read_only)
        assert host.changes == []
        assert _btrfs_calls(host, "subvolume", "create") == []

    def test_delete_is_refused_before_any_host_change(self, api, host, read_only):
        response = api.delete(f"{API}/data/volumes/documents")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == self.expected(read_only)
        assert host.changes == []
        assert _btrfs_calls(host, "subvolume", "delete") == []
        assert host.subvols.paths(DATA_UUID) == ["documents"]

    def test_volumes_are_still_listed(self, api, host, read_only):
        response = api.get(f"{API}/data/volumes")

        assert response.status_code == 200, response.text
        assert response.json() == [_volume("documents")]

    def test_the_pool_reports_read_only_too(self, api, host, read_only):
        # The Storage page disables Create and Delete from this flag.
        (pool,) = [p for p in api.get(API).json() if p["name"] == "data"]
        assert pool["read_only"] is True


class TestPoolMountedAtAnotherSubvolume:
    """The mounted subvolume is not the top level: a default subvolume set on a
    filesystem made elsewhere, or a mount with subvol=."""

    MESSAGE = (
        "Pool 'data' is mounted at subvolume 256, not the top level; volumes are not available "
        "for this pool."
    )

    @pytest.fixture
    def nested(self, host):
        # What /mnt/pools/data shows: subvolume "rootfs" (256). "home" is next to
        # it in the top level, so it is not even visible below /mnt/pools/data.
        rootfs = host.subvols.add(DATA_UUID, "rootfs")
        host.subvols.add(DATA_UUID, "home")
        host.subvols.add(DATA_UUID, "rootfs/documents", parent=rootfs)
        host.subvols.mounted_subvolume["/mnt/pools/data"] = rootfs
        return host

    def test_list_is_refused(self, api, nested):
        response = api.get(f"{API}/data/volumes")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == self.MESSAGE
        assert _btrfs_calls(nested, "subvolume", "list") == []

    def test_create_is_refused(self, api, nested):
        response = api.post(f"{API}/data/volumes", json={"name": "photos"})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == self.MESSAGE
        assert nested.changes == []

    @pytest.mark.parametrize("name", ["home", "documents"])
    def test_delete_is_refused(self, api, nested, name):
        response = api.delete(f"{API}/data/volumes/{name}")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == self.MESSAGE
        assert nested.changes == []
        assert nested.subvols.paths(DATA_UUID) == ["home", "rootfs", "rootfs/documents"]

    def test_share_paths_offer_the_pool_but_no_volumes(self, api, nested):
        response = api.get("/api/v1/shares/paths")

        assert response.status_code == 200, response.text
        assert [p["path"] for p in response.json()] == ["/mnt/pools/data"]

    def test_the_mounted_subvolume_is_asked_on_the_host(self, api, host):
        assert api.get(f"{API}/data/volumes").status_code == 200
        assert ["btrfs", "inspect-internal", "rootid", "/mnt/pools/data"] in host.calls

    def test_unknown_mounted_subvolume_is_an_error(self, api, host):
        host.subvols.add(DATA_UUID, "documents")
        host.fail[("btrfs", "inspect-internal", "rootid")] = (1, "ERROR: cannot open /mnt/pools/data: Permission denied")

        for response in (
            api.get(f"{API}/data/volumes"),
            api.post(f"{API}/data/volumes", json={"name": "photos"}),
            api.delete(f"{API}/data/volumes/documents"),
        ):
            assert response.status_code == 500, response.text
            assert "Permission denied" in response.json()["detail"]
        assert host.changes == []
        assert host.subvols.paths(DATA_UUID) == ["documents"]


# ---------------------------------------------------------------------------
# The storage functions validate their arguments themselves
# ---------------------------------------------------------------------------


class TestFunctionsValidateNames:
    @pytest.mark.parametrize("name", ["..", "../etc", "a/b", ".snapshots", ""])
    def test_volume_names(self, host, name):
        with pytest.raises(HTTPException) as create:
            pools.create_volume("data", VolumeCreate(name=name))
        with pytest.raises(HTTPException) as delete:
            pools.delete_volume("data", name)
        assert create.value.status_code == delete.value.status_code == 400
        assert host.calls == []

    @pytest.mark.parametrize("pool", ["..", "../etc", "data/x", ""])
    def test_pool_names(self, host, pool):
        for call in (
            lambda: pools.list_volumes(pool),
            lambda: pools.create_volume(pool, VolumeCreate(name="documents")),
            lambda: pools.delete_volume(pool, "documents"),
        ):
            with pytest.raises(HTTPException) as caught:
                call()
            assert caught.value.status_code == 400
        assert host.calls == []


def test_volume_create_body_has_no_pool():
    """The pool is part of the URL (POST /storage/pools/{pool_name}/volumes)."""
    assert set(VolumeCreate.model_fields) == {"name", "quota"}
    assert VolumeCreate.model_validate({"name": "documents", "quota": None}) == VolumeCreate(name="documents")

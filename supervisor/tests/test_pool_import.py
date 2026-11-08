"""Importing a btrfs filesystem found on the host's disks as a pool (ADR-0261).

``GET /api/v1/storage/filesystems/importable`` lists the btrfs filesystems that
cannot be mounted as a pool under their label; ``POST
/api/v1/storage/filesystems/{uuid}/import`` mounts one by UUID at
/mnt/pools/<name>, gives it the name as its label through the mount once the
mount shows its top level, and keeps it in the host's fstab (ADR-0034). A
filesystem whose label is a pool name is imported by Mount
(test_pool_persistence.py). Every host command goes to the FakeHost of
test_pool_persistence.py, which records the commands that change the host.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.storage import pools

from .test_pool_persistence import (
    BASE_FSTAB,
    DATA_UUID,
    GONE_UUID,
    MEDIA_UUID,
    NEW_UUID,
    OLD_UUID,
    admin_client,
    apps_router,
    entry,
    install_fake_host,
    marker,
    wait_for_app_starts,
)

API = "/api/v1/storage"
FOREIGN_UUID = "7d4f2c1a-5678-4e9f-8a0b-1c2d3e4f5a6b"
SECOND_UUID = "8e5a3d2b-6789-4fa0-9b1c-2d3e4f5a6b7c"
USED = 700_000_000_000
DISK_SIZE = 4_000_787_030_016  # FakeHost.add_disk's default
MANAGED_DATA = BASE_FSTAB + marker("data") + entry("data", DATA_UUID)


@pytest.fixture
def host(monkeypatch):
    """The managed pool 'data', mounted, and the disk /dev/sdb of another system.

    /dev/sdb holds a btrfs filesystem without a label and 700 GB of data;
    /mnt/pools/backup does not exist yet.
    """
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake.files["/etc/fstab"] = MANAGED_DATA
    fake.files["/etc/fstab.unos-backup"] = BASE_FSTAB
    fake.dirs.add("/mnt/pools/data")
    fake.mount_pool("data")
    fake.add_disk("sdb", serial="ST-1", model="ST4000VN006")
    fake.add_btrfs(None, FOREIGN_UUID, ["/dev/sdb"], used=USED)
    yield fake
    wait_for_app_starts()


@pytest.fixture
def api(host):
    with admin_client() as client:
        yield client


def importable(api) -> list[dict]:
    response = api.get(f"{API}/filesystems/importable")
    assert response.status_code == 200, response.text
    return response.json()


def import_as(api, uuid: str, name: str):
    return api.post(f"{API}/filesystems/{uuid}/import", json={"name": name})


def labels(host) -> dict[str, str | None]:
    return {fs["uuid"]: fs["label"] for fs in host.filesystems}


# ---------------------------------------------------------------------------
# What the Storage page offers
# ---------------------------------------------------------------------------


class TestImportableFilesystems:
    def test_a_filesystem_without_a_label_is_offered(self, api, host):
        assert importable(api) == [{
            "uuid": FOREIGN_UUID,
            "label": None,
            "label_problem": "missing",
            "devices": ["/dev/sdb"],
            "total_devices": 1,
            "missing_devices": 0,
            "size": DISK_SIZE,
            "used": USED,
        }]
        # Not a pool: GET /pools lists the filesystems whose label is a pool name.
        assert [p["name"] for p in api.get(f"{API}/pools").json()] == ["data"]
        assert host.changes == []

    def test_only_filesystems_that_cannot_be_mounted_under_their_label(self, api, host):
        # A pool: listed with the pools, where Mount imports it.
        host.add_disk("sdc")
        host.add_btrfs("media", MEDIA_UUID, ["/dev/sdc"])
        # A label that another system allowed, but that is no pool name.
        host.add_disk("sdd")
        host.add_btrfs("My Media", OLD_UUID, ["/dev/sdd"])
        # Two filesystems with one label: neither can be mounted as the pool 'backup'.
        host.add_disk("sde")
        host.add_btrfs("backup", GONE_UUID, ["/dev/sde"])
        host.add_disk("sdf")
        host.add_btrfs("backup", SECOND_UUID, ["/dev/sdf"])
        # Mounted on the host, here outside /mnt/pools: in use.
        host.add_disk("sdg")
        host.add_btrfs(None, NEW_UUID, ["/dev/sdg"])
        host.add_mount("/srv/scratch", "/dev/sdg", "btrfs", NEW_UUID)
        # The host's own btrfs filesystem.
        host.add_disk("nvme0n1", rota=False)
        host.add_btrfs(None, "1b2c3d4e-0000-4000-8000-00000000000b", ["/dev/nvme0n1"])
        host.add_mount("/boot", "/dev/nvme0n1", "btrfs", "1b2c3d4e-0000-4000-8000-00000000000b")

        found = {fs["uuid"]: (fs["label"], fs["label_problem"]) for fs in importable(api)}

        assert found == {
            FOREIGN_UUID: (None, "missing"),
            OLD_UUID: ("My Media", "invalid"),
            GONE_UUID: ("backup", "duplicate"),
            SECOND_UUID: ("backup", "duplicate"),
        }
        assert host.changes == []

    def test_a_filesystem_with_a_missing_device_is_offered_with_the_count(self, api, host):
        host.add_disk("sdc")
        host.add_btrfs(None, OLD_UUID, ["/dev/sdc"], missing=1, profile="raid1")
        mirror = next(fs for fs in importable(api) if fs["uuid"] == OLD_UUID)
        assert (mirror["devices"], mirror["total_devices"], mirror["missing_devices"]) == (
            ["/dev/sdc"], 2, 1)

    def test_nothing_is_offered_without_the_mount_table(self, api, host):
        # The host's own filesystems, and those in use, could not be told apart.
        host.fail[("findmnt",)] = (1, "findmnt: cannot open /proc/self/mountinfo")
        response = api.get(f"{API}/filesystems/importable")
        assert response.status_code == 500
        assert "findmnt failed" in response.json()["detail"]

    def test_btrfs_errors_are_reported(self, api, host):
        host.fail[("btrfs", "filesystem", "show")] = (1, "ERROR: cannot scan /dev/sdb")
        response = api.get(f"{API}/filesystems/importable")
        assert response.status_code == 500
        assert "cannot scan /dev/sdb" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


class TestImport:
    def test_mounts_by_uuid_without_formatting_and_keeps_the_pool_in_fstab(self, api, host):
        response = import_as(api, FOREIGN_UUID, "backup")

        assert response.status_code == 200, response.text
        pool = response.json()
        assert (pool["name"], pool["uuid"], pool["devices"]) == ("backup", FOREIGN_UUID, ["/dev/sdb"])
        assert (pool["mounted"], pool["persistent"], pool["health"]) == (True, True, "healthy")
        assert pool["mountpoint"] == "/mnt/pools/backup"
        assert pool["used_size"] == USED  # its data, read through the mount
        assert host.changes == [
            ["mkdir", "-p", "/mnt/pools/backup"],
            ["chattr", "+i", "/mnt/pools/backup"],  # guarded until the mount is on top
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={FOREIGN_UUID}", "/mnt/pools/backup"],
            # Through the mount, once it showed the top level.
            ["btrfs", "filesystem", "label", "/mnt/pools/backup", "backup"],
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
        ]
        assert host.fstab == MANAGED_DATA + marker("backup") + entry("backup", FOREIGN_UUID)
        assert ["btrfs", "inspect-internal", "rootid", "/mnt/pools/backup"] in host.calls
        assert not any(cmd[0] == "mkfs.btrfs" for cmd in host.calls)
        assert labels(host)[FOREIGN_UUID] == "backup"
        # udev reads the disk again, so that lsblk, and the disk list, show the new label.
        assert host.calls.index(["udevadm", "trigger", "--action=change", "/dev/sdb"]) > host.calls.index(
            ["btrfs", "filesystem", "label", "/mnt/pools/backup", "backup"])
        assert host.smb_applies == 1  # ADR-0248: the managed pools changed
        # A pool now, no longer offered for import.
        assert importable(api) == []
        assert [p["name"] for p in api.get(f"{API}/pools").json()] == ["data", "backup"]
        disk = next(d for d in api.get(f"{API}/disks").json() if d["name"] == "sdb")
        assert (disk["pool"], disk["in_use_reason"]) == ("backup", "Mounted at /mnt/pools/backup")

    def test_the_pool_is_mounted_again_after_a_reboot_with_its_data(self, api, host):
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 200
        # The reboot: nothing is mounted below /mnt/pools until the host mounts its fstab;
        # the startup reconcile mounts the entries whose disk was too slow for the boot.
        host.mounts = [m for m in host.mounts if not m["target"].startswith("/mnt/pools/")]
        host.changes.clear()

        results = pools.reconcile_pools()

        assert {"pool": "backup", "target": "/mnt/pools/backup", "status": "mounted"} in results
        assert ["mount", "/mnt/pools/backup"] in host.changes  # its fstab entry, by UUID
        pool = api.get(f"{API}/pools/backup").json()
        assert (pool["uuid"], pool["mounted"], pool["persistent"], pool["health"]) == (
            FOREIGN_UUID, True, True, "healthy")
        assert pool["used_size"] == USED

    def test_a_label_that_is_no_pool_name_is_replaced(self, api, host):
        host.add_disk("sdc")
        host.add_btrfs("My Media", OLD_UUID, ["/dev/sdc"])
        response = import_as(api, OLD_UUID, "media")
        assert response.status_code == 200, response.text
        assert labels(host)[OLD_UUID] == "media"
        assert response.json()["name"] == "media"

    def test_one_of_two_filesystems_with_one_label_is_imported_under_another_name(self, api, host):
        # The disks of two other UNOS hosts, whose pools were both named 'backup'.
        host.add_disk("sdc")
        host.add_btrfs("backup", OLD_UUID, ["/dev/sdc"])
        host.add_disk("sdd")
        host.add_btrfs("backup", SECOND_UUID, ["/dev/sdd"])

        refused = import_as(api, SECOND_UUID, "backup")
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "A btrfs filesystem labelled 'backup' already exists (on /dev/sdc). Choose another name."
        )
        response = import_as(api, SECOND_UUID, "backup2")
        assert response.status_code == 200, response.text
        assert response.json()["uuid"] == SECOND_UUID
        # The other one is the only 'backup' now: a pool, which Mount imports.
        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID]
        mounted = api.post(f"{API}/pools/backup/mount")
        assert mounted.status_code == 200, mounted.text
        assert mounted.json()["uuid"] == OLD_UUID

    def test_a_filesystem_on_two_disks(self, api, host):
        host.add_disk("sdc")
        host.add_disk("sdd")
        host.add_btrfs(None, OLD_UUID, ["/dev/sdc", "/dev/sdd"], profile="raid1")
        response = import_as(api, OLD_UUID, "mirror")
        assert response.status_code == 200, response.text
        assert (response.json()["devices"], response.json()["raid_level"]) == (
            ["/dev/sdc", "/dev/sdd"], "raid1")
        mount = ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={OLD_UUID}", "/mnt/pools/mirror"]
        # Every member is known to the kernel before the mount.
        assert host.calls.index(["btrfs", "device", "scan"]) < host.calls.index(mount)
        assert ["udevadm", "trigger", "--action=change", "/dev/sdc", "/dev/sdd"] in host.calls

    def test_a_filesystem_managed_under_its_old_name_gets_that_name_back(self, api, host):
        # UNOS manages it as 'old', but its label was changed on the host since.
        host.files["/etc/fstab"] = MANAGED_DATA + marker("old") + entry("old", FOREIGN_UUID)
        refused = import_as(api, FOREIGN_UUID, "backup")
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            f"UNOS manages filesystem {FOREIGN_UUID} as the pool 'old' (its /etc/fstab entry). "
            "Import it as 'old'."
        )
        assert host.changes == []
        response = import_as(api, FOREIGN_UUID, "old")
        assert response.status_code == 200, response.text
        assert labels(host)[FOREIGN_UUID] == "old"
        assert host.fstab == MANAGED_DATA + marker("old") + entry("old", FOREIGN_UUID)

    def test_the_import_starts_no_apps(self, api, host, monkeypatch):
        # Stopped apps of an earlier pool with this name keep their data on that pool,
        # not on the filesystem that takes its name. A Mount would start this one
        # (TestAppChecksBeforeStart in test_pool_persistence.py).
        checked = []
        monkeypatch.setattr(apps_router, "prepare_app_start",
                            lambda container, pools=None: checked.append(container.name))
        host.docker.add("jellyfin", labels={"unos.app": "jellyfin", "unos.app.pool": "backup"},
                        running=False, error="bind source path does not exist: /mnt/pools/backup/apps/x")
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 200
        wait_for_app_starts()
        assert (checked, host.docker.started) == ([], [])


# ---------------------------------------------------------------------------
# Refusals: nothing changes on the host
# ---------------------------------------------------------------------------


class TestRefusals:
    @staticmethod
    def refused(api, host, uuid: str, name: str, status: int = 409) -> str:
        """The detail of a refused import, after checking that it changed nothing."""
        fstab, before = host.fstab, labels(host)
        response = import_as(api, uuid, name)
        assert response.status_code == status, response.text
        assert host.changes == []
        assert host.fstab == fstab
        assert labels(host) == before
        return response.json()["detail"]

    def test_a_name_that_another_btrfs_filesystem_has(self, api, host):
        assert self.refused(api, host, FOREIGN_UUID, "data") == (
            "A btrfs filesystem labelled 'data' already exists (on /dev/sda). Choose another name."
        )

    def test_a_name_that_another_filesystem_of_the_host_has(self, api, host):
        # Two filesystems with one label: LABEL= in the host's fstab or root= of its
        # kernel could then name either.
        host.node("mmcblk1p1")["label"] = "rootfs"
        assert self.refused(api, host, FOREIGN_UUID, "rootfs") == (
            "The host already has a filesystem labelled 'rootfs' (on /dev/mmcblk1p1). "
            "Choose another name."
        )

    def test_a_name_that_unos_manages_for_another_filesystem(self, api, host):
        # The disks of pool 'gone' are not connected; its entry is not taken over.
        host.files["/etc/fstab"] = MANAGED_DATA + marker("gone") + entry("gone", GONE_UUID)
        assert self.refused(api, host, FOREIGN_UUID, "gone") == (
            f"UNOS manages the pool 'gone' on another filesystem ({GONE_UUID}). Choose another name."
        )

    def test_a_filesystem_mounted_on_the_host(self, api, host):
        host.add_mount("/media/usb", "/dev/sdb", "btrfs", FOREIGN_UUID)
        assert self.refused(api, host, FOREIGN_UUID, "backup") == (
            f"Filesystem {FOREIGN_UUID} is mounted at /media/usb. Unmount it on the host first."
        )

    def test_a_filesystem_still_mounted_in_another_mount_namespace(self, api, host):
        host.held.add(FOREIGN_UUID)  # e.g. a container's private mount
        assert self.refused(api, host, FOREIGN_UUID, "backup") == (
            f"Filesystem {FOREIGN_UUID} is still mounted in another mount namespace, for example "
            "by a container. Stop what uses it first."
        )

    def test_a_filesystem_with_a_missing_device(self, api, host):
        host.add_disk("sdc")
        host.add_btrfs(None, OLD_UUID, ["/dev/sdc"], missing=1, profile="raid1")
        assert self.refused(api, host, OLD_UUID, "mirror") == (
            f"Filesystem {OLD_UUID} is missing 1 device(s) and cannot be imported. Reconnect "
            "the missing disk(s) and try again."
        )

    def test_a_filesystem_that_is_a_pool_under_its_label(self, api, host):
        host.add_disk("sdc")
        host.add_btrfs("media", MEDIA_UUID, ["/dev/sdc"])
        message = f"Filesystem {MEDIA_UUID} is the pool 'media'. Mount it on the Storage page."
        assert self.refused(api, host, MEDIA_UUID, "media") == message
        assert self.refused(api, host, MEDIA_UUID, "films") == message

    def test_the_hosts_system_filesystem(self, api, host):
        system = "1b2c3d4e-0000-4000-8000-00000000000b"
        host.add_disk("nvme0n1", rota=False)
        host.add_btrfs(None, system, ["/dev/nvme0n1"])
        host.add_mount("/boot", "/dev/nvme0n1", "btrfs", system)
        assert self.refused(api, host, system, "boot") == (
            f"Filesystem {system} holds the host's system and cannot be a pool."
        )

    def test_a_mountpoint_that_holds_files(self, api, host):
        # Written to the system disk while nothing was mounted there.
        host.dirs.add("/mnt/pools/backup")
        host.contents["/mnt/pools/backup"] = ["notes.txt"]
        assert self.refused(api, host, FOREIGN_UUID, "backup").startswith(
            "/mnt/pools/backup is not empty: it holds 1 item(s) on the host's boot disk (notes.txt)."
        )

    def test_a_mountpoint_in_use(self, api, host):
        host.add_mount("/mnt/pools/backup", "tmpfs", "tmpfs")
        assert self.refused(api, host, FOREIGN_UUID, "backup") == (
            "Something is already mounted at /mnt/pools/backup"
        )

    def test_a_mountpoint_in_an_fstab_line_unos_does_not_manage(self, api, host):
        host.files["/etc/fstab"] = MANAGED_DATA + "LABEL=old /mnt/pools/backup ext4 defaults 0 2\n"
        assert self.refused(api, host, FOREIGN_UUID, "backup") == (
            "The host /etc/fstab already mounts /mnt/pools/backup through an entry UNOS does not "
            "manage (LABEL=old /mnt/pools/backup ext4 defaults 0 2). Remove or comment out that "
            "line first."
        )

    def test_an_unknown_filesystem(self, api, host):
        assert self.refused(api, host, NEW_UUID, "backup", 404) == (
            f"No btrfs filesystem with UUID {NEW_UUID} is on the host's disks. Attach them and "
            "reload the page."
        )

    @pytest.mark.parametrize("uuid", ["not-a-uuid", FOREIGN_UUID + "0", "g" * 8 + FOREIGN_UUID[8:]])
    def test_an_invalid_uuid(self, api, host, uuid):
        assert self.refused(api, host, uuid, "backup", 400) == "Invalid filesystem UUID"
        assert host.smb_applies == 0  # refused before the import

    @pytest.mark.parametrize("name", ["", "two words", "-rf", "x" * 64, "../etc"])
    def test_an_invalid_name(self, api, host, name):
        self.refused(api, host, FOREIGN_UUID, name, 400)
        assert host.smb_applies == 0

    def test_a_body_without_a_name(self, api, host):
        assert api.post(f"{API}/filesystems/{FOREIGN_UUID}/import", json={}).status_code == 422
        assert host.changes == []


class TestDefaultSubvolume:
    """btrfs subvolume set-default changes what a mount without subvol= shows."""

    def test_a_filesystem_that_does_not_mount_its_top_level_is_unmounted_again(self, api, host):
        host.filesystems[-1]["default_subvolume"] = 256
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Pool 'backup' was not mounted: its filesystem mounts subvolume 256 by default, not its "
            "top level, where UNOS keeps volumes and share folders. Nothing was changed. To use it, "
            "make the top level its default subvolume on the host ('btrfs subvolume set-default 5' "
            "on a mount of it), then try again."
        )
        assert host.changes == [
            ["mkdir", "-p", "/mnt/pools/backup"],
            ["chattr", "+i", "/mnt/pools/backup"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={FOREIGN_UUID}", "/mnt/pools/backup"],
            ["umount", "/mnt/pools/backup"],
        ]
        assert not host.mounted("/mnt/pools/backup")
        assert labels(host)[FOREIGN_UUID] is None  # not relabelled
        assert host.fstab == MANAGED_DATA
        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID]  # still offered


class TestFailures:
    def test_a_label_that_cannot_be_set_unmounts_the_filesystem_again(self, api, host):
        host.fail[("btrfs", "filesystem", "label")] = (1, "ERROR: unable to set label: Operation not permitted")
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Setting the label of the filesystem at /mnt/pools/backup failed: ERROR: unable to "
            "set label: Operation not permitted"
        )
        assert host.changes[-1] == ["umount", "/mnt/pools/backup"]
        assert not host.mounted("/mnt/pools/backup")
        assert labels(host)[FOREIGN_UUID] is None
        assert host.fstab == MANAGED_DATA

    def test_a_failed_mount_changes_neither_the_label_nor_the_fstab(self, api, host):
        host.fail[("mount",)] = (32, "mount: /mnt/pools/backup: wrong fs type, bad option")
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 500
        assert "wrong fs type" in response.json()["detail"]
        assert labels(host)[FOREIGN_UUID] is None
        assert host.fstab == MANAGED_DATA
        assert "/mnt/pools/backup" in host.immutable  # the empty mountpoint stays guarded

    def test_every_import_that_reaches_the_host_applies_smb_conf(self, api, host):
        """ADR-0248, as for the other endpoints that change the managed pools: also when it fails."""
        assert import_as(api, FOREIGN_UUID, "data").status_code == 409
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 200
        assert host.smb_applies == 2


class TestRoles:
    def test_a_regular_user_cannot_list_or_import(self, api, host):
        api.user.is_admin = False
        assert api.get(f"{API}/filesystems/importable").status_code == 403
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 403
        assert host.changes == [] and host.smb_applies == 0

    def test_nobody_signed_in_cannot_list_or_import(self, host):
        client = TestClient(app)
        assert client.get(f"{API}/filesystems/importable").status_code in (401, 403)
        assert client.post(f"{API}/filesystems/{FOREIGN_UUID}/import", json={"name": "backup"}).status_code in (401, 403)
        assert host.changes == []

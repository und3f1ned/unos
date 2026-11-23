"""Importing a btrfs filesystem found on the host's disks as a pool (ADR-0283).

``GET /api/v1/storage/filesystems/importable`` lists the btrfs filesystems that
are not the pool their label names, on data disks only; ``POST
/api/v1/storage/filesystems/{uuid}/import`` mounts one by UUID at
/mnt/pools/<name>, gives it the name as its label through the mount once the
mount shows its top level, and keeps it in the host's fstab (ADR-0034). A
filesystem whose label is a pool name is imported by Mount
(test_pool_persistence.py). Every host command goes to the FakeHost of
test_pool_persistence.py, which records the commands that change the host.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.shares import nfs, smb
from src.storage import disks, pools

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
THIRD_UUID = "9f6b4e3c-7890-4ab1-8c2d-3e4f5a6b7c8d"
USED = 700_000_000_000
DISK_SIZE = 4_000_787_030_016  # FakeHost.add_disk's default
MANAGED_DATA = BASE_FSTAB + marker("data") + entry("data", DATA_UUID)


@pytest.fixture
def host(monkeypatch):
    """The managed pool 'data', mounted, and the disk /dev/sdb of another system.

    /dev/sdb holds a btrfs filesystem without a label and 700 GB of data;
    /mnt/pools/backup does not exist yet. The SMB shares and NFS exports that
    UNOS knows of (smb.list_shares, nfs.list_exports) are the lists
    ``host.shares`` and ``host.exports``, empty here; test_smb_integration.py
    runs the import with the real share code.
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
    fake.shares, fake.exports = [], []
    monkeypatch.setattr(smb, "list_shares", lambda: list(fake.shares))
    monkeypatch.setattr(nfs, "list_exports", lambda: list(fake.exports))
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


def pool_names(api) -> list[str]:
    response = api.get(f"{API}/pools")
    assert response.status_code == 200, response.text
    return [pool["name"] for pool in response.json()]


def numbered_uuid(index: int) -> str:
    return f"{index:08x}-0000-4000-8000-{index:012x}"


def boot_virtual_and_system_devices(host) -> dict[str, str]:
    """Devices that no pool is made of, empty: what each is -> its path.

    An eMMC that the host uses: it mounts a vendor's partition of it, so it is
    hidden like the system's own (storage.free-mmc-as-data-disk); a second partition of the SD
    card that the host boots from (FakeHost's system disk, mmcblk1); a loop
    device; and a partition of an NVMe disk whose EFI partition the host
    mounts at /boot/efi.
    """
    host.add_disk("mmcblk0", size=32_000_000_000, rota=False, pttype="gpt")
    host.add_partition("mmcblk0", "mmcblk0p1", size=1_000_000_000, fstype="ext4")
    host.add_mount("/media/vendor", "/dev/mmcblk0p1", "ext4")
    host.add_partition("mmcblk0", "mmcblk0p5", size=20_000_000_000)
    host.add_partition("mmcblk1", "mmcblk1p2", size=30_000_000_000)
    host.add_disk("loop0", size=8_000_000_000, rota=False)["type"] = "loop"
    host.add_disk("nvme0n1", rota=False, pttype="gpt")
    host.add_partition("nvme0n1", "nvme0n1p1", fstype="vfat")
    host.add_mount("/boot/efi", "/dev/nvme0n1p1", "vfat")
    host.add_partition("nvme0n1", "nvme0n1p2", size=200_000_000_000)
    return {
        "eMMC": "/dev/mmcblk0p5",
        "SD card": "/dev/mmcblk1p2",
        "loop device": "/dev/loop0",
        "system disk": "/dev/nvme0n1p2",
    }


# Why disks.pool_device_problems refuses each of boot_virtual_and_system_devices.
DEVICE_PROBLEMS = {
    "eMMC": "/dev/mmcblk0p5 is on /dev/mmcblk0, a boot or virtual device",
    "SD card": "/dev/mmcblk1p2 is on /dev/mmcblk1, a boot or virtual device",
    "loop device": "/dev/loop0 is a boot or virtual device",
    "system disk": "/dev/nvme0n1p2 is on /dev/nvme0n1, a system disk (Mounted at /boot/efi)",
}


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
        # Neither 'backup' is a pool: a card of either would act on the other by its name.
        assert pool_names(api) == ["data", "media"]
        assert host.changes == []

    def test_a_duplicate_of_a_mounted_pool_is_only_found_on_the_disks(self, api, host):
        # Another system's disk whose pool was named 'data' too.
        host.add_disk("sdc")
        host.add_btrfs("data", OLD_UUID, ["/dev/sdc"])

        [pool] = api.get(f"{API}/pools").json()
        assert (pool["name"], pool["uuid"], pool["mounted"]) == ("data", DATA_UUID, True)
        found = {fs["uuid"]: (fs["label"], fs["label_problem"]) for fs in importable(api)}
        assert found == {FOREIGN_UUID: (None, "missing"), OLD_UUID: ("data", "duplicate")}
        # The card's Mount acts on the pool it shows.
        assert api.post(f"{API}/pools/data/mount").json()["uuid"] == DATA_UUID
        assert host.changes == []

    def test_a_duplicate_of_a_managed_pool_that_is_not_mounted_is_only_found_on_the_disks(self, api, host):
        # 'data' was not mounted at boot (its disk was late), and another 'data' is attached.
        host.mounts = [m for m in host.mounts if m["target"] != "/mnt/pools/data"]
        host.add_disk("sdc")
        host.add_btrfs("data", OLD_UUID, ["/dev/sdc"])

        [pool] = api.get(f"{API}/pools").json()
        assert (pool["name"], pool["uuid"], pool["mounted"], pool["persistent"]) == (
            "data", DATA_UUID, False, True)
        assert [(fs["uuid"], fs["label_problem"]) for fs in importable(api)] == [
            (FOREIGN_UUID, "missing"), (OLD_UUID, "duplicate")]

    def test_filesystems_on_boot_virtual_or_system_devices_are_not_offered(self, api, host):
        # Nor imported (TestBootVirtualAndSystemDevices): an eMMC that the host uses is hidden
        # (storage.free-mmc-as-data-disk), and no pool is made of such devices (ADR-0035).
        for index, path in enumerate(boot_virtual_and_system_devices(host).values(), 1):
            host.add_btrfs(None, numbered_uuid(index), [path])

        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID]
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
    def test_on_a_spacemit_k1_an_nvme_filesystem_is_imported_with_discard_and_fstrim(self, api, host):
        # The import mounts and keeps in fstab a filesystem on an NVMe drive of a K1 as every other
        # one: noatime alone, and an entry that fstrim.timer trims (ADR-0429).
        host.files["/proc/device-tree/compatible"] = "spacemit,k1-x\0"
        host.add_disk("nvme0n1", rota=False, serial="A1000-1", model="KINGSTON SA1000M8240G")
        host.add_btrfs(None, SECOND_UUID, ["/dev/nvme0n1"], used=USED)

        response = import_as(api, SECOND_UUID, "fast")

        assert response.status_code == 200, response.text
        assert ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={SECOND_UUID}", "/mnt/pools/fast"] in host.changes
        assert host.fstab == MANAGED_DATA + marker("fast") + entry("fast", SECOND_UUID)
        assert not any("/proc/device-tree/compatible" in call for call in host.calls)

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
        # Under a name that apps of an earlier pool use, an import is refused (TestEarlierPool).
        # The pool that UNOS manages under the name gets it back, with its apps, which the
        # import does not start: a Mount would (TestAppChecksBeforeStart in
        # test_pool_persistence.py).
        host.files["/etc/fstab"] = MANAGED_DATA + marker("backup") + entry("backup", FOREIGN_UUID)
        checked = []
        monkeypatch.setattr(apps_router, "prepare_app_start",
                            lambda container, pools=None: checked.append(container.name))
        host.docker.add("jellyfin", labels={"unos.app": "jellyfin", "unos.app.pool": "backup"},
                        running=False, error="bind source path does not exist: /mnt/pools/backup/apps/x")
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 200
        wait_for_app_starts()
        assert (checked, host.docker.started) == ([], [])


class TestEarlierPool:
    """Remove Pool keeps a pool's SMB shares with their access (ADR-0248), and NFS exports and
    apps keep their folders: imported under that name, another filesystem would serve them
    (ADR-0283). test_smb_integration.py runs this with the real share code."""

    @staticmethod
    def refused(api, host, name: str = "backup", status: int = 409) -> str:
        return TestRefusals.refused(api, host, FOREIGN_UUID, name, status)

    def test_smb_shares_of_an_earlier_pool(self, api, host):
        host.shares += [
            smb.SharePathInfo("docs", "/mnt/pools/backup/docs"),
            smb.SharePathInfo("photos", "/mnt/pools/backup/media/photos"),
            smb.SharePathInfo("other", "/mnt/pools/data/other"),
            smb.SharePathInfo("near", "/mnt/pools/backups/near"),
        ]
        assert self.refused(api, host) == (
            "The name 'backup' still belongs to an earlier pool: SMB shares (docs, photos) use it, "
            "and their data is on that pool's disks. Imported under this name, this filesystem "
            "would take that pool's place for them. Choose another name, or remove them first."
        )
        assert "/mnt/pools/backup" not in host.dirs
        # Under another name, nothing serves its folders.
        response = import_as(api, FOREIGN_UUID, "archive")
        assert response.status_code == 200, response.text

    def test_nfs_exports_and_containers_of_an_earlier_pool(self, api, host):
        host.exports.append(SimpleNamespace(path="/mnt/pools/backup/media"))
        # Stopped: Docker would start them at the next boot, from the imported filesystem.
        host.docker.add("jellyfin", labels={"unos.app": "jellyfin", "unos.app.pool": "backup"},
                        running=False, error="bind source path does not exist: /mnt/pools/backup/apps/x",
                        policy="always")
        host.docker.add("syncthing", sources=["/mnt/pools/backup/sync"], running=False)
        photos = host.docker.add_volume(
            "photos", options={"type": "none", "o": "bind", "device": "/mnt/pools/backup/photos"})
        host.docker.add("immich", mounts=[photos], running=False)
        host.docker.add("nextcloud", sources=["/mnt/pools/data/cloud"], labels={"unos.app.pool": "data"})
        assert self.refused(api, host) == (
            "The name 'backup' still belongs to an earlier pool: NFS exports (/mnt/pools/backup/media) "
            "and containers (immich, jellyfin, syncthing) use it, and their data is on that pool's "
            "disks. Imported under this name, this filesystem would take that pool's place for them. "
            "Choose another name, or remove them first."
        )
        assert host.docker.started == []

    def test_without_docker_the_import_is_refused(self, api, host):
        host.docker.unavailable = True
        assert self.refused(api, host, status=503).startswith(
            "Pool 'backup' was not imported: Docker did not answer, so it is not known whether apps "
            "keep data on a pool named 'backup'. Try again when Docker is running"
        )

    def test_shares_that_cannot_be_read(self, api, host, monkeypatch):
        def unreadable():
            raise OSError("database is locked")

        monkeypatch.setattr(smb, "list_shares", unreadable)
        assert self.refused(api, host, status=500) == (
            "Pool 'backup' was not imported: the SMB shares and NFS exports could not be read "
            "(database is locked)"
        )

    def test_the_pool_that_unos_manages_under_the_name_keeps_its_shares(self, api, host):
        # Its label was changed on the host; its fstab entry still names it 'backup'.
        host.files["/etc/fstab"] = MANAGED_DATA + marker("backup") + entry("backup", FOREIGN_UUID)
        host.shares.append(smb.SharePathInfo("docs", "/mnt/pools/backup/docs"))
        host.docker.add("jellyfin", labels={"unos.app.pool": "backup"}, running=False)
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 200, response.text
        assert labels(host)[FOREIGN_UUID] == "backup"


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


class TestBootVirtualAndSystemDevices:
    """No pool is made of a boot or virtual device or of the system disk, as Create refuses
    them (ADR-0035): an eMMC that the host uses is never mounted or written (storage.free-mmc-as-data-disk)."""

    @pytest.mark.parametrize("device", list(DEVICE_PROBLEMS))
    def test_an_import_is_refused(self, api, host, device):
        path = boot_virtual_and_system_devices(host)[device]
        host.add_btrfs(None, THIRD_UUID, [path])
        assert TestRefusals.refused(api, host, THIRD_UUID, "vendor") == (
            f"Filesystem {THIRD_UUID} cannot be a pool: {DEVICE_PROBLEMS[device]}."
        )
        assert not host.mounted("/mnt/pools/vendor")

    @pytest.mark.parametrize("device", list(DEVICE_PROBLEMS))
    def test_a_pool_found_on_them_is_neither_listed_nor_mounted(self, api, host, device):
        # Labelled with a pool name, as the firmware of a NAS may label its partitions.
        path = boot_virtual_and_system_devices(host)[device]
        host.add_btrfs("vendor", THIRD_UUID, [path])
        assert pool_names(api) == ["data"]
        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID]

        response = api.post(f"{API}/pools/vendor/mount")

        assert response.status_code == 409
        assert response.json()["detail"] == f"Pool 'vendor' cannot be mounted: {DEVICE_PROBLEMS[device]}."
        assert host.changes == []
        assert host.fstab == MANAGED_DATA

    def test_an_md_array_with_a_member_on_the_emmc(self, api, host):
        # lsblk shows the array below each of its members.
        boot_virtual_and_system_devices(host)
        host.add_disk("sdc", pttype="gpt")
        host.add_partition("sdc", "sdc1")
        host.add_partition("mmcblk0", "mmcblk0p6")
        for member in ("sdc1", "mmcblk0p6"):
            host.add_holder(member, "md0", "raid1")
        host.add_btrfs(None, THIRD_UUID, ["/dev/md0"])
        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID]
        assert TestRefusals.refused(api, host, THIRD_UUID, "mirror") == (
            f"Filesystem {THIRD_UUID} cannot be a pool: /dev/md0 is on /dev/mmcblk0, a boot or "
            "virtual device."
        )

    def test_an_md_array_on_two_data_disks_is_offered(self, api, host):
        host.add_disk("sdc", pttype="gpt")
        host.add_partition("sdc", "sdc1")
        host.add_disk("sdd", pttype="gpt")
        host.add_partition("sdd", "sdd1")
        for member in ("sdc1", "sdd1"):
            host.add_holder(member, "md0", "raid1")
        host.add_btrfs(None, THIRD_UUID, ["/dev/md0"])
        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID, THIRD_UUID]

    def test_a_device_that_lsblk_does_not_list_cannot_be_checked(self, host):
        assert disks.pool_device_problems(["/dev/sdb", "/dev/sdz"]) == {
            "/dev/sdz": "lsblk does not list /dev/sdz"
        }

    def test_without_lsblk_nothing_is_offered(self, api, host):
        host.fail[("lsblk",)] = (1, "lsblk: cannot read /sys/dev/block")
        response = api.get(f"{API}/filesystems/importable")
        assert response.status_code == 500
        assert response.json()["detail"] == "lsblk failed: lsblk: cannot read /sys/dev/block"

    def test_without_lsblk_a_found_pool_is_listed_but_not_mounted(self, api, host):
        host.add_disk("sdc")
        host.add_btrfs("media", MEDIA_UUID, ["/dev/sdc"])
        host.fail[("lsblk",)] = (1, "lsblk: cannot read /sys/dev/block")
        assert pool_names(api) == ["data", "media"]
        response = api.post(f"{API}/pools/media/mount")
        assert response.status_code == 500
        assert response.json()["detail"] == "lsblk failed: lsblk: cannot read /sys/dev/block"
        assert host.changes == []


class TestDefaultSubvolume:
    """btrfs subvolume set-default changes what a mount without subvol= shows."""

    def test_a_filesystem_that_does_not_mount_its_top_level_is_unmounted_again(self, api, host):
        host.filesystems[-1]["default_subvolume"] = 256
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Pool 'backup' was not mounted: its filesystem mounts subvolume 256 by default, not its "
            "top level, where UNOS keeps volumes and share folders. It was unmounted again, with its "
            "label and the host's /etc/fstab as they were. To use it, make the top level its default "
            "subvolume on the host ('btrfs subvolume set-default 5' on a mount of it), then try again."
        )
        assert host.changes == [
            ["mkdir", "-p", "/mnt/pools/backup"],
            ["chattr", "+i", "/mnt/pools/backup"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={FOREIGN_UUID}", "/mnt/pools/backup"],
            ["umount", "/mnt/pools/backup"],
            # The mountpoint it made goes again: no pool 'backup' is left to remove it with.
            ["chattr", "-i", "/mnt/pools/backup"],
            ["rmdir", "/mnt/pools/backup"],
        ]
        assert not host.mounted("/mnt/pools/backup")
        assert "/mnt/pools/backup" not in host.dirs
        assert labels(host)[FOREIGN_UUID] is None  # not relabelled
        assert host.fstab == MANAGED_DATA
        assert [fs["uuid"] for fs in importable(api)] == [FOREIGN_UUID]  # still offered

    def test_a_mountpoint_that_was_there_before_stays(self, api, host):
        # Left write-protected by an earlier pool of the name.
        host.dirs.add("/mnt/pools/backup")
        host.immutable.add("/mnt/pools/backup")
        host.filesystems[-1]["default_subvolume"] = 256
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 409
        assert ("/mnt/pools/backup" in host.dirs, "/mnt/pools/backup" in host.immutable) == (True, True)
        assert host.changes[-1] == ["umount", "/mnt/pools/backup"]


class TestFailures:
    def test_a_label_that_cannot_be_set_unmounts_the_filesystem_again(self, api, host):
        host.fail[("btrfs", "filesystem", "label")] = (1, "ERROR: unable to set label: Operation not permitted")
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Setting the label of the filesystem at /mnt/pools/backup failed: ERROR: unable to "
            "set label: Operation not permitted"
        )
        assert host.changes[-3:] == [
            ["umount", "/mnt/pools/backup"], ["chattr", "-i", "/mnt/pools/backup"],
            ["rmdir", "/mnt/pools/backup"]]
        assert not host.mounted("/mnt/pools/backup")
        assert "/mnt/pools/backup" not in host.dirs
        assert labels(host)[FOREIGN_UUID] is None
        assert host.fstab == MANAGED_DATA

    def test_a_failed_mount_changes_neither_the_label_nor_the_fstab(self, api, host):
        host.fail[("mount",)] = (32, "mount: /mnt/pools/backup: wrong fs type, bad option")
        response = import_as(api, FOREIGN_UUID, "backup")
        assert response.status_code == 500
        assert "wrong fs type" in response.json()["detail"]
        assert labels(host)[FOREIGN_UUID] is None
        assert host.fstab == MANAGED_DATA
        # No pool 'backup' was made: its mountpoint goes again.
        assert "/mnt/pools/backup" not in host.dirs and "/mnt/pools/backup" not in host.immutable

    def test_a_failed_mount_puts_back_a_marker_it_completed(self, api, host):
        # A marker without its entry line, as an editor of the host's fstab may leave it.
        host.files["/etc/fstab"] = MANAGED_DATA + marker("backup")
        host.fail[("mount",)] = (32, "mount: /mnt/pools/backup: wrong fs type, bad option")
        assert import_as(api, FOREIGN_UUID, "backup").status_code == 500
        assert host.fstab == MANAGED_DATA + marker("backup")

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

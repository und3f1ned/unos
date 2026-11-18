"""Adding a disk to a pool and removing one (storage.pool-add-and-remove-disks, ADR-0410).

An administrator adds a free disk to a mounted pool, after typing its path again: btrfs device add on
the host; the pool grows, and its profile stays. A disk that holds the system, another pool or a
mount is never offered nor added, and one with other data only with force. A disk leaves a pool
after its path is typed again: btrfs device remove moves its data to the other disks first, in a
transient unit of the host that outlives the supervisor; a removal that the profile or the space
of the disks left cannot survive is refused, and so are both while a scrub, a balance or another
removal of the pool runs. The page follows a removal over the events socket.
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from src import host
from src.storage import devices
from src.storage.schemas import DiskRemovalStatus

from .test_balance import MOUNTPOINT, FakeKernel, audited
from .test_pool_persistence import DATA_UUID, MEDIA_UUID, admin_client, install_fake_host, wait_for_app_starts

GIB = 1024**3
TB = 10**12
LOG = "/run/unos-disk-remove-data.log"
DEVICES = "/api/v1/storage/pools/data/devices"
REMOVAL = "/api/v1/storage/pools/data/removal"


def unit(devid: int, device: str, used: int) -> list[str]:
    """The systemd-run of a removal: btrfs in a unit of the host that outlives the supervisor's
    container, stopped before the pool is unmounted at a shutdown (RequiresMountsFor), its output in
    /run, whose first line names the disk and the bytes on it."""
    return [
        "systemd-run", "--unit=unos-disk-remove-data", "--description=UNOS: remove a disk from the pool data",
        "--collect", "--quiet", "--service-type=exec", f"--property=RequiresMountsFor={MOUNTPOINT}",
        f"--property=ExecStartPre=truncate --size=0 {LOG}",
        f"--property=ExecStartPre=echo UNOS removes devid {devid} {device} with {used} bytes allocated",
        f"--property=StandardOutput=append:{LOG}", "--property=StandardError=inherit",
        "--property=TimeoutStopSec=10min", "--", "btrfs", "device", "remove", str(devid), MOUNTPOINT,
    ]


class FakePool(FakeKernel):
    """The pool data with its disks as the kernel keeps them: btrfs filesystem show and df in the
    container, btrfs device add and the unit of a removal on the host. A disk that the kernel
    removes has the size 0 (btrfs_shrink_device), and the bytes on it fall as its chunks move."""

    def __init__(self, fake_host) -> None:
        super().__init__(fake_host)
        self.devids = {"/dev/sda": 1}
        self.used = {"/dev/sda": 2 * GIB}
        self.profiles = {"Data": "single", "Metadata": "single", "System": "single"}
        self.adds: list[list[str]] = []
        self.removals: list[list[str]] = []
        self.add_fails: str | None = None
        self.remove_fails: str | None = None  # what btrfs prints when the kernel refuses at once
        self.unseen_removal = False  # btrfs has not reached the kernel yet

    @property
    def fs(self) -> dict:
        return self.host.fs_by_label("data")

    def join(self, name: str, *, used: int = 2 * GIB, size: int = 4 * TB, **disk) -> None:
        """Another disk of the pool."""
        self.host.add_disk(name, size=size, **disk)
        device = f"/dev/{name}"
        self.fs["devices"].append(device)
        self.devids[device] = max(self.devids.values()) + 1
        self.used[device] = used

    def set_profile(self, profile: str) -> None:
        self.profiles = dict.fromkeys(self.profiles, profile.upper() if profile != "single" else profile)
        self.fs["profile"] = profile

    def show(self) -> str:
        fs = self.fs
        lines = [f"Label: 'data'  uuid: {fs['uuid']}",
                 f"\tTotal devices {len(fs['devices']) + fs['missing']} FS bytes used {fs['used']}"]
        for device in fs["devices"]:
            size = 0 if device == self.removing else self.host.node(device.removeprefix("/dev/"))["size"]
            lines.append(f"\tdevid {self.devids[device]:5d} size {size} used {self.used[device]} path {device}")
        if fs["missing"]:
            lines.append("\t*** Some devices missing")
        return "\n".join(lines) + "\n\n"

    def run_in_container(self, cmd, **kwargs):
        if cmd == ["btrfs", "filesystem", "df", "-b", MOUNTPOINT]:
            self.container.append(list(cmd))
            out = "".join(f"{kind}, {profile}: total=1073741824, used=1048576\n" for kind, profile in self.profiles.items())
            return subprocess.CompletedProcess(cmd, 0, out + "GlobalReserve, single: total=3407872, used=0\n", "")
        if cmd[0] in ("smartctl", "pgrep"):
            return self.host.run_in_container(cmd, **kwargs)
        return super().run_in_container(cmd, **kwargs)

    def run_on_host(self, cmd, **kwargs):
        if cmd[:3] == ["btrfs", "device", "add"]:
            return self.device_add(list(cmd))
        if cmd[:2] == ["systemd-run", "--unit=unos-disk-remove-data"]:
            return self.start_removal(list(cmd))
        return super().run_on_host(cmd, **kwargs)

    def device_add(self, cmd: list[str]) -> host.HostResult:
        self.adds.append(cmd)
        device, target = cmd[-2:]
        assert target == MOUNTPOINT, cmd
        if self.add_fails:
            return host.HostResult(1, "", self.add_fails)
        node = self.host._node_by_path(device)
        existing = self.host.signatures.get(device) or node.get("fstype") or node.get("children") or node.get("pttype")
        if existing and "-f" not in cmd:
            return host.HostResult(1, "", f"ERROR: {device} appears to contain an existing filesystem\n")
        self.host.signatures.pop(device, None)
        node.update(fstype=None, pttype=None, label=None)
        node.pop("children", None)
        self.fs["devices"].append(device)
        self.devids[device] = max(self.devids.values()) + 1
        self.used[device] = 0
        return host.HostResult(0, "", "")

    def start_removal(self, cmd: list[str]) -> host.HostResult:
        self.removals.append(cmd)
        btrfs = cmd[cmd.index("--") + 1:]
        assert btrfs[:3] == ["btrfs", "device", "remove"] and btrfs[4:] == [MOUNTPOINT], cmd
        devid = int(btrfs[3])
        echo = next(arg for arg in cmd if arg.startswith("--property=ExecStartPre=echo "))
        self.host.files[LOG] = echo.removeprefix("--property=ExecStartPre=echo ") + "\n"
        if self.remove_fails:
            self.host.files[LOG] += self.remove_fails
        elif not self.unseen_removal:
            self.removing = next(device for device, number in self.devids.items() if number == devid)
        return host.HostResult(0, "", "")

    def removed(self) -> None:
        """The kernel moved the last chunk: the disk leaves the pool, without a btrfs signature."""
        device, self.removing = self.removing, None
        self.fs["devices"].remove(device)
        del self.used[device]


@pytest.fixture
def pool(monkeypatch):
    """The pool data on the disk sda, mounted read-write, without a scrub, a balance or a removal,
    on a host whose only other disks are its SD card and zram swap (the dev device)."""
    fake_host = install_fake_host(monkeypatch)
    fake_host.add_disk("sda", model="WDC WD40EFRX", serial="WD-A", size=4 * TB)
    fake_host.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake_host.mount_pool("data")
    fake = FakePool(fake_host)
    monkeypatch.setattr(host, "run_in_container", fake.run_in_container)
    monkeypatch.setattr(host, "run_on_host", fake.run_on_host)
    yield fake
    wait_for_app_starts()


def identity(path: str, serial: str | None, size: int = 4 * TB, wwn: str | None = None) -> dict:
    return {"path": path, "serial": serial, "wwn": wwn, "size": size}


def add_body(path: str = "/dev/sdb", serial: str | None = "WD-B", **fields) -> dict:
    return {"device": path, "confirm_device": path, "identity": identity(path, serial), **fields}


def remove_body(devid: int, path: str, **fields) -> dict:
    return {"devid": devid, "device": path, "confirm_device": path, **fields}


def removal(**fields) -> dict:
    return {"pool": "data", "status": "idle", "devid": None, "device": None, "left": None, "total": None,
            "result": None, "error": None, **fields}


class TestTheDisksOfAPool:
    def test_the_dev_device_s_pool_has_one_disk_that_cannot_leave_and_nothing_to_add(self, pool):
        with admin_client() as client:
            r = client.get(DEVICES)

        assert r.status_code == 200, r.text
        assert r.json() == {
            "pool": "data",
            "profiles": ["single"],
            "members": [{"devid": 1, "path": "/dev/sda", "size": 4 * TB, "used": 2 * GIB, "model": "WDC WD40EFRX",
                         "serial": "WD-A", "problem": "/dev/sda is the only disk of the pool"}],
            "candidates": [],
            "removal": removal(),
        }
        assert pool.host.changes == [] and pool.adds == [] and pool.removals == []

    def test_only_disks_without_the_system_another_pool_or_a_mount_are_offered(self, pool):
        host_ = pool.host
        host_.add_disk("sdb", model="Blank", serial="WD-B", size=4 * TB)
        host_.add_disk("sdc", serial="WD-C", pttype="gpt")
        host_.add_partition("sdc", "sdc1", fstype="ext4", label="old")
        # Another pool's disk, not mounted: never offered.
        host_.add_disk("sdd", serial="WD-D")
        host_.add_btrfs("media", MEDIA_UUID, ["/dev/sdd"])
        # A btrfs filesystem that only wipefs sees (no label lsblk knows).
        host_.add_disk("sde", serial="WD-E")
        host_.signatures["/dev/sde"] = [{"type": "btrfs", "offset": "0x10040"}]
        # A disk with a mounted partition, and one that is swap.
        host_.add_disk("sdf", serial="WD-F", pttype="gpt")
        host_.add_partition("sdf", "sdf1", fstype="ext4")
        host_.add_mount("/mnt/usb", "/dev/sdf1", "ext4")
        host_.add_disk("sdg", serial="WD-G")
        host_.swaps.append("/dev/sdg")
        # A disk held by md.
        host_.add_disk("sdh", serial="WD-H")
        host_.add_holder("sdh", "md0", "raid1")
        with admin_client() as client:
            r = client.get(DEVICES)

        assert r.status_code == 200, r.text
        assert r.json()["candidates"] == [
            {"name": "sdb", "path": "/dev/sdb", "model": "Blank", "serial": "WD-B", "wwn": None, "size": 4 * TB,
             "has_data": False, "data_description": None},
            {"name": "sdc", "path": "/dev/sdc", "model": None, "serial": "WD-C", "wwn": None,
             "size": 4_000_787_030_016, "has_data": True, "data_description": "gpt partition table with 1 partition"},
        ]

    def test_a_sleeping_disk_is_offered_without_being_woken(self, pool):
        pool.host.add_disk("sdb", serial="WD-B")
        pool.host.asleep.add("/dev/sdb")
        with admin_client() as client:
            (candidate,) = client.get(DEVICES).json()["candidates"]

        assert (candidate["path"], candidate["has_data"]) == ("/dev/sdb", True)
        assert candidate["data_description"] == "unknown contents (the disk sleeps)"
        assert pool.host.woken == []

    def test_a_pool_that_is_not_mounted_is_refused(self, pool):
        pool.host.mounts.clear()
        with admin_client() as client:
            r = client.get(DEVICES)
        assert (r.status_code, r.json()) == (409, {"detail": f"Pool data is not mounted at {MOUNTPOINT}"})
        assert pool.container == []


class TestAdd:
    def test_a_free_disk_joins_the_pool_which_grows_and_keeps_its_profile(self, pool):
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        with admin_client() as client:
            before = client.get("/api/v1/storage/pools/data").json()
            r = client.post(DEVICES, json=add_body())

        assert r.status_code == 200, r.text
        assert pool.adds == [["btrfs", "device", "add", "/dev/sdb", MOUNTPOINT]]
        info = r.json()
        assert info["devices"] == ["/dev/sda", "/dev/sdb"]
        assert info["total_size"] == before["total_size"] + 4 * TB
        assert info["raid_level"] == before["raid_level"] == "single"
        assert pool.units == []  # no balance, no conversion
        assert audited() == [("pool_disk_added", "pool", "data",
                              f"Added /dev/sdb (serial WD-B, {4 * TB} bytes) to the pool data; its profiles stay single",
                              True)]

    def test_a_raid1_pool_stays_raid1(self, pool):
        pool.join("sdb", serial="WD-B")
        pool.set_profile("raid1")
        pool.host.add_disk("sdc", serial="WD-C", size=4 * TB)
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body("/dev/sdc", "WD-C"))
            profiles = client.get(DEVICES).json()["profiles"]

        assert r.status_code == 200, r.text
        assert (r.json()["raid_level"], profiles) == ("raid1", ["raid1"])
        assert pool.adds == [["btrfs", "device", "add", "/dev/sdc", MOUNTPOINT]]
        assert audited()[0][3].endswith("its profiles stay raid1")

    def test_a_disk_with_data_needs_force_and_is_erased(self, pool):
        pool.host.add_disk("sdc", serial="WD-C", size=4 * TB, pttype="gpt")
        pool.host.add_partition("sdc", "sdc1", fstype="ext4")
        contents = "gpt partition table with 1 partition"
        with admin_client() as client:
            refused = client.post(DEVICES, json=add_body("/dev/sdc", "WD-C"))
            assert pool.adds == []
            r = client.post(DEVICES, json=add_body("/dev/sdc", "WD-C", force=True, contents=contents))

        assert (refused.status_code, refused.json()) == (409, {
            "detail": "/dev/sdc contains data (gpt partition table with 1 partition). Set force to erase it and add it "
                      "anyway."})
        assert r.status_code == 200, r.text
        assert pool.adds == [["btrfs", "device", "add", "-f", "/dev/sdc", MOUNTPOINT]]
        assert audited()[0][3].endswith("; erased what it held (gpt partition table with 1 partition)")

    def test_a_sleeping_disk_s_contents_are_shown_before_they_are_erased(self, pool):
        """The list does not wake it (ADR-0378): its contents are unknown there. The add's read wakes
        it, and contents that the list did not show are refused, then listed."""
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        pool.host.signatures["/dev/sdb"] = [{"type": "ext4", "offset": "0x438"}]
        pool.host.asleep.add("/dev/sdb")
        with admin_client() as client:
            (listed,) = client.get(DEVICES).json()["candidates"]
            r = client.post(DEVICES, json=add_body(force=True, contents=listed["data_description"]))
            (now,) = client.get(DEVICES).json()["candidates"]

        assert listed["data_description"] == "unknown contents (the disk sleeps)"
        assert (r.status_code, r.json()) == (409, {
            "detail": "/dev/sdb holds ext4 signature, not what the list showed (unknown contents (the disk sleeps)). "
                      "Open the dialog again to see what an add would erase."})
        assert now["data_description"] == "ext4 signature"
        assert pool.adds == [] and audited() == []

    def test_a_partition_is_refused(self, pool):
        """Whole disks only: a partition beside a mounted one would be erased (ADR-0410)."""
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB, pttype="gpt")
        pool.host.add_partition("sdb", "sdb1", fstype="ext4")
        pool.host.add_mount("/mnt/usb", "/dev/sdb1", "ext4")
        pool.host.add_partition("sdb", "sdb2")
        with admin_client() as client:
            r = client.post(DEVICES, json={**add_body("/dev/sdb2", "WD-B", force=True),
                                           "identity": identity("/dev/sdb2", "WD-B", 1_000_000_000)})
        assert (r.status_code, r.json()) == (400, {"detail": "/dev/sdb2 is no whole disk: UNOS adds whole disks to a pool"})
        assert pool.adds == []

    def test_an_add_is_audited_also_when_the_pool_cannot_be_read_afterwards(self, pool, monkeypatch):
        from src.storage import pools

        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)

        def unreadable(name):
            raise RuntimeError("btrfs filesystem show failed: ERROR: cannot read")

        monkeypatch.setattr(pools, "get_pool", unreadable)
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body())
        assert r.status_code == 500
        assert pool.adds == [["btrfs", "device", "add", "/dev/sdb", MOUNTPOINT]]
        assert [(a, ok) for a, *_, ok in audited()] == [("pool_disk_added", True)]

    def test_force_does_not_erase_a_blank_disk_with_minus_f(self, pool):
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body(force=True))
        assert r.status_code == 200, r.text
        assert pool.adds == [["btrfs", "device", "add", "/dev/sdb", MOUNTPOINT]]

    @pytest.mark.parametrize("body, why", [
        (add_body(confirm_device="/dev/sdc"), "confirm_device must be the disk's path, typed again"),
        (add_body(confirm_device=""), "confirm_device must be the disk's path, typed again"),
        ({**add_body(), "identity": identity("/dev/sdc", "WD-B")}, "identity names another disk than device"),
        (add_body("/dev/../sda"), "Invalid device path"),
        (add_body("/dev/sdz", None), "/dev/sdz is not a disk or partition on this system"),
    ])
    def test_a_confirmation_or_a_disk_that_does_not_match_is_refused(self, pool, body, why):
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        with admin_client() as client:
            r = client.post(DEVICES, json=body)
        assert (r.status_code, r.json()) == (400, {"detail": why})
        assert pool.adds == [] and audited() == []

    def test_an_add_without_the_disk_s_identity_is_refused(self, pool):
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        with admin_client() as client:
            r = client.post(DEVICES, json={"device": "/dev/sdb", "confirm_device": "/dev/sdb"})
        assert r.status_code == 422
        assert pool.adds == []

    def test_a_path_that_now_names_another_disk_is_refused(self, pool):
        """Disks change names when they are reconnected (ADR-0035)."""
        pool.host.add_disk("sdb", serial="WD-OTHER", size=4 * TB)
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body())
        assert r.status_code == 409
        assert r.json()["detail"].startswith("/dev/sdb is no longer the disk that was selected")
        assert pool.adds == []

    @pytest.mark.parametrize("name, build, why", [
        ("sdb", lambda h: h.swaps.append("/dev/sdb"), "it is a system device (Active swap)"),
        ("sdb", lambda h: (h.add_partition("sdb", "sdb1", fstype="ext4"), h.add_mount("/mnt/usb", "/dev/sdb1", "ext4")),
         "Mounted at /mnt/usb"),
        ("sdb", lambda h: h.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"]), "it holds the btrfs filesystem of the pool 'media'"),
        ("sdb", lambda h: h.signatures.__setitem__("/dev/sdb", [{"type": "btrfs"}]), "it holds a btrfs filesystem"),
        ("sdb", lambda h: h.add_holder("sdb", "md0", "raid1"), "Used by md0 (raid1)"),
        ("zram0", lambda h: None, "it is a boot or virtual device"),
    ])
    def test_a_disk_of_the_system_another_pool_or_a_mount_is_never_added_also_with_force(self, pool, name, build, why):
        if name == "sdb":
            pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        build(pool.host)
        size = pool.host.node(name)["size"]
        body = {**add_body(f"/dev/{name}", pool.host.node(name)["serial"], force=True)}
        body["identity"]["size"] = size
        with admin_client() as client:
            r = client.post(DEVICES, json=body)
        assert (r.status_code, r.json()) == (409, {"detail": f"Refusing to add /dev/{name}: {why}"})
        assert pool.adds == [] and audited() == []

    def test_a_disk_of_the_pool_itself_is_refused(self, pool):
        """The dev device's case: the pool's only disk is no free disk."""
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body("/dev/sda", "WD-A"))
        assert (r.status_code, r.json()) == (409, {"detail": "/dev/sda is a disk of the pool data already"})
        assert pool.adds == []

    @pytest.mark.parametrize("setup, why", [
        (lambda p: p.host.mounts.clear(), f"Pool data is not mounted at {MOUNTPOINT}"),
        (lambda p: (p.host.mounts.clear(), p.host.mount_pool("data", vfs_options="ro,noatime", fs_options="ro")),
         "Pool data is mounted read-only: no disk can be added or removed"),
        (lambda p: p.fs.update(missing=1),
         "Pool data misses 1 disk(s): UNOS adds or removes a disk only while the pool has all its disks"),
        (lambda p: setattr(p, "scrub", "running"),
         "A scrub of pool data runs: wait for its end or cancel it, then add the disk"),
        (lambda p: setattr(p, "state", "running"),
         "A balance of pool data runs: wait for its end or cancel it, then add the disk"),
        (lambda p: setattr(p, "state", "paused"),
         "A balance of pool data is paused: resume it until it ends, or cancel it, then add the disk"),
        (lambda p: (p.join("sdc"), setattr(p, "removing", "/dev/sdc")),
         "A disk of pool data is being removed: wait for the end of its removal"),
    ])
    def test_refused_beside_a_scrub_a_balance_or_a_removal_and_on_a_pool_that_cannot_change(self, pool, setup, why):
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        setup(pool)
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body())
        assert (r.status_code, r.json()) == (409, {"detail": why})
        assert pool.adds == [] and pool.units == [] and audited() == []

    def test_a_failed_add_answers_btrfs_s_error_and_is_audited_as_failed(self, pool):
        pool.host.add_disk("sdb", serial="WD-B", size=4 * TB)
        pool.add_fails = "ERROR: error adding device '/dev/sdb': Input/output error\n"
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body())

        detail = "btrfs device add failed: ERROR: error adding device '/dev/sdb': Input/output error"
        assert (r.status_code, r.json()) == (500, {"detail": detail})
        assert audited() == [("pool_disk_added", "pool", "data",
                              f"Tried to add /dev/sdb to the pool data; then: {detail}", False)]

    def test_an_nvme_drive_joins_no_pool_with_trim_on_a_board_that_hangs_on_its_trim(self, pool):
        """ADR-0370: the SpacemiT K1 hangs on the TRIM of an NVMe drive."""
        pool.host.files["/proc/device-tree/compatible"] = "spacemit,k1-x\0"
        pool.host.add_disk("nvme0n1", serial="NV-1", size=256 * 10**9, rota=False)
        with admin_client() as client:
            r = client.post(DEVICES, json={**add_body("/dev/nvme0n1", "NV-1"),
                                           "identity": identity("/dev/nvme0n1", "NV-1", 256 * 10**9)})
        assert r.status_code == 409
        assert r.json()["detail"].startswith("Refusing to add /dev/nvme0n1: this board hangs on the TRIM of an NVMe drive")
        assert pool.adds == []

    def test_a_pool_without_trim_gets_its_new_disk_without_a_discard(self, pool):
        pool.host.files["/proc/device-tree/compatible"] = "spacemit,k1-x\0"
        pool.host.add_disk("nvme0n1", serial="NV-1", size=256 * 10**9, rota=False)
        pool.fs["devices"] = ["/dev/nvme0n1"]
        pool.devids = {"/dev/nvme0n1": 1}
        pool.used = {"/dev/nvme0n1": GIB}
        pool.host.mounts.clear()
        pool.host.mount_pool("data")
        with admin_client() as client:
            r = client.post(DEVICES, json=add_body("/dev/sda", "WD-A"))
        assert r.status_code == 200, r.text
        assert pool.adds == [["btrfs", "device", "add", "--nodiscard", "/dev/sda", MOUNTPOINT]]


M = devices.Member


class TestWhatTheDisksLeftMustHold:
    @pytest.mark.parametrize("found, devid, profiles, problem", [
        ([M(1, "/dev/sda", 4 * TB, GIB)], 1, ["single"], "/dev/sda is the only disk of the pool"),
        ([M(1, "/dev/sda", 4 * TB, GIB), M(2, "/dev/sdb", 4 * TB, GIB)], 2, ["raid1"],
         "the pool's raid1 profile needs at least 2 disks, and without /dev/sdb it would keep 1"),
        ([M(n, f"/dev/sd{c}", 4 * TB, GIB) for n, c in enumerate("abc", 1)], 3, ["raid1c3"],
         "the pool's raid1c3 profile needs at least 3 disks, and without /dev/sdc it would keep 2"),
        ([M(n, f"/dev/sd{c}", 4 * TB, GIB) for n, c in enumerate("abcd", 1)], 1, ["raid10"],
         "the pool's raid10 profile needs at least 4 disks, and without /dev/sda it would keep 3"),
        ([M(n, f"/dev/sd{c}", 4 * TB, GIB) for n, c in enumerate("abcd", 1)], 4, ["raid1c4"],
         "the pool's raid1c4 profile needs at least 4 disks, and without /dev/sdd it would keep 3"),
        ([M(1, "/dev/sda", 4 * TB, GIB), M(2, "/dev/sdb", 4 * TB, GIB)], 2, ["raid0"],
         "the pool's raid0 profile needs at least 2 disks, and without /dev/sdb it would keep 1"),
        ([M(n, f"/dev/sd{c}", 4 * TB, GIB) for n, c in enumerate("abc", 1)], 1, ["raid5"],
         "UNOS removes no disk from a pool with the raid5 profile"),
        ([M(n, f"/dev/sd{c}", 4 * TB, GIB) for n, c in enumerate("abc", 1)], 1, ["raid1", "raid6"],
         "UNOS removes no disk from a pool with the raid6 profile"),
        # Room enough.
        ([M(1, "/dev/sda", 4 * TB, GIB), M(2, "/dev/sdb", 4 * TB, TB)], 2, ["single"], None),
        ([M(1, "/dev/sda", 4 * TB, GIB), M(2, "/dev/sdb", 4 * TB, TB)], 2, ["dup", "single"], None),
        ([M(n, f"/dev/sd{c}", 4 * TB, TB) for n, c in enumerate("abc", 1)], 3, ["raid1"], None),
        ([M(n, f"/dev/sd{c}", 4 * TB, TB) for n, c in enumerate("abcd", 1)], 4, ["raid1c3"], None),
    ])
    def test_the_profile(self, found, devid, profiles, problem):
        assert devices.removal_problem(found, devid, profiles) == problem

    @pytest.mark.parametrize("found, devid, profiles", [
        # raid10 spreads each chunk over every disk with room: on one 4 TB and three 1 TB disks it holds
        # 2 TB, not the 4 TB of raid1, and 2.4 TB do not fit.
        ([M(1, "/dev/sda", 4 * TB, 12 * TB // 10)] + [M(n, f"/dev/sd{c}", TB, 9 * TB // 10)
                                                      for n, c in enumerate("bcde", 2)], 5, ["raid10"]),
        # single: the bytes on both disks do not fit on the one left with room for the chunk moved.
        ([M(1, "/dev/sda", TB, TB - GIB), M(2, "/dev/sdb", 4 * TB, GIB)], 2, ["single"]),
        # raid1 on 4, 4 and 1 TB: without a large disk, each chunk's second copy has only the small
        # one, which cannot hold a copy of each of 2 TB of chunks.
        ([M(1, "/dev/sda", 4 * TB, TB), M(2, "/dev/sdb", 4 * TB, TB), M(3, "/dev/sdc", TB, 0)], 1, ["raid1"]),
        # Partly empty chunks count too: a balance frees them.
        ([M(1, "/dev/sda", 2 * TB, 1900 * 10**9), M(2, "/dev/sdb", 2 * TB, 100 * 10**9)], 2, ["single"]),
    ])
    def test_the_space_of_the_disks_left(self, found, devid, profiles):
        problem = devices.removal_problem(found, devid, profiles)
        assert problem is not None and problem.startswith("the other disks cannot hold the pool's chunks")
        assert problem.endswith("A balance frees the space of partly empty chunks; or add a disk first")

    def test_the_first_chunk_moved_needs_room_on_as_many_disks_as_its_profile(self):
        """raid1 on three full 1 TB disks, then a 2 TB disk added (an add starts no balance): the end
        would fit, but the first chunk's two new copies have only the new disk."""
        found = [M(n, f"/dev/sd{c}", TB, TB) for n, c in enumerate("abc", 1)] + [M(4, "/dev/sdd", 2 * TB, 0)]
        assert devices.removal_problem(found, 1, ["raid1"]) == (
            "the first chunk that the removal moves needs 1.0 GiB unallocated on 2 of the other disks, and fewer "
            "have it. A full balance spreads the chunks over the disks; or add a disk first")
        spread = [M(n, f"/dev/sd{c}", TB, TB * 3 // 4) for n, c in enumerate("abc", 1)] + [M(4, "/dev/sdd", 2 * TB, TB // 2)]
        assert devices.removal_problem(spread, 1, ["raid1"]) is None

    def test_the_space_counts_the_chunk_being_moved(self):
        fits = [M(1, "/dev/sda", 2 * TB, 0), M(2, "/dev/sdb", 2 * TB, 2 * TB - devices.RESERVE)]
        assert devices.removal_problem(fits, 2, ["single"]) is None
        full = [M(1, "/dev/sda", 2 * TB, 0), M(2, "/dev/sdb", 2 * TB, 2 * TB - devices.RESERVE + 1)]
        assert devices.removal_problem(full, 2, ["single"]).startswith("the other disks cannot hold")

    @pytest.mark.parametrize("width, sizes, raw", [
        (1, [4 * TB, TB], 5 * TB),
        (2, [4 * TB, TB, TB], 4 * TB),  # raid1: each chunk's second copy on a small disk
        (2, [TB, TB, TB], 3 * TB),
        (4, [4 * TB, TB, TB, TB], 4 * TB),  # raid10 over four disks: the small ones bound it
        (3, [4 * TB, TB, TB], 3 * TB),  # raid0 over three
    ])
    def test_the_raw_capacity_of_chunks_spread_over_disks_of_their_own(self, width, sizes, raw):
        assert devices.raw_capacity(width, sizes) == raw


class TestRemoval:
    @pytest.fixture
    def two(self, pool):
        """The pool data on sda and sdb, single: either can leave."""
        pool.join("sdb", serial="WD-B", used=3 * GIB)
        return pool

    def test_a_disk_s_data_moves_to_the_other_disks_in_a_unit_of_the_host_until_it_leaves(self, two):
        with admin_client() as client:
            listed = client.get(DEVICES).json()["members"]
            r = client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))

            assert [m["problem"] for m in listed] == [None, None]
            assert r.status_code == 200, r.text
            assert r.json() == removal(status="running", devid=2, device="/dev/sdb", left=3 * GIB, total=3 * GIB)
            assert two.removals == [unit(2, "/dev/sdb", 3 * GIB)]
            assert audited() == [("pool_disk_removal_started", "pool", "data",
                                  "Started removing /dev/sdb (devid 2) from the pool data: its data moves to the other "
                                  "disks first", True)]

            two.used["/dev/sdb"] = GIB
            assert client.get(REMOVAL).json() == removal(status="running", devid=2, device="/dev/sdb", left=GIB,
                                                          total=3 * GIB)
            # Nothing else changes the pool meanwhile.
            again = client.post(REMOVAL, json=remove_body(1, "/dev/sda"))
            assert (again.status_code, again.json()) == (409, {
                "detail": "A disk of pool data is being removed: wait for the end of its removal"})

            two.removed()
            assert client.get(REMOVAL).json() == removal(devid=2, device="/dev/sdb", result="finished")
            after = client.get(DEVICES).json()
            pool_after = client.get("/api/v1/storage/pools/data").json()

        assert [m["path"] for m in after["members"]] == ["/dev/sda"]
        assert pool_after["devices"] == ["/dev/sda"]
        # The kernel wiped it: it is offered again.
        assert [c["path"] for c in after["candidates"]] == ["/dev/sdb"]
        assert len(two.removals) == 1

    def test_the_dev_device_s_only_disk_is_refused_and_nothing_runs(self, pool):
        with admin_client() as client:
            r = client.post(REMOVAL, json=remove_body(1, "/dev/sda"))
        assert (r.status_code, r.json()) == (409, {
            "detail": "Refusing to remove /dev/sda: /dev/sda is the only disk of the pool"})
        assert pool.removals == [] and audited() == []

    def test_a_raid1_pool_of_two_disks_keeps_both(self, two):
        two.set_profile("raid1")
        with admin_client() as client:
            listed = client.get(DEVICES).json()["members"]
            r = client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))
        why = "the pool's raid1 profile needs at least 2 disks, and without /dev/sdb it would keep 1"
        assert [m["problem"] for m in listed] == [why.replace("/dev/sdb", "/dev/sda"), why]
        assert (r.status_code, r.json()) == (409, {"detail": f"Refusing to remove /dev/sdb: {why}"})
        assert two.removals == []

    def test_a_raid1_pool_of_three_disks_lets_one_go(self, two):
        two.join("sdc", serial="WD-C")
        two.set_profile("raid1")
        with admin_client() as client:
            r = client.post(REMOVAL, json=remove_body(3, "/dev/sdc"))
        assert r.status_code == 200, r.text
        assert two.removals == [unit(3, "/dev/sdc", 2 * GIB)]

    def test_too_little_space_on_the_disks_left_is_refused(self, two):
        two.used["/dev/sdb"] = 4 * TB - GIB
        with admin_client() as client:
            r = client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))
        assert r.status_code == 409
        assert r.json()["detail"].startswith("Refusing to remove /dev/sdb: the other disks cannot hold the pool's chunks")
        assert two.removals == []

    @pytest.mark.parametrize("body, status, why", [
        (remove_body(2, "/dev/sdb", confirm_device="/dev/sda"), 400, "confirm_device must be the disk's path, typed again"),
        (remove_body(2, "/dev/sda"), 409, "/dev/sda is no longer devid 2 of the pool data: disks can change names when "
                                          "they are reconnected. Reload the list and select the disk again."),
        (remove_body(7, "/dev/sdb"), 409, "/dev/sdb is no longer devid 7 of the pool data: disks can change names when "
                                          "they are reconnected. Reload the list and select the disk again."),
        (remove_body(2, "/dev/../sdb"), 400, "Invalid device path"),
    ])
    def test_a_confirmation_or_a_disk_that_does_not_match_is_refused(self, two, body, status, why):
        with admin_client() as client:
            r = client.post(REMOVAL, json=body)
        assert (r.status_code, r.json()) == (status, {"detail": why})
        assert two.removals == [] and audited() == []

    @pytest.mark.parametrize("setup, why", [
        (lambda p: p.host.mounts.clear(), f"Pool data is not mounted at {MOUNTPOINT}"),
        (lambda p: (p.host.mounts.clear(), p.host.mount_pool("data", vfs_options="rw,noatime", fs_options="ro")),
         "Pool data is mounted read-only: no disk can be added or removed. btrfs made it read-only after an error; "
         "check the kernel log (dmesg)."),
        (lambda p: p.fs.update(missing=1),
         "Pool data misses 1 disk(s): UNOS adds or removes a disk only while the pool has all its disks"),
        (lambda p: setattr(p, "scrub", "running"),
         "A scrub of pool data runs: wait for its end or cancel it, then remove the disk"),
        (lambda p: setattr(p, "state", "running"),
         "A balance of pool data runs: wait for its end or cancel it, then remove the disk"),
        (lambda p: setattr(p, "state", "paused"),
         "A balance of pool data is paused: resume it until it ends, or cancel it, then remove the disk"),
    ])
    def test_refused_beside_a_scrub_or_a_balance_and_on_a_pool_that_cannot_change(self, two, setup, why):
        setup(two)
        with admin_client() as client:
            r = client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))
        assert (r.status_code, r.json()) == (409, {"detail": why})
        assert two.removals == [] and audited() == []

    def test_a_removal_that_the_kernel_refuses_at_once_answers_its_error(self, two):
        two.remove_fails = "ERROR: error removing devid 2: No space left on device\n"
        with admin_client() as client:
            r = client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))
            status = client.get(REMOVAL).json()

        assert (r.status_code, r.json()) == (500, {
            "detail": "Disk removal failed: ERROR: error removing devid 2: No space left on device"})
        assert status == removal(devid=2, device="/dev/sdb", result="failed",
                                 error="ERROR: error removing devid 2: No space left on device")
        assert audited() == []

    def test_a_removal_that_the_kernel_does_not_show_yet_answers_running(self, two):
        two.unseen_removal = True
        with admin_client() as client:
            r = client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))
        assert r.json() == removal(status="running", devid=2, device="/dev/sdb", left=3 * GIB, total=3 * GIB)
        assert devices.current() == [r.json()]

    def test_no_removal_since_the_boot_has_no_result(self, two):
        with admin_client() as client:
            assert client.get(REMOVAL).json() == removal()

    def test_a_removal_that_runs_shows_after_a_restart_of_the_supervisor(self, two):
        """The unit outlives the supervisor's container: the first read finds the disk with the size
        0, and the log the bytes it had at the start."""
        two.host.files[LOG] = f"UNOS removes devid 2 /dev/sdb with {5 * GIB} bytes allocated\n"
        two.removing = "/dev/sdb"
        assert devices.current() == []
        with admin_client() as client:
            r = client.get(REMOVAL)
        assert r.json() == removal(status="running", devid=2, device="/dev/sdb", left=3 * GIB, total=5 * GIB)
        assert devices.current() == [r.json()]
        assert two.removals == []

    def test_a_removal_started_by_hand_shows_without_its_start(self, two):
        two.removing = "/dev/sdb"
        with admin_client() as client:
            assert client.get(REMOVAL).json() == removal(status="running", devid=2, device="/dev/sdb", left=3 * GIB)

    def test_every_command_runs_in_a_worker_thread(self, two):
        with admin_client() as client:
            client.get(DEVICES)
            client.get(REMOVAL)
            client.post(REMOVAL, json=remove_body(2, "/dev/sdb"))
        assert two.on_loop and not any(two.on_loop)


class TestRemovalsForTheEventsSocket:
    def test_the_follow_reads_the_removals_that_run_until_they_end(self, pool, monkeypatch):
        pool.join("sdb", serial="WD-B")
        monkeypatch.setattr(devices, "READ_INTERVAL_SECONDS", 0)

        async def follow():
            devices.follow()  # none seen running: no read
            assert devices._reading is None
            devices.note("data", DiskRemovalStatus(pool="data", status="running"))
            pool.removing = "/dev/sdb"
            devices.follow()
            await devices._reading
            assert devices.current() == [removal(status="running", devid=2, device="/dev/sdb", left=2 * GIB)]
            pool.removed()
            devices.follow()
            await devices._reading
            assert devices.current() == [removal()]
            reading = devices._reading
            devices.follow()  # it no longer runs: nothing is read
            assert devices._reading is reading

        asyncio.run(follow())

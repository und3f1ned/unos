"""Disk-safety guards: host-view disk facts and the pool-creation guard matrix.

All host commands go to the in-memory ``FakeHost`` from test_pool_persistence;
every refusal is checked to happen before mkfs.btrfs is ever run.
"""

from __future__ import annotations

import pytest

from src import host as host_module
from src.host import HostResult
from src.storage import disks, pools
from src.storage.schemas import PartitionInfo, PoolCreate

from .test_pool_persistence import (
    BASE_FSTAB,
    DATA_UUID,
    NEW_UUID,
    admin_client,
    entry,
    install_fake_host,
    marker,
    wait_for_app_starts,
)

MEDIA_UUID = "4b2d0a7f-2345-4d4c-8c1b-9a4d3b2c5d6e"
BACKUP_UUID = "7d4f2c91-4567-4f6e-8e3d-bc6f5d4e7f80"
NVME_ROOT_UUID = "8e5a3da2-5678-4a7f-9f4e-cd7a6e5f8091"


@pytest.fixture
def fake_host(monkeypatch):
    yield install_fake_host(monkeypatch)
    # A mount starts the pool's apps in a background thread; it must finish
    # before the fake host's patches are undone.
    wait_for_app_starts()


@pytest.fixture
def api(fake_host):
    with admin_client() as client:
        yield client


@pytest.fixture
def nas(fake_host):
    """The ArmSoM layout: root on the SD card plus four data disks.

    sda      btrfs pool 'data', not mounted
    sdb      blank
    sdc      gpt, sdc1 ext4 'backup' mounted at /srv/backup
    nvme0n1  blank NVMe (no longer hidden by name)
    """
    fake_host.add_disk("sda", model="WDC WD40EFRX", serial="WD-1")
    fake_host.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake_host.dirs.add("/mnt/pools/data")
    fake_host.add_disk("sdb", model="WDC WD40EFRX", serial="WD-2")
    fake_host.add_disk("sdc", pttype="gpt", size=2_000_398_934_016)
    fake_host.add_partition("sdc", "sdc1", size=2_000_397_885_440, fstype="ext4",
                            label="backup", uuid=BACKUP_UUID)
    fake_host.add_mount("/srv/backup", "/dev/sdc1", "ext4", BACKUP_UUID)
    fake_host.add_disk("nvme0n1", rota=False, size=512_110_190_592, model="Samsung SSD 980")
    return fake_host


@pytest.fixture
def nvme_root(fake_host):
    """A host that boots from NVMe: /boot/efi on p1, / on p2, p3 unused."""
    fake_host.mounts = [m for m in fake_host.mounts if m["target"] != "/"]
    fake_host.add_disk("nvme0n1", rota=False, size=512_110_190_592, pttype="gpt")
    fake_host.add_partition("nvme0n1", "nvme0n1p1", fstype="vfat")
    fake_host.add_partition("nvme0n1", "nvme0n1p2", fstype="ext4", uuid=NVME_ROOT_UUID)
    fake_host.add_partition("nvme0n1", "nvme0n1p3")
    fake_host.add_mount("/boot/efi", "/dev/nvme0n1p1", "vfat")
    fake_host.add_mount("/", "/dev/nvme0n1p2", "ext4", NVME_ROOT_UUID)
    fake_host.files["/proc/cmdline"] = f"root=UUID={NVME_ROOT_UUID} ro quiet"
    fake_host.add_disk("sdb")
    return fake_host


def by_name(disk_list):
    return {disk.name: disk for disk in disk_list}


def create(devices, *, name="tank", raid_level="single", force=False, confirm_name=None,
           identities=()):
    return pools.create_pool(
        PoolCreate(
            name=name,
            devices=devices,
            raid_level=raid_level,
            confirm_name=name if confirm_name is None else confirm_name,
            force=force,
            device_identities=list(identities),
        )
    )


def identity(disk):
    """What the UI sends back for a disk of GET /storage/disks."""
    return {"path": disk.path, "serial": disk.serial, "wwn": disk.wwn, "size": disk.size}


def identities_of(host, *paths):
    """The identities of devices as the host has them: serial and WWN of the disk, device size.

    Read from the fake host's tree, without running a host command; works
    for partitions and hidden devices too.
    """
    result = []
    for path in paths:
        node = host.node(path.removeprefix("/dev/"))
        disk = host.node(node["pkname"]) if node["pkname"] else node
        result.append({"path": path, "serial": disk["serial"], "wwn": disk.get("wwn"),
                       "size": node["size"]})
    return result


def create_forced(host, devices, **kwargs):
    """create() with force, sending the identity of every device as the web interface does."""
    return create(devices, force=True, identities=identities_of(host, *devices), **kwargs)


def ran_mkfs(fake):
    return any(call[0] == "mkfs.btrfs" for call in fake.calls)


# ---------------------------------------------------------------------------
# disk facts (host view)
# ---------------------------------------------------------------------------


class TestListDisks:
    def test_reports_host_facts(self, nas):
        result = by_name(disks.list_disks())

        assert list(result) == ["sda", "sdb", "sdc", "nvme0n1"]  # SD card and zram hidden

        sda = result["sda"]
        assert (sda.fstype, sda.label, sda.pool) == ("btrfs", "data", "data")
        assert (sda.has_data, sda.in_use, sda.is_system) == (True, False, False)
        assert sda.in_use_reason is None
        assert (sda.model, sda.serial, sda.size, sda.rotational) == (
            "WDC WD40EFRX", "WD-1", 4_000_787_030_016, True)

        sdb = result["sdb"]
        assert (sdb.has_data, sdb.in_use, sdb.is_system, sdb.pool) == (False, False, False, None)
        assert ["wipefs", "--no-act", "--json", "/dev/sdb"] in nas.calls

        sdc = result["sdc"]
        assert (sdc.pttype, sdc.has_data, sdc.in_use, sdc.is_system) == ("gpt", True, True, False)
        assert sdc.in_use_reason == "Mounted at /srv/backup"
        assert (sdc.mountpoints, sdc.mountpoint) == (["/srv/backup"], "/srv/backup")
        assert sdc.partitions == [
            PartitionInfo(name="sdc1", path="/dev/sdc1", size=2_000_397_885_440,
                          fstype="ext4", label="backup", mountpoints=["/srv/backup"])
        ]

        nvme = result["nvme0n1"]
        assert (nvme.is_system, nvme.in_use, nvme.has_data, nvme.rotational) == (
            False, False, False, False)

    def test_system_nvme_is_listed_and_flagged(self, nvme_root):
        nvme = by_name(disks.list_disks())["nvme0n1"]
        assert (nvme.is_system, nvme.in_use) == (True, True)
        assert nvme.in_use_reason == "Mounted at /boot/efi"
        assert nvme.mountpoints == ["/boot/efi", "/"]

    def test_hidden_boot_and_virtual_devices(self, fake_host):
        fake_host.add_disk("mtdblock0", size=16_777_216)
        fake_host.add_disk("loop0", size=1_000_000)
        fake_host.add_disk("sdb")
        assert [d.name for d in disks.list_disks()] == ["sdb"]

    def test_root_shown_as_dev_root_is_matched_by_device_number(self, fake_host):
        fake_host.mounts = [m for m in fake_host.mounts if m["target"] != "/"]
        fake_host.files["/proc/cmdline"] = "rootwait"
        fake_host.add_disk("sdd", pttype="dos")
        fake_host.add_partition("sdd", "sdd1", fstype="ext4")
        fake_host.add_mount("/", "/dev/root", "ext4", majmin=fake_host.node("sdd1")["maj:min"])
        sdd = by_name(disks.list_disks())["sdd"]
        assert (sdd.is_system, sdd.in_use_reason) == (True, "Mounted at /")

    def test_boot_device_from_kernel_command_line(self, fake_host):
        # Read-only root on an overlay: only root= names the boot device.
        fake_host.mounts = [m for m in fake_host.mounts if m["target"] != "/"]
        fake_host.add_mount("/", "overlay", "overlay", majmin="0:30")
        fake_host.files["/proc/cmdline"] = "root=PARTUUID=4a3b2c1d-02 overlayroot=tmpfs"
        fake_host.add_disk("sdd", pttype="dos")
        fake_host.add_partition("sdd", "sdd1", fstype="vfat")
        fake_host.add_partition("sdd", "sdd2", fstype="ext4", partuuid="4A3B2C1D-02")
        sdd = by_name(disks.list_disks())["sdd"]
        assert (sdd.is_system, sdd.in_use_reason) == (True, "Host boot device")

    def test_active_swap_partition_is_system(self, fake_host):
        fake_host.add_disk("sde", pttype="gpt")
        fake_host.add_partition("sde", "sde1", fstype="swap")
        fake_host.swaps.append("/dev/sde1")
        sde = by_name(disks.list_disks())["sde"]
        assert (sde.is_system, sde.in_use_reason, sde.mountpoints) == (True, "Active swap", [])

    def test_members_of_a_mounted_multi_device_pool_are_in_use(self, fake_host):
        fake_host.add_disk("sdf")
        fake_host.add_disk("sdg")
        fake_host.add_btrfs("media", MEDIA_UUID, ["/dev/sdf", "/dev/sdg"], profile="raid1")
        fake_host.mount_pool("media")  # the kernel reports only /dev/sdf as the source
        result = by_name(disks.list_disks())
        assert result["sdf"].in_use_reason == "Mounted at /mnt/pools/media"
        assert result["sdg"].mountpoints == []
        assert (result["sdg"].in_use, result["sdg"].in_use_reason) == (True, "Member of pool 'media'")
        assert result["sdg"].pool == "media"

    def test_md_member_is_in_use(self, fake_host):
        fake_host.add_disk("sdh", fstype="linux_raid_member")
        fake_host.add_holder("sdh", "md0", "raid1", fstype="ext4")
        sdh = by_name(disks.list_disks())["sdh"]
        assert (sdh.in_use, sdh.in_use_reason, sdh.has_data) == (True, "Used by md0 (raid1)", True)

    def test_signature_only_wipefs_sees_counts_as_data(self, fake_host):
        fake_host.add_disk("sdb")
        fake_host.signatures["/dev/sdb"] = [{"device": "sdb", "offset": "0x3a3817d5000", "type": "zfs_member"}]
        sdb = by_name(disks.list_disks())["sdb"]
        assert (sdb.has_data, sdb.fstype, sdb.in_use) == (True, None, False)

    def test_probe_failure_counts_as_data(self, fake_host):
        fake_host.add_disk("sdb")
        fake_host.fail[("wipefs",)] = (1, "wipefs: error: /dev/sdb: probing initialization failed")
        assert by_name(disks.list_disks())["sdb"].has_data is True

    @pytest.mark.parametrize(
        ("stdout", "expected"),
        [
            ("", None),
            ('{"signatures": []}', None),
            ('{"signatures": [{"device": "sdb", "offset": "0x200", "type": "gpt"}]}', "gpt signature"),
            ('{"wipefs": [{"type": "LVM2_member"}]}', "LVM2_member signature"),  # table renamed
            ("DEVICE OFFSET TYPE\nsdb 0x0 xfs", "unrecognised signature"),
        ],
    )
    def test_probe_output_parsing(self, monkeypatch, stdout, expected):
        monkeypatch.setattr(
            host_module, "run_on_host", lambda cmd, **kwargs: HostResult(0, stdout, "")
        )
        assert disks._probe_signatures("/dev/sdb") == expected

    def test_lsblk_failure_raises(self, fake_host):
        fake_host.fail[("lsblk",)] = (1, "lsblk: cannot open /sys/dev/block")
        with pytest.raises(RuntimeError, match="lsblk failed"):
            disks.list_disks()

    def test_old_lsblk_without_mountpoints_column(self, nas):
        nas.fail[("lsblk", "-J", "-b", "-o", disks.LSBLK_COLUMNS)] = (1, "lsblk: unknown column: MOUNTPOINTS")
        result = by_name(disks.list_disks())
        assert result["sdc"].in_use_reason == "Mounted at /srv/backup"
        lsblk_calls = [call for call in nas.calls if call[0] == "lsblk"]
        assert [call[-1] for call in lsblk_calls] == [
            disks.LSBLK_COLUMNS, disks.LSBLK_COLUMNS.replace("MOUNTPOINTS", "MOUNTPOINT")]

    def test_unreadable_mount_table_still_uses_lsblk_mountpoints(self, nvme_root):
        nvme_root.fail[("findmnt",)] = (1, "findmnt: failed")
        nvme = by_name(disks.list_disks())["nvme0n1"]
        assert (nvme.is_system, nvme.in_use) == (True, True)


class TestDeviceFacts:
    def test_unknown_device(self, nas):
        with pytest.raises(ValueError, match="not a disk or partition"):
            disks.get_device_facts(["/dev/sdz"])

    def test_disk_and_its_own_partition(self, nas):
        with pytest.raises(ValueError, match="either the whole disk or its partitions"):
            disks.get_device_facts(["/dev/sdc", "/dev/sdc1"])

    def test_partition_facts(self, nas):
        facts = disks.get_device_facts(["/dev/sdc1"])["/dev/sdc1"]
        assert (facts.type, facts.in_use, facts.in_use_reason, facts.has_data) == (
            "part", True, "Mounted at /srv/backup", True)


# ---------------------------------------------------------------------------
# pool creation guard matrix
# ---------------------------------------------------------------------------


class TestCreatePoolGuards:
    def test_success_path_runs_exactly_these_commands(self, nas):
        info = create(["/dev/sdb"])

        assert nas.changes == [
            ["mkfs.btrfs", "-L", "tank", "-d", "single", "-m", "single", "/dev/sdb"],
            ["mkdir", "-p", "/mnt/pools/tank"],
            ["chattr", "+i", "/mnt/pools/tank"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={NEW_UUID}", "/mnt/pools/tank"],
            ["write", "/etc/fstab.unos-backup"],
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
        ]
        assert nas.fstab == BASE_FSTAB + marker("tank") + entry("tank", NEW_UUID)
        assert (info.name, info.uuid, info.devices) == ("tank", NEW_UUID, ["/dev/sdb"])
        assert (info.mounted, info.persistent, info.health) == (True, True, "healthy")
        # Device facts are read twice: the check and the re-check right before mkfs.
        mkfs_index = next(i for i, call in enumerate(nas.calls) if call[0] == "mkfs.btrfs")
        assert sum(call[0] == "lsblk" for call in nas.calls[:mkfs_index]) == 2

    def test_raid1_on_two_blank_disks(self, nas):
        nas.add_disk("sdd")
        create(["/dev/sdb", "/dev/sdd"], raid_level="raid1")
        assert nas.changes[0] == [
            "mkfs.btrfs", "-L", "tank", "-d", "raid1", "-m", "raid1", "/dev/sdb", "/dev/sdd"]

    def test_refuses_system_disk(self, nvme_root):
        with pytest.raises(pools.DeviceSafetyError, match="system device"):
            create_forced(nvme_root, ["/dev/nvme0n1"])
        assert not ran_mkfs(nvme_root)

    def test_refuses_partition_of_system_disk(self, nvme_root):
        with pytest.raises(pools.DeviceSafetyError, match="Partition of the system disk"):
            create_forced(nvme_root, ["/dev/nvme0n1p3"])
        assert not ran_mkfs(nvme_root)

    def test_refuses_disk_with_mounted_partition(self, nas):
        with pytest.raises(pools.DeviceSafetyError, match="Mounted at /srv/backup"):
            create_forced(nas, ["/dev/sdc"])
        assert not ran_mkfs(nas)

    def test_refuses_mounted_partition(self, nas):
        with pytest.raises(pools.DeviceSafetyError, match="Mounted at /srv/backup"):
            create_forced(nas, ["/dev/sdc1"])
        assert not ran_mkfs(nas)

    def test_refuses_member_of_mounted_pool(self, nas):
        nas.add_disk("sdf")
        nas.add_disk("sdg")
        nas.add_btrfs("media", MEDIA_UUID, ["/dev/sdf", "/dev/sdg"], profile="raid1")
        nas.mount_pool("media")
        with pytest.raises(pools.DeviceSafetyError, match="Member of pool 'media'"):
            create_forced(nas, ["/dev/sdb", "/dev/sdg"], raid_level="raid1")
        assert not ran_mkfs(nas)

    def test_existing_data_requires_force(self, nas):
        with pytest.raises(pools.DeviceSafetyError, match=r"contains data \(btrfs 'data'\).*force"):
            create(["/dev/sda"])
        assert not ran_mkfs(nas)

    def test_partition_table_requires_force(self, nas):
        nas.add_disk("sdd", pttype="gpt")
        nas.add_partition("sdd", "sdd1")
        with pytest.raises(pools.DeviceSafetyError, match="gpt partition table with 1 partition"):
            create(["/dev/sdd"])
        assert not ran_mkfs(nas)

    def test_force_overwrites_existing_data_with_f(self, nas):
        create_forced(nas, ["/dev/sda"])
        assert nas.changes[0] == [
            "mkfs.btrfs", "-L", "tank", "-d", "single", "-m", "single", "-f", "/dev/sda"]
        # The old pool lived only on /dev/sda and is gone now.
        assert [p.name for p in pools.list_pools()] == ["tank"]

    def test_force_is_not_passed_to_mkfs_for_blank_disks(self, nas):
        create_forced(nas, ["/dev/sdb"])
        assert "-f" not in nas.changes[0]

    def test_pool_can_be_recreated_on_its_own_disks(self, nas):
        create_forced(nas, ["/dev/sda"], name="data")
        assert nas.changes[0][-2:] == ["-f", "/dev/sda"]

    def test_refuses_name_of_an_existing_pool_elsewhere(self, nas):
        with pytest.raises(pools.PoolConflictError, match="labelled 'data' already exists"):
            create(["/dev/sdb"], name="data")
        assert not ran_mkfs(nas)

    def test_refuses_target_that_is_already_mounted(self, nas):
        nas.add_mount("/mnt/pools/tank", "/dev/nvme0n1", "ext4")
        with pytest.raises(pools.PoolConflictError, match="already mounted at /mnt/pools/tank"):
            create(["/dev/sdb"])
        assert not ran_mkfs(nas)

    def test_refuses_target_with_unmanaged_fstab_entry(self, nas):
        nas.files["/etc/fstab"] = BASE_FSTAB + "/dev/sdx1 /mnt/pools/tank ext4 defaults 0 2\n"
        with pytest.raises(pools.PoolConflictError, match="does not manage"):
            create(["/dev/sdb"])
        assert not ran_mkfs(nas)

    def test_confirm_name_mismatch_runs_nothing(self, nas):
        with pytest.raises(ValueError, match="confirm_name"):
            create(["/dev/sdb"], confirm_name="tnak")
        assert nas.calls == []

    @pytest.mark.parametrize(
        ("devices", "raid_level", "message"),
        [
            (["/dev/sdb", "/dev/sdb"], "raid1", "only be selected once"),
            (["/dev/sdb"], "raid1", "requires at least 2 devices"),
        ],
    )
    def test_invalid_selection_runs_nothing(self, nas, devices, raid_level, message):
        with pytest.raises(ValueError, match=message):
            create(devices, raid_level=raid_level)
        assert nas.calls == []

    def test_unknown_device(self, nas):
        with pytest.raises(ValueError, match="/dev/sdz is not a disk or partition"):
            create(["/dev/sdz"])
        assert not ran_mkfs(nas)

    def test_hidden_virtual_device(self, nas):
        with pytest.raises(ValueError, match="boot or virtual device"):
            create_forced(nas, ["/dev/zram0"])
        assert not ran_mkfs(nas)

    def test_disk_and_its_partition_together(self, nas):
        with pytest.raises(ValueError, match="either the whole disk or its partitions"):
            create_forced(nas, ["/dev/sdc", "/dev/sdc1"], raid_level="raid1")
        assert not ran_mkfs(nas)

    def test_devices_are_checked_again_right_before_mkfs(self, nas, monkeypatch):
        real = disks.get_device_facts
        calls = []

        def racing(paths):
            calls.append(paths)
            if len(calls) == 2:
                # Someone mounts the disk while the first checks were running.
                nas.add_mount("/media/usb", "/dev/sdb", "ext4")
            return real(paths)

        monkeypatch.setattr(disks, "get_device_facts", racing)
        with pytest.raises(pools.DeviceSafetyError, match="Mounted at /media/usb"):
            create(["/dev/sdb"])
        assert len(calls) == 2
        assert not ran_mkfs(nas)

    def test_mkfs_failure_changes_nothing_else(self, nas):
        nas.fail[("mkfs.btrfs",)] = (1, "ERROR: unable to open /dev/sdb: Device or resource busy")
        with pytest.raises(RuntimeError, match="mkfs.btrfs failed"):
            create(["/dev/sdb"])
        assert nas.changes == []
        assert nas.fstab == BASE_FSTAB

    def test_mount_failure_after_mkfs_does_not_persist(self, nas):
        nas.fail[("mount",)] = (32, "mount: /mnt/pools/tank: wrong fs type")
        with pytest.raises(RuntimeError, match="Mounting /mnt/pools/tank failed"):
            create(["/dev/sdb"])
        assert nas.fstab == BASE_FSTAB
        # The new filesystem exists, unmounted, and the mountpoint is guarded.
        [tank] = [p for p in pools.list_pools() if p.name == "tank"]
        assert (tank.mounted, tank.persistent, tank.health) == (False, False, "unmounted")
        assert "/mnt/pools/tank" in nas.immutable

    def test_refuses_a_mountpoint_that_holds_files_before_mkfs(self, nas):
        # A folder under the pools base that holds files, such as app data on the system disk.
        nas.dirs.add("/mnt/pools/default")
        nas.contents["/mnt/pools/default"] = ["transmission"]
        with pytest.raises(pools.PoolConflictError, match=r"/mnt/pools/default is not empty.*transmission"):
            create(["/dev/sdb"], name="default")
        assert not ran_mkfs(nas)
        assert nas.changes == []


# ---------------------------------------------------------------------------
# device identity: /dev/sdX may be another disk than the one the user selected
# ---------------------------------------------------------------------------


class TestDeviceIdentity:
    @pytest.fixture
    def usb(self, nas):
        """sdd: USB disk with data that the user selects and agrees to erase."""
        nas.add_disk("sdd", model="Elements 25A3", serial="WX-OLD", wwn="0x50014ee2b1a2c3d4",
                     pttype="gpt", size=2_000_398_934_016)
        nas.add_partition("sdd", "sdd1", fstype="ntfs", label="Backup")
        return nas

    def listed(self, name):
        return by_name(disks.list_disks())[name]

    def replug_other_disk_as_sdd(self, host):
        """The selected disk dropped off the bus; another USB disk got its name."""
        node = host.node("sdd")
        node.update(serial="WX-NEW", wwn="0x50014ee2b9f8e7d6", model="My Passport")
        node["children"][0].update(fstype="ext4", label="Photos")

    def test_disk_list_exposes_the_identity(self, usb):
        sdd = self.listed("sdd")
        assert (sdd.serial, sdd.wwn, sdd.size) == ("WX-OLD", "0x50014ee2b1a2c3d4", 2_000_398_934_016)
        assert any(call[:4] == ["lsblk", "-J", "-b", "-o"] and ",WWN," in call[4] for call in usb.calls)

    def test_matching_identity_creates_the_pool(self, usb):
        info = create(["/dev/sdd"], force=True, identities=[identity(self.listed("sdd"))])
        assert info.devices == ["/dev/sdd"]
        assert ran_mkfs(usb)

    def test_other_disk_under_the_selected_name_is_never_erased(self, usb):
        selected = identity(self.listed("sdd"))
        self.replug_other_disk_as_sdd(usb)
        with pytest.raises(pools.DeviceSafetyError, match=r"/dev/sdd is no longer the disk.*WX-OLD.*WX-NEW.*Reload"):
            create(["/dev/sdd"], force=True, identities=[selected])
        assert not ran_mkfs(usb)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("serial", "WX-OTHER"), ("serial", None), ("wwn", "0x0"), ("size", 1_000_000)],
    )
    def test_any_identity_difference_is_refused(self, usb, field, value):
        selected = {**identity(self.listed("sdd")), field: value}
        with pytest.raises(pools.DeviceSafetyError, match="no longer the disk"):
            create(["/dev/sdd"], force=True, identities=[selected])
        assert not ran_mkfs(usb)

    def test_disk_without_serial_or_wwn_is_matched_by_size(self, nas):
        nvme = self.listed("nvme0n1")
        assert (nvme.serial, nvme.wwn) == (None, None)
        create(["/dev/nvme0n1"], identities=[identity(nvme)])
        assert ran_mkfs(nas)

    def test_identity_values_are_compared_normalised(self, nas):
        sdb = self.listed("sdb")  # serial WD-2, no WWN
        create(["/dev/sdb"], identities=[{**identity(sdb), "serial": " WD-2 ", "wwn": ""}])
        assert ran_mkfs(nas)

    def test_identity_is_checked_again_right_before_mkfs(self, usb, monkeypatch):
        selected = identity(self.listed("sdd"))
        real = disks.get_device_facts
        calls = []

        def replugged_meanwhile(paths):
            calls.append(paths)
            if len(calls) == 2:
                self.replug_other_disk_as_sdd(usb)
            return real(paths)

        monkeypatch.setattr(disks, "get_device_facts", replugged_meanwhile)
        with pytest.raises(pools.DeviceSafetyError, match="no longer the disk"):
            create(["/dev/sdd"], force=True, identities=[selected])
        assert len(calls) == 2
        assert not ran_mkfs(usb)

    def test_identity_of_a_device_that_is_not_selected_is_invalid(self, usb):
        selected = identity(self.listed("sdd"))
        usb.calls.clear()
        with pytest.raises(ValueError, match="not selected"):
            create(["/dev/sdb"], identities=[selected])
        assert usb.calls == []

    def test_duplicate_identity_is_invalid(self, usb):
        selected = identity(self.listed("sdd"))
        usb.calls.clear()
        with pytest.raises(ValueError, match="more than once"):
            create(["/dev/sdd"], force=True, identities=[selected, selected])
        assert usb.calls == []

    @pytest.mark.parametrize("named", [(), ("/dev/sdd",)], ids=["none", "some"])
    def test_force_requires_the_identity_of_every_device(self, usb, named):
        # A script or a cached old page that erases disks by name alone: the
        # names may belong to other disks by now.
        with pytest.raises(
            ValueError, match="^device_identities must name every selected device when force is set$"
        ):
            create(["/dev/sdb", "/dev/sdd"], raid_level="raid1", force=True,
                   identities=identities_of(usb, *named))
        assert usb.calls == []

    def test_endpoint(self, api, usb):
        selected = identity(self.listed("sdd"))
        self.replug_other_disk_as_sdd(usb)
        response = api.post(
            "/api/v1/storage/pools",
            json=body(["/dev/sdd"], force=True, device_identities=[selected]),
        )
        assert response.status_code == 409
        assert "Reload the disk list" in response.json()["detail"]
        assert not ran_mkfs(usb)

        response = api.post(
            "/api/v1/storage/pools",
            json=body(["/dev/sdd"], force=True, device_identities=[identity(self.listed("sdd"))]),
        )
        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# POST /api/v1/storage/pools
# ---------------------------------------------------------------------------


def body(devices, *, name="tank", **extra):
    return {"name": name, "devices": devices, "raid_level": "single", "confirm_name": name, **extra}


class TestCreatePoolEndpoint:
    def test_creates_pool(self, api, nas):
        response = api.post("/api/v1/storage/pools", json=body(["/dev/sdb"]))
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["mounted"], result["persistent"], result["health"]) == (True, True, "healthy")
        assert result["missing_devices"] == 0 and result["device_errors"] == 0

    def test_system_disk_is_409(self, api, nvme_root):
        response = api.post(
            "/api/v1/storage/pools",
            json=body(["/dev/nvme0n1"], force=True,
                      device_identities=identities_of(nvme_root, "/dev/nvme0n1")),
        )
        assert response.status_code == 409
        assert "system device" in response.json()["detail"]
        assert not ran_mkfs(nvme_root)

    def test_existing_data_needs_force(self, api, nas):
        response = api.post("/api/v1/storage/pools", json=body(["/dev/sda"]))
        assert response.status_code == 409
        assert "force" in response.json()["detail"]
        response = api.post(
            "/api/v1/storage/pools",
            json=body(["/dev/sda"], force=True, device_identities=identities_of(nas, "/dev/sda")),
        )
        assert response.status_code == 200, response.text

    def test_force_without_device_identities_is_400(self, api, nas):
        response = api.post("/api/v1/storage/pools", json=body(["/dev/sda"], force=True))
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "device_identities must name every selected device when force is set"
        )
        assert nas.calls == []

    def test_confirm_name_mismatch_is_400(self, api, nas):
        response = api.post("/api/v1/storage/pools", json=body(["/dev/sdb"], confirm_name="other"))
        assert response.status_code == 400
        assert nas.calls == []

    def test_missing_confirm_name_is_rejected(self, api, nas):
        payload = body(["/dev/sdb"])
        del payload["confirm_name"]
        assert api.post("/api/v1/storage/pools", json=payload).status_code == 422
        assert nas.calls == []

    def test_invalid_device_path_is_400(self, api, nas):
        response = api.post("/api/v1/storage/pools", json=body(["/etc/passwd"]))
        assert response.status_code == 400
        assert nas.calls == []

    def test_disks_endpoint_exposes_safety_fields(self, api, nas):
        response = api.get("/api/v1/storage/disks")
        assert response.status_code == 200
        sdc = next(d for d in response.json() if d["name"] == "sdc")
        assert sdc["in_use"] is True and sdc["in_use_reason"] == "Mounted at /srv/backup"
        assert sdc["partitions"][0]["mountpoints"] == ["/srv/backup"]
        assert {"fstype", "label", "pttype", "has_data", "is_system", "pool", "wwn"} <= set(sdc)


# ---------------------------------------------------------------------------
# GET /storage/disks: the Physical Disks table of the Storage page
# ---------------------------------------------------------------------------


class TestDisksEndpoint:
    """storage.disk-list: every whole disk of the host, with what UNOS knows about each.

    The texts are the ones webui/tests/storage.spec.ts gives its route fakes.
    """

    def test_lists_every_whole_disk_with_model_size_and_serial(self, api, nas):
        response = api.get("/api/v1/storage/disks")

        assert response.status_code == 200, response.text
        # The boot SD card and zram are no disks of the Storage page.
        assert [(d["path"], d["model"], d["size"], d["serial"]) for d in response.json()] == [
            ("/dev/sda", "WDC WD40EFRX", 4_000_787_030_016, "WD-1"),
            ("/dev/sdb", "WDC WD40EFRX", 4_000_787_030_016, "WD-2"),
            ("/dev/sdc", None, 2_000_398_934_016, None),
            ("/dev/nvme0n1", "Samsung SSD 980", 512_110_190_592, None),
        ]

    def test_each_disk_says_what_uses_it_and_which_pool_it_belongs_to(self, api, nvme_root):
        # nvme0n1 holds the host's /boot/efi and /, sdb is blank (nvme_root).
        nvme_root.add_disk("sda", model="WDC WD40EFRX", serial="WD-1")
        nvme_root.add_btrfs("data", DATA_UUID, ["/dev/sda"])
        nvme_root.add_disk("sdc", pttype="gpt")
        nvme_root.add_partition("sdc", "sdc1", fstype="ext4", label="backup", uuid=BACKUP_UUID)
        nvme_root.add_mount("/srv/backup", "/dev/sdc1", "ext4", BACKUP_UUID)
        nvme_root.add_disk("sdd", fstype="exfat", label="CAMERA")
        nvme_root.add_disk("sdf")
        nvme_root.add_disk("sdg")
        nvme_root.add_btrfs("media", MEDIA_UUID, ["/dev/sdf", "/dev/sdg"], profile="raid1")
        nvme_root.mount_pool("media")

        response = api.get("/api/v1/storage/disks")

        assert response.status_code == 200, response.text
        fields = ("is_system", "in_use", "in_use_reason", "has_data", "pool")
        assert {d["name"]: tuple(d[f] for f in fields) for d in response.json()} == {
            "nvme0n1": (True, True, "Mounted at /boot/efi", True, None),
            "sdb": (False, False, None, False, None),
            # A pool that is not mounted: its disk holds data and is free to mount.
            "sda": (False, False, None, True, "data"),
            "sdc": (False, True, "Mounted at /srv/backup", True, None),
            "sdd": (False, False, None, True, None),
            # The members of a mounted pool.
            "sdf": (False, True, "Mounted at /mnt/pools/media", True, "media"),
            "sdg": (False, True, "Member of pool 'media'", True, "media"),
        }

    def test_a_disk_list_that_cannot_be_read_answers_500_with_the_reason(self, api, nas):
        nas.fail[("lsblk",)] = (1, "lsblk: failed to access sysfs directory: /sys/dev/block")

        response = api.get("/api/v1/storage/disks")

        # The Storage page shows the reason instead of "No disks found" (ADR-0271).
        assert (response.status_code, response.json()) == (
            500, {"detail": "lsblk failed: lsblk: failed to access sysfs directory: /sys/dev/block"})
        assert not any(call[0] == "wipefs" for call in nas.calls)

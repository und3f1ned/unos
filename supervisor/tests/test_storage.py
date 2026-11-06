import asyncio
import contextlib
import importlib
import json
import os
import signal
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.notifications.schemas import (
    EmailConfig,
    NotificationChannel,
    NotificationConfig,
    TelegramConfig,
)
from src.security.validators import (
    validate_device_list,
    validate_disk_name,
    validate_pool_name,
)
from src.storage import disks, pools
from src.storage.schemas import (
    DiskInfo,
    PoolCreate,
    PoolInfo,
    SmartData,
    VolumeCreate,
    VolumeInfo,
)

from .test_pool_persistence import (
    BASE_FSTAB,
    DATA_UUID,
    MEDIA_UUID,
    admin_client,
    entry,
    install_fake_host,
    marker,
    wait_for_app_starts,
)

health_router = importlib.import_module("src.health.router")


@pytest.fixture
def fake_host(monkeypatch):
    """Host commands are answered by the in-memory FakeHost (see test_pool_persistence)."""
    yield install_fake_host(monkeypatch)
    # A mount starts the pool's apps in a background thread; it must finish
    # before the fake host's patches are undone.
    wait_for_app_starts()


class TestDisksModule:
    """Tests for disk management (host view through src.host)."""

    @pytest.fixture
    def host_disks(self, fake_host):
        """sda: SSD whose partition sda1 is the host's root filesystem; sdb: HDD."""
        fake_host.mounts = [m for m in fake_host.mounts if m["target"] != "/"]
        fake_host.add_disk("sda", size=500107862016, rota=False, model="Samsung SSD 860",
                           serial="S3Z2NB0K123456", pttype="gpt")
        fake_host.add_partition("sda", "sda1", size=500106813440, fstype="ext4")
        fake_host.add_mount("/", "/dev/sda1", "ext4")
        fake_host.add_disk("sdb", size=4000787030016, rota=True, model="WDC WD40EFRX",
                           serial="WD-WCC4E0123456")
        return fake_host

    def test_list_disks_success(self, host_disks):
        """Test listing disks successfully."""
        result = disks.list_disks()

        # Only whole disks; the SD card and zram are boot/virtual devices
        assert [d.name for d in result] == ["sda", "sdb"]
        assert all(isinstance(d, DiskInfo) for d in result)

    def test_list_disks_returns_disk_info(self, host_disks):
        """Test that list_disks returns correct disk information."""
        result = disks.list_disks()

        sda = next(d for d in result if d.name == "sda")
        assert sda.path == "/dev/sda"
        assert sda.model == "Samsung SSD 860"
        assert sda.serial == "S3Z2NB0K123456"
        assert sda.size == 500107862016
        assert sda.rotational is False

    def test_list_disks_filters_partitions(self, host_disks):
        """Test that partitions are reported on their disk, not as disks."""
        result = disks.list_disks()

        disk_names = [d.name for d in result]
        assert "sda1" not in disk_names
        sda = next(d for d in result if d.name == "sda")
        assert [p.name for p in sda.partitions] == ["sda1"]

    def test_list_disks_flags_system_disk(self, host_disks):
        """The disk holding the host's root filesystem is listed as a system disk."""
        result = {d.name: d for d in disks.list_disks()}

        assert result["sda"].is_system is True
        assert result["sda"].in_use_reason == "Mounted at /"
        assert result["sdb"].is_system is False
        assert result["sdb"].in_use is False

    def test_list_disks_lsblk_failure(self, fake_host):
        """Test that RuntimeError is raised when lsblk fails."""
        fake_host.fail[("lsblk",)] = (1, "lsblk: command not found")

        with pytest.raises(RuntimeError, match="lsblk failed"):
            disks.list_disks()

    def test_list_disks_identifies_ssd(self, host_disks):
        """Test that SSDs are identified correctly (rotational=False)."""
        result = disks.list_disks()

        sda = next(d for d in result if d.name == "sda")
        sdb = next(d for d in result if d.name == "sdb")
        assert sda.rotational is False  # SSD
        assert sdb.rotational is True   # HDD

    def test_list_disks_accepts_legacy_lsblk_types(self, fake_host):
        """lsblk < 2.33 prints numbers and booleans as strings."""
        node = fake_host.add_disk("sdb")
        node.update(size="4000787030016", rota="0")

        [sdb] = disks.list_disks()
        assert (sdb.size, sdb.rotational) == (4000787030016, False)


class TestSmartData:
    """Tests for SMART data retrieval."""

    @pytest.fixture
    def mock_smartctl_output(self):
        """Mock smartctl JSON output."""
        return {
            "smart_status": {"passed": True},
            "temperature": {"current": 35},
            "ata_smart_attributes": {
                "table": [
                    {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
                    {"id": 9, "name": "Power_On_Hours", "raw": {"value": 12345}},
                    {"id": 194, "name": "Temperature_Celsius", "raw": {"value": 35}}
                ]
            }
        }

    def test_get_smart_data_success(self, mock_smartctl_output):
        """Test getting SMART data successfully."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_smartctl_output)

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.get_smart_data("/dev/sda")

        assert isinstance(result, SmartData)
        assert result.device == "/dev/sda"
        assert result.healthy is True
        assert result.temperature == 35
        assert result.power_on_hours == 12345
        assert result.reallocated_sectors == 0

    def test_get_smart_data_unhealthy_disk(self):
        """Test SMART data for unhealthy disk."""
        smart_output = {
            "smart_status": {"passed": False},
            "temperature": {"current": 55}
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(smart_output)

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.get_smart_data("/dev/sda")

        assert result.healthy is False

    def test_get_smart_data_invalid_json(self):
        """Test handling of invalid JSON from smartctl."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "Not valid JSON"
        mock_result.stderr = "smartctl failed"

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.get_smart_data("/dev/sda")

        assert result.healthy is False
        assert "error" in result.raw_data


class TestPoolsModule:
    """Tests for btrfs pool management."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings."""
        mock = MagicMock()
        mock.pools_base_path = "/mnt/pools"
        return mock

    def test_pool_create_schema(self):
        """Test PoolCreate schema."""
        pool = PoolCreate(
            name="datapool",
            devices=["/dev/sda", "/dev/sdb"],
            raid_level="raid1",
            confirm_name="datapool",
        )

        assert pool.name == "datapool"
        assert pool.devices == ["/dev/sda", "/dev/sdb"]
        assert pool.raid_level == "raid1"
        assert pool.force is False

    def test_pool_create_default_raid(self):
        """Test PoolCreate default raid level."""
        pool = PoolCreate(
            name="datapool",
            devices=["/dev/sda"],
            confirm_name="datapool",
        )

        assert pool.raid_level == "single"

    def test_pool_info_schema(self):
        """Test PoolInfo schema."""
        pool = PoolInfo(
            name="datapool",
            uuid="12345-67890",
            devices=["/dev/sda", "/dev/sdb"],
            total_size=4000000000000,
            used_size=1000000000000,
            mountpoint="/mnt/pools/datapool",
            raid_level="raid1"
        )
        
        assert pool.name == "datapool"
        assert pool.uuid == "12345-67890"
        assert len(pool.devices) == 2
        assert pool.total_size == 4000000000000
        # Never "healthy" unless the health check said so
        assert (pool.health, pool.mounted, pool.persistent) == ("unknown", False, False)

    def test_volume_create_schema(self):
        """VolumeCreate is the request body; the pool is in the URL (see test_volumes.py)."""
        volume = VolumeCreate(
            name="documents",
            quota=1073741824  # 1GB
        )

        assert volume.name == "documents"
        assert volume.quota == 1073741824
        assert "pool_name" not in VolumeCreate.model_fields

    def test_volume_create_no_quota(self):
        """Test VolumeCreate without quota."""
        volume = VolumeCreate(name="documents")

        assert volume.quota is None

    def test_volume_create_quota_fits_in_64_bits(self):
        """btrfs stores a quota group limit as an unsigned 64-bit number; 2^64 - 1 means no limit."""
        from pydantic import ValidationError

        assert VolumeCreate(name="documents", quota=(1 << 64) - 2).quota == (1 << 64) - 2
        for quota in ((1 << 64) - 1, 1 << 64, 10**30):
            with pytest.raises(ValidationError):
                VolumeCreate(name="documents", quota=quota)

    @pytest.mark.parametrize("quota", [2**64 - 1, 2**64])
    def test_volume_quota_without_a_limit_in_btrfs_is_refused(self, fake_host, quota):
        with admin_client() as api:
            response = api.post("/api/v1/storage/pools/data/volumes", json={"name": "documents", "quota": quota})

        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["loc"] == ["body", "quota"]
        assert fake_host.calls == []

    def test_volume_info_schema(self):
        """Test VolumeInfo schema."""
        volume = VolumeInfo(
            name="documents",
            pool="datapool",
            path="/mnt/pools/datapool/documents",
            quota=1073741824
        )
        
        assert volume.name == "documents"
        assert volume.pool == "datapool"

    def test_create_pool_validates_raid_level(self):
        """Test that invalid raid levels are rejected at schema level."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PoolCreate(
                name="testpool",
                devices=["/dev/sda"],
                raid_level="invalid",
                confirm_name="testpool",
            )

    def test_raid5_rejected_as_unsafe(self):
        """Test that RAID5 is rejected as unsafe (write hole bug)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PoolCreate(name="test", devices=["/dev/sda"], raid_level="raid5", confirm_name="test")

    def test_raid6_rejected_as_unsafe(self):
        """Test that RAID6 is rejected as unsafe (write hole bug)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PoolCreate(name="test", devices=["/dev/sda"], raid_level="raid6", confirm_name="test")

    @patch("src.storage.pools.settings")
    def test_create_pool_rejects_insufficient_devices_raid1c3(self, mock_settings):
        """Test that raid1c3 requires at least 3 devices."""
        mock_settings.pools_base_path = "/mnt/pools"

        pool = PoolCreate(
            name="testpool",
            devices=["/dev/sda", "/dev/sdb"],
            raid_level="raid1c3",
            confirm_name="testpool",
        )

        with pytest.raises(ValueError, match="requires at least 3 devices"):
            pools.create_pool(pool)

    @patch("src.storage.pools.settings")
    def test_create_pool_rejects_insufficient_devices_raid1c4(self, mock_settings):
        """Test that raid1c4 requires at least 4 devices."""
        mock_settings.pools_base_path = "/mnt/pools"

        pool = PoolCreate(
            name="testpool",
            devices=["/dev/sda", "/dev/sdb", "/dev/sdc"],
            raid_level="raid1c4",
            confirm_name="testpool",
        )

        with pytest.raises(ValueError, match="requires at least 4 devices"):
            pools.create_pool(pool)

    @patch("src.storage.pools.settings")
    def test_create_pool_rejects_insufficient_devices_raid10(self, mock_settings):
        """Test that raid10 requires at least 4 devices."""
        mock_settings.pools_base_path = "/mnt/pools"

        pool = PoolCreate(
            name="testpool",
            devices=["/dev/sda", "/dev/sdb", "/dev/sdc"],
            raid_level="raid10",
            confirm_name="testpool",
        )

        with pytest.raises(ValueError, match="requires at least 4 devices"):
            pools.create_pool(pool)

    def test_create_pool_requires_matching_confirm_name(self):
        """The typed confirmation must match before anything else happens."""
        pool = PoolCreate(name="testpool", devices=["/dev/sda"], confirm_name="testpoo")

        with pytest.raises(ValueError, match="confirm_name"):
            pools.create_pool(pool)

    def test_list_pools_parses_btrfs_show(self, fake_host):
        """Test listing pools from the host's btrfs filesystem show output."""
        fake_host.add_disk("sda")
        fake_host.add_disk("sdb")
        fake_host.add_btrfs("datapool", DATA_UUID, ["/dev/sda", "/dev/sdb"], profile="raid1")
        fake_host.mount_pool("datapool")

        [pool] = pools.list_pools()

        assert pool.name == "datapool"
        assert pool.uuid == DATA_UUID
        assert pool.devices == ["/dev/sda", "/dev/sdb"]
        assert pool.mountpoint == "/mnt/pools/datapool"
        assert pool.raid_level == "raid1"
        assert pool.total_size == 2 * 4_000_787_030_016
        assert pool.health == "healthy"

    def test_a_btrfs_label_that_is_not_utf8_hides_no_pool(self, fake_host):
        # btrfs filesystem show prints a label as it is. Read as strict UTF-8,
        # the pools could not be listed (400) while a disk with a btrfs
        # filesystem formatted elsewhere, labelled "caf\xe9" in Latin-1, was
        # connected.
        fake_host.add_disk("sda")
        fake_host.add_disk("sdb")
        fake_host.add_btrfs("datapool", DATA_UUID, ["/dev/sda"])
        fake_host.mount_pool("datapool")
        fake_host.add_btrfs(b"caf\xe9".decode("utf-8", "surrogateescape"), MEDIA_UUID, ["/dev/sdb"])

        # Not a valid pool name, so not a pool.
        assert [pool.name for pool in pools.list_pools()] == ["datapool"]
        with admin_client() as client:
            response = client.get("/api/v1/storage/pools")
        assert response.status_code == 200, response.text
        assert [pool["name"] for pool in response.json()] == ["datapool"]

    @pytest.mark.parametrize(
        ("vfs_options", "fs_options", "errors", "expected"),
        [
            ("rw,noatime", "rw,space_cache=v2", 0, ("healthy", False, False)),
            # btrfs made the filesystem read-only after an error (an aborted transaction)
            ("rw,noatime", "ro,space_cache=v2", 0, ("error", True, True)),
            # mounted with "-o ro" on purpose
            ("ro,noatime", "ro,space_cache=v2", 0, ("healthy", True, False)),
            # on purpose, and the error counters moved: an error, but btrfs forced nothing
            ("ro,noatime", "ro,space_cache=v2", 3, ("error", True, False)),
        ],
    )
    def test_list_pools_tells_a_forced_read_only_filesystem_from_a_read_only_mount(
        self, fake_host, vfs_options, fs_options, errors, expected
    ):
        fake_host.add_disk("sda")
        fake_host.add_btrfs("datapool", DATA_UUID, ["/dev/sda"])
        fake_host.mount_pool("datapool", vfs_options=vfs_options, fs_options=fs_options)
        fake_host.device_errors["/mnt/pools/datapool"] = errors

        [pool] = pools.list_pools()

        assert (pool.health, pool.read_only, pool.forced_read_only) == expected
        assert pool.device_errors == errors

    def test_pool_endpoints_report_a_forced_read_only_filesystem(self, fake_host):
        fake_host.add_disk("sda")
        fake_host.add_btrfs("datapool", DATA_UUID, ["/dev/sda"])
        fake_host.mount_pool("datapool", fs_options="ro,space_cache=v2")

        with admin_client() as api:
            [listed] = api.get("/api/v1/storage/pools").json()
            single = api.get("/api/v1/storage/pools/datapool").json()

        for pool in (listed, single):
            assert (pool["health"], pool["read_only"], pool["forced_read_only"]) == ("error", True, True)

    def test_delete_pool_unmounts(self, fake_host):
        """delete_pool unmounts on the host and forgets the fstab entry; data stays."""
        fake_host.add_disk("sda")
        fake_host.add_btrfs("testpool", DATA_UUID, ["/dev/sda"])
        fake_host.mount_pool("testpool")
        fake_host.files["/etc/fstab"] = BASE_FSTAB + marker("testpool") + entry("testpool", DATA_UUID)

        pools.delete_pool("testpool")

        assert ["umount", "/mnt/pools/testpool"] in fake_host.changes
        assert fake_host.fstab == BASE_FSTAB
        assert not any(call[0] in ("mkfs.btrfs", "wipefs") for call in fake_host.changes)
        assert [p.name for p in pools.list_pools()] == ["testpool"]


class TestStorageSchemas:
    """Tests for storage schemas validation."""

    def test_disk_info_required_fields(self):
        """Test DiskInfo requires name, path, size, rotational, type."""
        from pydantic import ValidationError
        
        # Should work with all required fields
        disk = DiskInfo(
            name="sda",
            path="/dev/sda",
            size=1000000000,
            rotational=True,
            type="disk"
        )
        assert disk.name == "sda"
        
        # Should fail without required field
        with pytest.raises(ValidationError):
            DiskInfo(name="sda", path="/dev/sda", size=1000000000, rotational=True)

    def test_smart_data_required_fields(self):
        """Test SmartData requires device and healthy."""
        smart = SmartData(device="/dev/sda", healthy=True)
        assert smart.device == "/dev/sda"
        assert smart.healthy is True

    def test_pool_create_requires_name_and_devices(self):
        """Test PoolCreate requires name, devices and the typed confirmation."""
        from pydantic import ValidationError

        # Should work with required fields
        pool = PoolCreate(name="pool1", devices=["/dev/sda"], confirm_name="pool1")
        assert pool.name == "pool1"

        # Should fail without devices
        with pytest.raises(ValidationError):
            PoolCreate(name="pool1", confirm_name="pool1")

        # Should fail without confirm_name
        with pytest.raises(ValidationError):
            PoolCreate(name="pool1", devices=["/dev/sda"])

    def test_raid_levels_supported(self):
        """Test supported (safe) RAID levels in schema."""
        for raid in ["single", "raid0", "raid1", "raid1c3", "raid1c4", "raid10"]:
            pool = PoolCreate(name="test", devices=["/dev/sda"], raid_level=raid, confirm_name="test")
            assert pool.raid_level == raid

    def test_unsafe_raid_levels_rejected(self):
        """Test that RAID5/6 are rejected at schema level (write hole bug)."""
        from pydantic import ValidationError

        for raid in ["raid5", "raid6"]:
            with pytest.raises(ValidationError):
                PoolCreate(name="test", devices=["/dev/sda"], raid_level=raid, confirm_name="test")


class TestStorageSecurityValidation:
    """Tests for storage security validation."""

    def test_disk_name_path_traversal_rejected(self):
        """Test that disk names with path traversal are rejected."""
        malicious_names = [
            "../etc/passwd",
            "sda/../../../etc/passwd",
            "..%2f..%2fetc",
        ]
        for name in malicious_names:
            with pytest.raises(HTTPException) as exc:
                validate_disk_name(name)
            assert exc.value.status_code == 400

    def test_disk_name_valid_formats(self):
        """Test valid disk name formats are accepted."""
        valid_names = ["sda", "sdb1", "nvme0n1", "nvme0n1p1", "vda"]
        for name in valid_names:
            result = validate_disk_name(name)
            assert result == name

    def test_pool_name_injection_rejected(self):
        """Test pool names with injection attempts are rejected."""
        malicious_names = [
            "pool;rm -rf /",
            "pool|cat /etc/passwd",
            "../etc",
            "pool/../secret",
        ]
        for name in malicious_names:
            with pytest.raises(HTTPException) as exc:
                validate_pool_name(name)
            assert exc.value.status_code == 400

    def test_pool_name_valid_formats(self):
        """Test valid pool name formats are accepted."""
        valid_names = ["datapool", "my_pool", "pool-01", "Pool123"]
        for name in valid_names:
            result = validate_pool_name(name)
            assert result == name

    def test_device_list_validation(self):
        """Test device list validation."""
        # Valid devices
        valid = ["/dev/sda", "/dev/sdb"]
        result = validate_device_list(valid)
        assert result == valid

        # Invalid device in list
        with pytest.raises(HTTPException):
            validate_device_list(["/dev/sda", "/etc/passwd"])

        # Empty list
        with pytest.raises(HTTPException):
            validate_device_list([])

    @patch("src.storage.pools.settings")
    @patch("src.storage.pools.subprocess.run")
    def test_create_pool_validates_devices(self, mock_run, mock_settings):
        """Test that create_pool validates device paths."""
        mock_settings.pools_base_path = "/mnt/pools"

        pool = PoolCreate(
            name="testpool",
            devices=["/etc/passwd"],  # Invalid device path
            raid_level="single",
            confirm_name="testpool",
        )

        with pytest.raises(HTTPException) as exc:
            pools.create_pool(pool)
        assert exc.value.status_code == 400
        assert "device" in exc.value.detail.lower() or "/dev/" in exc.value.detail


def _on_event_loop() -> bool:
    """Whether the calling thread is running an asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


SMART_JSON = json.dumps({
    "serial_number": "WD-1",
    "smart_status": {"passed": True},
    "ata_smart_attributes": {"table": [{"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0}}]},
})


class TestSmartOffTheEventLoop:
    """smartctl and SMTP block for seconds: never on the event loop (API, WebSocket)."""

    @pytest.fixture
    def recorded_run(self, monkeypatch):
        """subprocess.run stand-in: one data disk sda; records (program, on_event_loop, timeout).

        Host commands (src.host.run_on_host) are recorded by the program run
        through nsenter.
        """
        calls = []

        def run(cmd, **kwargs):
            if cmd[0] == "nsenter":
                cmd = cmd[cmd.index("--") + 1:]
            calls.append((cmd[0], _on_event_loop(), kwargs.get("timeout")))
            if cmd[0] == "lsblk":
                devices = {"blockdevices": [{"name": "sda", "type": "disk", "size": "4T"}]}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(devices), "")
            return subprocess.CompletedProcess(cmd, 0, SMART_JSON, "")

        monkeypatch.setattr(subprocess, "run", run)
        return calls

    async def test_monitor_round_runs_in_a_worker_thread(self, recorded_run, monkeypatch, tmp_path):
        monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart.json"))

        async def stop_after_one_round(_seconds):
            health_router._smart_monitor_running = False

        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
        monkeypatch.setattr(health_router, "_smart_monitor_running", True)
        await health_router.smart_monitor_loop()

        assert [(prog, on_loop) for prog, on_loop, _ in recorded_run] == [
            ("lsblk", False), ("smartctl", False)]
        assert all(timeout for _, _, timeout in recorded_run)
        assert "WD-1" in json.loads((tmp_path / "smart.json").read_text())

    def test_snapshot_leaves_sleeping_disks_asleep(self, monkeypatch):
        commands = []

        def run(cmd, **kwargs):
            commands.append(cmd)
            # smartctl -n standby: exit status bit 1 when the disk sleeps
            return subprocess.CompletedProcess(cmd, 2, '{"power_mode": "STANDBY"}', "")

        monkeypatch.setattr(subprocess, "run", run)
        assert health_router.collect_smart_snapshot("/dev/sda") is None
        assert commands == [["smartctl", "-n", "standby", "-j", "-a", "/dev/sda"]]

    def test_snapshot_of_a_failing_disk_is_kept(self, monkeypatch):
        # Exit status bit 3: "SMART status check returned DISK FAILING".
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 8, SMART_JSON, "")
        )
        snapshot = health_router.collect_smart_snapshot("/dev/sda")
        assert snapshot is not None and snapshot.serial == "WD-1"

    def test_storage_smart_endpoint(self, recorded_run):
        with admin_client() as api:
            response = api.get("/api/v1/storage/disks/sda/smart")
        assert response.status_code == 200, response.text
        assert response.json()["healthy"] is True
        # First the host's disks (only whole disks get SMART queries), then smartctl.
        assert recorded_run == [("lsblk", False, 30), ("smartctl", False, disks.SMARTCTL_TIMEOUT)]

    def test_storage_smart_endpoint_times_out(self, recorded_run, monkeypatch):
        answering = subprocess.run

        def hanging_smartctl(cmd, **kwargs):
            if cmd[0] == "smartctl":
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            return answering(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "run", hanging_smartctl)
        with admin_client() as api:
            response = api.get("/api/v1/storage/disks/sda/smart")
        assert response.status_code == 200
        body = response.json()
        assert body["healthy"] is False
        assert "did not answer" in body["raw_data"]["error"]

    @pytest.mark.parametrize("url", ["/api/v1/health/disks", "/api/v1/health/overview",
                                     "/api/v1/health/disk/sda"])
    def test_health_smart_endpoints_run_in_a_worker_thread(self, recorded_run, url):
        with admin_client() as api:
            response = api.get(url)
        assert response.status_code == 200, response.text
        assert recorded_run and not any(on_loop for _, on_loop, _ in recorded_run)


class TestScrubOffTheEventLoop:
    """btrfs scrub can block on a busy or hung disk: the API runs it in a worker
    thread, with a timeout, never on the event loop."""

    @pytest.fixture
    def scrub_run(self, monkeypatch):
        calls = []

        def run(cmd, **kwargs):
            calls.append((cmd[:3], _on_event_loop(), kwargs.get("timeout")))
            if cmd[:3] == ["btrfs", "scrub", "status"]:
                out = "Scrub started:    Thu Nov  6 07:00:00 2026\nStatus:           running\nDuration:         0:00:05\n"
                return subprocess.CompletedProcess(cmd, 0, out, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(subprocess, "run", run)
        return calls

    @pytest.mark.parametrize("method, first", [("post", "start"), ("get", "status"), ("delete", "cancel")])
    def test_the_scrub_endpoints_run_btrfs_in_a_worker_thread(self, scrub_run, method, first):
        with admin_client() as client:
            r = getattr(client, method)("/api/v1/storage/pools/data/scrub")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "running"
        assert scrub_run[0][0] == ["btrfs", "scrub", first]
        assert [(on_loop, timeout) for _, on_loop, timeout in scrub_run] == [
            (False, pools.SCRUB_COMMAND_TIMEOUT)] * len(scrub_run)

    def test_a_failed_start_is_still_500(self, monkeypatch):
        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: not a btrfs filesystem")

        monkeypatch.setattr(subprocess, "run", run)
        with admin_client() as client:
            r = client.post("/api/v1/storage/pools/data/scrub")
        assert r.status_code == 500
        assert "Scrub start failed" in r.json()["detail"]

    def test_an_invalid_pool_name_is_still_400(self, scrub_run):
        with admin_client() as client:
            r = client.get("/api/v1/storage/pools/bad name/scrub")
        assert r.status_code == 400
        assert scrub_run == []


# What a fake btrfs does. Without -B, btrfs-progs forks the process that runs
# the scrub and returns at once; that process keeps the standard output and
# error of btrfs open (it closes none of them) until the scrub ends, hours
# later on a large pool.
FAKE_BTRFS = {
    "running": (
        'case "$2" in\n'
        '  start) sleep 300 & echo $! > "$0.pid"; echo "scrub started on $3, fsid 4d1c2b3a (pid=$!)";;\n'
        '  status) printf "Scrub started:    Thu Nov  6 07:00:00 2026\\nStatus:           running\\n";;\n'
        "esac\n"
    ),
    "refused": 'echo "ERROR: not a btrfs filesystem: $3" >&2\nexit 1\n',
    "hanging": "exec sleep 300\n",
}


class TestScrubStart:
    """Start Scrub on the Storage page answers once btrfs has started the scrub.

    It read the output of btrfs through pipes up to their end, which the
    process of the scrub holds: in 1.0.0 the request (on the event loop, the
    whole API with it) waited for the whole scrub, and with the timeout it
    answered 500 after it while the scrub ran.
    """

    @pytest.mark.parametrize("btrfs", FAKE_BTRFS)
    def test_start_answers_when_btrfs_returns(self, tmp_path, monkeypatch, btrfs):
        monkeypatch.setattr(pools, "SCRUB_COMMAND_TIMEOUT", 5)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "btrfs").write_text("#!/bin/sh\n" + FAKE_BTRFS[btrfs])
        (bin_dir / "btrfs").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        scrub = bin_dir / "btrfs.pid"
        try:
            with admin_client() as client:
                r = client.post("/api/v1/storage/pools/data/scrub")

            if btrfs == "running":
                assert r.status_code == 200, r.text
                assert r.json()["status"] == "running"
                os.kill(int(scrub.read_text()), 0)  # still running: the request did not wait for it
            elif btrfs == "refused":
                assert r.status_code == 500
                assert r.json()["detail"].startswith("Scrub start failed: ERROR: not a btrfs filesystem")
            else:
                assert (r.status_code, r.json()["detail"]) == (
                    500, "btrfs scrub start did not answer within 5 seconds: pool data may not be responding")
        finally:
            if scrub.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(scrub.read_text()), signal.SIGKILL)


class TestAlertsOffTheEventLoop:
    @pytest.fixture
    def mail_channel(self, monkeypatch):
        config = NotificationConfig(
            channel=NotificationChannel.EMAIL,
            email_config=EmailConfig(smtp_host="mail.example", smtp_user="u", smtp_password="p",
                                     from_address="nas@example", to_addresses=["me@example"]),
        )
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: [config])
        return config

    async def test_email_is_sent_from_the_alert_threads(self, mail_channel, monkeypatch):
        main_loop = asyncio.get_running_loop()
        seen = []

        async def fake_send_email(config, event):
            seen.append((asyncio.get_running_loop() is main_loop,
                         threading.current_thread().name, event.title))
            return True, None

        monkeypatch.setattr(health_router, "send_email", fake_send_email)
        await health_router.send_raid_alert("data", "degraded", 0, "1 device(s) missing.")

        [(on_main_loop, thread, title)] = seen
        assert not on_main_loop
        assert thread.startswith("unos-alerts")
        assert title == "RAID Pool 'data' DEGRADED"

    async def test_unresponsive_mail_server_neither_blocks_nor_hangs(self, mail_channel, monkeypatch,
                                                                      caplog):
        release = threading.Event()

        async def hanging_send_email(config, event):
            release.wait(5)  # smtplib without a timeout, blocking its thread
            return True, None

        monkeypatch.setattr(health_router, "send_email", hanging_send_email)
        monkeypatch.setattr(health_router, "ALERT_TIMEOUT", 0.2)
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticking = asyncio.create_task(ticker())
        started = time.monotonic()
        try:
            await health_router.send_raid_alert("data", "error", 3, "details")
        finally:
            elapsed = time.monotonic() - started
            ticking.cancel()
            release.set()

        assert elapsed < 2
        assert ticks > 5  # the event loop kept running while the mail server hung
        assert "Could not send alert" in caplog.text

    async def test_telegram_alerts_are_bounded_too(self, monkeypatch, caplog):
        config = NotificationConfig(
            channel=NotificationChannel.TELEGRAM,
            telegram_config=TelegramConfig(bot_token="t", chat_id="1"),
        )
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: [config])
        monkeypatch.setattr(health_router, "ALERT_TIMEOUT", 0.05)

        async def slow_telegram(config, event):
            await asyncio.sleep(5)

        monkeypatch.setattr(health_router, "send_telegram", slow_telegram)
        await health_router.send_raid_alert("data", "error", 1, "details")
        assert "Could not send alert" in caplog.text

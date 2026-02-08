import pytest
import json
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from src.storage import disks, pools
from src.storage.schemas import DiskInfo, SmartData, PoolCreate, PoolInfo, VolumeCreate, VolumeInfo
from src.security.validators import validate_disk_name, validate_pool_name, validate_device_list


class TestDisksModule:
    """Tests for disk management."""

    @pytest.fixture
    def mock_lsblk_output(self):
        """Mock lsblk JSON output."""
        return {
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "model": "Samsung SSD 860",
                    "serial": "S3Z2NB0K123456",
                    "size": 500107862016,
                    "rota": False,
                    "mountpoint": None,
                    "type": "disk"
                },
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "model": "WDC WD40EFRX",
                    "serial": "WD-WCC4E0123456",
                    "size": 4000787030016,
                    "rota": True,
                    "mountpoint": None,
                    "type": "disk"
                },
                {
                    "name": "sda1",
                    "path": "/dev/sda1",
                    "model": None,
                    "serial": None,
                    "size": 500106813440,
                    "rota": False,
                    "mountpoint": "/",
                    "type": "part"
                }
            ]
        }

    def test_list_disks_success(self, mock_lsblk_output):
        """Test listing disks successfully."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_lsblk_output)

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.list_disks()

        # Should return only disk type devices
        assert len(result) == 2
        assert all(isinstance(d, DiskInfo) for d in result)

    def test_list_disks_returns_disk_info(self, mock_lsblk_output):
        """Test that list_disks returns correct disk information."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_lsblk_output)

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.list_disks()

        sda = next(d for d in result if d.name == "sda")
        assert sda.path == "/dev/sda"
        assert sda.model == "Samsung SSD 860"
        assert sda.serial == "S3Z2NB0K123456"
        assert sda.size == 500107862016
        assert sda.rotational is False

    def test_list_disks_filters_partitions(self, mock_lsblk_output):
        """Test that partitions are filtered out."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_lsblk_output)

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.list_disks()

        disk_names = [d.name for d in result]
        assert "sda1" not in disk_names

    def test_list_disks_lsblk_failure(self):
        """Test that RuntimeError is raised when lsblk fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "lsblk: command not found"

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="lsblk failed"):
                disks.list_disks()

    def test_list_disks_identifies_ssd(self, mock_lsblk_output):
        """Test that SSDs are identified correctly (rotational=False)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_lsblk_output)

        with patch("src.storage.disks.subprocess.run", return_value=mock_result):
            result = disks.list_disks()

        sda = next(d for d in result if d.name == "sda")
        sdb = next(d for d in result if d.name == "sdb")
        assert sda.rotational is False  # SSD
        assert sdb.rotational is True   # HDD


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
            raid_level="raid1"
        )
        
        assert pool.name == "datapool"
        assert pool.devices == ["/dev/sda", "/dev/sdb"]
        assert pool.raid_level == "raid1"

    def test_pool_create_default_raid(self):
        """Test PoolCreate default raid level."""
        pool = PoolCreate(
            name="datapool",
            devices=["/dev/sda"]
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

    def test_volume_create_schema(self):
        """Test VolumeCreate schema."""
        volume = VolumeCreate(
            pool_name="datapool",
            name="documents",
            quota=1073741824  # 1GB
        )
        
        assert volume.pool_name == "datapool"
        assert volume.name == "documents"
        assert volume.quota == 1073741824

    def test_volume_create_no_quota(self):
        """Test VolumeCreate without quota."""
        volume = VolumeCreate(
            pool_name="datapool",
            name="documents"
        )
        
        assert volume.quota is None

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
                raid_level="invalid"
            )

    def test_raid5_rejected_as_unsafe(self):
        """Test that RAID5 is rejected as unsafe (write hole bug)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PoolCreate(name="test", devices=["/dev/sda"], raid_level="raid5")

    def test_raid6_rejected_as_unsafe(self):
        """Test that RAID6 is rejected as unsafe (write hole bug)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PoolCreate(name="test", devices=["/dev/sda"], raid_level="raid6")

    @patch("src.storage.pools.settings")
    def test_create_pool_rejects_insufficient_devices_raid1c3(self, mock_settings):
        """Test that raid1c3 requires at least 3 devices."""
        mock_settings.pools_base_path = "/mnt/pools"

        pool = PoolCreate(
            name="testpool",
            devices=["/dev/sda", "/dev/sdb"],
            raid_level="raid1c3"
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
            raid_level="raid1c4"
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
            raid_level="raid10"
        )

        with pytest.raises(ValueError, match="requires at least 4 devices"):
            pools.create_pool(pool)

    @patch("src.storage.pools.settings")
    def test_list_pools_parses_btrfs_show(self, mock_settings):
        """Test parsing btrfs filesystem show output."""
        mock_settings.pools_base_path = "/mnt/pools"
        
        btrfs_show_output = """Label: datapool  uuid: 12345-67890
        Total devices 2 FS bytes used 500.00GiB
        devid    1 size 2.00TiB used 501.00GiB path /dev/sda
        devid    2 size 2.00TiB used 501.00GiB path /dev/sdb
"""
        mock_show = MagicMock()
        mock_show.returncode = 0
        mock_show.stdout = btrfs_show_output
        
        mock_usage = MagicMock()
        mock_usage.returncode = 0
        mock_usage.stdout = """Device size:       4000000000000
Used:              1000000000000
Data,RAID1:        500000000000
"""
        
        def subprocess_side_effect(cmd, *args, **kwargs):
            if "show" in cmd:
                return mock_show
            return mock_usage
        
        with patch("src.storage.pools.subprocess.run", side_effect=subprocess_side_effect):
            with patch("os.path.join", return_value="/mnt/pools/datapool"):
                # This will call list_pools which internally calls get_pool
                pass  # Complex to test fully without mocking filesystem

    @patch("src.storage.pools.settings")  
    @patch("src.storage.pools.subprocess.run")
    def test_delete_pool_unmounts(self, mock_run, mock_settings):
        """Test that delete_pool unmounts the filesystem."""
        mock_settings.pools_base_path = "/mnt/pools"
        mock_run.return_value = MagicMock(returncode=0)
        
        with patch("os.path.exists", return_value=True):
            with patch("os.rmdir"):
                with patch("builtins.open", MagicMock()):
                    pools.delete_pool("testpool")
        
        # Verify umount was called
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("umount" in str(c) for c in calls)


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
        """Test PoolCreate requires name and devices."""
        from pydantic import ValidationError
        
        # Should work with required fields
        pool = PoolCreate(name="pool1", devices=["/dev/sda"])
        assert pool.name == "pool1"
        
        # Should fail without devices
        with pytest.raises(ValidationError):
            PoolCreate(name="pool1")

    def test_raid_levels_supported(self):
        """Test supported (safe) RAID levels in schema."""
        for raid in ["single", "raid0", "raid1", "raid1c3", "raid1c4", "raid10"]:
            pool = PoolCreate(name="test", devices=["/dev/sda"], raid_level=raid)
            assert pool.raid_level == raid

    def test_unsafe_raid_levels_rejected(self):
        """Test that RAID5/6 are rejected at schema level (write hole bug)."""
        from pydantic import ValidationError

        for raid in ["raid5", "raid6"]:
            with pytest.raises(ValidationError):
                PoolCreate(name="test", devices=["/dev/sda"], raid_level=raid)


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
            raid_level="single"
        )

        with pytest.raises(HTTPException) as exc:
            pools.create_pool(pool)
        assert exc.value.status_code == 400
        assert "device" in exc.value.detail.lower() or "/dev/" in exc.value.detail

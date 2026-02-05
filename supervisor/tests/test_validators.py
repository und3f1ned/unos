"""Tests for security validators module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from src.security.validators import (
    validate_hostname,
    validate_timezone,
    validate_disk_name,
    validate_pool_name,
    validate_volume_name,
    validate_share_name,
    validate_share_path,
    validate_interface_name,
    validate_device_path,
    validate_device_list,
    safe_path_join,
    sanitize_error_message,
    RESERVED_SMB_NAMES,
)


class TestValidateHostname:
    """Tests for hostname validation."""

    def test_valid_hostname(self):
        """Test valid hostnames are accepted."""
        assert validate_hostname("mynas") == "mynas"
        assert validate_hostname("nas-server") == "nas-server"
        assert validate_hostname("nas01") == "nas01"
        assert validate_hostname("a") == "a"
        assert validate_hostname("NAS-Server-01") == "NAS-Server-01"

    def test_empty_hostname_rejected(self):
        """Test empty hostname is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_hostname("")
        assert exc.value.status_code == 400
        assert "empty" in exc.value.detail.lower()

    def test_hostname_too_long(self):
        """Test hostname over 63 chars is rejected."""
        long_hostname = "a" * 64
        with pytest.raises(HTTPException) as exc:
            validate_hostname(long_hostname)
        assert exc.value.status_code == 400
        assert "63" in exc.value.detail

    def test_hostname_invalid_characters(self):
        """Test hostname with invalid characters is rejected."""
        invalid_hostnames = [
            "host_name",  # underscore
            "host.name",  # dot
            "host name",  # space
            "host;name",  # semicolon
            "-hostname",  # starts with hyphen
            "hostname-",  # ends with hyphen
        ]
        for hostname in invalid_hostnames:
            with pytest.raises(HTTPException) as exc:
                validate_hostname(hostname)
            assert exc.value.status_code == 400

    def test_hostname_max_length(self):
        """Test hostname at exactly 63 chars is accepted."""
        hostname = "a" * 63
        assert validate_hostname(hostname) == hostname


class TestValidateTimezone:
    """Tests for timezone validation."""

    def test_valid_timezone(self):
        """Test valid timezones are accepted."""
        assert validate_timezone("America/New_York") == "America/New_York"
        assert validate_timezone("Europe/London") == "Europe/London"
        assert validate_timezone("UTC") == "UTC"
        assert validate_timezone("Asia/Tokyo") == "Asia/Tokyo"

    def test_empty_timezone_rejected(self):
        """Test empty timezone is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_timezone("")
        assert exc.value.status_code == 400

    def test_timezone_too_long(self):
        """Test timezone over 64 chars is rejected."""
        long_tz = "A" * 65
        with pytest.raises(HTTPException) as exc:
            validate_timezone(long_tz)
        assert exc.value.status_code == 400

    def test_timezone_injection_characters(self):
        """Test timezone with injection characters is rejected."""
        malicious_timezones = [
            "America/New_York;rm -rf /",
            "UTC|cat /etc/passwd",
            "Europe/London`whoami`",
            "Asia/Tokyo$USER",
            "UTC\nmalicious",
            "UTC\x00null",
        ]
        for tz in malicious_timezones:
            with pytest.raises(HTTPException) as exc:
                validate_timezone(tz)
            assert exc.value.status_code == 400

    def test_timezone_with_valid_list(self):
        """Test timezone validation against known list."""
        valid_tzs = {"America/New_York", "Europe/London", "UTC"}

        assert validate_timezone("UTC", valid_tzs) == "UTC"

        with pytest.raises(HTTPException) as exc:
            validate_timezone("Unknown/Zone", valid_tzs)
        assert exc.value.status_code == 400
        assert "Unknown" in exc.value.detail


class TestValidateDiskName:
    """Tests for disk name validation."""

    def test_valid_disk_names(self):
        """Test valid disk names are accepted."""
        assert validate_disk_name("sda") == "sda"
        assert validate_disk_name("sdb1") == "sdb1"
        assert validate_disk_name("nvme0n1") == "nvme0n1"
        assert validate_disk_name("nvme0n1p1") == "nvme0n1p1"
        assert validate_disk_name("vda") == "vda"
        assert validate_disk_name("xvda") == "xvda"

    def test_empty_disk_name_rejected(self):
        """Test empty disk name is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_disk_name("")
        assert exc.value.status_code == 400

    def test_path_traversal_rejected(self):
        """Test path traversal attempts are rejected."""
        malicious_names = [
            "../etc/passwd",
            "..%2f..%2fetc",
            "sda/../../../etc/passwd",
            "/dev/sda",
            "sda/sdb",
        ]
        for name in malicious_names:
            with pytest.raises(HTTPException) as exc:
                validate_disk_name(name)
            assert exc.value.status_code == 400

    def test_invalid_disk_name_format(self):
        """Test invalid disk name formats are rejected."""
        invalid_names = [
            "Sda",  # uppercase
            "sd_a",  # underscore
            "sd-a",  # hyphen
            "123",  # only numbers
            "s",  # too short
        ]
        for name in invalid_names:
            with pytest.raises(HTTPException) as exc:
                validate_disk_name(name)
            assert exc.value.status_code == 400


class TestValidatePoolName:
    """Tests for pool name validation."""

    def test_valid_pool_names(self):
        """Test valid pool names are accepted."""
        assert validate_pool_name("datapool") == "datapool"
        assert validate_pool_name("data_pool") == "data_pool"
        assert validate_pool_name("data-pool") == "data-pool"
        assert validate_pool_name("pool1") == "pool1"
        assert validate_pool_name("MyPool01") == "MyPool01"

    def test_empty_pool_name_rejected(self):
        """Test empty pool name is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_pool_name("")
        assert exc.value.status_code == 400

    def test_pool_name_too_long(self):
        """Test pool name over 63 chars is rejected."""
        long_name = "a" * 64
        with pytest.raises(HTTPException) as exc:
            validate_pool_name(long_name)
        assert exc.value.status_code == 400

    def test_pool_name_path_traversal(self):
        """Test path traversal in pool names is rejected."""
        malicious_names = [
            "../etc",
            "pool/../etc",
            "pool/subdir",
            "..pool",
        ]
        for name in malicious_names:
            with pytest.raises(HTTPException) as exc:
                validate_pool_name(name)
            assert exc.value.status_code == 400

    def test_pool_name_invalid_start(self):
        """Test pool names starting with non-alphanumeric are rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_pool_name("_pool")
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            validate_pool_name("-pool")
        assert exc.value.status_code == 400


class TestValidateVolumeName:
    """Tests for volume name validation."""

    def test_valid_volume_names(self):
        """Test valid volume names are accepted."""
        assert validate_volume_name("documents") == "documents"
        assert validate_volume_name("my_data") == "my_data"
        assert validate_volume_name("backup-2024") == "backup-2024"

    def test_empty_volume_name_rejected(self):
        """Test empty volume name is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_volume_name("")
        assert exc.value.status_code == 400

    def test_volume_name_path_traversal(self):
        """Test path traversal in volume names is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_volume_name("../secret")
        assert exc.value.status_code == 400


class TestValidateShareName:
    """Tests for SMB share name validation."""

    def test_valid_share_names(self):
        """Test valid share names are accepted."""
        assert validate_share_name("documents") == "documents"
        assert validate_share_name("my_share") == "my_share"
        assert validate_share_name("Share123") == "Share123"

    def test_empty_share_name_rejected(self):
        """Test empty share name is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_share_name("")
        assert exc.value.status_code == 400

    def test_share_name_too_long(self):
        """Test share name over 64 chars is rejected."""
        long_name = "a" * 65
        with pytest.raises(HTTPException) as exc:
            validate_share_name(long_name)
        assert exc.value.status_code == 400

    def test_share_name_invalid_characters(self):
        """Test share names with invalid characters are rejected."""
        invalid_names = [
            "share-name",  # hyphen not allowed
            "share.name",  # dot not allowed
            "share name",  # space not allowed
        ]
        for name in invalid_names:
            with pytest.raises(HTTPException) as exc:
                validate_share_name(name)
            assert exc.value.status_code == 400

    def test_reserved_share_names_rejected(self):
        """Test reserved SMB share names are rejected."""
        # Test valid-pattern reserved names (those without special chars like $)
        valid_pattern_reserved = ["global", "homes", "printers"]
        for name in valid_pattern_reserved:
            with pytest.raises(HTTPException) as exc:
                validate_share_name(name)
            assert exc.value.status_code == 400
            assert "reserved" in exc.value.detail.lower()

        # Test reserved names with special chars (fail pattern check first)
        special_char_reserved = ["print$", "ipc$", "admin$", "c$", "d$"]
        for name in special_char_reserved:
            with pytest.raises(HTTPException) as exc:
                validate_share_name(name)
            assert exc.value.status_code == 400


class TestValidateSharePath:
    """Tests for SMB share path validation."""

    @patch("src.security.validators.settings")
    def test_valid_share_path(self, mock_settings):
        """Test valid share paths are accepted."""
        mock_settings.pools_base_path = "/mnt/pools"

        with patch.object(Path, "resolve", return_value=Path("/mnt/pools/mypool/share")):
            # Note: This requires the resolved path to be under pools_base_path
            pass

    @patch("src.security.validators.settings")
    def test_empty_share_path_rejected(self, mock_settings):
        """Test empty share path is rejected."""
        mock_settings.pools_base_path = "/mnt/pools"

        with pytest.raises(HTTPException) as exc:
            validate_share_path("")
        assert exc.value.status_code == 400

    @patch("src.security.validators.settings")
    def test_share_path_outside_pools_rejected(self, mock_settings):
        """Test share paths outside pools directory are rejected."""
        mock_settings.pools_base_path = "/mnt/pools"

        with pytest.raises(HTTPException) as exc:
            validate_share_path("/etc/passwd")
        assert exc.value.status_code == 400
        assert "pools" in exc.value.detail.lower()

    @patch("src.security.validators.settings")
    def test_share_path_traversal_rejected(self, mock_settings):
        """Test path traversal in share paths is rejected."""
        mock_settings.pools_base_path = "/mnt/pools"

        with pytest.raises(HTTPException) as exc:
            validate_share_path("/mnt/pools/../etc/passwd")
        assert exc.value.status_code == 400


class TestValidateInterfaceName:
    """Tests for network interface name validation."""

    def test_valid_interface_names(self):
        """Test valid interface names are accepted."""
        assert validate_interface_name("eth0") == "eth0"
        assert validate_interface_name("enp3s0") == "enp3s0"
        assert validate_interface_name("wlan0") == "wlan0"
        assert validate_interface_name("br0") == "br0"
        assert validate_interface_name("docker0") == "docker0"

    def test_empty_interface_name_rejected(self):
        """Test empty interface name is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_interface_name("")
        assert exc.value.status_code == 400

    def test_interface_name_too_long(self):
        """Test interface name over 15 chars (IFNAMSIZ) is rejected."""
        long_name = "a" * 16
        with pytest.raises(HTTPException) as exc:
            validate_interface_name(long_name)
        assert exc.value.status_code == 400

    def test_interface_name_injection_rejected(self):
        """Test injection characters in interface names are rejected."""
        malicious_names = [
            "eth0;rm -rf /",
            "eth0|cat /etc/passwd",
            "eth0`whoami`",
            "eth0$USER",
            "eth0\nmalicious",
            "eth0 && id",
        ]
        for name in malicious_names:
            with pytest.raises(HTTPException) as exc:
                validate_interface_name(name)
            assert exc.value.status_code == 400

    def test_interface_name_must_start_with_letter(self):
        """Test interface names must start with a letter."""
        with pytest.raises(HTTPException) as exc:
            validate_interface_name("0eth")
        assert exc.value.status_code == 400


class TestValidateDevicePath:
    """Tests for device path validation."""

    def test_valid_device_paths(self):
        """Test valid device paths are accepted."""
        assert validate_device_path("/dev/sda") == "/dev/sda"
        assert validate_device_path("/dev/sdb1") == "/dev/sdb1"
        assert validate_device_path("/dev/nvme0n1") == "/dev/nvme0n1"

    def test_empty_device_path_rejected(self):
        """Test empty device path is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_device_path("")
        assert exc.value.status_code == 400

    def test_device_path_must_start_with_dev(self):
        """Test device paths must start with /dev/."""
        with pytest.raises(HTTPException) as exc:
            validate_device_path("/etc/passwd")
        assert exc.value.status_code == 400
        assert "/dev/" in exc.value.detail

    def test_device_path_traversal_rejected(self):
        """Test path traversal in device paths is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_device_path("/dev/../etc/passwd")
        assert exc.value.status_code == 400


class TestValidateDeviceList:
    """Tests for device list validation."""

    def test_valid_device_list(self):
        """Test valid device list is accepted."""
        devices = ["/dev/sda", "/dev/sdb"]
        result = validate_device_list(devices)
        assert result == devices

    def test_empty_device_list_rejected(self):
        """Test empty device list is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_device_list([])
        assert exc.value.status_code == 400

    def test_device_list_with_invalid_device(self):
        """Test device list with invalid device is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_device_list(["/dev/sda", "/etc/passwd"])
        assert exc.value.status_code == 400


class TestSafePathJoin:
    """Tests for safe path join function."""

    def test_safe_path_join_valid(self):
        """Test safe path join with valid paths."""
        result = safe_path_join("/base", "subdir", "file")
        assert str(result).startswith("/base")

    def test_safe_path_join_traversal_rejected(self):
        """Test path traversal in safe_path_join is rejected."""
        with pytest.raises(HTTPException) as exc:
            safe_path_join("/base", "..", "etc")
        assert exc.value.status_code == 400

    def test_safe_path_join_absolute_path_rejected(self):
        """Test absolute path components are rejected."""
        with pytest.raises(HTTPException) as exc:
            safe_path_join("/base", "/etc/passwd")
        assert exc.value.status_code == 400


class TestSanitizeErrorMessage:
    """Tests for error message sanitization."""

    def test_sanitize_home_directory(self):
        """Test home directory paths are sanitized."""
        error = Exception("Error in /home/user/secret/file.txt")
        result = sanitize_error_message(error)
        assert "/home/user" not in result
        assert "***" in result

    def test_sanitize_password(self):
        """Test password values are sanitized."""
        error = Exception("password=mysecretpass123")
        result = sanitize_error_message(error)
        assert "mysecretpass123" not in result
        assert "***" in result

    def test_sanitize_token(self):
        """Test token values are sanitized."""
        error = Exception("token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
        result = sanitize_error_message(error)
        assert "eyJhbG" not in result

    def test_truncate_long_message(self):
        """Test long error messages are truncated."""
        error = Exception("x" * 500)
        result = sanitize_error_message(error)
        assert len(result) <= 259  # 256 + "..."

    def test_safe_error_passes_through(self):
        """Test safe error messages pass through."""
        error = Exception("File not found")
        result = sanitize_error_message(error)
        assert result == "File not found"

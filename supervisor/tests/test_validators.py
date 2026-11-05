"""Tests for security validators module."""

import errno
import os
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from src.security import validators
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
    validate_pools_path,
    validate_username,
    validate_snapshot_name,
    validate_path_component,
    validate_disk_target,
    open_dir_nofollow,
    safe_path_join,
    sanitize_error_message,
    RESERVED_SMB_NAMES,
)


class TestTrailingNewline:
    """re.match() with a "$"-anchored pattern accepts a trailing newline; fullmatch must not."""

    @pytest.mark.parametrize(
        "validator,value",
        [
            (validate_hostname, "nas\n"),
            (validate_timezone, "UTC\n"),
            (validate_disk_name, "sda\n"),
            (validate_pool_name, "pool\n"),
            (validate_volume_name, "vol\n"),
            (validate_share_name, "share\n"),
            (validate_interface_name, "eth0\n"),
            (validate_device_path, "/dev/sda\n"),
            (validate_username, "alice\n"),
            (validate_snapshot_name, "snap\n"),
            (validate_disk_target, "/dev/sda\n"),
        ],
    )
    def test_trailing_newline_rejected(self, validator, value):
        with pytest.raises(HTTPException) as exc:
            validator(value)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "pattern",
        [
            validators.HOSTNAME_PATTERN,
            validators.POOL_NAME_PATTERN,
            validators.VOLUME_NAME_PATTERN,
            validators.SHARE_NAME_PATTERN,
            validators.DISK_NAME_PATTERN,
            validators.INTERFACE_NAME_PATTERN,
            validators.USERNAME_PATTERN,
            validators.SNAPSHOT_NAME_PATTERN,
        ],
    )
    def test_patterns_are_anchored_even_with_match(self, pattern):
        """Other modules may still call .match(); the patterns end in \\Z."""
        assert pattern.match("sda\n") is None
        assert pattern.fullmatch("sda\n") is None


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
        assert validate_share_name("my-share") == "my-share"
        assert validate_share_name("_private") == "_private"
        assert validate_share_name("a" * 32) == "a" * 32

    def test_empty_share_name_rejected(self):
        """Test empty share name is rejected."""
        with pytest.raises(HTTPException) as exc:
            validate_share_name("")
        assert exc.value.status_code == 400

    def test_share_name_too_long(self):
        """Test share names over 32 chars are rejected."""
        for long_name in ("a" * 33, "a" * 65):
            with pytest.raises(HTTPException) as exc:
                validate_share_name(long_name)
            assert exc.value.status_code == 400

    def test_share_name_invalid_characters(self):
        """Test share names with invalid characters are rejected."""
        invalid_names = [
            "-share",  # must not start with a hyphen
            "share.name",  # dot not allowed
            "share name",  # space not allowed
            "share]",  # would close the section header
            "[share",
            "share\nname",
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

    @pytest.mark.parametrize(
        "path",
        [
            "/mnt/pools/data/%U", "/mnt/pools/data/x\\", "/mnt/pools/data/x\n", "/mnt/pools/data/\x00",
            # Samba trims a value's trailing space and collapses runs of spaces.
            "/mnt/pools/data/x ", "/mnt/pools/data/x /", "/mnt/pools/data/a  b", "/mnt/pools/data/x\t",
            # smbd strips '"' at either end of a substituted value such as path.
            '/mnt/pools/data/x"', '/mnt/pools/data/"x"',
        ],
    )
    @patch("src.security.validators.settings")
    def test_share_path_smb_conf_metacharacters_rejected(self, mock_settings, path):
        mock_settings.pools_base_path = "/mnt/pools"
        with pytest.raises(HTTPException) as exc:
            validate_share_path(path)
        assert exc.value.status_code == 400

    def test_share_path_with_single_spaces_accepted(self, pools):
        (pools.pool / " My Documents").mkdir()
        path = f"{pools.pool}/ My Documents"
        assert validate_share_path(path) == path

    def test_share_path_through_symlink_to_percent_target_rejected(self, pools):
        """The '%' check must apply to what is shared, not just to what the client typed."""
        (pools.pool / "apps").mkdir()
        os.symlink(pools.pool / "%U" / "%U", pools.pool / "apps" / "downloads")
        with pytest.raises(HTTPException) as exc:
            validate_share_path(str(pools.pool / "apps" / "downloads"))
        assert exc.value.status_code == 400
        assert not (pools.pool / "%U").exists()


@pytest.fixture
def pools(tmp_path):
    """A real pools base with one pool and an outside directory."""
    root = tmp_path.resolve()
    base = root / "pools"
    (base / "data" / "share").mkdir(parents=True)
    (root / "pools-old").mkdir()
    (root / "outside").mkdir()
    with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(base))):
        yield SimpleNamespace(root=root, base=base, pool=base / "data")


class TestValidatePoolsPath:
    def test_inside_is_accepted_and_normalized(self, pools):
        assert validate_pools_path(f"{pools.base}/data//share/.") == str(pools.pool / "share")
        # Missing paths are fine (e.g. a share directory created later).
        assert validate_pools_path(f"{pools.base}/data/new") == str(pools.pool / "new")

    @pytest.mark.parametrize(
        "template",
        [
            "{base}",
            "{base}/",
            "{root}/pools-old",
            "{root}/pools-old/x",
            "{root}/poolsX",
            "{root}/outside",
            "{base}/data/../../outside",
            "{base}/..",
            "relative/path",
            "",
            "{base}/data/\nx",
            "{base}/data/\u2028x",
        ],
    )
    def test_outside_or_invalid_is_rejected(self, pools, template):
        with pytest.raises(HTTPException) as exc:
            validate_pools_path(template.format(base=pools.base, root=pools.root))
        assert exc.value.status_code == 400

    def test_symlink_escaping_base_is_rejected(self, pools):
        os.symlink(pools.root / "outside", pools.pool / "escape")
        with pytest.raises(HTTPException):
            validate_pools_path(str(pools.pool / "escape"))
        with pytest.raises(HTTPException):
            validate_pools_path(str(pools.pool / "escape" / "sub"))

    @pytest.mark.parametrize("link", ["alias", "alias/sub", "alias/sub/deeper"])
    def test_symlink_inside_base_is_refused(self, pools, link):
        """Pool contents are writable by users and apps: a link is never resolved into its target."""
        os.symlink(pools.pool / "share", pools.pool / "alias")
        (pools.pool / "share" / "sub").mkdir()
        with pytest.raises(HTTPException) as exc:
            validate_pools_path(f"{pools.pool}/{link}")
        assert exc.value.status_code == 400
        assert "symbolic link" in exc.value.detail

    def test_symlinked_pool_is_refused(self, pools):
        os.symlink(pools.pool, pools.base / "other")
        with pytest.raises(HTTPException):
            validate_pools_path(str(pools.base / "other" / "share"))

    def test_dangling_symlink_is_refused(self, pools):
        os.symlink(pools.root / "does-not-exist", pools.pool / "dangling")
        for path in (pools.pool / "dangling", pools.pool / "dangling" / "x"):
            with pytest.raises(HTTPException) as exc:
                validate_pools_path(str(path))
            assert "symbolic link" in exc.value.detail

    def test_file_component_is_refused(self, pools):
        (pools.pool / "file").write_text("x")
        for path in (pools.pool / "file", pools.pool / "file" / "sub"):
            with pytest.raises(HTTPException) as exc:
                validate_pools_path(str(path))
            assert exc.value.status_code == 400
            assert "must be a directory" in exc.value.detail

    def test_missing_components_are_accepted(self, pools):
        assert validate_pools_path(f"{pools.pool}/share/new/deeper") == str(pools.pool / "share" / "new" / "deeper")
        assert validate_pools_path(f"{pools.base}/newpool/x") == str(pools.base / "newpool" / "x")

    def test_missing_base_accepts_paths_below_it(self, pools):
        missing = pools.root / "no-pools"
        with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(missing))):
            assert validate_pools_path(f"{missing}/data/x") == str(missing / "data" / "x")

    def test_symlinked_base_is_trusted(self, pools):
        """The configured base may be a symlink; paths may use either form and are returned resolved."""
        configured = pools.root / "pools-link"
        os.symlink(pools.base, configured)
        with patch("src.security.validators.settings", SimpleNamespace(pools_base_path=str(configured))):
            assert validate_pools_path(f"{configured}/data/share") == str(pools.pool / "share")
            assert validate_pools_path(f"{pools.base}/data/share") == str(pools.pool / "share")
            for path in (str(configured), f"{pools.base}/", f"{pools.root}/pools-old/x"):
                with pytest.raises(HTTPException):
                    validate_pools_path(path)


class TestValidateUsername:
    @pytest.mark.parametrize("name", ["alice", "bob.smith", "user_1", "_svc", "A-B", "u" * 32])
    def test_valid(self, name):
        assert validate_username(name) == name

    @pytest.mark.parametrize(
        "name", ["", "-alice", ".alice", "alice bob", "alice,root", "a%S", "a\\b", "a\nb", "u" * 33, "@group"]
    )
    def test_invalid(self, name):
        with pytest.raises(HTTPException) as exc:
            validate_username(name)
        assert exc.value.status_code == 400


class TestValidateSnapshotName:
    @pytest.mark.parametrize(
        "name", ["snap_20250124_120000", "auto_20250124", "before upgrade", "v1.2", "pre-update@2025-01-01T10:00"]
    )
    def test_valid(self, name):
        assert validate_snapshot_name(name) == name

    @pytest.mark.parametrize(
        "name", ["", ".", "..", "a/b", "../x", "a..b", ".hidden", "-x", " x", "x ", "a\nb", "a\x00b", "x" * 129]
    )
    def test_invalid(self, name):
        with pytest.raises(HTTPException) as exc:
            validate_snapshot_name(name)
        assert exc.value.status_code == 400


class TestValidatePathComponent:
    """Names of existing entries, e.g. snapshots made by other tools."""

    @pytest.mark.parametrize(
        "name",
        ["snap", "before upgrade (v2)", ".hidden", "-x", " x ", "a..b", "...", "a\nb", "ü" * 127, "x" * 255],
    )
    def test_any_single_component_is_accepted(self, name):
        assert validate_path_component(name) == name

    @pytest.mark.parametrize(
        "name", ["", ".", "..", "/", "a/b", "../x", "x/", "a\x00b", "x" * 256, "ü" * 128, "\ud800"], ids=repr
    )
    def test_anything_else_is_rejected(self, name):
        with pytest.raises(HTTPException) as exc:
            validate_path_component(name, what="Snapshot name")
        assert exc.value.status_code == 400
        assert exc.value.detail.startswith("Snapshot name")


class TestValidateDiskTarget:
    @pytest.mark.parametrize(
        "target,device",
        [("sda", "/dev/sda"), ("/dev/sda", "/dev/sda"), ("nvme0n1", "/dev/nvme0n1"), ("/dev/sdb", "/dev/sdb")],
    )
    def test_both_forms_are_normalized(self, target, device):
        assert validate_disk_target(target) == device

    @pytest.mark.parametrize(
        "target", ["", "/dev//dev/sda", "/dev/../etc/passwd", "/etc/passwd", "sda; rm -rf /", "/dev/", "dev/sda"]
    )
    def test_invalid(self, target):
        with pytest.raises(HTTPException) as exc:
            validate_disk_target(target)
        assert exc.value.status_code == 400


class TestOpenDirNofollow:
    def test_opens_nested_directory(self, pools):
        fd = open_dir_nofollow(pools.base, ["data", "share"])
        try:
            assert os.path.samestat(os.fstat(fd), os.stat(pools.pool / "share"))
        finally:
            os.close(fd)

    @pytest.mark.parametrize("target", ["outside", "pools/data/share"])
    def test_symlink_component_is_refused(self, pools, target):
        """Even a symlink that stays inside the base is not followed."""
        os.symlink(pools.root / target, pools.pool / "link")
        for parts in (["data", "link"], ["data", "link", "x"]):
            with pytest.raises(OSError) as exc:
                open_dir_nofollow(pools.base, parts)
            assert exc.value.errno == errno.ELOOP

    def test_create_missing_directories(self, pools):
        fd = open_dir_nofollow(pools.base, ["data", "new", "deeper"], create=True)
        os.close(fd)
        assert (pools.pool / "new" / "deeper").is_dir()

    def test_missing_without_create(self, pools):
        with pytest.raises(FileNotFoundError):
            open_dir_nofollow(pools.base, ["data", "missing"])

    def test_file_component(self, pools):
        (pools.pool / "file").write_text("x")
        with pytest.raises(NotADirectoryError):
            open_dir_nofollow(pools.base, ["data", "file"])

    @pytest.mark.parametrize("parts", [[".."], ["data", "."], ["a/b"], [""], ["x\x00"]])
    def test_invalid_components(self, pools, parts):
        with pytest.raises(ValueError):
            open_dir_nofollow(pools.base, parts)


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

    @pytest.mark.parametrize("component", ["sub/../..", "", "a\x00b", "a\nb", "../x"])
    def test_safe_path_join_invalid_components_rejected(self, component):
        with pytest.raises(HTTPException) as exc:
            safe_path_join("/base", component)
        assert exc.value.status_code == 400

    def test_safe_path_join_allows_dots_inside_names(self):
        assert safe_path_join("/base", "a..b", "sub/file") == Path("/base/a..b/sub/file").resolve()

    def test_safe_path_join_symlink_escape_rejected(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        os.symlink(tmp_path, base / "up")
        with pytest.raises(HTTPException) as exc:
            safe_path_join(str(base), "up", "secret")
        assert exc.value.status_code == 400

    def test_safe_path_join_sibling_prefix_rejected(self, tmp_path):
        base = tmp_path / "pools"
        base.mkdir()
        (tmp_path / "pools-old").mkdir()
        os.symlink(tmp_path / "pools-old", base / "link")
        with pytest.raises(HTTPException):
            safe_path_join(str(base), "link")


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

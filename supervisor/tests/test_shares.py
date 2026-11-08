"""SMB share list for storage/pools.py and the share validators.

The SMB share API (database rows, smb.conf rendered from them) is tested in
test_share_api.py, test_share_apply.py and test_share_import.py; the
injection tests of the release before slice 2 moved to test_share_api.py as
API-level 400s. NFS exports are tested in test_nfs.py.
"""

import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from src.host import HostResult
from src.shares import service, smb, smbconf
from src.security.validators import validate_share_name, validate_share_path, RESERVED_SMB_NAMES

INITIAL_SMB_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no

"""


class TestSmbListShares:
    """smb.list_shares(): the shares storage/pools.py must not lose (ADR-0069)."""

    @pytest.fixture
    def temp_smb_conf(self):
        """Create temporary smb.conf."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(INITIAL_SMB_CONF)
            path = f.name
        yield path
        if os.path.exists(path):
            os.unlink(path)

    @pytest.fixture
    def mock_settings(self, temp_smb_conf, tmp_path):
        """Mock settings with temp paths (no database: only the file is read)."""
        mock_settings = MagicMock()
        mock_settings.smb_conf_path = temp_smb_conf
        mock_settings.pools_base_path = str(tmp_path)  # Use tmp_path as pools base
        mock_settings.db_path = ":memory:"
        with patch("src.shares.smb.settings", mock_settings):
            with patch("src.security.validators.settings", mock_settings):
                yield mock_settings

    @pytest.fixture
    def mock_subprocess(self):
        """Host commands must not run: list_shares only reads."""
        with patch("src.host.run_on_host") as mock_run:
            mock_run.return_value = HostResult(0, "", "")
            yield mock_run

    def test_list_shares_empty(self, mock_settings, mock_subprocess):
        """Test listing shares when none exist."""
        shares = smb.list_shares()
        assert shares == []
        mock_subprocess.assert_not_called()

    def test_parse_smb_conf(self, mock_settings, mock_subprocess, tmp_path):
        """Test parsing smb.conf with multiple shares."""
        # Create conf with multiple shares
        conf_content = """[global]
   workgroup = WORKGROUP

[share1]
   path = /data/share1
   comment = First share
   browseable = yes
   read only = no
   guest ok = yes

[share2]
   path = /data/share2
   comment = Second share
   browseable = no
   read only = yes
   valid users = user1, user2
"""
        with open(mock_settings.smb_conf_path, "w") as f:
            f.write(conf_content)

        shares = smb.list_shares()

        # list_shares() serves storage/pools.py only: names and paths.
        assert shares == [
            smb.SharePathInfo(name="share1", path="/data/share1"),
            smb.SharePathInfo(name="share2", path="/data/share2"),
        ]

    def test_parser_splits_lines_like_samba(self, mock_settings, mock_subprocess):
        """Only "\\n" ends a line; str.splitlines() would also split on U+2028."""
        with open(mock_settings.smb_conf_path, "w", encoding="utf-8") as f:
            f.write("[a]\n   path = /x\n   comment = one\u2028path = /\n")
        (share,) = smb.list_shares()
        assert share.path == "/x"


class TestShareSecurityValidation:
    """Tests for share security validation."""

    def test_share_name_valid(self):
        """Test valid share names are accepted."""
        valid_names = ["documents", "my_share", "Share123", "data_backup", "my-share", "_x"]
        for name in valid_names:
            result = validate_share_name(name)
            assert result == name

    def test_share_name_invalid_characters_rejected(self):
        """Test share names with invalid characters are rejected."""
        invalid_names = [
            "-share",  # leading hyphen
            "share.name",  # dot
            "share name",  # space
            "share;name",  # semicolon
            "share|name",  # pipe
            "share]",  # section terminator
            "share\n",  # trailing newline
            "a" * 33,  # too long
        ]
        for name in invalid_names:
            with pytest.raises(HTTPException) as exc:
                validate_share_name(name)
            assert exc.value.status_code == 400

    def test_reserved_share_names_rejected(self):
        """Test reserved SMB share names are rejected."""
        # Test valid-pattern reserved names (those without special chars like $)
        valid_pattern_reserved = ["global", "homes", "printers", "Global", "HOMES"]
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
        assert {"global", "globals", "homes", "printers", "print$", "ipc$"} <= RESERVED_SMB_NAMES

    @pytest.mark.parametrize("name", ["globals", "Globals", "GLOBALS", "gLoBaLs", "global", "GLOBAL"])
    def test_names_samba_reads_as_the_global_section_are_refused_everywhere(self, name):
        """Samba reads [global] and [globals], in any case, as the global section: such a
        "share" would never be served, and its lines would become every share's defaults."""
        with pytest.raises(HTTPException) as exc:
            validate_share_name(name)
        assert (exc.value.status_code, exc.value.detail) == (400, f"'{name}' is a reserved share name")
        assert service._name_problem(name) == f"'{name}' is a reserved share name"
        with pytest.raises(smb.SambaConfigError, match="Refusing to write an invalid share name"):
            smbconf._safe_name(name)

    def test_no_valid_share_name_is_read_as_the_global_section(self):
        """The validator and the parser must agree (validators cannot import smbconf, which
        imports validators): every spelling that Samba reads as [global] is refused."""
        spellings = set()
        for word in ("global", "globals"):
            for bits in range(2 ** len(word)):
                spellings.add("".join(c.upper() if bits >> i & 1 else c for i, c in enumerate(word)))
            for i in range(len(word) + 1):
                for blank in (" ", "\t"):
                    spellings.add(word[:i] + blank + word[i:])
        assert len(spellings) > 2 ** 6 + 2 ** 7
        for name in sorted(spellings):
            assert smbconf.is_global_name(name), name
            with pytest.raises(HTTPException):
                validate_share_name(name)
        for name in ("globalx", "my-globals", "global_", "Globals2", "_global", "glob"):
            assert validate_share_name(name) == name
            assert not smbconf.is_global_name(name)

    @patch("src.security.validators.settings")
    def test_share_path_must_be_in_pools(self, mock_settings):
        """Test share paths must be within pools directory."""
        mock_settings.pools_base_path = "/mnt/pools"

        # Path outside pools should be rejected
        with pytest.raises(HTTPException) as exc:
            validate_share_path("/etc/passwd")
        assert exc.value.status_code == 400
        assert "pools" in exc.value.detail.lower()

        with pytest.raises(HTTPException) as exc:
            validate_share_path("/root/secret")
        assert exc.value.status_code == 400

    @patch("src.security.validators.settings")
    def test_share_path_traversal_rejected(self, mock_settings):
        """Test path traversal in share paths is rejected."""
        mock_settings.pools_base_path = "/mnt/pools"

        with pytest.raises(HTTPException) as exc:
            validate_share_path("/mnt/pools/../etc/passwd")
        assert exc.value.status_code == 400

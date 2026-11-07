"""SMB share list for storage/pools.py, NFS exports and the share validators.

The SMB share API (database rows, smb.conf rendered from them) is tested in
test_share_api.py, test_share_apply.py and test_share_import.py; the
injection tests of the release before slice 2 moved to test_share_api.py as
API-level 400s.
"""

import os
import tempfile
from types import SimpleNamespace

import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.host import HostResult
from src.shares import service, smb, smbconf, nfs
from src.shares.schemas import NfsExportCreate
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
    """smb.list_shares(): the shares storage/pools.py must not lose (spec 2.7)."""

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

        # list_shares() serves storage/pools.py only: names and paths (spec 2.7).
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


class TestNfsRouter:
    """Validation errors must surface as 400, not 500."""

    @pytest.fixture
    def client(self, tmp_path):
        from src.auth.utils import verify_token
        from src.main import app

        base = tmp_path / "pools"
        (base / "data").mkdir(parents=True)
        settings = SimpleNamespace(pools_base_path=str(base))
        app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True)
        with patch("src.security.validators.settings", settings):
            with patch("src.host.run_on_host", return_value=HostResult(0, "", "")):
                yield SimpleNamespace(client=TestClient(app), base=base)
        app.dependency_overrides.pop(verify_token, None)

    def test_nfs_bad_client_is_400(self, client, tmp_path):
        exports = tmp_path / "exports"
        exports.write_text("")
        with patch("src.shares.nfs.settings", SimpleNamespace(nfs_exports_path=str(exports))):
            response = client.client.post(
                "/api/v1/shares/nfs",
                json={"path": str(client.base / "data"), "clients": "* /etc", "options": "rw"},
            )
        assert response.status_code == 400
        assert exports.read_text() == ""


class TestNfsModule:
    """Tests for NFS export management."""

    @pytest.fixture
    def temp_exports(self):
        """Create temporary exports file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("")
            path = f.name
        yield path
        if os.path.exists(path):
            os.unlink(path)

    @pytest.fixture
    def mock_settings(self, temp_exports, tmp_path):
        """Mock settings with temp paths."""
        mock_settings = MagicMock()
        mock_settings.nfs_exports_path = temp_exports
        mock_settings.pools_base_path = str(tmp_path)
        with patch("src.shares.nfs.settings", mock_settings):
            with patch("src.security.validators.settings", mock_settings):
                yield mock_settings

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess for exportfs."""
        with patch("src.shares.nfs.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            yield mock_run

    def test_list_exports_empty(self, mock_settings, mock_subprocess):
        """Test listing exports when none exist."""
        exports = nfs.list_exports()
        assert exports == []

    def test_create_export_success(self, mock_settings, mock_subprocess, tmp_path):
        """Test creating a new NFS export."""
        export_path = str(tmp_path / "nfs_export")
        os.makedirs(export_path)

        export = NfsExportCreate(
            path=export_path,
            clients="192.168.1.0/24",
            options="rw,sync,no_subtree_check"
        )

        result = nfs.create_export(export)

        assert result.path == export_path
        assert result.clients == "192.168.1.0/24"
        mock_subprocess.assert_called()
        with open(mock_settings.nfs_exports_path) as f:
            assert f.read() == f"{export_path} 192.168.1.0/24(rw,sync,no_subtree_check)\n"

    def test_create_export_duplicate(self, mock_settings, mock_subprocess, tmp_path):
        """Test that duplicate exports are rejected."""
        export_path = str(tmp_path / "nfs_export")
        os.makedirs(export_path)

        export = NfsExportCreate(path=export_path)
        nfs.create_export(export)

        with pytest.raises(ValueError, match="already exists"):
            nfs.create_export(export)

    def test_delete_export_success(self, mock_settings, mock_subprocess, tmp_path):
        """Test deleting an NFS export."""
        export_path = str(tmp_path / "nfs_export")
        os.makedirs(export_path)

        export = NfsExportCreate(path=export_path)
        nfs.create_export(export)

        nfs.delete_export(export_path)

        exports = nfs.list_exports()
        assert len([e for e in exports if e.path == export_path]) == 0

    def test_delete_export_not_found(self, mock_settings, mock_subprocess):
        """Test deleting a non-existent export."""
        with pytest.raises(ValueError, match="not found"):
            nfs.delete_export("/nonexistent/path")

    def test_parse_exports(self, mock_settings, mock_subprocess):
        """Test parsing exports file with multiple entries."""
        exports_content = """/data/share1 192.168.1.0/24(rw,sync)
/data/share2 *(ro,async)
"""
        with open(mock_settings.nfs_exports_path, "w") as f:
            f.write(exports_content)

        exports = nfs.list_exports()

        assert len(exports) == 2

        export1 = next(e for e in exports if e.path == "/data/share1")
        assert export1.clients == "192.168.1.0/24"
        assert export1.options == "rw,sync"

        export2 = next(e for e in exports if e.path == "/data/share2")
        assert export2.clients == "*"
        assert export2.options == "ro,async"

    @pytest.mark.parametrize(
        "clients", ["*", "192.168.1.0/24", "10.0.0.1", "fe80::/64", "192.168.1.0/255.255.255.0",
                    "nas.local", "*.example.com", "host?"],
    )
    def test_valid_clients_accepted(self, mock_settings, mock_subprocess, tmp_path, clients):
        result = nfs.create_export(NfsExportCreate(path=str(tmp_path / "e"), clients=clients))
        assert result.clients == clients

    @pytest.mark.parametrize(
        "clients",
        ["* (rw) evil", "*(rw,no_root_squash)", "host\n/etc *(rw)", "a b", "", "host#x",
         "a,b", "@netgroup", "10.0.0.0/33", "-host", "x" * 256],
    )
    def test_hostile_clients_rejected(self, mock_settings, mock_subprocess, tmp_path, clients):
        with pytest.raises((ValueError, HTTPException)):
            nfs.create_export(NfsExportCreate(path=str(tmp_path / "e"), clients=clients))
        assert nfs.list_exports() == []

    @pytest.mark.parametrize(
        "options", ["rw,sync,no_subtree_check", "ro", "rw,anonuid=1000,anongid=100,fsid=1,sec=krb5:sys",
                    "rw,no_root_squash,insecure", "rw,fsid=root"],
    )
    def test_valid_options_accepted(self, mock_settings, mock_subprocess, tmp_path, options):
        assert nfs.create_export(NfsExportCreate(path=str(tmp_path / "e"), options=options)).options == options

    @pytest.mark.parametrize(
        "options",
        ["rw)\n/ *(rw", "rw,nosuchopt", "", "rw,,sync", "anonuid=abc", "mp=/etc", "rw sync",
         "rw)(", "sec=none", "rw\n", "refer=/x@host"],
    )
    def test_hostile_options_rejected(self, mock_settings, mock_subprocess, tmp_path, options):
        with pytest.raises(ValueError):
            nfs.create_export(NfsExportCreate(path=str(tmp_path / "e"), options=options))
        assert nfs.list_exports() == []

    @pytest.mark.parametrize(
        "path_template", ["/etc", "{tmp}", "{tmp}/../x", "{tmp}/a b", "{tmp}/x\n/ *", "{tmp}/a#b", "{tmp}/a(b)", "x"],
    )
    def test_hostile_paths_rejected(self, mock_settings, mock_subprocess, tmp_path, path_template):
        with pytest.raises((ValueError, HTTPException)):
            nfs.create_export(NfsExportCreate(path=path_template.format(tmp=tmp_path)))
        assert nfs.list_exports() == []

    def test_export_through_symlink_rejected(self, mock_settings, mock_subprocess, tmp_path):
        (tmp_path / "private").mkdir()
        (tmp_path / "apps").mkdir()
        os.symlink(tmp_path / "private", tmp_path / "apps" / "downloads")
        for path in (tmp_path / "apps" / "downloads", tmp_path / "apps" / "downloads" / "sub"):
            with pytest.raises(HTTPException) as exc:
                nfs.create_export(NfsExportCreate(path=str(path)))
            assert exc.value.status_code == 400
        assert nfs.list_exports() == []
        assert list((tmp_path / "private").iterdir()) == []

    def test_export_dir_creation_does_not_follow_swapped_symlink(self, mock_settings, mock_subprocess, tmp_path):
        """A component replaced by a symlink after validation must not be followed by mkdir."""
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, tmp_path / "swapped")
        swapped = str(tmp_path / "swapped" / "new")
        with patch("src.shares.nfs.validate_pools_path", return_value=swapped):
            with pytest.raises(ValueError, match="not a usable directory"):
                nfs.create_export(NfsExportCreate(path=swapped))
        assert list(outside.iterdir()) == []
        assert nfs.list_exports() == []


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

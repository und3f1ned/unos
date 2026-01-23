import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
from src.shares import smb, nfs
from src.shares.schemas import SmbShareCreate, SmbShareInfo, NfsExportCreate


class TestSmbModule:
    """Tests for SMB share management."""

    @pytest.fixture
    def temp_smb_conf(self):
        """Create temporary smb.conf."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write("""[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no

""")
            path = f.name
        yield path
        if os.path.exists(path):
            os.unlink(path)

    @pytest.fixture
    def mock_settings(self, temp_smb_conf):
        """Mock settings with temp paths."""
        mock_settings = MagicMock()
        mock_settings.smb_conf_path = temp_smb_conf
        with patch("src.shares.smb.settings", mock_settings):
            yield mock_settings

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess for smbcontrol."""
        with patch("src.shares.smb.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            yield mock_run

    def test_list_shares_empty(self, mock_settings, mock_subprocess):
        """Test listing shares when none exist."""
        shares = smb.list_shares()
        assert shares == []

    def test_create_share_success(self, mock_settings, mock_subprocess, tmp_path):
        """Test creating a new SMB share."""
        share_path = str(tmp_path / "test_share")
        share = SmbShareCreate(
            name="test_share",
            path=share_path,
            comment="Test share",
            browseable=True,
            readonly=False,
            guest_ok=True,
            valid_users=[]
        )
        
        result = smb.create_share(share)
        
        assert result.name == "test_share"
        assert result.path == share_path
        assert result.comment == "Test share"
        assert result.browseable is True
        assert result.readonly is False
        assert result.guest_ok is True
        assert os.path.exists(share_path)
        mock_subprocess.assert_called()

    def test_create_share_invalid_name(self, mock_settings, mock_subprocess):
        """Test that invalid share names are rejected."""
        share = SmbShareCreate(
            name="invalid-name!",
            path="/tmp/test"
        )
        
        with pytest.raises(ValueError, match="alphanumeric"):
            smb.create_share(share)

    def test_create_share_duplicate(self, mock_settings, mock_subprocess, tmp_path):
        """Test that duplicate share names are rejected."""
        share_path = str(tmp_path / "test_share")
        share = SmbShareCreate(name="test_share", path=share_path)
        
        smb.create_share(share)
        
        with pytest.raises(ValueError, match="already exists"):
            smb.create_share(share)

    def test_get_share_exists(self, mock_settings, mock_subprocess, tmp_path):
        """Test getting an existing share."""
        share_path = str(tmp_path / "test_share")
        share = SmbShareCreate(name="test_share", path=share_path)
        smb.create_share(share)
        
        result = smb.get_share("test_share")
        
        assert result is not None
        assert result.name == "test_share"

    def test_get_share_not_found(self, mock_settings, mock_subprocess):
        """Test getting a non-existent share."""
        result = smb.get_share("nonexistent")
        assert result is None

    def test_delete_share_success(self, mock_settings, mock_subprocess, tmp_path):
        """Test deleting an existing share."""
        share_path = str(tmp_path / "test_share")
        share = SmbShareCreate(name="test_share", path=share_path)
        smb.create_share(share)
        
        smb.delete_share("test_share")
        
        result = smb.get_share("test_share")
        assert result is None

    def test_delete_share_not_found(self, mock_settings, mock_subprocess):
        """Test deleting a non-existent share."""
        with pytest.raises(ValueError, match="not found"):
            smb.delete_share("nonexistent")

    def test_update_share(self, mock_settings, mock_subprocess, tmp_path):
        """Test updating an existing share."""
        share_path = str(tmp_path / "test_share")
        share = SmbShareCreate(name="test_share", path=share_path, comment="Original")
        smb.create_share(share)
        
        updated = SmbShareCreate(
            name="test_share",
            path=share_path,
            comment="Updated comment",
            readonly=True
        )
        result = smb.update_share("test_share", updated)
        
        assert result.comment == "Updated comment"
        assert result.readonly is True

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
        
        assert len(shares) == 2
        
        share1 = next(s for s in shares if s.name == "share1")
        assert share1.path == "/data/share1"
        assert share1.browseable is True
        assert share1.guest_ok is True
        
        share2 = next(s for s in shares if s.name == "share2")
        assert share2.readonly is True
        assert share2.valid_users == ["user1", "user2"]


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
    def mock_settings(self, temp_exports):
        """Mock settings with temp paths."""
        mock_settings = MagicMock()
        mock_settings.nfs_exports_path = temp_exports
        with patch("src.shares.nfs.settings", mock_settings):
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

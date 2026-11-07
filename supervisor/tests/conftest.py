import pytest
import os
import secrets
import tempfile
from unittest.mock import patch, MagicMock

# Set test environment variables before importing anything
os.environ["UNOS_DB_PATH"] = ":memory:"
# Random per run: secrets this repo published (such as the "test-secret-key"
# used here before) are refused at startup (config.INSECURE_JWT_SECRETS).
os.environ["UNOS_JWT_SECRET"] = secrets.token_hex(32)
os.environ["UNOS_SMB_CONF_PATH"] = "/tmp/test_smb.conf"
os.environ["UNOS_NFS_EXPORTS_PATH"] = "/tmp/test_exports"
os.environ["UNOS_POOLS_BASE_PATH"] = "/tmp/test_pools"


@pytest.fixture(autouse=True)
def _test_pools_count_as_mounted(monkeypatch):
    """A share folder is created only through a pool root on the pool's mount (spec 5.4
    rule 7): where /proc/self/mountinfo exists (Linux), the pool root's mount ID must be
    a mount at <pools base>/<pool>. The tests' pools are plain folders, not mount
    points, so the check is replaced here. Tests of the check itself use the function
    as service imports it (test_share_api.REAL_POOL_ROOT_MOUNT_PROBLEM)."""
    if os.path.exists("/proc/self/mountinfo"):
        from src.shares import service

        monkeypatch.setattr(service, "_pool_root_mount_problem", lambda root_fd, pool: None)


@pytest.fixture
def temp_smb_conf():
    """Create temporary smb.conf for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
        f.write("""[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user

""")
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def temp_exports():
    """Create temporary exports file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("")
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run for testing."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock_run

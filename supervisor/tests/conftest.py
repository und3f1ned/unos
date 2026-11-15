import pytest
import os
import secrets
import sys
import tempfile
from unittest.mock import patch, MagicMock

# The suite runs git in temporary repositories of its own. git exports GIT_DIR, GIT_INDEX_FILE
# and others to its hooks and to `git rebase -x` commands; inherited, they would point those git
# runs at the developer's repository. They are removed here, when pytest loads this file and
# before any test module builds an environment from os.environ.
for _name in [name for name in os.environ if name.startswith("GIT_")]:
    del os.environ[_name]

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
    """A share folder is created only through a pool root on the pool's mount (ADR-0152):
    where /proc/self/mountinfo exists (Linux), the pool root's mount ID must be
    a mount at <pools base>/<pool>. The tests' pools are plain folders, not mount
    points, so the check is replaced here. Tests of the check itself use the function
    as service imports it (test_share_api.REAL_POOL_ROOT_MOUNT_PROBLEM)."""
    if os.path.exists("/proc/self/mountinfo"):
        from src.shares import service

        monkeypatch.setattr(service, "_pool_root_mount_problem", lambda root_fd, pool: None)


@pytest.fixture(autouse=True)
def _smart_history_not_read_yet(monkeypatch):
    """The SMART monitor keeps its history in RAM from its first pass on (ADR-0269): every
    test starts before that pass, so the SMART endpoints read the history file it writes."""
    health_router = sys.modules.get("src.health.router")
    if health_router is not None:
        monkeypatch.setattr(health_router, "_smart_history", health_router._SmartHistoryState())


@pytest.fixture(autouse=True)
def _no_problems_yet(monkeypatch):
    """The problems of the NAS, the SMART monitor's verdicts of the disks (and the disks' I/O counts at
    its reads, ADR-0378) and the pools that the RAID monitor read last are kept in RAM (ADR-0332): every
    test starts without them, as the supervisor does."""
    problems = sys.modules.get("src.health.problems")
    if problems is not None:
        monkeypatch.setattr(problems, "_by_source", {})
    health_router = sys.modules.get("src.health.router")
    if health_router is not None:
        monkeypatch.setattr(health_router, "_disk_verdicts", {})
        monkeypatch.setattr(health_router, "_disk_io_at_read", {})
        monkeypatch.setattr(health_router, "_pools_read", None)


@pytest.fixture(autouse=True)
def _no_scrubs_seen_yet(monkeypatch):
    """The scrubs that the events socket follows are kept in RAM (ADR-0367): every test starts
    without them, as the supervisor does."""
    scrubs = sys.modules.get("src.storage.scrubs")
    if scrubs is not None:
        monkeypatch.setattr(scrubs, "_scrubs", {})
        monkeypatch.setattr(scrubs, "_version", 0)
        monkeypatch.setattr(scrubs, "_noted_at", {})
        monkeypatch.setattr(scrubs, "_reading", None)
        monkeypatch.setattr(scrubs, "_read_at", float("-inf"))


@pytest.fixture(autouse=True)
def _notification_files_in_tmp_path(monkeypatch, tmp_path):
    """The notification channels and their history are files under /data
    (src/notifications/router.py), and every alert that a monitor sends is recorded in the
    history: in the tests they live in the test's tmp_path, never in /data of this machine."""
    import importlib

    notifications = importlib.import_module("src.notifications.router")
    monkeypatch.setattr(notifications, "CONFIG_FILE", str(tmp_path / "notifications.json"))
    monkeypatch.setattr(notifications, "LOGS_FILE", str(tmp_path / "notification_logs.json"))


@pytest.fixture(autouse=True)
def _audit_log_in_tmp_path(monkeypatch, tmp_path_factory):
    """Changes of backups, power, tasks, the host's settings, storage, snapshots, containers and
    apps write audit entries (ADR-0350, ADR-0351), as sign-ins and shares do: in the tests the
    audit log is in a temporary folder of its own, never in /data of this machine, and not in the
    test's tmp_path, which some tests use as the pools' folder and compare whole. A test that
    reads it sets AUDIT_FILE itself."""
    from src.audit import router as audit_router

    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path_factory.mktemp("audit") / "audit.jsonl"))


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
def mock_subprocess():
    """Mock subprocess.run for testing."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock_run

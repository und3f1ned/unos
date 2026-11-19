import pytest
import atexit
import os
import secrets
import shutil
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
# smb.conf, the NFS export file and the pools' folder are in a temporary folder of this process
# alone, removed when the run ends: two runs at once, in two worktrees, and the workers of one
# parallel run (pytest-xdist) never share them (ADR-0383). pytest_unconfigure removes it at the end
# of a run or a worker, atexit when pytest stops before (a usage error).
_RUN_DIR = tempfile.mkdtemp(prefix="unos-tests-")
os.environ["UNOS_SMB_CONF_PATH"] = os.path.join(_RUN_DIR, "smb.conf")
os.environ["UNOS_NFS_EXPORTS_PATH"] = os.path.join(_RUN_DIR, "exports")
os.environ["UNOS_POOLS_BASE_PATH"] = os.path.join(_RUN_DIR, "pools")
# The supervisor's data directory too (src/paths.py, ADR-0417): no test reaches /data. The supervisor
# reads it once, at its first import, so no test changes it: a test moves a module's path with
# monkeypatch (test_data_dir.py). Read-only, as /data is to the tests: a test that did not move a
# path it writes fails, instead of leaving a file to the next tests of its process.
os.environ["UNOS_DATA_DIR"] = os.path.join(_RUN_DIR, "data")
os.mkdir(os.environ["UNOS_DATA_DIR"], 0o555)
atexit.register(shutil.rmtree, _RUN_DIR, ignore_errors=True)


def pytest_unconfigure(config):
    shutil.rmtree(_RUN_DIR, ignore_errors=True)


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
def _no_install_jobs_yet(monkeypatch):
    """The install jobs of apps are kept in RAM (ADR-0384): every test starts without them, as the
    supervisor does, and a job that a test left running is stopped and waited for at its end. It
    takes monkeypatch so that it ends before monkeypatch puts back what the test replaced: a job
    left running must never meet the real Docker or host."""
    install_jobs = sys.modules.get("src.apps.install_jobs")
    if install_jobs is not None:
        install_jobs._jobs.clear()
        install_jobs._version = 0
        # A GET that waits for a job's end looks at it often: the install tests wait for each end.
        monkeypatch.setattr(install_jobs, "WAIT_STEP_SECONDS", 0.01)
    yield
    install_jobs = sys.modules.get("src.apps.install_jobs")
    if install_jobs is not None:
        install_jobs.stop_for_shutdown(timeout=10)
        running = install_jobs._running
        assert running is None or running.thread is None or not running.thread.is_alive(), (
            "an install job of the test still runs"
        )
        install_jobs._jobs.clear()
        install_jobs._version = 0


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
def _no_disk_nodes_of_this_machine(monkeypatch, tmp_path_factory):
    """host.run_smartctl first makes the container's node of the disk the host's (ADR-0414): no test
    reads this machine's sysfs or makes or replaces a node in its /dev. The three folders are
    missing ones of the test's own, so container_disk_node changes nothing; install_fake_host gives
    it the fake host's disks and container, and tests/test_hot_plugged_smart.py folders that it fills."""
    host = sys.modules.get("src.host")
    if host is not None:
        folder = tmp_path_factory.mktemp("disk-nodes")
        monkeypatch.setattr(host, "SYS_BLOCK", str(folder / "no-sys-block"))
        monkeypatch.setattr(host, "CONTAINER_DEV", str(folder / "no-dev"))
        monkeypatch.setattr(host, "HOST_DEV", str(folder / "no-host-dev"))
        monkeypatch.setattr(host, "_disk_node_problems", set())


@pytest.fixture(autouse=True)
def _no_volume_use_read_yet(monkeypatch, tmp_path_factory):
    """The use of the volumes' quotas that the RAID monitor reads, with its history, is kept in RAM and
    written to /data only at shutdown (storage.volume_usage): every test starts without it, its file is in a
    temporary folder of its own, and the kernel's sysfs of this machine is never read (a test that
    reads sysfs gives qgroups.SYS_FS_BTRFS a folder of its own)."""
    volume_usage = sys.modules.get("src.storage.volume_usage")
    if volume_usage is not None:
        monkeypatch.setattr(volume_usage, "_state", volume_usage._State())
        folder = tmp_path_factory.mktemp("volume-usage")
        monkeypatch.setattr(volume_usage, "HISTORY_FILE", str(folder / "volume_usage.json"))
    qgroups = sys.modules.get("src.storage.qgroups")
    if qgroups is not None:
        monkeypatch.setattr(qgroups, "SYS_FS_BTRFS", "/nonexistent/unos-tests/sys/fs/btrfs")


@pytest.fixture(autouse=True)
def _no_web_ui_to_ask(monkeypatch, tmp_path_factory):
    """The served certificate (ADR-0404): its file in a temporary folder of each test, and no web
    UI's Caddy to shake hands with: the handshake goes to a closed port of this machine, and the
    status says unknown (a test that asks a TLS server gives serving.CADDY_TLS_PORT its own), so
    that no test looks up the name webui or reaches a server of this machine's network; and no
    Docker for `caddy reload`, so that no test runs docker exec in a container of this machine."""
    serving = sys.modules.get("src.security.serving")
    if serving is not None:
        monkeypatch.setattr(serving, "SERVED_DIR", str(tmp_path_factory.mktemp("served-certificate")))
        monkeypatch.setattr(serving, "CADDY_TLS_HOST", "127.0.0.1")
        monkeypatch.setattr(serving, "CADDY_TLS_PORT", 1)

        def no_docker():
            from src.containers.docker import DockerUnavailable
            raise DockerUnavailable("the tests reach no Docker")

        monkeypatch.setattr(serving, "_docker_client", no_docker)


@pytest.fixture(autouse=True)
def _no_app_use_read_yet(monkeypatch):
    """The use of the apps' containers and its problems are kept in RAM (ADR-0390): every test
    starts without them, and the cgroups of this machine are never read (a test that reads cgroups
    gives usage.CGROUP_ROOT a folder of its own)."""
    usage = sys.modules.get("src.apps.usage")
    if usage is not None:
        monkeypatch.setattr(usage, "_state", usage._State())
        monkeypatch.setattr(usage, "CGROUP_ROOT", usage.Path("/nonexistent/unos-tests/sys/fs/cgroup"))


@pytest.fixture(autouse=True)
def _no_power_reads_yet(monkeypatch):
    """The hdparm -C reads of the disks' power that run, and those that timed out, are kept in RAM
    (storage.disks.power_state, ADR-0378): every test starts without them, as the supervisor does."""
    disks = sys.modules.get("src.storage.disks")
    if disks is not None:
        monkeypatch.setattr(disks, "_power_reading", set())
        monkeypatch.setattr(disks, "_power_stuck", {})


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
def _no_balances_seen_yet(monkeypatch):
    """The balances that the events socket follows, and when each was first seen running, are kept
    in RAM (ADR-0379): every test starts without them, as the supervisor does."""
    balances = sys.modules.get("src.storage.balances")
    if balances is not None:
        monkeypatch.setattr(balances, "_balances", {})
        monkeypatch.setattr(balances, "_version", 0)
        monkeypatch.setattr(balances, "_noted_at", {})
        monkeypatch.setattr(balances, "_reading", None)
        monkeypatch.setattr(balances, "_read_at", float("-inf"))
        monkeypatch.setattr(balances, "_since", {})


@pytest.fixture(autouse=True)
def _no_disk_removals_seen_yet(monkeypatch):
    """The disk removals that the events socket follows are kept in RAM (ADR-0411): every test
    starts without them, as the supervisor does."""
    devices = sys.modules.get("src.storage.devices")
    if devices is not None:
        monkeypatch.setattr(devices, "_removals", {})
        monkeypatch.setattr(devices, "_version", 0)
        monkeypatch.setattr(devices, "_noted_at", {})
        monkeypatch.setattr(devices, "_reading", None)
        monkeypatch.setattr(devices, "_read_at", float("-inf"))


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

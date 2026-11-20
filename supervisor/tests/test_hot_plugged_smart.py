"""SMART of a disk attached after the supervisor started (storage.hot-plugged-disks-smart, ADR-0414).

smartctl runs in the supervisor's container, whose /dev Docker fills once, when the container
starts: a disk attached later, such as a USB disk, had no node there, so its SMART data, its
self-tests, the SMART monitor's records of it and the power read of smartctl failed with "No such
device" until the supervisor restarted, while lsblk on the host listed it (ADR-0360). Every
smartctl now goes through host.run_smartctl, which first makes the container's node of the disk
the host's (host.container_disk_node): the device number of /sys/block/<name>/dev, which the
container sees as the host does.

``FakeHost`` (test_pool_persistence.py) models the container's /dev (``container_dev``): a node
for every disk there when the container started (add_disk), none for one attached later
(attach_disk), and smartctl opens the node, so it reads the disk that has the node's number now.
The second part tests the real sysfs read and node making in folders of the test's own.
"""

import ast
import importlib
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from src import host
from src.audit import router as audit_router

from .test_pool_persistence import admin_client, install_fake_host
from .test_scheduler_validation import _create_smart_task, make_env

health_router = importlib.import_module("src.health.router")

SRC = Path(__file__).resolve().parent.parent / "src"

# The real sysfs read and node making, before install_fake_host replaces them in a test.
REAL_HOST_DISK_NUMBER = host._host_disk_number
REAL_CONTAINER_NODE_NUMBER = host._container_node_number
REAL_DEV_IS_THE_CONTAINERS = host._dev_is_the_containers
REAL_MAKE_CONTAINER_NODE = host._make_container_node


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """The dev device's disk sda, there when the supervisor started, and a USB disk sdb attached
    since, which the container's /dev has no node of."""
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", size=640_135_028_736, model="WDC WD6400AACS-00G8B0", serial="WD-W3")
    fake.attach_disk("sdb", size=2_000_398_934_016, model="USB Disk", serial="USB-1")
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(tmp_path / "smart_history.json"))
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    return fake


@pytest.fixture
def api(fake):
    with admin_client() as client:
        yield client


def number(fake, name: str) -> str:
    return fake.node(name)["maj:min"]


def smartctl_of(fake, device: str) -> list[list[str]]:
    return [cmd for cmd in fake.container_calls if cmd[0] == "smartctl" and cmd[-1] == device]


# --- the steps of the entry: Disk Health, its self-tests, the SMART monitor -------------------------


def test_disk_health_shows_the_smart_data_of_a_disk_attached_after_the_start(api, fake):
    response = api.get("/api/v1/health/disk/sdb")

    assert response.status_code == 200, response.text
    info = response.json()
    assert (info["device"], info["serial"], info["model"], info["health_status"]) == (
        "/dev/sdb", "USB-1", "USB Disk", "PASSED")
    assert info["power_on_hours"] == 51832
    assert fake.container_dev["/dev/sdb"] == number(fake, "sdb")
    assert fake.nodes_made == [("/dev/sdb", number(fake, "sdb"))]


def test_disk_health_lists_it_with_its_smart_data_and_counts_it_healthy(api, fake):
    disks = api.get("/api/v1/health/disks").json()["disks"]
    overview = api.get("/api/v1/health/overview").json()

    assert [(d["device"], d["serial"], d["health_status"]) for d in disks] == [
        ("/dev/sda", "WD-W3", "PASSED"), ("/dev/sdb", "USB-1", "PASSED")]
    assert (overview["total_disks"], overview["healthy"], overview["unknown"]) == (2, 2, 0)
    # One node, made at the first read; the disk that was there keeps Docker's.
    assert fake.nodes_made == [("/dev/sdb", number(fake, "sdb"))]


def test_a_self_test_starts_on_it(api, fake):
    response = api.post("/api/v1/health/disk/sdb/test?test_type=short")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "started", "message": "Short test started on /dev/sdb"}
    assert fake.self_tests == [("/dev/sdb", "short")]


@pytest.fixture
def scheduler_env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


def test_a_smart_test_task_runs_on_it(scheduler_env):
    env = scheduler_env
    env.host.attach_disk("sdb", serial="USB-1")
    env.run.side_effect = env.host.run_in_container
    task = _create_smart_task(env, "/dev/sdb")

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    assert (run["status"], run["error"]) == ("success", None), run
    assert env.host.self_tests == [("/dev/sdb", "short")]


def test_the_smart_monitor_records_it(fake):
    assert health_router._smart_monitor_pass() == []

    history = health_router.load_smart_history()
    assert {serial: [entry["device"] for entry in entries] for serial, entries in history.items()} == {
        "WD-W3": ["/dev/sda"], "USB-1": ["/dev/sdb"]}
    assert set(health_router._disk_verdicts) == {"/dev/sda", "/dev/sdb"}


def test_a_sleeping_disk_attached_after_the_start_is_left_asleep(api, fake):
    """The node is made from sysfs, which sends the disk nothing; smartctl -n standby then finds it
    asleep (ADR-0378)."""
    fake.asleep.add("/dev/sdb")

    info = api.get("/api/v1/health/disk/sdb").json()
    health_router._smart_monitor_pass()

    assert (info["health_status"], fake.woken) == ("STANDBY", [])
    assert fake.nodes_made == [("/dev/sdb", number(fake, "sdb"))]
    assert "USB-1" not in health_router.load_smart_history()


# --- the other reads of smartctl: the Storage page's SMART data, the power read, the idle timeout ---


def test_the_storage_page_reads_its_smart_data(api, fake):
    data = api.get("/api/v1/storage/disks/sdb/smart").json()

    assert (data["device"], data["healthy"], data["temperature"]) == ("/dev/sdb", True, 31)


@pytest.mark.parametrize("case", ["no hdparm", "EPC idle_a"])
def test_its_power_read_with_smartctl_answers(api, fake, case):
    """Where hdparm cannot tell (a host without it, an EPC disk's power condition), smartctl -n asks
    the disk in the container (ADR-0378), without waking it."""
    if case == "no hdparm":
        fake.missing_programs.add("hdparm")
    else:
        fake.epc["/dev/sdb"] = "idle_a"

    disks = {d["name"]: d for d in api.get("/api/v1/storage/disks").json()}

    assert disks["sdb"]["power_state"] == "active"
    assert smartctl_of(fake, "/dev/sdb")


def test_an_idle_timeout_turns_off_its_automatic_offline_scans(api, fake):
    fake.offline_collection["/dev/sdb"] = True

    response = api.put("/api/v1/storage/disks/sdb/spindown", json={"minutes": 5})

    assert response.status_code == 200, response.text
    assert response.json()["warning"] is None
    assert fake.offline_collection["/dev/sdb"] is False


# --- the node: left alone when right, replaced when wrong, never guessed ------------------------------


def test_a_disk_that_was_there_at_the_start_keeps_its_node_and_nothing_is_logged(api, fake, caplog):
    with caplog.at_level(logging.DEBUG, logger="src.host"):
        assert api.get("/api/v1/health/disk/sda").json()["health_status"] == "PASSED"
        assert api.post("/api/v1/health/disk/sda/test?test_type=short").status_code == 200
        health_router._smart_monitor_pass()

    assert fake.nodes_made == [("/dev/sdb", number(fake, "sdb"))]  # the monitor's read of sdb
    assert [r.getMessage() for r in caplog.records if r.name == "src.host"] == [
        f"Made /dev/sdb in the container as the host's disk {number(fake, 'sdb')}"]


def test_a_second_read_makes_nothing_and_logs_nothing(api, fake, caplog):
    assert api.get("/api/v1/health/disk/sdb").status_code == 200
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="src.host"):
        assert api.get("/api/v1/health/disk/sdb").json()["serial"] == "USB-1"
        health_router._smart_monitor_pass()

    assert len(fake.nodes_made) == 1
    assert [r for r in caplog.records if r.name == "src.host"] == []


def test_a_node_whose_number_another_disk_took_is_replaced_before_smartctl_reads_it(api, fake, caplog):
    """An NVMe disk's device number is not its name's: the disk that left took its node's number with
    it, and a disk attached since may have it now. The node of the name must be the new disk's."""
    fake.add_disk("nvme1n1", rota=False, serial="NV-GONE", majmin="259:2")  # there at the start
    fake.detach_disk("nvme1n1")
    fake.attach_disk("nvme2n1", rota=False, serial="NV-OTHER", majmin="259:2")
    fake.attach_disk("nvme1n1", rota=False, serial="NV-NEW", majmin="259:7")

    with caplog.at_level(logging.INFO, logger="src.host"):
        info = api.get("/api/v1/health/disk/nvme1n1").json()

    assert info["serial"] == "NV-NEW"
    assert fake.container_dev["/dev/nvme1n1"] == "259:7"
    assert "Replaced /dev/nvme1n1 in the container as the host's disk 259:7" in caplog.text


def test_a_disk_that_left_before_smartctl_ran_is_not_read(api, fake, caplog, monkeypatch):
    """lsblk listed it, and it left before smartctl: no node is made of a name the host has no
    whole disk of, smartctl does not run, and the answer is that of a disk the host does not have."""
    def gone_after_the_listing(cmd, **kwargs):
        result = fake.run(cmd, **kwargs)
        if cmd[0] == "lsblk":
            fake.detach_disk("sdb")
        return result

    monkeypatch.setattr(host, "run_on_host", gone_after_the_listing)
    with caplog.at_level(logging.DEBUG, logger="src.host"):
        read = api.get("/api/v1/health/disk/sdb")
        fake.attach_disk("sdb", serial="USB-1")
        started = api.post("/api/v1/health/disk/sdb/test?test_type=short")

    assert (read.status_code, read.json()["detail"]) == (400, "The host has no disk sdb")
    assert (started.status_code, started.json()["detail"]) == (400, "The host has no disk sdb")
    assert fake.nodes_made == [] and smartctl_of(fake, "/dev/sdb") == []
    assert [r for r in caplog.records if r.name == "src.host"] == []
    with pytest.raises(host.NoHostDisk, match="The host has no disk sdb"):
        host.run_smartctl(["-n", "standby", "-j", "-a"], "/dev/sdb", timeout=30)


def test_a_partition_gets_no_node_and_no_smartctl(fake):
    fake.add_partition("sda", "sda1", fstype="btrfs")

    with pytest.raises(host.NoHostDisk, match="The host has no disk sda1"):
        host.run_smartctl(["-t", "short"], "/dev/sda1", timeout=30)

    assert fake.nodes_made == [] and fake.container_calls == []


def refused(path, number):
    """os.mknod's refusal, of the temporary name that _make_container_node gives the node."""
    raise PermissionError(1, "Operation not permitted", os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.unos-new"))


REFUSED = "[Errno 1] Operation not permitted: '{}'"


@pytest.fixture
def node_fails(fake, monkeypatch):
    monkeypatch.setattr(host, "_make_container_node", refused)
    # As container_disk_node joins it (tests/conftest.py's missing folder) and its temporary name.
    return REFUSED.format(os.path.join(host.CONTAINER_DEV, ".sdb.unos-new"))


def test_a_node_that_cannot_be_made_fails_the_reads_with_the_reason_and_runs_no_smartctl(
        api, fake, node_fails, caplog):
    with caplog.at_level(logging.WARNING, logger="src.host"):
        first = api.get("/api/v1/health/disk/sdb")
        second = api.get("/api/v1/health/disk/sdb")
        started = api.post("/api/v1/health/disk/sdb/test?test_type=short")
        disks = api.get("/api/v1/health/disks").json()["disks"]
        storage = api.get("/api/v1/storage/disks/sdb/smart").json()
        assert health_router._smart_monitor_pass() == []

    reason = f"The container's node of /dev/sdb could not be made: {node_fails}"
    for response in (first, second):
        assert (response.status_code, response.json()["detail"]) == (500, f"Failed to get SMART data: {reason}")
    assert (started.status_code, started.json()["detail"]) == (500, f"smartctl could not run on /dev/sdb: {reason}")
    assert [(d["device"], d["health_status"]) for d in disks] == [("/dev/sda", "PASSED"), ("/dev/sdb", "UNKNOWN")]
    assert (storage["healthy"], storage["power_state"], storage["raw_data"]) == (False, "unknown", {"error": reason})
    assert list(health_router.load_smart_history()) == ["WD-W3"]
    assert smartctl_of(fake, "/dev/sdb") == [] and fake.self_tests == []
    # Logged once for the disk, however often it is asked for.
    assert [r.getMessage() for r in caplog.records if r.name == "src.host"] == [
        f"Could not make the container's node of /dev/sdb the host's disk: {node_fails}"]


def test_a_node_that_cannot_be_made_leaves_the_power_unknown(api, fake, node_fails):
    fake.missing_programs.add("hdparm")

    disks = {d["name"]: d for d in api.get("/api/v1/storage/disks").json()}

    assert (disks["sda"]["power_state"], disks["sdb"]["power_state"]) == ("active", "unknown")
    assert smartctl_of(fake, "/dev/sdb") == []


def test_a_node_that_cannot_be_made_fails_the_smart_test_task_with_the_reason(scheduler_env, monkeypatch):
    env = scheduler_env
    env.host.attach_disk("sdb", serial="USB-1")
    env.run.side_effect = env.host.run_in_container
    monkeypatch.setattr(host, "_make_container_node", refused)
    task = _create_smart_task(env, "/dev/sdb")

    env.client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")

    (run,) = env.client.get(f"/api/v1/scheduler/runs/{task['id']}").json()
    temporary = os.path.join(host.CONTAINER_DEV, ".sdb.unos-new")
    assert (run["status"], run["error"]) == (
        "failed", f"[Errno 1] The container's node of /dev/sdb could not be made: {REFUSED.format(temporary)}")
    assert env.host.self_tests == []


def test_a_problem_is_logged_again_once_the_node_was_right_in_between(fake, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="src.host"):
        monkeypatch.setattr(host, "_make_container_node", refused)
        for _ in range(2):
            with pytest.raises(host.DiskNodeError):
                host.container_disk_node("/dev/sdb")
        monkeypatch.setattr(host, "_make_container_node", fake.make_container_node)
        host.container_disk_node("/dev/sdb")
        fake.container_dev.pop("/dev/sdb")
        monkeypatch.setattr(host, "_make_container_node", refused)
        with pytest.raises(host.DiskNodeError):
            host.container_disk_node("/dev/sdb")

    assert len([r for r in caplog.records if r.name == "src.host"]) == 2


def test_where_host_commands_run_locally_nothing_is_made(fake, monkeypatch):
    """The stand-in and the compose test stack run smartctl, if at all, on the machine itself."""
    monkeypatch.setenv("UNOS_HOST_EXEC", "local")

    host.container_disk_node("/dev/sdb")

    assert fake.nodes_made == [] and fake.disk_asked is None


def test_a_dev_that_is_the_host_s_own_is_never_written(fake, monkeypatch):
    """The host's /dev bind-mounted into the container, or a supervisor that runs on the host: the
    kernel keeps its nodes, and UNOS makes none there."""
    monkeypatch.setattr(host, "_dev_is_the_containers", lambda: False)

    host.container_disk_node("/dev/sdb")

    assert fake.nodes_made == []


@pytest.mark.parametrize("device", ["/dev/disk/by-id/usb-USB_Disk", "/dev/../dev/sdb", "sdb", "/dev/Sdb"])
def test_a_device_that_is_not_dev_and_a_plain_name_is_left_to_smartctl_as_before(fake, device):
    host.container_disk_node(device)

    assert fake.nodes_made == [] and fake.disk_asked is None


def _smartctl_names(tree: ast.AST) -> list[ast.Constant]:
    """The string constants "smartctl" of ``tree`` that name the program: not those that read a member
    of smartctl's JSON (``data.get("smartctl")``, ``data["smartctl"]``, ``_member(data, "smartctl",
    ...)``)."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    names = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and node.value == "smartctl"):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Subscript):
            continue
        if (isinstance(parent, ast.Call) and node is not (parent.args or [None])[0]) or (
                isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "get"):
            continue
        names.append(node)
    return names


def test_every_smartctl_goes_through_run_smartctl():
    """smartctl given straight to run_in_container would find no node of a disk attached after the
    start in the container (ADR-0414): the program's name appears in host.run_smartctl alone, in
    whatever form (a list over several lines, a tuple, a constant)."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed = set()
        if path == SRC / "host.py":
            run_smartctl = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_smartctl")
            allowed = {id(n) for n in ast.walk(run_smartctl)}
        found += [f"{path.relative_to(SRC)}:{n.lineno}" for n in _smartctl_names(tree) if id(n) not in allowed]
    assert found == []


@pytest.mark.parametrize("source", [
    'run_in_container(["smartctl", "-a", device], timeout=5)',
    'run_in_container([\n    "smartctl",\n    "-a",\n    device,\n], timeout=5)',
    'run_in_container(("smartctl", "-a", device), timeout=5)',
    'SMARTCTL = "smartctl"',
    'subprocess.run(["smartctl", "-a", device])',
])
def test_the_convention_test_finds_smartctl_in_any_form(source):
    assert len(_smartctl_names(ast.parse(source))) == 1


@pytest.mark.parametrize("source", ['data.get("smartctl")', 'data["smartctl"]', '_member(data, "smartctl", "messages")'])
def test_the_convention_test_leaves_the_members_of_smartctl_s_json(source):
    assert _smartctl_names(ast.parse(source)) == []


# --- the real sysfs read and node making, in folders of the test's own ------------------------------


@pytest.fixture
def folders(tmp_path, monkeypatch):
    sys_block = tmp_path / "sys" / "block"
    dev = tmp_path / "dev"
    sys_block.mkdir(parents=True)
    dev.mkdir()
    monkeypatch.setattr(host, "SYS_BLOCK", str(sys_block))
    monkeypatch.setattr(host, "CONTAINER_DEV", str(dev))
    return sys_block, dev


def sysfs_disk(sys_block: Path, name: str, majmin: str) -> None:
    (sys_block / name).mkdir()
    (sys_block / name / "dev").write_text(f"{majmin}\n")


def test_sysfs_names_the_number_of_a_whole_disk(folders):
    sys_block, _ = folders
    sysfs_disk(sys_block, "sdb", "8:16")
    sysfs_disk(sys_block, "nvme1n1", "259:7")

    assert REAL_HOST_DISK_NUMBER("sdb") == (8, 16)
    assert REAL_HOST_DISK_NUMBER("nvme1n1") == (259, 7)
    assert REAL_HOST_DISK_NUMBER("sdc") is None


def test_sysfs_without_a_number_is_an_error(folders):
    sys_block, _ = folders
    sysfs_disk(sys_block, "sdb", "eight")

    with pytest.raises(OSError, match="No device number"):
        REAL_HOST_DISK_NUMBER("sdb")


def test_no_sysfs_of_block_devices_leaves_dev_as_docker_made_it(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "SYS_BLOCK", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        REAL_HOST_DISK_NUMBER("sdb")

    monkeypatch.setattr(host, "CONTAINER_DEV", str(tmp_path / "dev"))
    host.container_disk_node("/dev/sdb")  # nothing: no error, no node

    assert not (tmp_path / "dev").exists()


def test_an_entry_that_is_no_block_device_has_no_number(folders):
    _, dev = folders
    (dev / "sdb").write_text("")
    (dev / "sdc").symlink_to("/dev/null")

    assert REAL_CONTAINER_NODE_NUMBER(str(dev / "sda")) is None
    assert REAL_CONTAINER_NODE_NUMBER(str(dev / "sdb")) == host._NOT_A_BLOCK_NODE
    assert REAL_CONTAINER_NODE_NUMBER(str(dev / "sdc")) == host._NOT_A_BLOCK_NODE
    assert REAL_CONTAINER_NODE_NUMBER("/dev/null") == host._NOT_A_BLOCK_NODE  # a character device


def _a_block_device() -> str | None:
    """The first block device node of this machine's /dev (macOS's disk0, Linux's loop0 or sda), or None;
    only its inode is read."""
    try:
        entries = sorted(os.scandir("/dev"), key=lambda e: e.name)
    except OSError:
        return None
    return next((e.path for e in entries if stat.S_ISBLK(e.stat(follow_symlinks=False).st_mode)), None)


def test_a_block_device_node_answers_its_number():
    path = _a_block_device()
    if path is None:
        pytest.skip("this machine's /dev has no block device node")
    rdev = os.lstat(path).st_rdev

    assert REAL_CONTAINER_NODE_NUMBER(path) == (os.major(rdev), os.minor(rdev))


def test_a_dev_on_the_host_s_filesystem_is_the_host_s(tmp_path, monkeypatch):
    (tmp_path / "dev").mkdir()
    (tmp_path / "host-dev").mkdir()
    monkeypatch.setattr(host, "CONTAINER_DEV", str(tmp_path / "dev"))
    monkeypatch.setattr(host, "HOST_DEV", str(tmp_path / "host-dev"))  # the same filesystem

    assert REAL_DEV_IS_THE_CONTAINERS() is False
    monkeypatch.setattr(host, "HOST_DEV", "/dev")
    if os.stat("/dev").st_dev == os.stat(tmp_path).st_dev:
        pytest.skip("this machine's /dev and the test's folder are one filesystem")
    assert REAL_DEV_IS_THE_CONTAINERS() is True
    monkeypatch.setattr(host, "HOST_DEV", str(tmp_path / "missing"))
    with pytest.raises(OSError):
        REAL_DEV_IS_THE_CONTAINERS()


@pytest.fixture
def mknod(monkeypatch):
    """os.mknod for a user who may make no block device: a file that names the mode and the number."""
    made = []

    def fake_mknod(path, mode, device):
        made.append((os.path.basename(path), mode, device))
        Path(path).write_text(f"{mode} {os.major(device)}:{os.minor(device)}")

    monkeypatch.setattr(host.os, "mknod", fake_mknod)
    return made


def test_a_node_is_a_block_device_of_mode_0600_renamed_into_place(folders, mknod):
    _, dev = folders
    (dev / "sdb").write_text("an old entry")
    (dev / ".sdb.unos-new").write_text("left by a run that stopped")

    REAL_MAKE_CONTAINER_NODE(str(dev / "sdb"), (8, 16))

    assert mknod == [(".sdb.unos-new", stat.S_IFBLK | 0o600, os.makedev(8, 16))]
    assert (dev / "sdb").read_text() == f"{stat.S_IFBLK | 0o600} 8:16"
    assert sorted(p.name for p in dev.iterdir()) == ["sdb"]


def test_a_node_that_cannot_be_made_leaves_the_old_entry_and_no_temporary(folders, monkeypatch):
    _, dev = folders
    (dev / "sdb").write_text("an old entry")

    def refused_mknod(path, mode, device):
        raise PermissionError(1, "Operation not permitted", path)

    monkeypatch.setattr(host.os, "mknod", refused_mknod)
    with pytest.raises(PermissionError):
        REAL_MAKE_CONTAINER_NODE(str(dev / "sdb"), (8, 16))

    assert (dev / "sdb").read_text() == "an old entry"
    assert sorted(p.name for p in dev.iterdir()) == ["sdb"]


def test_a_node_that_cannot_be_renamed_into_place_leaves_no_temporary(folders, mknod, monkeypatch):
    _, dev = folders
    (dev / "sdb").mkdir()  # a folder, which no node replaces

    with pytest.raises(OSError):
        REAL_MAKE_CONTAINER_NODE(str(dev / "sdb"), (8, 16))

    assert sorted(p.name for p in dev.iterdir()) == ["sdb"] and (dev / "sdb").is_dir()


def test_the_real_functions_make_the_node_of_an_attached_disk_and_leave_a_right_one(folders, mknod, monkeypatch):
    sys_block, dev = folders
    sysfs_disk(sys_block, "sdb", "8:16")
    monkeypatch.setattr(host, "_dev_is_the_containers", lambda: True)  # a tmpfs of its own, as in the container
    # The files of the mknod fixture are no block devices: this test reads a node's number from it.
    monkeypatch.setattr(host, "_container_node_number", lambda path: (
        tuple(int(n) for n in Path(path).read_text().split()[1].split(":")) if os.path.exists(path) else None))

    host.container_disk_node("/dev/sdb")
    host.container_disk_node("/dev/sdb")

    assert mknod == [(".sdb.unos-new", stat.S_IFBLK | 0o600, os.makedev(8, 16))]
    with pytest.raises(host.NoHostDisk, match="The host has no disk sdc"):
        host.container_disk_node("/dev/sdc")
    assert sorted(p.name for p in dev.iterdir()) == ["sdb"]


def test_the_real_functions_write_nothing_into_a_dev_on_the_host_s_filesystem(folders, mknod, monkeypatch):
    sys_block, dev = folders
    sysfs_disk(sys_block, "sdb", "8:16")
    monkeypatch.setattr(host, "HOST_DEV", str(dev))

    host.container_disk_node("/dev/sdb")

    assert mknod == [] and list(dev.iterdir()) == []


@pytest.mark.skipif(not sys.platform.startswith("linux") or os.geteuid() != 0,
                    reason="only root on Linux makes block device nodes")
def test_a_real_node_is_a_block_device_with_the_number(folders):
    _, dev = folders
    # A number of the experimental range (240-254) that no driver serves: nothing opens it anyway.
    REAL_MAKE_CONTAINER_NODE(str(dev / "sdx"), (240, 7))

    status = os.lstat(dev / "sdx")
    assert stat.S_ISBLK(status.st_mode) and stat.S_IMODE(status.st_mode) == 0o600
    assert REAL_CONTAINER_NODE_NUMBER(str(dev / "sdx")) == (240, 7)

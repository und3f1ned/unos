"""The job of .github/workflows/btrfs.yml (ADR-0464): scripts/ci/btrfs-loop.sh makes three loop devices
as root on a GitHub-hosted runner, runs the real supervisor with two of them as its disks
(scripts/ci/btrfs_loop.py serve) and checks pools, volumes, snapshots, a restore and a scrub through
its API against what btrfs and findmnt show on the runner (btrfs_loop.py check).

Tested here, without root and without btrfs: the script refuses every other machine before it makes
anything, and against fakes of losetup, the supervisor and the check it runs its steps in order, exits
with the check's status and detaches what it attached, also after a failed check; serve makes exactly the
job's loop devices disks, refuses a device that is no loop device or whose backing file is not the job's,
replaces no other attribute of the supervisor and leaves the host paths at their defaults; the check reads
btrfs's and findmnt's output. The flow itself runs on GitHub, and in a throwaway privileged container
(docs/TESTING.md).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "btrfs-loop.sh"
MODULE = REPO_ROOT / "scripts" / "ci" / "btrfs_loop.py"
RUNNER = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}


def _load():
    spec = importlib.util.spec_from_file_location("btrfs_loop", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


btrfs_loop = _load()


# --- btrfs-loop.sh against fakes ---------------------------------------------------------------

# Each fake logs its call to $LOG. losetup attaches FILE to /dev/loop90, 91, 92 in turn; findmnt finds
# $MOUNTED mounted from /dev/loop90 (nothing when it is empty), and umount exits $UMOUNT_STATUS; uname
# and id answer $FAKE_UNAME and $FAKE_UID. The Python is the script's argument: serve runs until it is
# stopped, or exits 3 at once with $SERVE_ENDS, and check then waits until it is gone, or with
# $SERVE_HANGS ignores TERM; it writes its PID to $LOG.serve. check exits $CHECK_STATUS, or with
# $CHECK_WAITS runs until a TERM, which it logs.
FAKES = {
    "uname": 'echo "${FAKE_UNAME:-Linux}"',
    "id": 'echo "${FAKE_UID:-0}"',
    "modprobe": 'echo "modprobe $*" >> "$LOG"',
    "truncate": 'echo "truncate $*" >> "$LOG"; : > "${@: -1}"',
    "losetup": """
        echo "losetup $*" >> "$LOG"
        if [ "$1" = --find ]; then
            n=$(grep -c '^losetup --find' "$LOG"); echo "/dev/loop$((89 + n))"
        fi
    """,
    "findmnt": """
        echo "findmnt $*" >> "$LOG"
        if [ -n "${MOUNTED:-}" ] && [ "$3" = /dev/loop90 ]; then echo "$MOUNTED"; else exit 1; fi
    """,
    "umount": 'echo "umount $*" >> "$LOG"; exit "${UMOUNT_STATUS:-0}"',
    "python": """
        shift  # the path of btrfs_loop.py
        echo "python $*" >> "$LOG"
        case "$1" in
            serve) echo "the supervisor's line"; echo $$ > "$LOG.serve"
                   [ -z "${SERVE_ENDS:-}" ] || exit 3
                   if [ -n "${SERVE_HANGS:-}" ]; then trap '' TERM; else
                       trap 'echo "serve stopped" >> "$LOG"; exit 143' TERM
                   fi
                   while :; do sleep 0.1; done ;;
            check) if [ -n "${SERVE_ENDS:-}" ]; then
                       # Until the script has reaped the supervisor that ended.
                       for _ in $(seq 400); do
                           [ -s "$LOG.serve" ] && ! kill -0 "$(cat "$LOG.serve")" 2> /dev/null && break
                           sleep 0.05
                       done
                   fi
                   if [ -n "${CHECK_WAITS:-}" ]; then
                       trap 'echo "check stopped" >> "$LOG"; exit 130' TERM
                       echo "check runs" >> "$LOG"
                       while :; do sleep 0.1; done
                   fi
                   exit "${CHECK_STATUS:-0}" ;;
        esac
    """,
}


@pytest.fixture
def fakes(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        path = bin_dir / name
        path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body).strip() + "\n")
        path.chmod(0o755)
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    log = tmp_path / "log"
    log.touch()

    def environment(extra):
        return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "LOG": str(log), "RUNNER_TEMP": str(temp),
                **(extra or {})}

    def calls():
        return [line for line in log.read_text().splitlines() if not line.startswith("modprobe")]

    def run(extra=None, check_status=0):
        log.write_text("")  # a run of its own: losetup counts its devices from /dev/loop90 again
        result = subprocess.run(["bash", str(SCRIPT), str(bin_dir / "python")],
                                env=environment({"CHECK_STATUS": str(check_status), **(extra or {})}),
                                capture_output=True, text=True, timeout=60)
        return result, calls(), temp

    run.start = lambda extra: subprocess.Popen(["bash", str(SCRIPT), str(bin_dir / "python")],
                                               env=environment(extra), stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True)
    run.calls = calls
    run.temp = temp
    run.served = lambda: Path(f"{log}.serve").read_text().strip()
    return run


@pytest.mark.parametrize("env", [
    {},
    {"GITHUB_ACTIONS": "true"},
    {"RUNNER_ENVIRONMENT": "github-hosted"},
    {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"},
    {"GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT": "github-hosted"},
], ids=["none", "actions-only", "environment-only", "self-hosted", "not-actions"])
def test_the_script_refuses_a_machine_that_is_not_a_github_hosted_runner_before_it_makes_anything(fakes, env):
    result, calls, temp = fakes(env)

    assert result.returncode == 2
    assert "runs only on a GitHub-hosted runner" in result.stderr
    assert calls == [] and list(temp.iterdir()) == []


@pytest.mark.parametrize(("uname", "uid", "reason"), [
    ("Darwin", "0", "runs only on Linux"),
    ("Linux", "1001", "runs only as root"),
])
def test_the_script_refuses_another_system_and_another_user_than_root(fakes, uname, uid, reason):
    result, calls, temp = fakes({**RUNNER, "FAKE_UNAME": uname, "FAKE_UID": uid})

    assert result.returncode == 2 and reason in result.stderr
    assert calls == [] and list(temp.iterdir()) == []


def test_the_script_refuses_without_the_python_to_run(fakes, tmp_path):
    result = subprocess.run(["bash", str(SCRIPT)], env={**RUNNER, "PATH": os.environ["PATH"]}, capture_output=True,
                            text=True, timeout=30)

    assert result.returncode == 2 and "usage:" in result.stderr


@pytest.mark.parametrize("check_status", [0, 1])
def test_the_script_runs_the_supervisor_and_the_check_on_three_loop_devices_and_detaches_them(fakes, check_status):
    result, calls, temp = fakes(RUNNER, check_status=check_status)

    assert result.returncode == check_status, result.stderr
    # Three sparse files of 1 GiB in a folder of its own below RUNNER_TEMP, each on a loop device.
    files = [call.split()[-1] for call in calls if call.startswith("truncate")]
    assert [call.split()[:-1] for call in calls if call.startswith("truncate")] == [["truncate", "-s", "1G"]] * 3
    assert len({Path(f).parent for f in files}) == 1 and Path(files[0]).parent.parent == temp
    assert [call for call in calls if call.startswith("losetup --find")] == [f"losetup --find --show {f}" for f in files]
    # The supervisor with the first two as its disks, then the check, which names the third as the
    # device that is no disk of the job.
    serve = next(call for call in calls if call.startswith("python serve"))
    state = Path(files[0]).parent
    assert serve == f"python serve --state {state} --port 46190 --disk /dev/loop90 --disk /dev/loop91"
    # With the supervisor's PID, so that it stops at once when the supervisor has ended.
    assert ("python check --url http://127.0.0.1:46190 --disk /dev/loop90 --disk /dev/loop91 --other /dev/loop92 "
            f"--supervisor {fakes.served()}") in calls
    # At the end, also after a failed check: the supervisor stopped, its log printed in a group of the
    # job's log, the devices detached and the folder removed.
    assert calls.index("serve stopped") > calls.index(next(c for c in calls if c.startswith("python check")))
    assert [call for call in calls if call.startswith("losetup -d")] == [
        "losetup -d /dev/loop90", "losetup -d /dev/loop91", "losetup -d /dev/loop92"]
    assert calls.index("losetup -d /dev/loop90") > calls.index("serve stopped")
    assert "::group::The supervisor's log (it stopped with status 143)" in result.stdout
    assert "the supervisor's line" in result.stdout
    assert list(temp.iterdir()) == []


def test_the_teardown_unmounts_what_is_left_and_a_failure_there_fails_a_passed_check(fakes):
    result, calls, temp = fakes({**RUNNER, "MOUNTED": "/mnt/pools/unoscheck1a2b3c"})

    assert result.returncode == 0, result.stderr
    assert calls.index("umount /mnt/pools/unoscheck1a2b3c") < calls.index("losetup -d /dev/loop90")

    result, calls, temp = fakes({**RUNNER, "MOUNTED": "/mnt/pools/unoscheck1a2b3c", "UMOUNT_STATUS": "32"})

    assert result.returncode == 1 and "the teardown failed" in result.stderr
    # It goes on: every device detached, the folder removed.
    assert len([call for call in calls if call.startswith("losetup -d")]) == 3
    assert list(temp.iterdir()) == []


def test_the_teardown_keeps_the_status_of_a_failed_check(fakes):
    result, _, _ = fakes({**RUNNER, "MOUNTED": "/mnt/pools/x", "UMOUNT_STATUS": "32"}, check_status=130)

    assert result.returncode == 130


def test_the_log_names_the_status_of_a_supervisor_that_ended_early(fakes):
    result, _, _ = fakes({**RUNNER, "SERVE_ENDS": "1"}, check_status=1)

    assert result.returncode == 1
    assert "the supervisor had ended before the teardown" in result.stdout
    assert "::group::The supervisor's log (it stopped with status 3)" in result.stdout


def test_a_signal_goes_on_to_the_check_and_the_teardown_runs(fakes):
    """A TERM that reaches the script: the check gets TERM and runs its clean-up, then the script tears
    down and exits with the signal's status."""
    process = fakes.start({**RUNNER, "CHECK_WAITS": "1"})
    deadline = time.monotonic() + 30
    while "check runs" not in fakes.calls():
        assert time.monotonic() < deadline and process.poll() is None
        time.sleep(0.05)

    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)
    calls = fakes.calls()

    assert process.returncode == 143
    assert calls.index("check stopped") < calls.index("serve stopped") < calls.index("losetup -d /dev/loop90")
    assert list(fakes.temp.iterdir()) == []


def test_a_supervisor_that_ignores_term_is_killed_and_the_teardown_goes_on(fakes):
    result, calls, temp = fakes({**RUNNER, "SERVE_HANGS": "1"})

    assert result.returncode == 0, result.stderr
    assert "the supervisor had not stopped 10 s after TERM: killed" in result.stdout
    assert "::group::The supervisor's log (it stopped with status 137)" in result.stdout
    assert len([call for call in calls if call.startswith("losetup -d")]) == 3
    assert list(temp.iterdir()) == []


def test_the_script_is_executable_and_stops_at_its_first_failure():
    text = SCRIPT.read_text()

    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in text


# --- serve: the job's loop devices as the supervisor's disks ---------------------------------------


def test_only_loop_devices_can_be_the_jobs_disks():
    assert btrfs_loop.loop_names(["/dev/loop5", "/dev/loop12"]) == frozenset({"loop5", "loop12"})
    for paths in (["/dev/sda"], ["/dev/mmcblk0"], ["/dev/loop5p1"], ["loop5"], ["/dev/loop5", "/dev/loop5"],
                  ["/dev/../dev/loop5"], []):
        with pytest.raises(SystemExit):
            btrfs_loop.loop_names(paths)


def test_lsblks_tree_names_only_the_jobs_loop_devices_disks():
    tree = [
        {"name": "sda", "type": "disk", "children": [{"name": "sda1", "type": "part"}]},
        {"name": "loop5", "type": "loop", "fstype": None},
        {"name": "loop6", "type": "loop", "fstype": "btrfs"},
        {"name": "loop7", "type": "loop"},
        {"name": "mmcblk0", "type": "disk"},
    ]
    before = json.dumps(tree)

    changed = btrfs_loop.as_disks(tree, frozenset({"loop5", "loop6"}))

    assert [(node["name"], node["type"]) for node in changed] == [
        ("sda", "disk"), ("loop5", "disk"), ("loop6", "disk"), ("loop7", "loop"), ("mmcblk0", "disk")]
    assert changed[2]["fstype"] == "btrfs" and changed[0] is tree[0]
    assert json.dumps(tree) == before  # lsblk's own answer is not changed


def test_configure_replaces_the_two_functions_and_hides_every_other_device():
    tree = [{"name": "loop5", "type": "loop"}, {"name": "loop7", "type": "loop"}, {"name": "sdb", "type": "disk"}]
    disks = SimpleNamespace(_lsblk_tree=lambda: tree, is_hidden_disk=lambda name: name.startswith(("loop", "mmcblk")))

    btrfs_loop.configure(disks, frozenset({"loop5"}))

    assert [node["type"] for node in disks._lsblk_tree()] == ["disk", "loop", "disk"]
    # The runner's own disks are hidden too: the supervisor offers the job's loop devices alone.
    assert [disks.is_hidden_disk(name) for name in ("loop5", "loop7", "loop50", "mmcblk0", "sdb", "nvme0n1")] == [
        False, True, True, True, True, True]


def test_serve_refuses_a_loop_device_whose_backing_file_is_not_in_the_jobs_folder(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    sys_block = tmp_path / "sys-block"
    for name, backing in (("loop5", state / "disk1.img"), ("loop6", tmp_path / "elsewhere.img"),
                          ("loop7", state / "sub" / "disk.img")):
        (sys_block / name / "loop").mkdir(parents=True)
        (sys_block / name / "loop" / "backing_file").write_text(f"{backing}\n")

    btrfs_loop.check_backing(frozenset({"loop5"}), state, sys_block)
    for names in ({"loop6"}, {"loop7"}, {"loop8"}, {"loop5", "loop6"}):
        with pytest.raises(SystemExit):
            btrfs_loop.check_backing(frozenset(names), state, sys_block)


def test_serve_replaces_only_the_two_functions_of_the_disks_module():
    """As the stand-in (ADR-0419): functions replaced, no constant patched, no setting that would turn a
    check of a NAS off. Only _lsblk_tree and is_hidden_disk of src.storage.disks."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    assigned = sorted(ast.unparse(target) for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AugAssign,
                                                                                           ast.AnnAssign))
                      for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                      if isinstance(target, ast.Attribute) and not ast.unparse(target).startswith("self."))
    calls = [ast.unparse(node) for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr"]

    # The two functions, in configure(); besides them only the check's own objects: its API client's
    # token and a step's text.
    assert sorted(set(assigned)) == ["api.token", "disks._lsblk_tree", "disks.is_hidden_disk", "s.text"]
    assert calls == []
    assert "configure(importlib.import_module(\"src.storage.disks\"), names)" in MODULE.read_text()


# What a supervisor started with serve's environment reads, in a process of its own: the supervisor
# reads its data directory once, at its first import.
SUPERVISOR_READS = """
import json, sys, tempfile
from src import host, paths
from src.config import get_settings
s = get_settings()
print(json.dumps({"data": str(paths.DATA_DIR), "db": s.db_path, "smb": s.smb_conf_path, "nfs": s.nfs_exports_path,
                  "certificate": s.served_certificate_dir, "backups": s.update_backup_dir, "install": s.install_dir,
                  "tmp": tempfile.gettempdir(), "pools": s.pools_base_path, "fstab": s.fstab_path,
                  "mounts": s.mounts_base_path, "docker": s.docker_socket, "prefix": host._prefix()}))
"""


def test_the_supervisor_keeps_its_files_in_the_jobs_folder_and_the_host_paths_and_commands_of_a_nas(tmp_path):
    """Its own files in the job's folder; the pools, fstab, the Docker socket and the host commands through
    nsenter as on a NAS (no UNOS_HOST_EXEC, which would run them without it)."""
    state = tmp_path / "state"
    env = btrfs_loop.supervisor_environment(state)
    for folder in (state / "data", state / "tmp"):
        folder.mkdir(parents=True)
    # As serve: none of this process's settings of the supervisor or of Docker.
    inherited = {name: value for name, value in os.environ.items() if not name.startswith(("UNOS_", "DOCKER_"))}

    result = subprocess.run([sys.executable, "-c", SUPERVISOR_READS], cwd=REPO_ROOT / "supervisor",
                            env={**inherited, **env}, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr[-4000:]
    read = json.loads(result.stdout)
    assert env["UNOS_DISABLE_BACKGROUND_TASKS"] == "1"
    assert read["data"] == str(state / "data") and read["db"] == str(state / "data" / "unos.db")
    for name in ("smb", "nfs", "certificate", "backups", "install", "tmp"):
        assert Path(read[name]).resolve().is_relative_to(state.resolve()), (name, read[name])
    assert (read["pools"], read["fstab"], read["mounts"], read["docker"]) == (
        "/mnt/pools", "/etc/fstab", "/mnt", "/var/run/docker.sock")
    assert read["prefix"][0] == "nsenter"


SERVE = ["serve", "--state", "/nonexistent", "--port", "1", "--disk", "/dev/loop5", "--disk", "/dev/loop6"]
CHECK = ["check", "--url", "http://127.0.0.1:1", "--disk", "/dev/loop5", "--disk", "/dev/loop6", "--other",
         "/dev/loop7"]


@pytest.mark.parametrize("argv", [SERVE, CHECK], ids=["serve", "check"])
@pytest.mark.parametrize("env", [{}, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}],
                         ids=["none", "self-hosted"])
def test_both_refuse_a_machine_that_is_not_a_github_hosted_runner(monkeypatch, capsys, argv, env):
    """Also when started without btrfs-loop.sh: sudo btrfs_loop.py serve on a workstation or a NAS."""
    for name in ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as exit_:
        btrfs_loop.main(argv)

    assert exit_.value.code == 2
    assert "runs only on a GitHub-hosted runner" in capsys.readouterr().err


def test_serve_refuses_anything_but_root_on_linux(monkeypatch, capsys):
    if sys.platform.startswith("linux") and os.geteuid() == 0:
        pytest.skip("runs as root on Linux")
    for name, value in RUNNER.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as exit_:
        btrfs_loop.main(SERVE)

    assert exit_.value.code == 2 and "as root on Linux" in capsys.readouterr().err


def test_the_check_asks_only_the_supervisor_on_the_runner():
    for url in ("http://example.org:46190", "https://127.0.0.1:46190", "file:///etc/passwd",
                "http://127.0.0.1:46190/../x"):
        with pytest.raises(SystemExit):
            btrfs_loop.Api(url)
    assert btrfs_loop.Api("http://127.0.0.1:46190").base == "http://127.0.0.1:46190/api/v1"


# --- check: what the runner shows ----------------------------------------------------------------


def test_the_check_reads_btrfs_filesystem_show():
    text = ("Label: 'unoscheck1a2b3c'  uuid: 0f8e6c52-3b1d-4a7e-9c2f-5d6e7f8a9b0c\n"
            "\tTotal devices 2 FS bytes used 144.00KiB\n"
            "\tdevid    1 size 1.00GiB used 228.75MiB path /dev/loop90\n"
            "\tdevid    2 size 1.00GiB used 228.75MiB path /dev/loop91\n\n")

    assert btrfs_loop.filesystem_show(text) == {
        "label": "unoscheck1a2b3c", "uuid": "0f8e6c52-3b1d-4a7e-9c2f-5d6e7f8a9b0c",
        "devices": ["/dev/loop90", "/dev/loop91"]}
    assert btrfs_loop.filesystem_show("") == {"label": None, "uuid": None, "devices": []}


def test_the_check_reads_the_profiles_of_btrfs_filesystem_df():
    text = ("Data, RAID1: total=102.38MiB, used=64.00MiB\nSystem, RAID1: total=8.00MiB, used=16.00KiB\n"
            "Metadata, RAID1: total=102.38MiB, used=208.00KiB\nGlobalReserve, single: total=5.50MiB, used=0.00B\n")

    assert btrfs_loop.profiles(text) == {"Data": "RAID1", "System": "RAID1", "Metadata": "RAID1"}


def test_the_check_reads_the_counts_of_btrfs_scrub_status_r():
    text = ("UUID:             46f1819f-92fd-4952-a774-fccadda45689\nScrub started:    Sun Nov 22 21:21:16 2026\n"
            "Status:           finished\nDuration:         0:00:00\n\tdata_extents_scrubbed: 2048\n"
            "\tdata_bytes_scrubbed: 134217728\n\tread_errors: 0\n\tcsum_errors: 1\n\tlast_physical: 224133120\n")

    assert btrfs_loop.scrub_counts(text) == {"data_extents_scrubbed": 2048, "data_bytes_scrubbed": 134217728,
                                             "read_errors": 0, "csum_errors": 1, "last_physical": 224133120}


def test_the_check_reads_btrfs_subvolume_list():
    text = ("ID 256 gen 12 top level 5 path unoscheck1a2b3c\n"
            "ID 257 gen 13 cgen 13 top level 256 otime 2026-11-22 10:00:00 path unoscheck1a2b3c/.snapshots/a b\n"
            "ID 258 gen 14 top level 256 path unoscheck1a2b3c/restore_20261122_100001\n")

    assert btrfs_loop.subvolumes(text) == {"unoscheck1a2b3c": 5, "unoscheck1a2b3c/.snapshots/a b": 256,
                                           "unoscheck1a2b3c/restore_20261122_100001": 256}
    assert btrfs_loop.subvolumes("") == {}


# --- check: its steps against a fake supervisor and runner -----------------------------------------

DISKS = ["/dev/loop90", "/dev/loop91"]
OTHER = "/dev/loop92"
MIB = 1 << 20


class FakeRunner:
    """The supervisor's API and the runner, as the check sees them on a fresh GitHub-hosted runner: one
    state that both answer from, as btrfs, findmnt and blkid would show it. BROKEN names the one place
    where it shows something wrong."""

    def __init__(self, broken=""):
        self.broken = broken
        self.secrets = []
        self.token = None
        self.requests = []
        self.pools = {}  # name -> the pool's answer
        self.mounts = {"/mnt/pools/tank": {}} if broken == "a NAS's pool" else {}  # mountpoint -> findmnt's
        self.fstab = {}  # mountpoint -> findmnt --fstab's
        self.subvolumes_of = {}  # mountpoint -> path in the pool -> [parent ID, read-only]
        self.files = {}  # path -> bytes
        self.fstypes = {}
        self.scrubbed = False

    # the API

    def request(self, method, path, json_body=None, params=None):
        self.requests.append((method, path))
        status, body = self.answer(method, path, json_body or {}, params or {})
        return btrfs_loop.relay.Answer(status, json.dumps(body))

    def answer(self, method, path, body, params):  # one branch per route that the check calls
        parts = path.strip("/").split("/")
        if (method, path) == ("GET", "/health"):
            return 200, {"status": "ok"}
        if (method, path) == ("POST", "/auth/setup"):
            return 200, {}
        if (method, path) == ("POST", "/auth/login"):
            return 200, {"access_token": "t0ken"}
        if (method, path) == ("GET", "/storage/disks"):
            listed = [*DISKS, "/dev/sda"] if self.broken == "a runner's disk offered" else DISKS
            return 200, [{"path": d, "has_data": bool(self.fstypes.get(d)), "in_use": False, "is_system": False}
                         for d in listed]
        if (method, path) == ("GET", "/storage/pools"):
            return 200, list(self.pools.values())
        if (method, path) == ("POST", "/storage/pools"):
            if OTHER in body["devices"]:
                detail = (f"{OTHER}: a raid1 pool needs two devices" if self.broken == "refused for another reason"
                          else f"{OTHER} is not a disk or partition on this system")
                return 400, {"detail": detail}
            name = body["name"]
            mountpoint = f"/mnt/pools/{name}"
            uuid = "0f8e6c52-3b1d-4a7e-9c2f-5d6e7f8a9b0c"
            self.pools[name] = {"name": name, "uuid": uuid, "mounted": True, "persistent": True,
                                "mountpoint": mountpoint, "raid_level": "raid1", "health": "healthy",
                                "devices": body["devices"]}
            self.fstypes.update(dict.fromkeys(body["devices"], "btrfs"))
            self.mounts[mountpoint] = {"target": mountpoint, "source": body["devices"][0], "fstype": "btrfs",
                                       "uuid": uuid}
            self.fstab[mountpoint] = {"target": mountpoint, "source": f"UUID={uuid}", "fstype": "btrfs"}
            self.subvolumes_of[mountpoint] = {}
            return 200, self.pools[name]
        if parts[:2] == ["storage", "pools"] and len(parts) >= 3:
            pool = self.pools[parts[2]]
            subvolumes = self.subvolumes_of[pool["mountpoint"]]
            if len(parts) == 3 and method == "DELETE":
                if self.broken != "the fstab line kept":
                    self.fstab.pop(pool["mountpoint"], None)
                self.mounts.pop(pool["mountpoint"], None)
                pool.update(mounted=False, persistent=False)
                return 200, {}
            if parts[3:] == ["volumes"] and method == "POST":
                subvolumes[body["name"]] = [5, False]
                return 200, {"path": f"{pool['mountpoint']}/{body['name']}"}
            if parts[3:] == ["volumes"]:
                return 200, [{"name": p} for p, (top, _) in subvolumes.items() if top == 5]
            if parts[3] == "volumes" and method == "DELETE":
                left = [p for p in subvolumes if p.startswith(parts[4] + "/")]
                if left:
                    return 409, {"detail": f"it holds {left}"}
                subvolumes.pop(parts[4])
                return 200, {}
            if parts[3:] == ["scrub"]:
                self.scrubbed = self.scrubbed or method == "POST"
                return 200, {"status": "finished" if self.scrubbed else "idle", "errors": 0, "duration": 1}
        if parts[0] == "snapshots":
            volume = params.get("subvolume") or body.get("subvolume") or body["snapshot_path"].split("/.snapshots/")[0]
            mountpoint, name = volume.rsplit("/", 1)
            subvolumes = self.subvolumes_of[mountpoint]
            if (method, parts[1:]) == ("POST", []):
                subvolumes[f"{name}/.snapshots/{body['name']}"] = [256, self.broken != "the snapshot writable"]
                self.copy(volume, f"{volume}/.snapshots/{body['name']}")
                return 200, {"path": f"{volume}/.snapshots/{body['name']}"}
            if (method, parts[1:]) == ("POST", ["restore"]):
                source = volume if self.broken == "the restore of the volume as it is" else body["snapshot_path"]
                subvolumes[f"{name}/restore_20261123_120000"] = [256, False]
                self.copy(source, f"{volume}/restore_20261123_120000")
                return 200, {"target": f"{volume}/restore_20261123_120000"}
            kind = "restore_" if parts[1:2] == ["copies"] else ".snapshots/"
            if method == "GET":
                return 200, [{"name": p.rsplit("/", 1)[1], "path": f"{mountpoint}/{p}"} for p in subvolumes
                             if p.startswith(f"{name}/{kind}")]
            found = f"{name}/{kind}{parts[-1]}" if kind == ".snapshots/" else f"{name}/{parts[-1]}"
            return (200, {}) if subvolumes.pop(found, None) else (404, {"detail": "not found"})
        raise AssertionError(f"the fake does not know {method} {path}")

    def copy(self, source, target):
        self.files.update({target + path[len(source):]: data for path, data in list(self.files.items())
                           if path.startswith(source + "/") and path.count("/") == source.count("/") + 1})

    # the runner

    @staticmethod
    def sectors():
        return None

    def filesystem(self, mountpoint):
        pool = next(p for p in self.pools.values() if p["mountpoint"] == mountpoint)
        return {"label": pool["name"], "uuid": pool["uuid"], "devices": list(pool["devices"])}

    def profiles(self, mountpoint):
        return {"Data": "single" if self.broken == "single data" else "RAID1", "Metadata": "RAID1",
                "System": "RAID1"}

    def subvolumes(self, mountpoint, snapshots=False):
        return {p: top for p, (top, _) in self.subvolumes_of[mountpoint].items()
                if not snapshots or "/.snapshots/" in p}

    def read_only(self, path):
        mountpoint = next(m for m in self.subvolumes_of if path.startswith(m + "/"))
        return self.subvolumes_of[mountpoint][path[len(mountpoint) + 1:]][1]

    def mount(self, mountpoint):
        return self.mounts.get(mountpoint)

    def fstab_line(self, mountpoint):
        return self.fstab.get(mountpoint)

    def mounts_below(self, base):
        return sorted(m for m in self.mounts if m.startswith(base + "/"))

    def fstab_below(self, base):
        return sorted(m for m in self.fstab if m.startswith(base + "/"))

    def fstype(self, device):
        return self.fstypes.get(device, "")

    def scrub_status(self, mountpoint):
        return "Status:           finished\nDuration:         0:00:01\nError summary:    no errors found\n"

    def scrub_counts(self, mountpoint):
        # Snapshots and restored copies share the volume's extents: its own files, once per disk.
        data = sum(len(d) for p, d in self.files.items()
                   if p.startswith(mountpoint + "/") and "/.snapshots/" not in p and "/restore_" not in p)
        return {"data_bytes_scrubbed": data if self.broken == "one copy scrubbed" else 2 * data,
                "read_errors": 0, "csum_errors": 1 if self.broken == "a checksum error" else 0}

    def write(self, path, data):
        self.files[path] = data

    def write_random(self, path, mib):
        self.files[path] = os.urandom(mib * MIB)

    def read(self, path):
        return self.files[path]


def _check(fake):
    import io

    out = io.StringIO()
    c = btrfs_loop.CiCheck("btrfs", fake, fake, out=out)
    status = c.execute(lambda c: btrfs_loop.storage_check(c, DISKS, OTHER, "pass-word"))
    return status, out.getvalue()


def test_the_check_passes_on_a_runner_that_shows_what_the_api_answered():
    fake = FakeRunner()

    status, out = _check(fake)

    assert status == 0, out
    assert out.splitlines()[-1] == "scenario btrfs: OK, 13 steps passed"
    assert ("POST", "/storage/pools/" + next(iter(fake.pools)) + "/scrub") in fake.requests
    # Forgotten, and nothing left mounted or in fstab; the disks keep the filesystem.
    assert not fake.mounts and not fake.fstab and fake.fstypes == dict.fromkeys(DISKS, "btrfs")


@pytest.mark.parametrize(("broken", "step"), [
    ("a runner's disk offered", 2),
    ("a NAS's pool", 3),
    ("refused for another reason", 4),
    ("single data", 5),
    ("the snapshot writable", 8),
    ("the restore of the volume as it is", 9),
    ("a checksum error", 10),
    ("one copy scrubbed", 10),
    ("the fstab line kept", 12),
])
def test_the_check_fails_the_step_whose_result_the_runner_does_not_show(broken, step):
    fake = FakeRunner(broken)

    status, out = _check(fake)

    assert status == 1, out
    failed = [line for line in out.splitlines() if line.startswith("FAIL")]
    assert failed[0].startswith(f"FAIL  {step}. "), out
    assert out.splitlines()[-1].startswith("scenario btrfs: FAILED"), out
    if broken == "a NAS's pool":
        # Stopped before it changed anything: no pool made, nothing deleted.
        assert not [r for r in fake.requests if r[0] != "GET" and not r[1].startswith("/auth/")], fake.requests
    else:
        # The clean-ups undo what the steps made, but the fstab line that Forget kept.
        assert set(fake.mounts) == set() and set(fake.fstab) <= {f"/mnt/pools/{n}" for n in fake.pools}


def test_the_check_stops_at_once_when_the_supervisor_has_ended():
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    api = SimpleNamespace(request=lambda *args, **kwargs: (_ for _ in ()).throw(btrfs_loop.relay.ApiError("refused")))
    started = time.monotonic()

    with pytest.raises(btrfs_loop.scenario.Failed, match="the supervisor ended before it answered"):
        btrfs_loop.wait_for_start(api, ended.pid)

    assert time.monotonic() - started < 10

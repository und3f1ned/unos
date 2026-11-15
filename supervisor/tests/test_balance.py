"""The balance of a pool (storage.pool-balance, ADR-0377).

An administrator starts a balance of a mounted pool: by default of the data and metadata chunks
less than 20% full (-dusage=20 -musage=20), or of every chunk (--full-balance). btrfs runs it in
a transient systemd unit of the host, unos-balance-<pool>, so that a restart of the supervisor's
container, which kills the processes in it, does not cancel it; its output goes to
/run/unos-balance-<pool>.log, which tells how it ended. Its status, its pause and its cancel are
btrfs commands in the container (ADR-0290); its resume runs in the unit again. Refused with 409,
and nothing run, while the pool is not mounted or is read-only, while a scrub of it runs, and while
a balance of it runs or is paused; a scrub is refused while a balance runs. Each change is in the
audit log. The page follows a balance that runs over the events socket (ADR-0367), with an
estimate of the time left.
"""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from src import host
from src.audit import router as audit_router
from src.storage import balances
from src.storage.schemas import BalanceStatus

from .audit_helpers import audit_entries
from .test_pool_persistence import DATA_UUID, admin_client, install_fake_host, wait_for_app_starts
from .test_storage import SCRUB_OUTPUT

MOUNTPOINT = "/mnt/pools/data"
LOG = "/run/unos-balance-data.log"
URL = "/api/v1/storage/pools/data/balance"
# The unit outlives the supervisor's container. systemd stops it at a shutdown of the host, before it
# unmounts the pool (RequiresMountsFor): its SIGTERM would cancel the balance and delete it from the
# disk, so ExecStop pauses it first, and the kernel resumes it at the next mount, as after a power
# cut. Every command of the unit opens its output file, ExecStop too: btrfs appends to it, emptied
# before it starts (ExecStartPre).
UNIT = [
    "systemd-run", "--unit=unos-balance-data", "--description=UNOS: balance the pool data", "--collect", "--quiet",
    "--service-type=exec", f"--property=RequiresMountsFor={MOUNTPOINT}", f"--property=ExecStartPre=truncate --size=0 {LOG}",
    f"--property=StandardOutput=append:{LOG}", "--property=StandardError=inherit",
    f"--property=ExecStop=-btrfs balance pause {MOUNTPOINT}", "--property=TimeoutStopSec=10min", "--",
]
# What btrfs-progs prints, with exit status 2, for a pause or a cancel when the kernel has no
# balance to stop (ENOTCONN, cmds/balance.c): its words differ between the two.
NOT_RUNNING = {
    "pause": "ERROR: balance pause on '{}' failed: Not running\n",
    "cancel": "ERROR: balance cancel on '{}' failed: Not in progress\n",
}


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class FakeKernel:
    """The balance of the pool data as the kernel runs it and btrfs reports it: the container's
    btrfs balance status, pause and cancel and btrfs scrub status and start (host.run_in_container),
    and the host's systemd-run, whose unit runs btrfs balance start or resume and writes its output
    to the log file (host.files of the FakeHost)."""

    def __init__(self, fake_host) -> None:
        self.host = fake_host
        self.state: str | None = None  # None, "running" or "paused"
        self.requests: set[str] = set()  # "pause", "cancel": waiting for the chunk being moved
        self.completed = 0
        self.expected = 12
        self.considered = 0
        self.unit: list[str] | None = None  # the btrfs command of the unit that is active
        self.units: list[list[str]] = []  # every systemd-run
        self.container: list[list[str]] = []
        self.on_loop: list[bool] = []
        self.scrub = "never"
        self.refuse_start: str | None = None  # what btrfs prints when the kernel refuses the balance
        self.ends_at_once: str | None = None  # the output of a balance that ends before it is read
        self.unseen = False  # the unit's btrfs has not reached the kernel yet
        self.stops_wait = False  # a pause or cancel waits for the chunk being moved
        self.status_fails: str | None = None

    # --- the container's btrfs

    def run_in_container(self, cmd, **kwargs):
        self.container.append(list(cmd))
        self.on_loop.append(_on_event_loop())
        assert kwargs.get("timeout"), cmd
        result = lambda code=0, out="", err="": subprocess.CompletedProcess(cmd, code, out, err)  # noqa: E731
        if cmd[:3] == ["btrfs", "scrub", "status"]:
            return result(out=SCRUB_OUTPUT[self.scrub])
        if cmd[:3] == ["btrfs", "replace", "status"]:
            return result(out="Never started\n")
        if cmd[:3] == ["btrfs", "scrub", "start"]:
            self.scrub = "running"
            return result()
        assert cmd[:2] == ["btrfs", "balance"] and cmd[3:] == [MOUNTPOINT], cmd
        if cmd[2] == "status":
            if self.status_fails:
                return result(2, err=self.status_fails)
            if self.state is None or self.unseen:
                return result(out=f"No balance found on '{MOUNTPOINT}'\n")
            # btrfs-progs names a cancel requested before a pause (cmds/balance.c).
            request = next((r for r in ("cancel", "pause") if r in self.requests), None)
            how = f"is {self.state}" + (f", {request} requested" if request else "")
            left = 100 * (1 - self.completed / self.expected) if self.expected else float("nan")
            return result(1, out=f"Balance on '{MOUNTPOINT}' {how}\n{self.completed} out of about {self.expected} "
                                 f"chunks balanced ({self.considered} considered), {left:3.0f}% left\n")
        if cmd[2] in ("pause", "cancel"):
            if self.state is None or (cmd[2] == "pause" and self.state == "paused"):
                return result(2, err=NOT_RUNNING[cmd[2]].format(MOUNTPOINT))
            self.requests.add(cmd[2])
            if self.stops_wait and self.state == "running":
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            self.chunk_moved()
            return result()
        raise AssertionError(f"unexpected container command: {cmd}")

    def chunk_moved(self) -> None:
        """The kernel has moved the chunk and acts on the requests. A pause wins over a cancel
        requested meanwhile (btrfs_balance(), Linux 6.1.150 and later), and the cancel still
        returns 0; the unit's btrfs ends with a line for each request. A paused balance is
        cancelled at once."""
        if self.state == "running" and self.requests:
            self.host.files[LOG] = "".join(f"balance {done} by user\n" for request, done in
                                           (("pause", "paused"), ("cancel", "canceled")) if request in self.requests)
            self.unit = None
            self.state = "paused" if "pause" in self.requests else None
        elif self.state == "paused" and "cancel" in self.requests:
            self.state = None
        self.requests = set()

    def finish(self, relocated: int = 3, considered: int = 12) -> None:
        self.state, self.unit = None, None
        self.host.files[LOG] = f"Done, had to relocate {relocated} out of {considered} chunks\n"

    # --- the host's systemd-run

    def run_on_host(self, cmd, **kwargs):
        if cmd[0] != "systemd-run":
            return self.host.run(cmd, **kwargs)
        self.units.append(list(cmd))
        self.on_loop.append(_on_event_loop())
        assert cmd[: len(UNIT)] == UNIT, cmd
        btrfs = cmd[len(UNIT):]
        if self.unit is not None:
            return host.HostResult(1, "", "Failed to start transient service unit: Unit unos-balance-data.service "
                                          "was already loaded or has a fragment file.\n")
        self.host.files[LOG] = ""  # ExecStartPre=truncate
        if self.refuse_start:
            self.host.files[LOG] = self.refuse_start
        elif self.ends_at_once:
            self.host.files[LOG] = self.ends_at_once
        elif btrfs[:3] == ["btrfs", "balance", "resume"]:
            assert self.state == "paused", "btrfs refuses a resume of a balance that is not paused"
            self.state, self.unit, self.completed, self.considered = "running", btrfs, 0, 0
        else:
            self.state, self.unit, self.completed, self.considered = "running", btrfs, 0, 0
        return host.HostResult(0, "", "")


@pytest.fixture
def kernel(monkeypatch):
    """The pool data mounted read-write on the FakeHost, no balance and no scrub."""
    fake_host = install_fake_host(monkeypatch)
    fake_host.add_disk("sda")
    fake_host.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake_host.mount_pool("data")
    fake = FakeKernel(fake_host)
    monkeypatch.setattr(host, "run_in_container", fake.run_in_container)
    monkeypatch.setattr(host, "run_on_host", fake.run_on_host)
    yield fake
    wait_for_app_starts()


def audited() -> list[tuple]:
    from pathlib import Path

    return [(e["action"], e["resource_type"], e["resource_id"], e["details"], e["success"])
            for e in audit_entries(Path(audit_router.AUDIT_FILE))]


def answer(**fields) -> dict:
    return {"pool": "data", "status": "idle", "completed": None, "expected": None, "considered": None,
            "seconds_left": None, "result": None, "error": None, **fields}


class TestStart:
    def test_a_balance_of_the_chunks_less_than_20_percent_full_runs_in_a_unit_of_the_host(self, kernel):
        with admin_client() as client:
            r = client.post(URL, json={})

        assert r.status_code == 200, r.text
        assert r.json() == answer(status="running", completed=0, expected=12, considered=0)
        assert kernel.units == [UNIT + ["btrfs", "balance", "start", "-dusage=20", "-musage=20", MOUNTPOINT]]
        assert audited() == [("balance_started", "pool", "data",
                              "Started a balance of the pool data: the data and metadata chunks less than 20% full",
                              True)]

    def test_the_usage_limit_of_the_request(self, kernel):
        with admin_client() as client:
            r = client.post(URL, json={"usage": 5})

        assert r.status_code == 200, r.text
        assert kernel.units[0][len(UNIT):] == ["btrfs", "balance", "start", "-dusage=5", "-musage=5", MOUNTPOINT]

    def test_a_full_balance_rewrites_every_chunk_without_btrfs_s_ten_second_warning(self, kernel):
        with admin_client() as client:
            r = client.post(URL, json={"full": True})

        assert r.status_code == 200, r.text
        assert kernel.units[0][len(UNIT):] == ["btrfs", "balance", "start", "--full-balance", MOUNTPOINT]
        assert audited()[0][3] == "Started a full balance of the pool data: every chunk"

    @pytest.mark.parametrize("body", [{"usage": 0}, {"usage": 100}, {"usage": -5}, {"usage": "x"},
                                      {"full": "maybe"}, {"usage": 20, "full": True}])
    def test_a_usage_limit_out_of_1_to_99_or_one_with_a_full_balance_is_refused(self, kernel, body):
        with admin_client() as client:
            r = client.post(URL, json=body)

        assert r.status_code == 422, r.text
        assert kernel.units == [] and kernel.container == []

    def test_a_pool_that_is_not_mounted_is_refused_and_no_btrfs_runs(self, kernel):
        kernel.host.mounts.clear()
        with admin_client() as client:
            r = client.post(URL, json={})
            status = client.get(URL)

        assert (r.status_code, r.json()) == (409, {"detail": f"Pool data is not mounted at {MOUNTPOINT}"})
        assert (status.status_code, status.json()) == (409, {"detail": f"Pool data is not mounted at {MOUNTPOINT}"})
        assert kernel.units == [] and kernel.container == [] and audited() == []

    @pytest.mark.parametrize("vfs, fs, why", [
        ("ro,noatime", "ro", "Pool data is mounted read-only: a balance cannot rewrite its chunks"),
        ("rw,noatime", "ro", "Pool data is mounted read-only: a balance cannot rewrite its chunks. btrfs made it "
                             "read-only after an error; check the kernel log (dmesg)."),
    ])
    def test_a_read_only_pool_is_refused(self, kernel, vfs, fs, why):
        kernel.host.mounts.clear()
        kernel.host.mount_pool("data", vfs_options=vfs, fs_options=fs)
        with admin_client() as client:
            r = client.post(URL, json={})

        assert (r.status_code, r.json()) == (409, {"detail": why})
        assert kernel.units == [] and kernel.container == []

    def test_a_balance_is_refused_while_a_scrub_runs(self, kernel):
        kernel.scrub = "running"
        with admin_client() as client:
            r = client.post(URL, json={})

        assert (r.status_code, r.json()) == (409, {
            "detail": "A scrub of pool data runs: wait for its end or cancel it, then start the balance"})
        assert kernel.units == [] and audited() == []

    @pytest.mark.parametrize("state, why", [
        ("running", "A balance of pool data runs already"),
        ("paused", "A balance of pool data is paused: resume or cancel it"),
    ])
    def test_one_balance_per_pool(self, kernel, state, why):
        kernel.state = state
        with admin_client() as client:
            r = client.post(URL, json={})

        assert (r.status_code, r.json()) == (409, {"detail": why})
        assert kernel.units == [] and audited() == []

    def test_a_start_that_btrfs_refuses_answers_its_error(self, kernel):
        kernel.refuse_start = ("ERROR: unable to start balance, another exclusive operation 'device replace' in "
                               "progress\n")
        with admin_client() as client:
            r = client.post(URL, json={})

        assert (r.status_code, r.json()) == (500, {
            "detail": "Balance start failed: ERROR: unable to start balance, another exclusive operation "
                      "'device replace' in progress"})
        assert audited() == []

    def test_a_balance_with_nothing_to_rewrite_answers_how_it_ended(self, kernel):
        kernel.ends_at_once = "Done, had to relocate 0 out of 4 chunks\n"
        with admin_client() as client:
            r = client.post(URL, json={})

        assert r.status_code == 200, r.text
        assert r.json() == answer(result="finished", completed=0, considered=4)
        assert [a for a, *_ in audited()] == ["balance_started"]

    def test_a_balance_that_the_kernel_does_not_report_yet_answers_running(self, kernel):
        """systemd-run returns once btrfs runs, maybe before it reached the kernel (as a scrub,
        ADR-0367): the answer is running, and the events socket follows it."""
        kernel.unseen = True
        with admin_client() as client:
            r = client.post(URL, json={})

        assert r.status_code == 200, r.text
        assert r.json() == answer(status="running")
        assert balances.current() == [answer(status="running")]

    def test_a_unit_that_systemd_refuses_answers_its_error(self, kernel):
        kernel.unit = ["btrfs", "balance", "start", MOUNTPOINT]  # a unit left from a balance by hand
        with admin_client() as client:
            r = client.post(URL, json={})

        assert r.status_code == 500
        assert r.json()["detail"].startswith("Balance start failed: Failed to start transient service unit")

    def test_every_command_runs_in_a_worker_thread(self, kernel):
        with admin_client() as client:
            client.post(URL, json={})
            client.get(URL)
            client.post(f"{URL}/pause")
            client.post(f"{URL}/resume")
            client.delete(URL)

        assert kernel.on_loop and not any(kernel.on_loop)


# What btrfs balance status prints (btrfs-progs 6, cmds/balance.c), by the fake kernel's state.
class TestStatus:
    @pytest.mark.parametrize("state, request_, expected", [
        (None, None, answer()),
        ("running", None, answer(status="running", completed=2, expected=12, considered=5)),
        ("running", "pause", answer(status="pausing", completed=2, expected=12, considered=5)),
        ("running", "cancel", answer(status="cancelling", completed=2, expected=12, considered=5)),
        ("paused", None, answer(status="paused", completed=2, expected=12, considered=5)),
    ])
    def test_the_status_is_what_btrfs_reports(self, kernel, state, request_, expected):
        kernel.state, kernel.requests = state, {request_} - {None}
        kernel.completed, kernel.considered = 2, 5
        with admin_client() as client:
            r = client.get(URL)

        assert r.status_code == 200, r.text
        assert r.json() == expected
        assert kernel.container == [["btrfs", "balance", "status", MOUNTPOINT]]

    @pytest.mark.parametrize("log, expected", [
        ("Done, had to relocate 3 out of 12 chunks\n", answer(result="finished", completed=3, considered=12)),
        ("balance canceled by user\n", answer(result="cancelled")),
        ("ERROR: error during balancing '/mnt/pools/data': No space left on device\n"
         "There may be more info in syslog - try dmesg | tail\n",
         answer(result="failed", error="ERROR: error during balancing '/mnt/pools/data': No space left on device")),
        # Paused, and no longer: cancelled, or resumed by the kernel when the pool was mounted again.
        ("balance paused by user\n", answer()),
        # A pause wins over a cancel requested meanwhile: the balance was paused.
        ("balance paused by user\nbalance canceled by user\n", answer()),
        # When btrfs ended by itself, systemd still runs the unit's ExecStop, whose pause finds nothing
        # to pause.
        ("Done, had to relocate 3 out of 12 chunks\nERROR: balance pause on '/mnt/pools/data' failed: Not running\n",
         answer(result="finished", completed=3, considered=12)),
        ("balance canceled by user\nERROR: balance pause on '/mnt/pools/data' failed: Not running\n",
         answer(result="cancelled")),
        ("ERROR: error during balancing '/mnt/pools/data': No space left on device\n"
         "There may be more info in syslog - try dmesg | tail\n"
         "ERROR: balance pause on '/mnt/pools/data' failed: Not running\n",
         answer(result="failed", error="ERROR: error during balancing '/mnt/pools/data': No space left on device")),
        ("ERROR: balance pause on '/mnt/pools/data' failed: Not running\n", answer()),
        # The unit's btrfs was killed: it printed nothing.
        ("", answer()),
        # No balance since the host started (/run is a tmpfs).
        (None, answer()),
    ])
    def test_an_idle_pool_says_how_its_last_balance_ended(self, kernel, log, expected):
        if log is not None:
            kernel.host.files[LOG] = log
        with admin_client() as client:
            r = client.get(URL)

        assert (r.status_code, r.json()) == (200, expected)

    def test_a_status_that_btrfs_cannot_report_is_an_error(self, kernel):
        kernel.status_fails = f"ERROR: not a btrfs filesystem: {MOUNTPOINT}\n"
        with admin_client() as client:
            r = client.get(URL)

        assert (r.status_code, r.json()) == (500, {
            "detail": f"Balance status failed: ERROR: not a btrfs filesystem: {MOUNTPOINT}"})

    def test_a_status_that_does_not_answer(self, kernel, monkeypatch):
        def hangs(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(host, "run_in_container", hangs)
        with admin_client() as client:
            r = client.get(URL)

        assert (r.status_code, r.json()) == (500, {
            "detail": "btrfs balance status did not answer within 60 seconds: pool data may not be responding"})


class TestPauseResumeCancel:
    def test_pause_resume_and_cancel_act_on_the_running_balance(self, kernel):
        with admin_client() as client:
            client.post(URL, json={})
            kernel.completed, kernel.considered = 2, 4
            paused = client.post(f"{URL}/pause")
            assert (paused.status_code, paused.json()) == (200, answer(
                status="paused", completed=2, expected=12, considered=4))
            assert kernel.unit is None  # the unit's btrfs ended with the pause

            resumed = client.post(f"{URL}/resume")
            assert (resumed.status_code, resumed.json()) == (200, answer(
                status="running", completed=0, expected=12, considered=0))
            assert kernel.units[-1] == UNIT + ["btrfs", "balance", "resume", MOUNTPOINT]

            cancelled = client.delete(URL)
            assert (cancelled.status_code, cancelled.json()) == (200, answer(result="cancelled"))
            assert client.get(URL).json() == answer(result="cancelled")

        assert [(a, d) for a, _, _, d, _ in audited()] == [
            ("balance_started",
             "Started a balance of the pool data: the data and metadata chunks less than 20% full"),
            ("balance_paused", "Paused the balance of the pool data"),
            ("balance_resumed", "Resumed the balance of the pool data"),
            ("balance_cancelled", "Cancelled the balance of the pool data"),
        ]

    def test_a_cancel_of_a_paused_balance_answers_cancelled(self, kernel):
        kernel.state = "paused"
        with admin_client() as client:
            r = client.delete(URL)

        assert (r.status_code, r.json()) == (200, answer(result="cancelled"))
        assert [a for a, *_ in audited()] == ["balance_cancelled"]

    @pytest.mark.parametrize("action, method, status", [
        ("pause", "post", "pausing"), ("cancel", "delete", "cancelling"),
    ])
    def test_a_pause_or_cancel_that_waits_for_the_chunk_being_moved_answers_at_once(
            self, kernel, action, method, status):
        """The kernel pauses or cancels after the chunk it moves, a minute or more on a hard disk:
        btrfs waits until then, and the answer comes after BALANCE_STOP_WAIT with the request."""
        kernel.state, kernel.stops_wait = "running", True
        url = f"{URL}/pause" if action == "pause" else URL
        with admin_client() as client:
            r = getattr(client, method)(url)

        assert (r.status_code, r.json()["status"]) == (200, status)
        assert [cmd[2] for cmd in kernel.container] == ["status", action, "status"]
        assert [c for c in kernel.container if c[2] == action][0] == ["btrfs", "balance", action, MOUNTPOINT]
        assert [a for a, *_ in audited()] == [f"balance_{'paused' if action == 'pause' else 'cancelled'}"]
        assert balances.current() == [answer(status=status, completed=0, expected=12, considered=0)]

    @pytest.mark.parametrize("action, method", [("pause", "post"), ("cancel", "delete")])
    def test_a_pause_or_cancel_without_a_balance_answers_the_status_and_writes_nothing(self, kernel, action, method):
        url = f"{URL}/pause" if action == "pause" else URL
        with admin_client() as client:
            r = getattr(client, method)(url)

        assert (r.status_code, r.json()) == (200, answer())
        assert audited() == []

    def test_a_pause_of_a_paused_balance_answers_paused_and_writes_nothing(self, kernel):
        """The kernel pauses only a balance that runs: btrfs says "Not running" for a paused one."""
        kernel.state, kernel.completed, kernel.considered = "paused", 2, 4
        with admin_client() as client:
            r = client.post(f"{URL}/pause")

        assert (r.status_code, r.json()) == (200, answer(status="paused", completed=2, expected=12, considered=4))
        assert [cmd[2] for cmd in kernel.container] == ["status", "pause", "status"]
        assert audited() == []

    def test_a_cancel_while_a_pause_waits_for_the_chunk_being_moved_is_refused(self, kernel):
        """The kernel pauses a balance with a pause and a cancel requested, and the cancel returns
        0 all the same: it would be audited as cancelled while the balance stays on the disk."""
        kernel.state, kernel.stops_wait, kernel.completed = "running", True, 4
        with admin_client() as client:
            pausing = client.post(f"{URL}/pause")
            refused = client.delete(URL)
            kernel.chunk_moved()
            paused = client.get(URL)
            cancelled = client.delete(URL)

        assert (pausing.status_code, pausing.json()["status"]) == (200, "pausing")
        assert (refused.status_code, refused.json()) == (409, {
            "detail": "A pause of the balance of pool data waits for the chunk being moved: cancel the balance once "
                      "it is paused"})
        assert paused.json()["status"] == "paused"
        assert (cancelled.status_code, cancelled.json()) == (200, answer(result="cancelled"))
        assert [cmd[2] for cmd in kernel.container if cmd[2] != "status"] == ["pause", "cancel"]
        assert [a for a, *_ in audited()] == ["balance_paused", "balance_cancelled"]

    def test_a_pause_while_a_cancel_waits_for_the_chunk_being_moved_is_refused(self, kernel):
        """The pause would win, and the balance cancelled in the audit log would stay on the disk."""
        kernel.state, kernel.stops_wait = "running", True
        with admin_client() as client:
            cancelling = client.delete(URL)
            refused = client.post(f"{URL}/pause")
            kernel.chunk_moved()
            ended = client.get(URL)

        assert (cancelling.status_code, cancelling.json()["status"]) == (200, "cancelling")
        assert (refused.status_code, refused.json()) == (409, {
            "detail": "A cancel of the balance of pool data waits for the chunk being moved"})
        assert ended.json() == answer(result="cancelled")
        assert [cmd[2] for cmd in kernel.container if cmd[2] != "status"] == ["cancel"]
        assert [a for a, *_ in audited()] == ["balance_cancelled"]

    @pytest.mark.parametrize("action, method, status", [
        ("pause", "post", "pausing"), ("cancel", "delete", "cancelling"),
    ])
    def test_a_second_pause_or_cancel_while_the_first_waits_answers_the_status_and_writes_nothing(
            self, kernel, action, method, status):
        kernel.state, kernel.requests = "running", {action}
        url = f"{URL}/pause" if action == "pause" else URL
        with admin_client() as client:
            r = getattr(client, method)(url)

        assert (r.status_code, r.json()["status"]) == (200, status)
        assert [cmd[2] for cmd in kernel.container] == ["status"]
        assert audited() == []

    def test_a_cancel_that_a_pause_from_elsewhere_beats_answers_paused_and_writes_nothing(self, kernel, monkeypatch):
        """A pause by hand on the host between the status UNOS read and its cancel wins."""
        kernel.state = "running"
        real = kernel.run_in_container

        def paused_by_hand_meanwhile(cmd, **kwargs):
            if cmd[2] == "cancel":
                kernel.requests.add("pause")
            return real(cmd, **kwargs)

        monkeypatch.setattr(host, "run_in_container", paused_by_hand_meanwhile)
        with admin_client() as client:
            r = client.delete(URL)

        assert (r.status_code, r.json()["status"]) == (200, "paused")
        assert audited() == []

    def test_a_cancel_of_a_paused_balance_on_a_read_only_pool_is_refused(self, kernel):
        """The kernel keeps a paused balance of a read-only filesystem for its next read-write
        mount: it refuses the cancel (EROFS)."""
        kernel.state = "paused"
        kernel.host.mounts.clear()
        kernel.host.mount_pool("data", vfs_options="ro,noatime", fs_options="ro")
        with admin_client() as client:
            r = client.delete(URL)

        assert (r.status_code, r.json()) == (409, {
            "detail": "Pool data is mounted read-only: its paused balance can be cancelled once it is mounted "
                      "read-write"})
        assert [cmd[2] for cmd in kernel.container] == ["status"]
        assert kernel.state == "paused" and audited() == []

    def test_a_resume_needs_a_paused_balance(self, kernel):
        with admin_client() as client:
            idle = client.post(f"{URL}/resume")
            kernel.state = "running"
            running = client.post(f"{URL}/resume")

        assert (idle.status_code, idle.json()) == (409, {"detail": "No balance of pool data is paused"})
        assert (running.status_code, running.json()) == (409, {"detail": "No balance of pool data is paused"})
        assert kernel.units == [] and audited() == []

    def test_a_resume_is_refused_while_a_scrub_runs_or_the_pool_is_read_only(self, kernel):
        kernel.state, kernel.scrub = "paused", "running"
        with admin_client() as client:
            scrubbing = client.post(f"{URL}/resume")
            kernel.scrub = "finished"
            kernel.host.mounts.clear()
            kernel.host.mount_pool("data", vfs_options="ro,noatime", fs_options="ro")
            read_only = client.post(f"{URL}/resume")

        assert (scrubbing.status_code, scrubbing.json()) == (409, {
            "detail": "A scrub of pool data runs: wait for its end or cancel it, then resume the balance"})
        assert (read_only.status_code, read_only.json()) == (409, {
            "detail": "Pool data is mounted read-only: a balance cannot rewrite its chunks"})
        assert kernel.units == []

    def test_a_pause_that_btrfs_refuses_otherwise_is_an_error(self, kernel, monkeypatch):
        kernel.state = "running"
        real = kernel.run_in_container

        def refuses(cmd, **kwargs):
            if cmd[2] == "pause":
                return subprocess.CompletedProcess(cmd, 1, "", "ERROR: balance pause on '/mnt/pools/data' failed: "
                                                               "Read-only file system\n")
            return real(cmd, **kwargs)

        monkeypatch.setattr(host, "run_in_container", refuses)
        with admin_client() as client:
            r = client.post(f"{URL}/pause")

        assert (r.status_code, r.json()) == (500, {
            "detail": "Balance pause failed: ERROR: balance pause on '/mnt/pools/data' failed: Read-only file system"})
        assert audited() == []


class TestTheTimeLeft:
    def test_the_estimate_comes_from_the_chunks_balanced_since_the_balance_was_first_seen(self, kernel, monkeypatch):
        clock = SimpleNamespace(now=1000.0)
        monkeypatch.setattr(balances, "_now", lambda: clock.now)
        with admin_client() as client:
            started = client.post(URL, json={}).json()
            assert started["seconds_left"] is None  # nothing balanced yet

            clock.now += 30
            kernel.completed, kernel.considered = 3, 3
            assert client.get(URL).json()["seconds_left"] == 90  # 9 chunks left at 10 s each

            kernel.state = "paused"
            assert client.get(URL).json()["seconds_left"] is None

            # A resume counts from 0 again: the estimate starts again.
            kernel.state, kernel.completed, kernel.considered = "running", 0, 0
            clock.now += 600
            assert client.get(URL).json()["seconds_left"] is None
            clock.now += 20
            kernel.completed = 4
            assert client.get(URL).json()["seconds_left"] == 40  # 8 left at 5 s each

    def test_a_balance_found_running_is_estimated_from_its_first_read(self, kernel, monkeypatch):
        """One that ran before the supervisor's restart, or that the kernel resumed at the mount."""
        clock = SimpleNamespace(now=50.0)
        monkeypatch.setattr(balances, "_now", lambda: clock.now)
        kernel.state, kernel.completed = "running", 6
        with admin_client() as client:
            assert client.get(URL).json()["seconds_left"] is None
            clock.now += 60
            kernel.completed = 8
            assert client.get(URL).json()["seconds_left"] == 120  # 4 left at 30 s each


class TestScrubAndBalance:
    @pytest.mark.parametrize("state", ["running", "pausing", "cancelling"])
    def test_a_scrub_is_refused_while_a_balance_runs(self, kernel, state):
        kernel.state = "running"
        kernel.requests = {"running": set(), "pausing": {"pause"}, "cancelling": {"cancel"}}[state]
        with admin_client() as client:
            r = client.post("/api/v1/storage/pools/data/scrub")

        assert (r.status_code, r.json()) == (409, {
            "detail": "A balance of pool data runs: wait for its end, or pause or cancel it, then start the scrub"})
        assert ["btrfs", "scrub", "start", MOUNTPOINT] not in kernel.container

    def test_a_scrub_runs_beside_a_paused_balance(self, kernel):
        kernel.state = "paused"
        with admin_client() as client:
            r = client.post("/api/v1/storage/pools/data/scrub")

        assert r.status_code == 200, r.text
        assert ["btrfs", "scrub", "start", MOUNTPOINT] in kernel.container

    def test_a_balance_status_that_cannot_be_read_refuses_no_scrub(self, kernel):
        kernel.status_fails = "ERROR: cannot read the balance status\n"
        with admin_client() as client:
            r = client.post("/api/v1/storage/pools/data/scrub")

        assert r.status_code == 200, r.text


class TestBalancesForTheEventsSocket:
    def test_a_pool_joins_when_its_balance_runs_and_then_keeps_each_answer(self, kernel):
        with admin_client() as client:
            assert client.get(URL).json() == answer()
            assert balances.current() == []  # never seen running

            started = client.post(URL, json={}).json()
            assert balances.current() == [started]

            kernel.completed = 1
            read = client.get(URL).json()
            assert balances.current() == [read] and read["completed"] == 1

            paused = client.post(f"{URL}/pause").json()
            assert balances.current() == [paused] and paused["status"] == "paused"

    def test_a_balance_that_runs_in_the_kernel_shows_after_a_restart_of_the_supervisor(self, kernel):
        """It runs in the host's unit, which a restart of the supervisor's container leaves alone:
        the supervisor that starts knows nothing of it, and the first read of its status, as the
        Storage page makes when it loads, finds it and has the events socket follow it."""
        kernel.state, kernel.unit = "running", ["btrfs", "balance", "start", "-dusage=20", "-musage=20", MOUNTPOINT]
        kernel.completed, kernel.considered = 5, 7
        assert balances.current() == []
        with admin_client() as client:
            r = client.get(URL)

        assert (r.status_code, r.json()) == (200, answer(status="running", completed=5, expected=12, considered=7))
        assert balances.current() == [r.json()]
        assert kernel.units == []  # nothing started again

    def test_a_status_that_cannot_be_read_is_noted_for_a_followed_balance_only(self, kernel):
        kernel.status_fails = "ERROR: cannot read the balance status\n"
        detail = "Balance status failed: ERROR: cannot read the balance status"
        with admin_client() as client:
            assert client.get(URL).json() == {"detail": detail}
            assert balances.current() == []

            balances.note("data", BalanceStatus(pool="data", status="running"))
            assert client.get(URL).json() == {"detail": detail}
        assert balances.current() == [{"pool": "data", "error": detail}]

    def test_the_follow_reads_the_balances_that_run_until_they_end(self, kernel, monkeypatch):
        monkeypatch.setattr(balances, "READ_INTERVAL_SECONDS", 0)

        async def follow():
            balances.follow()  # none seen running: no read
            assert balances._reading is None
            balances.note("data", BalanceStatus(pool="data", status="running"))
            kernel.state, kernel.completed = "running", 5
            balances.follow()
            await balances._reading
            assert balances.current()[0]["completed"] == 5
            kernel.finish()
            balances.follow()
            await balances._reading
            assert balances.current() == [answer(result="finished", completed=3, considered=12)]
            reads = len(kernel.container)
            balances.follow()  # it no longer runs: no read starts
            assert len(kernel.container) == reads

        asyncio.run(follow())

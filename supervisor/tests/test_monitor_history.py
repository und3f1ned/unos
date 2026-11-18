"""The history of System > Resource Monitor (ADR-0398): a point every ten seconds of the host's CPU,
memory, network and physical disks, kept for an hour in RAM, and GET /api/v1/monitor/history, which
the tab reads once when it opens; the old monitor routes that no page called are gone."""

import asyncio
import logging
import threading
import time
from collections import deque, namedtuple

import psutil
import pytest

from src.monitor import history
from src.websocket import events

from .test_authz import admin_headers, alice_headers, authz_db, client, fresh_rate_limits  # noqa: F401
from .test_ws_stats import host_disks, host_net_dev, net_dev  # noqa: F401

API = "/api/v1"

# psutil's CPU times as Linux has them: the guest times are counted in user and nice already.
CpuTimes = namedtuple("CpuTimes", "user nice system idle iowait irq softirq steal guest guest_nice")


@pytest.fixture
def host(monkeypatch, host_net_dev, host_disks):  # noqa: F811
    """A fresh history on a clock that the test moves, and the host's counters, which the test sets:
    ``host.cpu`` the busy and idle seconds of the CPUs, ``host.memory`` the percentage of memory in
    use, ``host.net`` and ``host.disks`` (host_disks.counters) each device's two counters."""
    now = [5000.0]
    monkeypatch.setattr(history, "_clock", lambda: now[0])
    monkeypatch.setattr(history, "_points", deque(maxlen=history._points.maxlen))
    monkeypatch.setattr(history, "_last", None)
    monkeypatch.setattr(history, "_failing", False)
    monkeypatch.setattr(events, "_net_unreadable_logged", False)
    state = type("Host", (), {})()
    state.now = now
    state.cpu = {"busy": 0.0, "idle": 0.0}
    state.memory = 0.0
    state.net = {}
    state.disks = host_disks
    host_disks.sysfs(physical=["sda", "nvme0n1"], virtual=["zram0", "loop0", "dm-0"])

    def cpu_times(percpu=False):
        assert not percpu
        # user, nice and system are busy, idle and iowait not; guest is within user already.
        busy = state.cpu["busy"]
        return CpuTimes(busy / 2, 0.0, busy / 2, state.cpu["idle"], 0.0, 0.0, 0.0, 0.0, 7.0, 0.0)

    monkeypatch.setattr(psutil, "cpu_times", cpu_times)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("Memory", (), {"percent": state.memory})())

    def sample(after: float = history.INTERVAL_SECONDS, *, cpu=(0.0, 0.0), memory=None, net=None, disks=None):
        """Move the clock by ``after`` seconds, the counters by what is given, and take a sample."""
        now[0] += after
        state.cpu = {"busy": state.cpu["busy"] + cpu[0], "idle": state.cpu["idle"] + cpu[1]}
        if memory is not None:
            state.memory = memory
        for name, (rx, tx) in (net or {}).items():
            before = state.net.get(name, (0, 0))
            state.net[name] = (before[0] + rx, before[1] + tx)
        host_net_dev.write_text(net_dev(**state.net))
        for name, (read, written) in (disks or {}).items():
            before = host_disks.counters.get(name, (0, 0))
            host_disks.counters[name] = (before[0] + read, before[1] + written)
        history.take_sample()

    state.sample = sample
    return state


def points() -> list[dict]:
    return history.current()["points"]


def test_each_point_is_the_average_of_the_hosts_counters_since_the_last_sample(host):
    host.sample(0, memory=30.0, net={"eth0": (0, 0)}, disks={"sda": (0, 0)})
    assert points() == [], "the first sample is only the base of the next"

    host.sample(cpu=(5.0, 5.0), memory=42.5, net={"eth0": (10_000, 5_000)}, disks={"sda": (40_960, 81_920)})
    assert history.current() == {
        "interval_seconds": history.INTERVAL_SECONDS,
        "points": [{
            "age_seconds": 0.0,
            "cpu_percent": 50.0,
            "memory_percent": 42.5,
            "net_recv_rate": 1000.0,
            "net_sent_rate": 500.0,
            "disk_read_rate": 4096.0,
            "disk_write_rate": 8192.0,
        }],
    }

    # Ages are seconds before the answer on the monotonic clock: no time of the wall clock, which a
    # board without a battery-backed clock sets at boot (ADR-0388).
    host.sample(cpu=(1.0, 9.0), net={"eth0": (0, 1_000)})
    host.now[0] += 4
    assert [(p["age_seconds"], p["cpu_percent"], p["net_sent_rate"]) for p in points()] == [
        (14.0, 50.0, 500.0), (4.0, 10.0, 100.0),
    ]


def test_only_the_hosts_listed_interfaces_and_physical_disks_count(host):
    """As the stats message (ADR-0388): not the loopback, Docker's bridges and veths, and not zram,
    loop, device mapper or md devices or partitions."""
    hidden = {"lo": (0, 0), "docker0": (0, 0), "veth1a2b": (0, 0), "br-0123abcd": (0, 0)}
    virtual = {"zram0": (0, 0), "loop0": (0, 0), "dm-0": (0, 0), "sda1": (0, 0)}
    host.sample(0, net={"eth0": (0, 0), "wlan0": (0, 0), **hidden},
                disks={"sda": (0, 0), "nvme0n1": (0, 0), **virtual})
    busy = (9_999_000, 9_999_000)
    host.sample(net={"eth0": (1_000, 0), "wlan0": (2_000, 0), **dict.fromkeys(hidden, busy)},
                disks={"sda": (1_000, 0), "nvme0n1": (3_000, 0), **dict.fromkeys(virtual, busy)})

    [point] = points()
    assert (point["net_recv_rate"], point["net_sent_rate"]) == (300.0, 0.0)
    assert (point["disk_read_rate"], point["disk_write_rate"]) == (400.0, 0.0)


def test_a_device_that_comes_goes_or_starts_again_never_makes_a_rate_jump(host):
    host.disks.sysfs(physical=["sdb"])
    host.sample(0, net={"eth0": (5_000, 5_000), "usb0": (1_000, 1_000)}, disks={"sda": (5_000, 5_000)})
    host.net.pop("usb0")
    host.net["wg0"] = (50_000_000, 50_000_000)  # comes with all its counters
    host.net["eth0"] = (10, 10)  # its driver was loaded again
    host.sample(net={}, disks={"sdb": (90_000_000, 90_000_000)})

    [point] = points()
    assert (point["net_recv_rate"], point["net_sent_rate"]) == (0.0, 0.0)
    assert (point["disk_read_rate"], point["disk_write_rate"]) == (0.0, 0.0)


def test_a_sample_after_a_gap_is_only_the_base_of_the_next(host):
    """The average over a gap (the sampler's worker thread held, a suspended host) would show as
    one point of ten seconds: no point, as for the first sample."""
    host.sample(0, net={"eth0": (0, 0)})
    host.sample(history.MAX_GAP_SECONDS + 1, cpu=(30.0, 0.0), net={"eth0": (99_000, 0)})
    assert points() == []

    host.sample(cpu=(2.0, 8.0), net={"eth0": (1_000, 0)})
    assert [(p["cpu_percent"], p["net_recv_rate"]) for p in points()] == [(20.0, 100.0)]


def test_the_history_keeps_an_hour_in_ram(host, tmp_path, monkeypatch):
    assert history.KEPT_SECONDS == 3600
    assert history._points.maxlen == int(history.KEPT_SECONDS / history.INTERVAL_SECONDS)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    host.sample(0)
    for step in range(history._points.maxlen + 20):
        host.sample(cpu=(step % 10, 10 - step % 10))

    kept = points()
    assert len(kept) == history._points.maxlen
    assert kept[0]["age_seconds"] == history.KEPT_SECONDS - history.INTERVAL_SECONDS
    assert kept[-1]["age_seconds"] == 0.0
    # Oldest first: the 20 oldest went.
    assert [p["cpu_percent"] for p in kept[:3]] == [0.0, 10.0, 20.0]
    # Nothing is written (ADR-0214): the history lives in the process only.
    assert list(workdir.iterdir()) == []


def test_only_the_last_hour_is_answered_also_after_samples_failed(host):
    """The deque's length bounds the points only while samples succeed: after a time without them,
    older points are still kept, and are not answered."""
    host.sample(0)
    host.sample(cpu=(1.0, 1.0))
    host.now[0] += history.KEPT_SECONDS - 2 * history.INTERVAL_SECONDS
    host.sample(0)
    host.sample(cpu=(1.0, 3.0))
    assert [p["age_seconds"] for p in points()] == [3590.0, 0.0]
    host.now[0] += 11
    assert [p["age_seconds"] for p in points()] == [11.0]


@pytest.mark.skipif(not hasattr(time, "CLOCK_BOOTTIME"), reason="CLOCK_BOOTTIME is Linux's")
def test_the_ages_count_the_time_the_host_was_suspended():
    """UNOS offers suspend: the monotonic clock stops while the host sleeps, and the points from
    before it would show an hour too young after a suspend of an hour."""
    before = time.clock_gettime(time.CLOCK_BOOTTIME)
    taken = history._clock()
    assert before <= taken <= time.clock_gettime(time.CLOCK_BOOTTIME)


def test_the_history_can_be_read_while_the_sampler_adds_to_it(monkeypatch):
    """The sampler appends in a worker thread while the API reads in the event loop: an append
    during a walk of a deque raises RuntimeError."""
    kept = deque(maxlen=360)
    for i in range(360):
        kept.append(history._Point(i, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
    monkeypatch.setattr(history, "_points", kept)
    monkeypatch.setattr(history, "_clock", lambda: 400.0)
    stop = threading.Event()

    def sampler():
        i = 360
        while not stop.is_set():
            kept.append(history._Point(i % 400, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
            i += 1

    thread = threading.Thread(target=sampler)
    thread.start()
    try:
        for _ in range(300):
            assert len(history.current()["points"]) == 360
    finally:
        stop.set()
        thread.join()


def test_a_failing_sample_is_logged_once_until_one_succeeds(host, monkeypatch, caplog):
    """A sample every ten seconds must not write a log line every ten seconds (ADR-0214)."""
    def broken(percpu=False):
        raise OSError("no /proc/stat")

    host.sample(0)
    working = psutil.cpu_times
    monkeypatch.setattr(psutil, "cpu_times", broken)
    with caplog.at_level(logging.WARNING, logger=history.__name__):
        for _ in range(5):
            history.sample()
        assert [r.getMessage() for r in caplog.records] == [
            "The Resource Monitor's history cannot read the host's counters: no /proc/stat"
        ]
        monkeypatch.setattr(psutil, "cpu_times", working)
        history.sample()
        history.sample()
        monkeypatch.setattr(psutil, "cpu_times", broken)
        history.sample()
    assert len(caplog.records) == 2


def test_the_loop_takes_a_sample_every_interval_in_a_worker_thread(monkeypatch):
    threads = []

    def sample():
        import threading
        threads.append(threading.current_thread() is threading.main_thread())

    async def run():
        monkeypatch.setattr(history, "INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(history, "sample", sample)
        await history.start_history()
        await asyncio.sleep(0.2)
        await history.stop_history()
        taken = len(threads)
        await asyncio.sleep(0.05)
        return taken

    taken = asyncio.run(run())
    assert taken >= 3
    assert len(threads) == taken, "a sample was taken after the stop"
    assert not any(threads), "a sample ran in the event loop"


class TestTheHistoryApi:
    def test_an_administrator_reads_the_history(self, client, admin_headers, monkeypatch):  # noqa: F811
        monkeypatch.setattr(history, "_clock", lambda: 100.0)
        monkeypatch.setattr(history, "_points", deque([
            history._Point(at=80.0, cpu_percent=12.5, memory_percent=40.0, net_recv_rate=1.0,
                           net_sent_rate=2.0, disk_read_rate=3.0, disk_write_rate=4.0),
            history._Point(at=90.0, cpu_percent=25.0, memory_percent=41.0, net_recv_rate=5.0,
                           net_sent_rate=6.0, disk_read_rate=7.0, disk_write_rate=8.0),
        ], maxlen=360))

        answer = client.get(f"{API}/monitor/history", headers=admin_headers)

        assert answer.status_code == 200
        assert answer.json() == {"interval_seconds": 10.0, "points": [
            {"age_seconds": 20.0, "cpu_percent": 12.5, "memory_percent": 40.0, "net_recv_rate": 1.0,
             "net_sent_rate": 2.0, "disk_read_rate": 3.0, "disk_write_rate": 4.0},
            {"age_seconds": 10.0, "cpu_percent": 25.0, "memory_percent": 41.0, "net_recv_rate": 5.0,
             "net_sent_rate": 6.0, "disk_read_rate": 7.0, "disk_write_rate": 8.0},
        ]}

    def test_a_regular_user_and_nobody_signed_in_are_refused(self, client, alice_headers):  # noqa: F811
        assert client.get(f"{API}/monitor/history", headers=alice_headers).status_code == 403
        assert client.get(f"{API}/monitor/history").status_code == 401

    @pytest.mark.parametrize("path", [
        "/monitor/cpu", "/monitor/memory", "/monitor/processes", "/monitor/history/cpu",
        "/monitor/history/memory", "/monitor/all",
    ])
    def test_the_routes_that_no_page_called_are_gone(self, client, admin_headers, path):  # noqa: F811
        """ADR-0207: the stats message of /ws/v1/events is the one source of live stats, and the
        history is GET /monitor/history's; /monitor/history/cpu and /memory kept only what calls of
        /monitor/cpu and /memory had taken."""
        assert client.get(f"{API}{path}", headers=admin_headers).status_code == 404

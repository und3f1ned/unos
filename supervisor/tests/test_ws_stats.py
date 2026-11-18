"""The stats message of /ws/v1/events (ADR-0006, ADR-0374)."""

from types import SimpleNamespace

import psutil
import pytest

from src.websocket import events


@pytest.fixture
def clock(monkeypatch):
    """A fresh sampler on a clock that the test moves: a list of one time in seconds."""
    now = [1000.0]
    monkeypatch.setattr(events, "_clock", lambda: now[0])
    monkeypatch.setattr(events, "_sample", None)
    monkeypatch.setattr(events, "_sample_at", None)
    monkeypatch.setattr(events, "_processes", [])
    monkeypatch.setattr(events, "_processes_at", None)
    monkeypatch.setattr(events, "_prev_time", None)
    monkeypatch.setattr(events, "_prev_net", {})
    monkeypatch.setattr(events, "_prev_disks", {})
    monkeypatch.setattr(events, "_net_unreadable_logged", False)
    return now


# The header of /proc/net/dev, then one line per interface: received bytes, packets, errs, drop,
# fifo, frame, compressed, multicast, then sent bytes, packets, errs, drop, fifo, colls, carrier,
# compressed.
NET_DEV_HEADER = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
)


def net_dev(**interfaces: tuple[int, int]) -> str:
    """/proc/net/dev with each interface's received and sent bytes ("_" in a name is "-")."""
    lines = [
        f"{name.replace('_', '-'):>6}: {rx} 7 0 0 0 0 0 0 {tx} 5 0 0 0 0 0 0\n"
        for name, (rx, tx) in interfaces.items()
    ]
    return NET_DEV_HEADER + "".join(lines)


@pytest.fixture
def host_net_dev(monkeypatch, tmp_path):
    """The host's /proc/net/dev, as PID 1 sees it, in a file the test writes: on a NAS host the
    supervisor runs in the host's namespaces (no UNOS_HOST_EXEC=local), and psutil's view, which
    is its container's own network namespace, is never asked."""
    path = tmp_path / "net-dev"
    monkeypatch.setattr(events, "HOST_NET_DEV", str(path))
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)

    def container_view(*args, **kwargs):
        raise AssertionError("the container's own interfaces were read")

    for name in ("net_io_counters", "net_if_stats", "net_if_addrs"):
        monkeypatch.setattr(events.psutil, name, container_view)
    return path


@pytest.fixture
def host_disks(monkeypatch, tmp_path):
    """The host's block devices: ``host_disks.sysfs(physical=[...], virtual=[...])`` makes them in a
    /sys/block of the test (a physical disk has its ``device`` link there, a zram, loop, dm or md
    device none, a partition no entry), and ``host_disks.counters`` is /proc/diskstats as psutil
    reads it per device: name -> (read bytes, written bytes). ``host_disks.reads`` counts psutil's
    reads; its totals over every device of /sys/block are never asked."""
    sys_block = tmp_path / "sys-block"
    sys_block.mkdir()
    monkeypatch.setattr(events, "SYS_BLOCK", str(sys_block))
    disks = SimpleNamespace(counters={}, reads=0)

    def sysfs(physical=(), virtual=()):
        for name in physical:
            (sys_block / name / "device").mkdir(parents=True)
        for name in virtual:
            (sys_block / name).mkdir()

    def disk_io_counters(perdisk=False, nowrap=True):
        assert perdisk, "psutil's totals of every device of /sys/block were read"
        disks.reads += 1
        return {
            name: psutil._common.sdiskio(1, 1, read, written, 0, 0)
            for name, (read, written) in disks.counters.items()
        }

    monkeypatch.setattr(events.psutil, "disk_io_counters", disk_io_counters)
    disks.sysfs = sysfs
    return disks


def test_the_network_is_the_traffic_of_the_hosts_interfaces(clock, host_net_dev):
    """system.dashboard (ADR-0388): the supervisor's container has a network namespace of its own
    (unos-net), so psutil showed its own traffic to Caddy as the host's. The network of the stats
    message is the host's, from PID 1's /proc/net/dev, over the interfaces that System > Network
    lists: not the loopback, dummy0 or Docker's bridges and veths."""
    host_net_dev.write_text(net_dev(
        lo=(9_000_000, 9_000_000),
        eth0=(1_000_000, 500_000),
        wlan0=(20_000, 10_000),
        docker0=(70_000, 80_000),
        dummy0=(70_000, 80_000),
        veth1a2b3c=(70_000, 80_000),
        br_0123456789ab=(70_000, 80_000),
    ))
    first = events.get_system_stats()["data"]["network"]

    clock[0] += 2
    host_net_dev.write_text(net_dev(
        lo=(19_000_000, 19_000_000),
        eth0=(1_002_000, 501_000),
        wlan0=(20_400, 10_000),
        docker0=(170_000, 180_000),
        dummy0=(170_000, 180_000),
        veth1a2b3c=(170_000, 180_000),
        br_0123456789ab=(170_000, 180_000),
    ))
    second = events.get_system_stats()["data"]["network"]

    assert (first["bytes_recv_rate"], first["bytes_sent_rate"]) == (0.0, 0.0)
    assert second == {
        "bytes_recv_rate": 1200.0,
        "bytes_sent_rate": 500.0,
        "interfaces": [
            {"name": "eth0", "bytes_recv": 1_002_000, "bytes_sent": 501_000,
             "bytes_recv_rate": 1000.0, "bytes_sent_rate": 500.0},
            {"name": "wlan0", "bytes_recv": 20_400, "bytes_sent": 10_000,
             "bytes_recv_rate": 200.0, "bytes_sent_rate": 0.0},
        ],
    }


def test_an_interface_that_comes_or_goes_never_makes_a_rate_negative(clock, host_net_dev):
    """Summed as psutil sums them, the counters of the host's interfaces fall when one goes away
    (a USB adapter unplugged, a VPN stopped) and jump by all its counters when one comes: a
    negative rate, or one far too high. Each rate is the sum of the interfaces that both samples
    have, and an interface whose counters went back (its driver loaded again) counts nothing for
    that sample (ADR-0388)."""
    host_net_dev.write_text(net_dev(eth0=(1_000_000, 1_000_000), wlan0=(5_000_000, 4_000_000)))
    events.get_system_stats()

    clock[0] += 2
    host_net_dev.write_text(net_dev(eth0=(1_004_000, 1_002_000), usb0=(80_000_000, 90_000_000)))
    unplugged = events.get_system_stats()["data"]["network"]

    clock[0] += 2
    host_net_dev.write_text(net_dev(eth0=(1_000, 2_000), usb0=(80_010_000, 90_020_000)))
    reloaded = events.get_system_stats()["data"]["network"]

    assert (unplugged["bytes_recv_rate"], unplugged["bytes_sent_rate"]) == (2000.0, 1000.0)
    assert [i["name"] for i in unplugged["interfaces"]] == ["eth0", "usb0"]
    assert unplugged["interfaces"][1]["bytes_recv_rate"] == 0.0
    assert (reloaded["bytes_recv_rate"], reloaded["bytes_sent_rate"]) == (5000.0, 10000.0)
    assert reloaded["interfaces"][0]["bytes_recv_rate"] == 0.0


def test_where_the_host_is_the_machine_itself_psutil_reads_its_interfaces(monkeypatch, clock, tmp_path):
    """With UNOS_HOST_EXEC=local (the stand-in, the compose test stack) the host is the machine the
    supervisor runs on: psutil's counters of each of its interfaces, with the same interfaces left
    out, and PID 1's file is not read (ADR-0388)."""
    monkeypatch.setenv("UNOS_HOST_EXEC", "local")
    monkeypatch.setattr(events, "HOST_NET_DEV", str(tmp_path / "not-read"))
    samples = iter([
        {"lo": (5_000, 5_000), "en0": (10_000, 4_000), "docker0": (1_000, 1_000)},
        {"lo": (9_000, 9_000), "en0": (30_000, 5_000), "docker0": (9_000, 9_000)},
    ])

    def net_io_counters(pernic=False, nowrap=True):
        assert pernic
        return {
            name: psutil._common.snetio(sent, received, 3, 3, 0, 0, 0, 0)
            for name, (received, sent) in next(samples).items()
        }

    monkeypatch.setattr(events.psutil, "net_io_counters", net_io_counters)

    events.get_system_stats()
    clock[0] += 2
    network = events.get_system_stats()["data"]["network"]

    assert network == {
        "bytes_recv_rate": 10_000.0,
        "bytes_sent_rate": 500.0,
        "interfaces": [{"name": "en0", "bytes_recv": 30_000, "bytes_sent": 5_000,
                        "bytes_recv_rate": 10_000.0, "bytes_sent_rate": 500.0}],
    }
    assert not events._net_unreadable_logged


def test_the_rates_take_the_time_between_the_samples(clock, host_net_dev):
    """A board without a clock that keeps time sets its clock at boot, from the network: rates over
    the wall clock came out near zero or negative then. They take the monotonic clock (ADR-0388)."""
    host_net_dev.write_text(net_dev(eth0=(0, 0)))
    events.get_system_stats()

    clock[0] += 4
    host_net_dev.write_text(net_dev(eth0=(8_000, 4_000)))
    network = events.get_system_stats()["data"]["network"]

    assert (network["bytes_recv_rate"], network["bytes_sent_rate"]) == (2000.0, 1000.0)


def test_a_sample_after_a_gap_gives_no_rates_of_the_gap(clock, host_net_dev, host_disks):
    """Samples are taken only while a socket asks. The first one after an hour without a dashboard
    gave the hour's average as the current rate, shown for two seconds; its rates are 0, like the
    first sample's after a start, and the next one has the rates of its two seconds (ADR-0388)."""
    host_disks.sysfs(physical=["sda"])
    host_disks.counters = {"sda": (1_000_000, 0)}
    host_net_dev.write_text(net_dev(eth0=(0, 0)))
    events.get_system_stats()

    clock[0] += 3600
    host_disks.counters = {"sda": (900_000_000, 0)}
    host_net_dev.write_text(net_dev(eth0=(36_000_000_000, 3_600_000)))
    after_the_gap = events.get_system_stats()["data"]

    clock[0] += 2
    host_disks.counters = {"sda": (902_097_152, 4_096)}
    host_net_dev.write_text(net_dev(eth0=(36_000_002_000, 3_600_000)))
    next_one = events.get_system_stats()["data"]

    assert events.RATE_MAX_GAP_SECONDS >= 3 * events.STATS_INTERVAL_SECONDS
    network, disks = after_the_gap["network"], after_the_gap["disk_io"]
    assert (network["bytes_recv_rate"], network["bytes_sent_rate"]) == (0.0, 0.0)
    assert (disks["read_rate"], disks["write_rate"]) == (0.0, 0.0)
    network, disks = next_one["network"], next_one["disk_io"]
    assert (network["bytes_recv_rate"], network["bytes_sent_rate"]) == (1000.0, 0.0)
    assert (disks["read_rate"], disks["write_rate"]) == (1_048_576.0, 2_048.0)


def test_unreadable_host_counters_send_no_traffic_and_are_logged_once(clock, host_net_dev, caplog):
    """When PID 1's /proc/net/dev cannot be read, the stats message still goes out, without
    interfaces and with no traffic rather than the container's, and the log says so once, not every
    two seconds (ADR-0214)."""
    events.get_system_stats()
    clock[0] += 2
    network = events.get_system_stats()["data"]["network"]

    assert network == {"bytes_recv_rate": 0.0, "bytes_sent_rate": 0.0, "interfaces": []}
    assert caplog.text.count("host's network counters") == 1


def test_the_disk_rates_count_the_hosts_physical_disks_only(clock, host_disks):
    """psutil's totals count every device of /sys/block: zram (Armbian keeps its swap and /var/log
    there), loop devices, and the device mapper and md devices over the disks, which counted a
    write to LUKS or LVM twice. The Disk I/O card counts the physical disks only, those with a
    ``device`` in /sys/block, without their partitions; the list of block devices keeps every one
    but loop and RAM devices for the Storage page (ADR-0360, ADR-0388)."""
    host_disks.sysfs(physical=["sda", "nvme0n1", "mmcblk0"], virtual=["zram0", "loop0", "dm-0", "md0"])
    devices = ["sda", "sda1", "nvme0n1", "nvme0n1p1", "mmcblk0", "mmcblk0p2", "zram0", "loop0", "dm-0", "md0"]
    host_disks.counters = {name: (1_000_000, 1_000_000) for name in devices}
    events.get_system_stats()

    clock[0] += 2
    host_disks.counters = {name: (1_000_000 + 10_000, 1_000_000 + 20_000) for name in devices}
    disk_io = events.get_system_stats()["data"]["disk_io"]

    # sda, nvme0n1 and mmcblk0: 3 x 10 000 bytes read and 3 x 20 000 written in 2 seconds.
    assert disk_io == {
        "read_rate": 15_000.0,
        "write_rate": 30_000.0,
        "disks": [{"name": name} for name in devices if name != "loop0"],
    }
    # One read of /proc/diskstats per sample: the list and the rates come from it.
    assert host_disks.reads == 2


def test_a_disk_that_comes_or_goes_never_makes_the_disk_rates_jump(clock, host_disks):
    """A disk attached between two samples added all its counters at once, and one removed made the
    rates negative. The rates are the sum of the disks that both samples have, and a disk whose
    counters went back counts nothing for that sample, as for the interfaces (ADR-0388)."""
    host_disks.sysfs(physical=["sda", "sdb", "sdc"])
    host_disks.counters = {"sda": (1_000_000, 2_000_000), "sdb": (5_000_000, 5_000_000)}
    events.get_system_stats()

    clock[0] += 2
    host_disks.counters = {"sda": (3_097_152, 2_000_000), "sdc": (900_000_000, 800_000_000)}
    swapped = events.get_system_stats()["data"]["disk_io"]

    clock[0] += 2
    host_disks.counters = {"sda": (1_000, 1_000), "sdc": (900_004_096, 800_000_000)}
    went_back = events.get_system_stats()["data"]["disk_io"]

    assert (swapped["read_rate"], swapped["write_rate"]) == (1_048_576.0, 0.0)
    assert [disk["name"] for disk in swapped["disks"]] == ["sda", "sdc"]
    assert (went_back["read_rate"], went_back["write_rate"]) == (2_048.0, 0.0)


def test_one_sample_serves_every_socket_of_an_interval(monkeypatch, clock):
    """Every open socket took a sample of its own every two seconds: on a SpacemiT K1 0.4 s of CPU
    each, and as much again for every further tab (ADR-0374). One sample now serves every socket
    until it is STATS_INTERVAL_SECONDS old."""
    taken = []

    def sample():
        taken.append(clock[0])
        return {"type": "stats", "data": {"sample": len(taken)}}

    monkeypatch.setattr(events, "get_system_stats", sample)

    first = [events.shared_system_stats() for _ in range(5)]
    clock[0] += events.STATS_INTERVAL_SECONDS / 2
    later = events.shared_system_stats()
    clock[0] += events.STATS_INTERVAL_SECONDS
    next_one = events.shared_system_stats()

    assert taken == [1000.0, 1000.0 + events.STATS_INTERVAL_SECONDS * 1.5]
    assert all(message is first[0] for message in [*first, later])
    assert next_one["data"] == {"sample": 2}


def test_the_process_list_is_taken_at_most_every_process_list_interval(monkeypatch, clock):
    """The list of the host's processes was most of a sample's cost: 0.4 s of a K1's CPU for 315
    processes. A sample reuses the last list until it is PROCESS_LIST_INTERVAL_SECONDS old
    (ADR-0374)."""
    listings = []
    real = psutil.process_iter

    def counting(*args, **kwargs):
        listings.append(clock[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(events.psutil, "process_iter", counting)

    first = events.get_system_stats()
    clock[0] += events.STATS_INTERVAL_SECONDS
    second = events.get_system_stats()
    clock[0] += events.PROCESS_LIST_INTERVAL_SECONDS
    events.get_system_stats()

    assert events.PROCESS_LIST_INTERVAL_SECONDS >= 5 * events.STATS_INTERVAL_SECONDS
    assert len(listings) == 2
    assert second["data"]["processes"] == first["data"]["processes"]


def test_only_the_listed_processes_are_read_in_full(monkeypatch, clock):
    """Name, memory, status and user of each of 311 processes cost a K1 0.41 s of CPU, the CPU
    share alone 0.13 s: the listing reads the CPU share of every process and the rest for the ten
    on the dashboard only (ADR-0375)."""
    asked = []
    real = psutil.process_iter

    def recording(attrs=None, *args, **kwargs):
        asked.append(attrs)
        return real(attrs, *args, **kwargs)

    users = []
    real_username = psutil.Process.username

    def username(self):
        users.append(self.pid)
        return real_username(self)

    monkeypatch.setattr(events.psutil, "process_iter", recording)
    monkeypatch.setattr(psutil.Process, "username", username)

    processes = events.get_system_stats()["data"]["processes"]

    assert asked == [["cpu_percent"]]
    assert 0 < len(processes) <= 10
    assert sorted(users) == sorted(proc["pid"] for proc in processes)
    shares = [proc["cpu_percent"] for proc in processes]
    assert shares == sorted(shares, reverse=True)
    assert set(processes[0]) == {"pid", "name", "cpu_percent", "memory_percent", "memory_mb", "status", "username"}


def test_the_stats_message_has_one_shape(clock, host_net_dev, host_disks):
    """system.no-legacy-basic-stats (ADR-0207): the flat fields kept for backward compatibility
    (cpu_percent, memory_used and the others) are gone, as is the disk usage of the container's
    root, which no page read; each value is in its object, as webui/src/lib/stores/stats.ts reads
    it. The network and the disks have their rates, and no totals of bytes, packets or operations,
    which no page read (system.stats-without-unread-parts); an interface has its name, counters
    and rates: its address, speed and state were the container's, and System > Network takes them
    from the host; a block device has its name, which the Storage page reads (ADR-0360, ADR-0388)."""
    host_net_dev.write_text(net_dev(eth0=(1_000, 2_000)))
    host_disks.sysfs(physical=["sda"])
    host_disks.counters = {"sda": (4_096, 8_192), "sda1": (4_096, 8_192)}
    message = events.get_system_stats()

    assert message["type"] == "stats"
    data = message["data"]
    # No history: no page read it (system.stats-without-unread-parts).
    # One temperature, the CPU's (system.temperatures, ADR-0346), in place of the first reading of
    # every sensor group.
    assert sorted(data) == ["cpu", "disk_io", "memory", "network", "processes", "timestamp"]
    assert sorted(data["cpu"]) == [
        "cores", "load_1", "load_15", "load_5", "per_core", "percent", "temperature", "threads",
    ]
    assert sorted(data["memory"]) == [
        "available", "free", "percent", "swap_percent", "swap_total", "swap_used", "total", "used",
    ]
    assert sorted(data["network"]) == ["bytes_recv_rate", "bytes_sent_rate", "interfaces"]
    assert [sorted(interface) for interface in data["network"]["interfaces"]] == [
        ["bytes_recv", "bytes_recv_rate", "bytes_sent", "bytes_sent_rate", "name"],
    ]
    assert data["disk_io"] == {"read_rate": 0.0, "write_rate": 0.0, "disks": [{"name": "sda"}, {"name": "sda1"}]}


def test_the_web_ui_keeps_no_unread_stats_code():
    """system.stats-without-unread-parts (ADR-0207): every module of $lib/api and $lib/stores has a
    reader, every value that the stats store exports is imported somewhere, and no client asks for
    /system/stats or /system/temperatures, which no page reads."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "webui" / "src"
    texts = {path: path.read_text(encoding="utf-8") for path in src.rglob("*")
             if path.is_file() and path.suffix in (".ts", ".svelte")}
    lib = src / "lib"
    for folder in ("api", "stores"):
        for module in sorted((lib / folder).glob("*.ts")):
            names = [f"$lib/{folder}/{module.stem}"]
            readers = [path for path, text in texts.items() if path != module and (
                any(f"'{name}'" in text or f'"{name}"' in text for name in names)
                or (path.parent == module.parent and re.search(rf"""from ['"]\./{module.stem}['"]""", text)))]
            assert readers, f"$lib/{folder}/{module.name} has no reader"
    store = lib / "stores" / "stats.ts"
    for name in re.findall(r"^export (?:const|let|function|async function) (\w+)", texts[store], re.M):
        importers = [path for path, text in texts.items() if path != store and re.search(
            rf"""import\s*\{{[^}}]*\b{name}\b[^}}]*\}}\s*from\s*['"]\$lib/stores/stats['"]""", text)]
        assert importers, f"$lib/stores/stats.ts exports {name}, which nothing imports"
    system = texts[lib / "api" / "system.ts"]
    for endpoint in ("/system/stats", "/system/temperatures"):
        assert endpoint not in system, endpoint


def test_nothing_asks_for_the_removed_container_stats_endpoints():
    """system.no-container-stats-endpoints (ADR-0388, ADR-0207): no page, script or test of the
    web UI asks for the routes that reported the supervisor container's network."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    removed = re.compile(r"/system/stats\b|/monitor/(?:network|disk-io|history/network|history/disk-io)\b")
    files = [path for folder in ("webui/src", "webui/tests", "scripts") for path in (root / folder).rglob("*")
             if path.is_file() and path.suffix in (".ts", ".svelte", ".py", ".sh", ".md")]
    assert files
    asking = [str(path.relative_to(root)) for path in files if removed.search(path.read_text(encoding="utf-8"))]
    assert asking == []

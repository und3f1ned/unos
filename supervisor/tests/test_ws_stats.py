"""The stats message of /ws/v1/events (ADR-0006, ADR-0374)."""

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
    return now


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


def test_the_stats_message_has_one_shape():
    """system.no-legacy-basic-stats (ADR-0207): the flat fields kept for backward compatibility
    (cpu_percent, memory_used and the others) are gone, as is the disk usage of the container's
    root, which no page read; each value is in its object, as webui/src/lib/stores/stats.ts reads
    it."""
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
    assert sorted(data["network"]) == [
        "bytes_recv", "bytes_recv_rate", "bytes_sent", "bytes_sent_rate", "interfaces", "packets_recv", "packets_sent",
    ]


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

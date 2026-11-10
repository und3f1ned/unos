"""The stats message of /ws/v1/events (ADR-0006)."""

from src.websocket import events


def test_the_stats_message_has_one_shape():
    """system.no-legacy-basic-stats (ADR-0207): the flat fields kept for backward compatibility
    (cpu_percent, memory_used and the others) are gone, as is the disk usage of the container's
    root, which no page read; each value is in its object, as webui/src/lib/stores/stats.ts reads
    it."""
    message = events.get_system_stats()

    assert message["type"] == "stats"
    data = message["data"]
    assert sorted(data) == ["cpu", "disk_io", "history", "memory", "network", "processes", "temperatures", "timestamp"]
    assert sorted(data["cpu"]) == ["cores", "load_1", "load_15", "load_5", "per_core", "percent", "threads"]
    assert sorted(data["memory"]) == [
        "available", "free", "percent", "swap_percent", "swap_total", "swap_used", "total", "used",
    ]
    assert sorted(data["network"]) == [
        "bytes_recv", "bytes_recv_rate", "bytes_sent", "bytes_sent_rate", "interfaces", "packets_recv", "packets_sent",
    ]

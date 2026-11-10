"""The stats message of /ws/v1/events (ADR-0006)."""

from src.websocket import events


def test_the_stats_message_has_one_shape():
    """system.no-legacy-basic-stats (ADR-0207): the flat fields kept for backward compatibility
    (cpu_percent, memory_used, disk_percent and the others) are gone; each value is in its object,
    which the web UI reads (webui/src/lib/stores/stats.ts)."""
    message = events.get_system_stats()

    assert message["type"] == "stats"
    assert sorted(message["data"]) == [
        "cpu", "disk_io", "history", "memory", "network", "processes", "temperatures", "timestamp",
    ]
    assert sorted(message["data"]["cpu"]) == ["cores", "load_1", "load_15", "load_5", "per_core", "percent", "threads"]
    assert {"total", "used", "percent"} <= set(message["data"]["memory"])

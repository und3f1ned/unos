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

"""The app lifespan must start and stop every background loop.

Regression test: the loops used to be registered with ``@router.on_event``,
which FastAPI ignores when the app defines a ``lifespan``. As a result the
scheduler (snapshots, scrubs, SMART tests, backups) and the RAID, SMART and
UPS monitors never ran.
"""

import asyncio
import importlib
import logging
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src import host, main

# The packages re-export their APIRouter as ``router``, so import the modules
# explicitly instead of ``from src.x import router``.
health = importlib.import_module("src.health.router")
power = importlib.import_module("src.power.router")
scheduler = importlib.import_module("src.scheduler.router")

LOOPS = {
    "scheduler": (scheduler, "scheduler_loop", "_scheduler_task"),
    "raid_monitor": (health, "raid_monitor_loop", "_raid_monitor_task"),
    "smart_monitor": (health, "smart_monitor_loop", "_smart_monitor_task"),
    "ups_monitor": (power, "ups_monitor_loop", "_ups_monitor_task"),
}


def _patch_loops(started: set[str], *, host_tool_check: bool = True):
    patches = []
    for name, (module, func, _task_attr) in LOOPS.items():

        async def fake_loop(_name: str = name) -> None:
            started.add(_name)
            await asyncio.Event().wait()  # run until cancelled

        patches.append(patch.object(module, func, fake_loop))
    # The startup pool reconciliation and host tool check run host commands
    # (nsenter); keep them out of unit tests.
    patches.append(patch.object(main, "reconcile_pools_on_startup", AsyncMock(return_value=[])))
    if host_tool_check:
        patches.append(patch.object(main, "check_host_tools", AsyncMock(return_value=None)))
    return patches


def test_lifespan_starts_and_stops_all_background_loops(monkeypatch):
    monkeypatch.delenv("UNOS_DISABLE_BACKGROUND_TASKS", raising=False)
    started: set[str] = set()
    patches = _patch_loops(started)
    for p in patches:
        p.start()
    try:
        with TestClient(main.app):
            tasks = {
                name: getattr(module, task_attr)
                for name, (module, _func, task_attr) in LOOPS.items()
            }
            for name, task in tasks.items():
                assert task is not None, f"{name} task was not created"
                assert not task.done(), f"{name} task is not running"
        # Leaving the context runs the lifespan shutdown, which cancels the loops.
        assert started == set(LOOPS), f"loops never started: {set(LOOPS) - started}"
        for name, task in tasks.items():
            assert task.cancelled() or task.done(), f"{name} task was not stopped"
        main.check_host_tools.assert_called_once_with()
    finally:
        for p in patches:
            p.stop()


def test_background_loops_can_be_disabled(monkeypatch):
    monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
    started: set[str] = set()
    patches = _patch_loops(started)
    for p in patches:
        p.start()
    try:
        with TestClient(main.app):
            pass
        assert started == set()
        main.check_host_tools.assert_not_called()
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Startup check for host programs
# ---------------------------------------------------------------------------


def test_missing_host_tools_are_named_in_one_warning(monkeypatch, caplog):
    calls = []

    def fake_missing(names, *, timeout):
        calls.append((list(names), timeout, threading.current_thread() is threading.main_thread()))
        return ["btrfs", "mkfs.btrfs", "testparm"]

    monkeypatch.setattr(host, "missing_host_binaries", fake_missing)
    with caplog.at_level(logging.WARNING, logger="src.main"):
        asyncio.run(main.check_host_tools())

    [(names, timeout, on_main_thread)] = calls
    assert names == list(host.HOST_TOOLS)
    assert timeout == main.HOST_TOOL_CHECK_TIMEOUT
    assert not on_main_thread  # runs in a worker thread, not on the event loop
    [record] = [r for r in caplog.records if r.name == "src.main"]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert "btrfs, mkfs.btrfs, testparm" in message
    assert "apt-get install btrfs-progs samba-common-bin" in message


@pytest.mark.parametrize("result", [[], None])
def test_no_warning_when_nothing_is_missing_or_the_check_could_not_run(monkeypatch, caplog, result):
    monkeypatch.setattr(host, "missing_host_binaries", lambda names, *, timeout: result)
    with caplog.at_level(logging.WARNING, logger="src.main"):
        asyncio.run(main.check_host_tools())

    # When the check cannot run, host.missing_host_binaries logs why itself.
    assert [r for r in caplog.records if r.name == "src.main"] == []


def test_failing_check_does_not_raise(monkeypatch, caplog):
    def broken(names, *, timeout):
        raise RuntimeError("boom")

    monkeypatch.setattr(host, "missing_host_binaries", broken)
    with caplog.at_level(logging.WARNING, logger="src.main"):
        asyncio.run(main.check_host_tools())
    assert "boom" in caplog.text


def test_slow_host_tool_check_does_not_delay_startup(monkeypatch):
    monkeypatch.delenv("UNOS_DISABLE_BACKGROUND_TASKS", raising=False)
    release = threading.Event()
    checking = threading.Event()

    def slow_check(names, *, timeout):
        checking.set()
        release.wait(10)
        return []

    monkeypatch.setattr(host, "missing_host_binaries", slow_check)
    patches = _patch_loops(set(), host_tool_check=False)
    for p in patches:
        p.start()
    try:
        started = time.monotonic()
        with TestClient(main.app) as client:
            startup = time.monotonic() - started
            assert checking.wait(5), "the host tool check did not run"
            assert client.get("/api/v1/health").status_code == 200
            release.set()
        assert startup < 2
    finally:
        release.set()
        for p in patches:
            p.stop()


def test_lifespan_logs_missing_host_tools(monkeypatch, caplog):
    monkeypatch.delenv("UNOS_DISABLE_BACKGROUND_TASKS", raising=False)
    checked = threading.Event()

    def fake_missing(names, *, timeout):
        checked.set()
        return ["chattr"]

    monkeypatch.setattr(host, "missing_host_binaries", fake_missing)
    patches = _patch_loops(set(), host_tool_check=False)
    for p in patches:
        p.start()
    try:
        with caplog.at_level(logging.WARNING, logger="src.main"):
            with TestClient(main.app):
                assert checked.wait(5)
                deadline = time.monotonic() + 5
                while "chattr" not in caplog.text and time.monotonic() < deadline:
                    time.sleep(0.01)
        assert "missing programs" in caplog.text and "e2fsprogs" in caplog.text
    finally:
        for p in patches:
            p.stop()

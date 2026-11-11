"""The supervisor's JSON state files are replaced whole, and kept when they cannot be read (ADR-0324).

* replace_file of src/state_files.py: a process killed while it writes leaves the old file or the
  new one, never a part, and a failure leaves no temporary file.
* load_json: a file that is not UTF-8, not JSON or no state of its kind is renamed to <name>.bad
  and named in a warning, and the defaults are used; a file that cannot be opened raises.
* Each state file of the supervisor (STATE_FILES, which a scan of src keeps complete) is written
  through a rename, and its loader keeps a damaged file as <name>.bad.
"""

import asyncio
import errno
import importlib
import json
import logging
import os
import re
import signal
import stat
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from src import state_files
from src.notifications.schemas import NotificationChannel, NotificationConfig, NotificationEvent, NotificationLog
from src.scheduler.schemas import ScheduleFrequency, ScheduledTask, TaskRun, TaskType
from src.security.schemas import FirewallAction, FirewallRule
from src.updates.schemas import UpdateConfig

# The packages export their APIRouter as `router`, which hides the module of that name.
health = importlib.import_module("src.health.router")
notifications = importlib.import_module("src.notifications.router")
offsite = importlib.import_module("src.offsite.service")
power = importlib.import_module("src.power.router")
scheduler = importlib.import_module("src.scheduler.router")
security = importlib.import_module("src.security.router")
updates = importlib.import_module("src.updates.updater")

SUPERVISOR = Path(__file__).resolve().parents[1]
SRC = SUPERVISOR / "src"


# --- replace_file ----------------------------------------------------------------------------

# A child that replaces the file and stops at a point of the write that the parent names: it
# prints "stopped" there and waits to be killed.
_CHILD = textwrap.dedent(
    """\
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    from src import state_files
    path, stop_at = sys.argv[2], sys.argv[3]

    def stop(*args, **kwargs):
        print("stopped", flush=True)
        time.sleep(600)

    real_replace = os.replace
    if stop_at == "before-rename":  # the new content is written, not yet in place
        os.replace = lambda src, dst: stop()
    elif stop_at == "after-rename":
        os.replace = lambda src, dst: (real_replace(src, dst), stop())
    state_files.replace_file(path, b"new " * 300_000, sync=False)
    """
)


@pytest.mark.parametrize("stop_at, expected", [("before-rename", b"old content"), ("after-rename", b"new " * 300_000)])
def test_a_process_killed_while_it_replaces_a_file_leaves_the_old_or_the_new_one(tmp_path, stop_at, expected):
    path = tmp_path / "state.json"
    path.write_bytes(b"old content")
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(SUPERVISOR), str(path), stop_at],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline() == "stopped\n"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=30)

    assert path.read_bytes() == expected


@pytest.mark.parametrize("fails", ["fsync", "replace"])
def test_a_failed_write_leaves_the_file_and_no_temporary_file(tmp_path, monkeypatch, fails):
    path = tmp_path / "state.json"
    path.write_bytes(b"old content")

    def full_disk(*args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, fails, full_disk)
    with pytest.raises(OSError):
        state_files.replace_file(path, b"new content", sync=True)

    assert path.read_bytes() == b"old content"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


@pytest.mark.parametrize("sync", [True, False])
def test_only_a_write_that_asks_for_it_syncs(tmp_path, monkeypatch, sync):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))

    state_files.replace_file(tmp_path / "state.json", b"{}", sync=sync)

    assert bool(synced) is sync


def test_a_file_for_secrets_is_created_with_its_mode_whatever_the_umask(tmp_path):
    path = tmp_path / "keys.json"
    previous = os.umask(0o077)
    try:
        state_files.replace_file(path, b"{}", sync=False, mode=0o600)
        state_files.replace_file(tmp_path / "plain.json", b"{}", sync=False)
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "plain.json").stat().st_mode) == 0o644


# --- load_json ---------------------------------------------------------------------------------


def _read_list(value: Any) -> list:
    if not isinstance(value, list):
        raise ValueError("it holds no list")
    return value


@pytest.mark.parametrize(
    "content, reason",
    [(b"{not json", "Expecting property name"), (b'["\x80"]', "codec can't decode"), (b'{"a": 1}', "it holds no list")],
    ids=["not-json", "not-utf-8", "no-state"],
)
def test_a_file_that_cannot_be_read_is_kept_as_bad_and_named(tmp_path, caplog, content, reason):
    path = tmp_path / "state.json"
    path.write_bytes(content)
    (tmp_path / "state.json.bad").write_bytes(b"an earlier one")

    with caplog.at_level(logging.WARNING, logger="src.state_files"):
        assert state_files.load_json(path, _read_list, list, what="the test state") == []

    assert not path.exists()
    assert (tmp_path / "state.json.bad").read_bytes() == content
    [record] = caplog.records
    assert f"Could not read the test state {path}" in record.getMessage()
    assert reason in record.getMessage()
    assert f"kept as {path}.bad, using the defaults" in record.getMessage()


def test_a_missing_file_is_the_defaults_and_a_readable_one_is_its_state(tmp_path):
    path = tmp_path / "state.json"
    assert state_files.load_json(path, _read_list, list, what="the test state") == []
    path.write_text("[1, 2]")
    assert state_files.load_json(path, _read_list, list, what="the test state") == [1, 2]
    assert path.exists()


def test_a_file_that_a_writer_puts_in_place_meanwhile_is_read_not_set_aside(tmp_path):
    """A reader that found the file damaged sets aside only that file: when a writer has replaced
    it meanwhile, the reader reads what the writer wrote."""
    path = tmp_path / "state.json"
    path.write_bytes(b"{damaged")
    real_loads = json.loads

    def loads_then_a_writer(raw, *args, **kwargs):
        if raw == b"{damaged":
            state_files.replace_file(path, b"[1]", sync=False)  # a writer, between the read and the rename
        return real_loads(raw, *args, **kwargs)

    import unittest.mock
    with unittest.mock.patch.object(state_files.json, "loads", loads_then_a_writer):
        assert state_files.load_json(path, _read_list, list, what="the test state") == [1]

    assert path.read_bytes() == b"[1]"
    assert not (tmp_path / "state.json.bad").exists()


def test_a_file_that_cannot_be_opened_raises_and_stays(tmp_path):
    path = tmp_path / "state.json"
    path.mkdir()  # reading a directory fails with IsADirectoryError, an OSError

    with pytest.raises(OSError):
        state_files.load_json(path, _read_list, list, what="the test state")
    assert path.is_dir()


# --- every state file ----------------------------------------------------------------------------


@dataclass
class StateFile:
    """A state file of the supervisor: where its path is, how a state is saved and loaded."""

    name: str
    module: Any
    attr: str
    save: Callable[[], None]
    load: Callable[[], Any]
    default: Any


def _run(coroutine):
    return asyncio.run(coroutine)


_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
_TASK = ScheduledTask(id="t1", name="Nightly scrub", task_type=TaskType.SCRUB, frequency=ScheduleFrequency.DAILY)
_RUN = TaskRun(id="r1", task_id="t1", task_name="Nightly scrub", started_at=_NOW.isoformat(), status="success")
_LOG = NotificationLog(
    id="n1",
    event=NotificationEvent(type="info", title="Test", message="A test"),
    channel=NotificationChannel.EMAIL,
    channel_name="Mail",
    sent_at=_NOW.isoformat(),
    success=True,
)

STATE_FILES = [
    StateFile("notifications.json", notifications, "CONFIG_FILE",
              lambda: notifications.save_configs([NotificationConfig(id="c1", name="Mail", channel="email")]),
              notifications.load_configs, []),
    StateFile("notification_logs.json", notifications, "LOGS_FILE",
              lambda: notifications.save_logs([_LOG]), notifications.load_logs, []),
    StateFile("power_config.json", power, "CONFIG_FILE",
              lambda: power.save_config(power.PowerConfig(auto_shutdown=True)), power.load_config, power.PowerConfig()),
    StateFile("ups_config.json", power, "UPS_CONFIG_FILE",
              lambda: power.save_ups_config(power.UPSConfig(enabled=True)), power.load_ups_config, power.UPSConfig()),
    StateFile("wol_config.json", power, "WOL_FILE",
              lambda: power._remember_wol("eth0", True), power._load_wol_choices, {}),
    StateFile("firewall_rules.json", security, "FIREWALL_RULES_FILE",
              lambda: security.save_firewall_rules([FirewallRule(id="f1", name="ssh", action=FirewallAction.ALLOW)]),
              security.load_firewall_rules, []),
    StateFile("raid_status.json", health, "RAID_STATUS_FILE",
              lambda: health.save_raid_status({"alpha": {"status": "healthy"}}), health.load_previous_raid_status, {}),
    StateFile("smart_history.json", health, "SMART_HISTORY_FILE",
              lambda: health.save_smart_history({"S1": [{"timestamp": _NOW.isoformat()}]}),
              lambda: health.load_smart_history_or_start_anew(), {}),
    StateFile("update_state.json", updates, "UPDATE_STATE_FILE",
              lambda: updates.Updater()._save_progress_state(), lambda: updates.Updater().get_progress().status.value,
              "idle"),
    StateFile("update_history.json", updates, "UPDATE_HISTORY_FILE",
              lambda: _run(updates.Updater()._add_history_entry("1.0.0", "1.1.0", "completed", _NOW)),
              lambda: _run(updates.Updater().get_update_history()), []),
    StateFile("update_config.json", updates, "UPDATE_CONFIG_FILE",
              lambda: _run(updates.Updater().save_config(UpdateConfig(auto_install=True))),
              lambda: _run(updates.Updater().get_config()), UpdateConfig()),
    StateFile("scheduled_tasks.json", scheduler, "TASKS_FILE",
              lambda: scheduler.save_tasks([_TASK]), scheduler.load_tasks, []),
    StateFile("task_runs.json", scheduler, "RUNS_FILE", lambda: scheduler.save_run(_RUN), scheduler.load_runs, []),
    StateFile("scheduled_backup_keys.json", scheduler, "BACKUP_KEYS_FILE",
              lambda: scheduler.save_backup_keys({"t1": b"k" * scheduler.BACKUP_KEY_SIZE}), scheduler.load_backup_keys,
              {}),
    # The off-device copy (ADR-0336): its settings, its secrets (0600) and the result of its last copy and restore.
    StateFile("offsite.json", offsite, "SETTINGS_FILE",
              lambda: offsite.write_settings(offsite.OffsiteSettings(kind="disk", path="/mnt/usb/copy",
                                                                     sources=["/mnt/pools/data"])),
              offsite.load_settings, None),
    StateFile("offsite_secrets.json", offsite, "SECRETS_FILE",
              lambda: offsite.write_secrets(offsite.Secrets(password="a password of the copies")),
              offsite.load_secrets, offsite.Secrets()),
    StateFile("offsite_status.json", offsite, "STATUS_FILE",
              lambda: offsite.write_status(offsite.Status(last_copy=offsite.CopyResult(
                  started=_NOW.isoformat(), trigger="task"))),
              offsite.load_status, offsite.Status()),
]
_IDS = [f.name for f in STATE_FILES]


@pytest.fixture
def state_file(request, tmp_path, monkeypatch) -> tuple[StateFile, Path]:
    """The state file of the test's parameter, moved into tmp_path."""
    case: StateFile = request.param
    path = tmp_path / "data" / case.name
    path.parent.mkdir()
    original = getattr(case.module, case.attr)
    monkeypatch.setattr(case.module, case.attr, type(original)(path) if isinstance(original, Path) else str(path))
    if case.module is updates:
        monkeypatch.setattr(updates, "DATA_DIR", path.parent)
    return case, path


@pytest.mark.parametrize("state_file", STATE_FILES, ids=_IDS, indirect=True)
def test_each_state_file_is_replaced_by_a_rename(state_file):
    case, path = state_file
    path.write_bytes(b"[]" if case.default == [] else b"{}")
    before = path.stat().st_ino

    case.save()

    assert path.stat().st_ino != before, "the file was written in place"
    assert sorted(p.name for p in path.parent.iterdir()) == [case.name]
    json.loads(path.read_bytes())


@pytest.mark.parametrize("state_file", STATE_FILES, ids=_IDS, indirect=True)
def test_each_state_file_that_cannot_be_read_is_kept_as_bad(state_file, caplog):
    case, path = state_file
    path.write_bytes(b"{damaged")

    with caplog.at_level(logging.WARNING):
        assert case.load() == case.default

    assert (path.parent / f"{case.name}.bad").read_bytes() == b"{damaged"
    assert not path.exists()
    assert any(f"{path}.bad" in record.getMessage() for record in caplog.records), caplog.text


def test_the_table_has_every_json_state_file_of_the_supervisor():
    """A module-level path of a .json file under /data in src is a state file of the table."""
    found = set()
    for source in SRC.rglob("*.py"):
        for line in source.read_text().splitlines():
            match = re.match(r'^[A-Z_]+ = (?:"/data/(?:[^"/]+/)*([^"/]+\.json)"|DATA_DIR / "([^"]+\.json)")$', line)
            if match:
                found.add(match[1] or match[2])
    assert found == {f.name for f in STATE_FILES}


def test_no_module_writes_or_reads_its_state_file_around_the_helpers():
    """No module opens the path of one of its state files with write_text, write_bytes, read_text,
    read_bytes, open or json.dump: only through state_files (ADR-0324)."""
    offenders = []
    for case in STATE_FILES:
        text = Path(case.module.__file__).read_text()
        attr = re.escape(case.attr)
        for pattern in (
            rf"{attr}\)?\.(?:write_text|write_bytes|read_text|read_bytes|open)\(",
            rf"open\((?:Path\()?{attr}\b",
        ):
            offenders += [f"{case.name}: {m.group(0)}" for m in re.finditer(pattern, text)]
    assert offenders == []


def test_no_state_file_is_read_or_written_around_the_helpers():
    """No JSON file under /data is named anywhere but in a module-level constant or a setting
    (config.py): no function opens one by a literal path around its module's loader and writer."""
    offenders = []
    for source in SRC.rglob("*.py"):
        text = source.read_text()
        for name in re.findall(r'"/data/([^"]+\.json)"', text):
            declared = rf'^(?:[A-Z_]+ =|    [a-z_]+: str =) "/data/{re.escape(name)}"$'
            if not re.search(declared, text, re.M):
                offenders.append(f"{source.relative_to(SRC)}: /data/{name}")
    assert offenders == []


# --- what the readers answer for a file that cannot be opened ----------------------------------------


@pytest.fixture
def runs_file_cannot_be_opened(tmp_path, monkeypatch) -> Path:
    """A directory in the place of the runs' file: reading and replacing it fail with an OSError."""
    path = tmp_path / "task_runs.json"
    path.mkdir()
    monkeypatch.setattr(scheduler, "RUNS_FILE", str(path))
    monkeypatch.setattr(scheduler, "TASKS_FILE", str(tmp_path / "scheduled_tasks.json"))
    return path


def test_the_scheduler_starts_when_its_runs_file_cannot_be_opened(runs_file_cannot_be_opened, monkeypatch, caplog):
    async def no_loop():
        return None

    async def start_and_stop():
        await scheduler.start_scheduler()
        await scheduler.stop_scheduler()

    monkeypatch.setattr(scheduler, "scheduler_loop", no_loop)
    with caplog.at_level(logging.WARNING):
        _run(start_and_stop())

    assert f"Could not read the runs of the scheduled tasks {runs_file_cannot_be_opened}" in caplog.text
    assert runs_file_cannot_be_opened.is_dir()


def test_a_run_goes_on_when_it_cannot_be_recorded(runs_file_cannot_be_opened, caplog):
    with caplog.at_level(logging.WARNING):
        scheduler.save_run(_RUN)

    assert f"Could not record the run {_RUN.id} of the task {_RUN.task_name}" in caplog.text
    assert runs_file_cannot_be_opened.is_dir()


def test_the_raid_monitor_reads_a_status_that_cannot_be_opened_as_none(tmp_path, monkeypatch, caplog):
    path = tmp_path / "raid_status.json"
    path.mkdir()
    monkeypatch.setattr(health, "RAID_STATUS_FILE", str(path))

    with caplog.at_level(logging.WARNING):
        assert health.load_previous_raid_status() == {}
    with pytest.raises(OSError):
        health.raid_status_or_none()  # /health/full says "unknown", as before
    assert f"Could not read the RAID status {path}" in caplog.text

"""Scheduled tasks (snapshots, scrubs, SMART tests, backups, off-device copies, custom commands).

Trust model: tasks run as root inside the privileged supervisor container,
which shares the host's PID namespace and has the Docker socket and /mnt. A
``custom`` task runs an arbitrary shell command there, so being able to
create, edit, toggle or run tasks is equivalent to root on the host. That is
intended, but only for administrators:

* every endpoint of this router requires an administrator (``require_admin``
  on the router itself, in addition to the admin-only include in main.py);
* tasks are stored only in TASKS_FILE under /data, which no other API can
  write (the file manager is confined to the pools base and backup restore
  does not restore it).

Do not expose task creation or editing to regular users. Task targets (pool
names, disks) are validated when a task is saved and again before it runs,
because the tasks file may predate validation or have been edited by hand.

Backup tasks encrypt their backups (ADR-0325). Their passphrase is never
stored: BACKUP_KEYS_FILE (0600) keeps the key derived from it, apart from
TASKS_FILE, which the API returns and every scheduled backup contains.

Schedules follow the clock of the host's time zone, read on the host for each
pass of the scheduler loop and each answer, and the zone read last while the
host cannot be asked (ADR-0266). A custom task's run records the command's
output and exit status; the command is stopped after COMMAND_TIMEOUT, and the
processes of its session end with it (ADR-0267). A run that a stop of the
supervisor interrupts is recorded as failed (ADR-0268).
"""

import base64
import contextlib
import io
import json
import logging
import os
import secrets
import signal
import stat
import subprocess
import threading
import asyncio
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Annotated, NamedTuple, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi import Path as PathParameter
from croniter import croniter
from pydantic import TypeAdapter
import psutil

from .. import host, state_files
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction
from ..auth.utils import require_admin, verify_token
from ..health import problems
from ..offsite import service as offsite
from ..openapi import BODY, answer, errors
from ..backup.router import (
    BACKUP_KEY_SIZE,
    ENCRYPTED_EXTENSION,
    backup_temp_file,
    copy_database_for_backup,
    encrypt_with_key,
    new_backup_key,
    publish_backup_file,
    remove_stale_temp_files,
)
from ..security.validators import (
    pools_base,
    safe_path_join,
    validate_disk_target,
    validate_pool_name,
)
from ..snapshots.router import AUTO_SNAPSHOT_PREFIX, delete_snapshot_by_id, make_snapshot, utc_stamp
from ..storage import disks as storage_disks
from ..storage import pools as storage_pools
from ..storage import scrubs as storage_scrubs
from ..storage.schemas import ScrubStatus
from .schemas import ScheduledTask, ScheduledTaskInfo, TaskRun, TaskType, ScheduleFrequency

router = APIRouter(
    prefix="/scheduler",
    tags=["scheduler"],
    dependencies=[Depends(require_admin)],
)
logger = logging.getLogger(__name__)

SNAPSHOT_DIR_NAME = ".snapshots"
MAX_TASK_NAME_LENGTH = 100
MAX_COMMAND_LENGTH = 4096
MAX_KEEP_COUNT = 1000
# Seconds `smartctl -t short` may take. It only starts the test, which then
# runs inside the disk, so it normally returns within seconds; a disk in
# standby spins up first, which takes tens of seconds behind some USB
# bridges. Nobody waits for a scheduled run, so this allows more than the
# 30 s of the health API's test endpoint, like storage.disks.SMARTCTL_TIMEOUT.
# Both commands are killed at their timeout and not waited for (ADR-0290).
SMART_TEST_TIMEOUT = 60
# Seconds `btrfs scrub start` may take: it returns once the scrub runs in the
# background, unless the filesystem hangs (as storage.pools.SCRUB_COMMAND_TIMEOUT).
SCRUB_START_TIMEOUT = 60
# Seconds the `btrfs subvolume show` of a snapshot may take when old snapshots
# are cleaned up: it reads the creation time, unless the filesystem hangs.
SUBVOLUME_SHOW_TIMEOUT = 60
# Seconds the command of a custom task may run; then it is stopped, with what it
# started (ADR-0267). Long enough for a copy of a pool to another disk.
COMMAND_TIMEOUT = 24 * 3600
# What a run keeps of a command's output: its last part, so that RUNS_FILE, which
# is rewritten on the system disk at each run, stays small (_save_runs).
MAX_OUTPUT_BYTES = 16 * 1024
OUTPUT_CUT = "[Only the last 16 KiB of the output are kept.]\n"
# Seconds to wait, once the processes of a command were killed, for them to end
# and for the rest of their output: a process in the kernel's uninterruptible
# sleep ends only when the kernel lets it.
STOP_WAIT = 5
# The error of a run that a stop of the supervisor interrupted (ADR-0268).
STOPPED_DURING_RUN = "The supervisor stopped during the run"
# How far back a pass of the scheduler loop looks for the times it has not
# handled yet: a pass that comes late still starts the runs of the minutes
# since the pass before, but a clock set forward by hours (a resume from
# suspend, NTP after a boot without a real-time clock) does not start every task
# of the hours it skipped (ADR-0266).
CATCH_UP = timedelta(minutes=5)
MINUTE = timedelta(minutes=1)
# Seconds between the checks of the tasks for their problems (ADR-0332), and how long after a time
# of a task's schedule without a run that run counts as missed.
TASK_CHECK_INTERVAL = 300
MISSED_AFTER = timedelta(minutes=10)
TASK_PROBLEMS = "tasks"  # their source in health.problems

TASKS_FILE = "/data/scheduled_tasks.json"
RUNS_FILE = "/data/task_runs.json"
SCHEDULED_BACKUP_DIR = "/data/backups"
DB_FILE = "/data/unos.db"
# The keys of the backup tasks by task id, base64 of backup.router.new_backup_key.
BACKUP_KEYS_FILE = "/data/scheduled_backup_keys.json"
MIN_PASSPHRASE_LENGTH = 8  # as for passwords and SMB passwords
NO_PASSPHRASE = "A backup task needs a passphrase: its backups are encrypted with it"
NO_BACKUP_KEY = "No passphrase is set for this backup task: edit the task and set one"
# Held while BACKUP_KEYS_FILE is read and written again (set_backup_key runs in worker threads).
_backup_keys_lock = threading.Lock()

# In-memory scheduler state
_scheduler_running = False
_scheduler_task: Optional[asyncio.Task] = None
_task_check_task: Optional[asyncio.Task] = None
# The runs the loop started and that have not ended: the event loop keeps only
# weak references to tasks. stop_scheduler ends them.
_runs: set[asyncio.Task] = set()
# The name of the asyncio task of a run: this, then the task's name (start_run).
RUN_PREFIX = "unos-task-run:"
# The warnings about tasks already logged: each is logged once, not at every pass.
_reported: set[str] = set()
# The time zone the host named when it was last asked (schedule_zone); None until then.
_last_zone: tzinfo | None = None
# Why the host's time zone could not be read, as last logged; None while it is read.
_zone_problem: str | None = None
_sleep = asyncio.sleep  # replaced by the tests, as _utc_now


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _warn_once(message: str) -> None:
    if message not in _reported:
        _reported.add(message)
        logger.warning(message)


def _report_zone_problem(problem: str | None) -> None:
    """Log ``problem`` when it is new, not at each pass while it lasts; None: the zone was read."""
    global _zone_problem
    if problem == _zone_problem:
        return
    if problem is not None:
        logger.warning(problem)
    else:
        logger.info("The time zone of the host is read again")
    _zone_problem = problem


_TASKS = TypeAdapter(list[ScheduledTask])
_RUNS = TypeAdapter(list[TaskRun])


def load_tasks() -> list[ScheduledTask]:
    """The scheduled tasks; none without a file, or for one kept as .bad (ADR-0324)."""
    return state_files.load_json(Path(TASKS_FILE), _TASKS.validate_python, list, what="the scheduled tasks")


def save_tasks(tasks: list[ScheduledTask]) -> None:
    """Replace the scheduled tasks' file (ADR-0324)."""
    state_files.write_json(Path(TASKS_FILE), [t.model_dump() for t in tasks], sync=False)


def load_runs() -> list[TaskRun]:
    """Load task runs from file."""
    # UTF-8 (_save_runs); the last 100 (a file written elsewhere may hold more). A history: a file
    # that cannot be opened reads as no runs, with a warning, as the start (finish_interrupted_runs)
    # and the runs themselves need (ADR-0324).
    try:
        return state_files.load_json(
            Path(RUNS_FILE), lambda data: _RUNS.validate_python(data)[-100:], list,
            what="the runs of the scheduled tasks",
        )
    except OSError as exc:
        logger.warning("Could not read the runs of the scheduled tasks %s: %s", RUNS_FILE, exc)
        return []


def _save_runs(runs: list[TaskRun]) -> None:
    """Write the last 100 of ``runs`` into RUNS_FILE, which is rewritten on the system disk at each run.

    As UTF-8 text, not with JSON's \\u escapes, so that the output a run keeps (MAX_OUTPUT_BYTES)
    takes about as many bytes in the file in any language: an escaped Cyrillic letter takes 6
    bytes instead of 2 (ADR-0267). A lone surrogate, which UTF-8 cannot hold (the bytes of a
    file name that are not UTF-8, in an error), is written as its JSON escape.
    """
    state_files.write_json(Path(RUNS_FILE), [r.model_dump() for r in runs[-100:]], sync=False, ensure_ascii=False)


def save_run(run: TaskRun) -> None:
    """Save a task run. A run that cannot be recorded goes on, with a warning (ADR-0324)."""
    runs = load_runs()
    # Update existing or append
    for i, r in enumerate(runs):
        if r.id == run.id:
            runs[i] = run
            break
    else:
        runs.append(run)
    try:
        _save_runs(runs)
    except OSError as exc:
        logger.warning("Could not record the run %s of the task %s in %s: %s", run.id, run.task_name, RUNS_FILE, exc)


def load_backup_keys() -> dict[str, bytes]:
    """The keys of the backup tasks by task id; an entry that is not a key is left out.

    A file that is not JSON or holds no object counts as empty, since none of
    its keys can be used: it is kept as scheduled_backup_keys.json.bad, with its
    mode 0600 (ADR-0324), the runs of the backup tasks fail with NO_BACKUP_KEY,
    and the next passphrase that is set writes a new file. A file that cannot
    be read is HTTPException(500), so that no write replaces the keys it may
    still hold.
    """
    try:
        return state_files.load_json(Path(BACKUP_KEYS_FILE), _backup_keys, dict, what="the keys of the backup tasks")
    except OSError as e:
        raise HTTPException(500, f"The keys of the backup tasks cannot be read: {e}") from e


def _backup_keys(data: object) -> dict[str, bytes]:
    """The decoded keys file's keys (load_backup_keys); ValueError for no object."""
    if not isinstance(data, dict):
        raise ValueError("it holds no keys by task")
    keys = {}
    for task_id, value in data.items():
        try:
            key = base64.b64decode(value, validate=True)
        except (TypeError, ValueError):  # not a string, not ASCII, not base64 (binascii.Error)
            continue
        if len(key) == BACKUP_KEY_SIZE:
            keys[task_id] = key
    return keys


def save_backup_keys(keys: dict[str, bytes]) -> None:
    """Replace BACKUP_KEYS_FILE with ``keys``: a new file, readable and writable by root only (0600).

    The file and its folder are synced before this returns: after a power
    cut, a passphrase that was set must not turn out to be the one before.
    """
    path = Path(BACKUP_KEYS_FILE)
    keys_json = {task_id: base64.b64encode(key).decode() for task_id, key in keys.items()}
    state_files.write_json(path, keys_json, sync=True, mode=0o600)
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def set_backup_key(task_id: str, key: bytes | None) -> None:
    """Store the key of a backup task, or with ``None`` remove the key of ``task_id``.

    Blocking (fsync): call it in a worker thread. Writes only a change, one
    at a time; HTTPException(500) when the file cannot be read.
    """
    with _backup_keys_lock:
        keys = load_backup_keys()
        if key is None:
            if keys.pop(task_id, None) is None:
                return
        else:
            keys[task_id] = key
        save_backup_keys(keys)


def get_cron_expression(task: ScheduledTask) -> str:
    """Generate cron expression from task settings."""
    if task.frequency == ScheduleFrequency.CUSTOM and task.cron_expression:
        return task.cron_expression

    minute = task.minute
    hour = task.hour

    if task.frequency == ScheduleFrequency.HOURLY:
        return f"{minute} * * * *"
    elif task.frequency == ScheduleFrequency.DAILY:
        return f"{minute} {hour} * * *"
    elif task.frequency == ScheduleFrequency.WEEKLY:
        dow = task.day_of_week if task.day_of_week is not None else 0
        return f"{minute} {hour} * * {dow}"
    elif task.frequency == ScheduleFrequency.MONTHLY:
        dom = task.day_of_month if task.day_of_month is not None else 1
        return f"{minute} {hour} {dom} * *"

    return f"{minute} {hour} * * *"


def _named_zone(name: str | None) -> tuple[tzinfo, str | None]:
    """The zone of host.time_zone_name's answer, or UTC and why the supervisor cannot use it."""
    if name is None:
        return timezone.utc, "/etc/localtime is not a link into a zoneinfo folder"
    try:
        return ZoneInfo(name), None
    # Not a key, not in the supervisor's database, or one of its folders (IsADirectoryError).
    except (OSError, ValueError, ZoneInfoNotFoundError) as e:
        return timezone.utc, str(e)


def schedule_zone() -> tzinfo:
    """The host's time zone, whose clock the schedules follow (ADR-0266).

    Read on the host each time (host.time_zone_name), so that the schedules follow a
    change of the zone at once; its rules come from the supervisor's own time zone
    database. UTC when the host names no zone that the supervisor can use. When the
    host cannot be asked (a timeout, no process can start, nsenter fails), the zone
    it named last, so that one failed read moves no run: UTC only before the first
    read. Each new problem is logged once, and again after the zone was read.
    Blocking (a host command): call it in a worker thread.
    """
    global _last_zone
    try:
        name = host.time_zone_name()
    except (OSError, subprocess.SubprocessError, host.HostCommandError) as e:
        zone = _last_zone or timezone.utc
        _report_zone_problem(f"The time zone of the host cannot be read ({e}): the schedules follow {zone}")
        return zone
    zone, problem = _named_zone(name)
    _last_zone = zone
    _report_zone_problem(
        None if problem is None else f"The time zone of the host cannot be read ({problem}): the schedules follow UTC"
    )
    return zone


def _first_instant(wall: datetime, zone: tzinfo) -> datetime:
    """The first instant (UTC) at which the clock of ``zone`` shows the naive time ``wall`` or a later one.

    That is the first of the two in the hour that the clock shows twice when daylight
    saving time ends, and for a time that the clock skips when it begins, the end of
    the skip: 02:30 comes at 03:00 in central Europe (ADR-0266).
    """
    at = wall.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    # A skipped time is read with the offset before the change, which gives an
    # instant after the skip: go back to its end.
    while (at - MINUTE).astimezone(zone).replace(tzinfo=None) >= wall:
        at -= MINUTE
    return at


def next_run_after(task: ScheduledTask, after: datetime, zone: tzinfo) -> datetime:
    """The first instant (UTC) after ``after`` at which ``task`` is due.

    Its schedule is a series of times on the clock of ``zone``; each comes at
    _first_instant. Raises ValueError (croniter's errors) for a schedule that has no
    next time or cannot be read, as in a tasks file written by hand.
    """
    times = croniter(get_cron_expression(task), after.astimezone(zone).replace(tzinfo=None))
    while True:
        at = _first_instant(times.get_next(datetime), zone)
        if at > after:  # in the hour shown twice, the times of its first showing have passed
            return at


def get_next_run(task: ScheduledTask, zone: tzinfo, now: datetime) -> str | None:
    """When ``task`` runs next, in ISO 8601 with the offset of ``zone``; None when it never does.

    Whatever its schedule raises, the task list is still answered (as due_tasks runs the others).
    """
    try:
        return next_run_after(task, now, zone).astimezone(zone).isoformat()
    except Exception:
        return None


def due_tasks(tasks: list[ScheduledTask], after: datetime, now: datetime, zone: tzinfo) -> list[ScheduledTask]:
    """The enabled tasks with a time of their schedule in (``after``, ``now``].

    A task whose schedule cannot be followed, whatever it raises (croniter's errors are
    ValueErrors), is left out, and the others still run.
    """
    due = []
    for task in tasks:
        if not task.enabled:
            continue
        try:
            if next_run_after(task, after, zone) <= now:
                due.append(task)
        except Exception as e:
            _warn_once(f"Task {task.id} does not run: its schedule has no next time ({e})")
    return due


def _bad_task(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def pool_path(pool_name: str | None) -> Path:
    """Return the mount point of a pool, validated to be inside the pools base."""
    if not pool_name:
        raise _bad_task("No target pool specified")
    validate_pool_name(pool_name)
    return safe_path_join(str(pools_base()), pool_name)


def disk_device(target_disk: str | None) -> str:
    """Return ``/dev/<disk>`` for a whole disk of the host given as ``sda`` or ``/dev/sda``.

    Blocking (lsblk runs on the host): call it in a worker thread.
    HTTPException 400 for anything but one of the host's whole disks, 500
    when the host's disks cannot be listed (storage.disks.whole_disk_device,
    which the SMART endpoints of the health API use as well).
    """
    if not target_disk:
        raise _bad_task("No target disk specified")
    return storage_disks.whole_disk_device(target_disk)


def _command_problem(command: str | None) -> str | None:
    """Why ``command`` cannot be the command of a custom task, or None (checked when saved and before each run)."""
    if not (command or "").strip():
        return "No command specified"
    if "\x00" in command or len(command) > MAX_COMMAND_LENGTH:
        return f"Command must be at most {MAX_COMMAND_LENGTH} characters without NUL bytes"
    return None


def _validate_schedule(task: ScheduledTask) -> None:
    if not 0 <= task.hour <= 23:
        raise _bad_task("hour must be between 0 and 23")
    if not 0 <= task.minute <= 59:
        raise _bad_task("minute must be between 0 and 59")
    if task.day_of_week is not None and not 0 <= task.day_of_week <= 6:
        raise _bad_task("day_of_week must be between 0 and 6")
    if task.day_of_month is not None and not 1 <= task.day_of_month <= 31:
        raise _bad_task("day_of_month must be between 1 and 31")
    if task.frequency == ScheduleFrequency.CUSTOM:
        expression = task.cron_expression or ""
        if not expression.isprintable() or not croniter.is_valid(expression):
            raise _bad_task("A valid cron expression is required for a custom schedule")


def validate_task(task: ScheduledTask, *, kept_disk: str | None = None) -> ScheduledTask:
    """Validate a task before it is saved and normalize its targets.

    Raises HTTPException(400) for invalid input. Validation lives here rather
    than in the schema so that tasks already stored in TASKS_FILE keep
    loading (load_tasks drops the whole file if one entry fails to parse).

    kept_disk: the stored disk of the task being edited; it is kept without the host check (the run checks it).
    """
    name = task.name.strip()
    if not name or len(name) > MAX_TASK_NAME_LENGTH or not name.isprintable():
        raise _bad_task(f"Task name must be 1-{MAX_TASK_NAME_LENGTH} printable characters")
    task.name = name

    _validate_schedule(task)

    if task.keep_count is not None and not 1 <= task.keep_count <= MAX_KEEP_COUNT:
        raise _bad_task(f"keep_count must be between 1 and {MAX_KEEP_COUNT}")

    if task.task_type in (TaskType.SNAPSHOT, TaskType.SCRUB):
        pool_path(task.target_pool)
    elif task.task_type == TaskType.SMART_TEST:
        # An edit that keeps the disk of the task does not need the disk
        # connected: the disk is checked on the host again when the task runs.
        if kept_disk is not None and task.target_disk and validate_disk_target(task.target_disk) == kept_disk:
            task.target_disk = kept_disk
        else:
            task.target_disk = disk_device(task.target_disk)
    elif task.task_type == TaskType.CUSTOM:
        problem = _command_problem(task.command)
        if problem:
            raise _bad_task(problem)

    return task


def new_task_key(task: ScheduledTask, *, has_key: bool) -> bytes | None:
    """The key of a backup task from the passphrase sent with it, or None when there is none to store.

    Blocking (PBKDF2): call it in a worker thread. Without a passphrase, a
    task that has a key (``has_key``) keeps it; any other backup task is
    refused with HTTPException(400).
    """
    if task.task_type != TaskType.BACKUP:
        return None
    if not task.passphrase:
        if has_key:
            return None
        raise _bad_task(NO_PASSPHRASE)
    if len(task.passphrase) < MIN_PASSPHRASE_LENGTH:
        raise _bad_task(f"The passphrase must have at least {MIN_PASSPHRASE_LENGTH} characters")
    try:
        return new_backup_key(task.passphrase)
    except UnicodeEncodeError:
        raise _bad_task("The passphrase must be valid Unicode text") from None


def _real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _snapshot_dir(pool: Path) -> Path | None:
    """``<pool>/.snapshots`` if it exists and is a real directory (not a symlink)."""
    snap_dir = pool / SNAPSHOT_DIR_NAME
    if os.path.islink(snap_dir):
        raise _bad_task(f"{snap_dir} must not be a symbolic link")
    if not os.path.lexists(snap_dir):
        return None
    if not _real_dir(snap_dir):
        raise _bad_task(f"{snap_dir} is not a directory")
    return snap_dir


async def create_scheduled_backup(keep_count: int | None, key: bytes) -> tuple[bool, str]:
    """Create a scheduled backup encrypted with ``key`` (new_backup_key) and optionally cleanup old ones."""
    return await asyncio.to_thread(_create_scheduled_backup, keep_count, key)


def _create_scheduled_backup(keep_count: int | None, key: bytes) -> tuple[bool, str]:
    import tarfile
    import tempfile
    import shutil
    import os

    BACKUP_DIR = Path(SCHEDULED_BACKUP_DIR)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Written under a temporary name, which the backup list does not show, and
    # published without replacing a backup made in the same second.
    tmp = backup_temp_file(BACKUP_DIR, "scheduled")

    try:
        # Left by scheduled backups (or creates, uploads) that were killed,
        # e.g. by a power cut in the night.
        remove_stale_temp_files(BACKUP_DIR)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create manifest
            manifest = {
                "version": "1.0",
                "created": _utc_now().isoformat(),
                "hostname": os.uname().nodename,
                "type": "scheduled"
            }

            manifest_path = tmpdir_path / "manifest.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

            # Backup database with SQLite's backup API: a plain file copy of a
            # database in use can be torn and would then fail the restore check.
            db_path = Path(DB_FILE)
            if db_path.exists():
                copy_database_for_backup(db_path, tmpdir_path / "unos.db")

            # No fstab: the container's describes nothing of the host (ADR-0345).

            # Backup scheduled tasks
            tasks_file = Path(TASKS_FILE)
            if tasks_file.exists():
                shutil.copy2(tasks_file, tmpdir_path / "scheduled_tasks.json")

            # Create tarball, the manifest first: the backup list reads a plain
            # backup, such as a decrypted copy, only up to it (a tmpfs lists
            # the newest file first). It is encrypted in memory, as an
            # encrypted manual backup is (backup.router._create_backup).
            plain = io.BytesIO()
            with tarfile.open(fileobj=plain, mode="w:gz") as tar:
                for item in sorted(tmpdir_path.iterdir(), key=lambda p: (p.name != "manifest.json", p.name)):
                    tar.add(item, arcname=item.name)
        with open(tmp, "xb") as out:
            out.write(encrypt_with_key(plain.getbuffer(), key))
        backup_name = publish_backup_file(tmp, "auto_backup", ENCRYPTED_EXTENSION)

        # Cleanup old auto backups if keep_count is set. A backup that is gone
        # meanwhile was removed by a scheduled backup running at the same time.
        cleanup_msg = ""
        if keep_count and keep_count > 0:
            auto_backups = []
            for path in BACKUP_DIR.glob(f"auto_backup_*{ENCRYPTED_EXTENSION}"):
                try:
                    auto_backups.append((path.stat().st_mtime, path))
                except FileNotFoundError:
                    continue
            auto_backups.sort(reverse=True)
            deleted = 0
            for _, old_backup in auto_backups[keep_count:]:
                try:
                    old_backup.unlink()
                except FileNotFoundError:
                    continue
                deleted += 1
            if deleted > 0:
                cleanup_msg = f" Cleaned up {deleted} old backup(s)."

        return True, f"Backup {backup_name} created.{cleanup_msg}"

    except Exception as e:
        return False, f"Backup failed: {str(e)}"
    finally:
        tmp.unlink(missing_ok=True)


def _cleanup_old_snapshots(pool_name: str, keep_count: int) -> str:
    """Delete the oldest ``auto_`` snapshots of a pool beyond ``keep_count``, by creation time.

    Each is deleted by its subvolume id after the snapshot router's checks
    (delete_snapshot_by_id, ADR-0242); one that fails them is kept. When btrfs
    does not answer within SUBVOLUME_SHOW_TIMEOUT, nothing is deleted and
    subprocess.TimeoutExpired is raised (ADR-0290).
    """
    pool = pool_path(pool_name)
    snapshots_dir = _snapshot_dir(pool)
    if snapshots_dir is None:
        return ""

    # Get all auto-created snapshots (prefixed with "auto_"); never follow symlinks.
    auto_snapshots = []
    for snap in snapshots_dir.iterdir():
        if _real_dir(snap) and snap.name.startswith(AUTO_SNAPSHOT_PREFIX):
            # Get creation time from btrfs; killed at the timeout and not
            # waited for, and the cleanup ends there (ADR-0290).
            result = host.run_in_container(
                ["btrfs", "subvolume", "show", str(snap)], timeout=SUBVOLUME_SHOW_TIMEOUT
            )
            if result.returncode == 0:
                created = None
                for line in result.stdout.split("\n"):
                    if "Creation time:" in line:
                        raw_date = line.split(":", 1)[1].strip()
                        try:
                            created = datetime.strptime(raw_date[:19], "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            created = datetime.min
                        break
                if created:
                    auto_snapshots.append((snap, created))

    # Sort by creation time (newest first)
    auto_snapshots.sort(key=lambda x: x[1], reverse=True)

    # Delete snapshots beyond keep_count
    deleted_count = 0
    for snap, _ in auto_snapshots[keep_count:]:
        try:
            delete_snapshot_by_id(pool, snap.name, snap.name)
        except HTTPException:
            continue
        deleted_count += 1

    if deleted_count > 0:
        return f"Cleaned up {deleted_count} old snapshot(s)"
    return ""


async def cleanup_old_snapshots(pool_name: str, keep_count: int) -> str:
    """Remove old auto-created snapshots, keeping only the most recent ones."""
    return await asyncio.to_thread(_cleanup_old_snapshots, pool_name, keep_count)


def _pool_is_mounted(pool: Path) -> bool:
    return _real_dir(pool) and os.path.ismount(pool)


def _create_snapshot(pool_name: str) -> tuple[bool, str]:
    """A read-only snapshot ``auto_<date>_<time>`` (UTC, ADR-0243) of a mounted pool.

    Made like those of the Snapshots page, by make_snapshot of the snapshot
    router (ADR-0242); its HTTPException becomes the run's error in
    execute_task.
    """
    pool = pool_path(pool_name)
    # Checked before .snapshots is created, so an unmounted pool does not get
    # a directory on the root filesystem under its mount point.
    if not _pool_is_mounted(pool):
        return False, f"Pool {pool_name} is not mounted at {pool}"
    name = f"{AUTO_SNAPSHOT_PREFIX}{utc_stamp()}"
    make_snapshot(pool, name)
    return True, name


async def execute_task(task: ScheduledTask) -> tuple[bool, str]:
    """Execute a snapshot, scrub, SMART test, backup or off-device copy task: whether it succeeded, and
    its output or error.

    Targets are validated again here: the tasks file may contain entries
    written before validation existed or edited by hand. The commands of
    custom tasks run through run_command (run_task).
    """
    try:
        if task.task_type == TaskType.SNAPSHOT:
            ok, result = await asyncio.to_thread(_create_snapshot, task.target_pool)
            if not ok:
                return False, result
            name = result

            # Cleanup old snapshots if keep_count is set
            if task.keep_count and task.keep_count > 0:
                try:
                    cleanup_result = await cleanup_old_snapshots(
                        task.target_pool, task.keep_count
                    )
                except subprocess.TimeoutExpired:
                    # The keep count was not applied: the run fails, as a
                    # scrub or SMART test without an answer does (ADR-0290).
                    return False, (
                        f"Snapshot {name} created, but old snapshots were not cleaned up: btrfs "
                        f"did not answer within {SUBVOLUME_SHOW_TIMEOUT} seconds. "
                        "The pool may not be responding."
                    )
                if cleanup_result:
                    return True, f"Snapshot {name} created. {cleanup_result}"

            return True, f"Snapshot {name} created"

        elif task.task_type == TaskType.SCRUB:
            pool = pool_path(task.target_pool)
            # Started as the Storage page starts it (storage.scrub-needs-mounted-pool): no btrfs
            # on a pool that is not mounted, whose path is a folder on the root filesystem; killed
            # at its timeout and not waited for (ADR-0290), so a hung filesystem neither holds a
            # worker thread nor leaves the run "running".
            try:
                result = await asyncio.to_thread(storage_pools.scrub_start, pool, SCRUB_START_TIMEOUT)
            except storage_pools.PoolConflictError as exc:
                return False, str(exc)
            except subprocess.TimeoutExpired:
                return False, (
                    f"btrfs scrub start timed out: {task.target_pool} did not answer within "
                    f"{SCRUB_START_TIMEOUT} seconds. The pool may not be responding."
                )
            if result.returncode != 0:
                return False, result.stderr
            # The Storage page follows it over the events socket (ADR-0367).
            storage_scrubs.note(task.target_pool, ScrubStatus(pool=task.target_pool, status="running"))
            return True, "Scrub started"

        elif task.task_type == TaskType.SMART_TEST:
            # Accepts "sda" and "/dev/sda" (what the UI sends); checks the host's disks.
            device = await asyncio.to_thread(disk_device, task.target_disk)
            # Killed at its timeout and not waited for (ADR-0290): a hung disk
            # or USB bridge neither holds a worker thread nor leaves the run "running".
            try:
                result = await asyncio.to_thread(
                    host.run_smartctl, ["-t", "short"], device, timeout=SMART_TEST_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                return False, (
                    f"smartctl timed out: {device} did not answer within {SMART_TEST_TIMEOUT} "
                    "seconds. The disk or its USB bridge may not be responding."
                )
            if result.returncode != 0:
                return False, result.stderr or result.stdout
            return True, "SMART test started"

        elif task.task_type == TaskType.BACKUP:
            # Encrypted with the key of the task (ADR-0325); never unencrypted.
            key = load_backup_keys().get(task.id or "")
            if key is None:
                return False, NO_BACKUP_KEY
            return await create_scheduled_backup(task.keep_count, key)

        elif task.task_type == TaskType.OFFSITE_COPY:
            # The copy of the configuration and the pools' data to the off-device copy's repository;
            # one that finds a copy running ends as a success that says so (ADR-0337).
            return await offsite.run_copy("task")

        return False, "Unknown task type"

    except HTTPException as e:
        return False, str(e.detail)
    except Exception as e:
        return False, str(e)


class CommandResult(NamedTuple):
    """How the command of a custom task ended."""

    exit_code: int | None  # None: it did not exit by itself (stopped at the time limit, or by a signal)
    output: str  # what it and the processes it started wrote, standard output and error together
    error: str | None  # why its run failed; None for the exit status 0


class _OutputTail:
    """The last MAX_OUTPUT_BYTES of what a command wrote, and whether more came before them."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.cut = False

    def add(self, chunk: bytes) -> None:
        self.data += chunk
        if len(self.data) > MAX_OUTPUT_BYTES:
            del self.data[: len(self.data) - MAX_OUTPUT_BYTES]
            self.cut = True

    def text(self) -> str:
        if not self.cut:
            return self.data.decode(errors="replace")
        # The cut can fall inside a character: start with the next one.
        return OUTPUT_CUT + self.data.lstrip(_UTF8_CONTINUATION).decode(errors="replace")


_UTF8_CONTINUATION = bytes(range(0x80, 0xC0))  # the bytes after the first of a character


async def _read_output(fd: int, output: _OutputTail) -> None:
    """Read the pipe ``fd`` into ``output`` until it ends, when no process holds it open; then close it."""
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(fd, "rb", buffering=0)
    )
    try:
        while chunk := await reader.read(65536):
            output.add(chunk)
    finally:
        transport.close()


def _session_processes(sid: int) -> list[int]:
    """The processes of the session ``sid`` that have not ended; a zombie has, it only waits to be reaped."""
    found = []
    for pid in psutil.pids():
        try:
            if os.getsid(pid) == sid and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                found.append(pid)
        except (OSError, psutil.Error):  # it ended meanwhile
            continue
    return found


async def _end_session(sid: int) -> None:
    """Kill (SIGKILL) the processes of the session ``sid``, a command's, until none is left or STOP_WAIT passed.

    Each process of the command, also one that moved to a process group of its own, as
    `timeout` (without --foreground) and job control (`set -m`) put them: only setsid leaves
    the session. Again and again, as a process may start another before it is killed, and
    one in the kernel's uninterruptible sleep ends only when the kernel lets it. The session's
    id stays taken while a process is left in it, and Linux gives out process ids in turn, up
    to pid_max: a process found here is the command's (ADR-0267).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STOP_WAIT
    # In a worker thread: it reads /proc for each process of the host (pid: host).
    while processes := await asyncio.to_thread(_session_processes, sid):
        for pid in processes:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        if loop.time() >= deadline:
            logger.warning("Processes of a custom task's command did not end when killed: %s", processes)
            return
        await asyncio.sleep(0.05)


def _duration(seconds: float) -> str:
    """``seconds`` in words: "24 hours", "2 seconds"."""
    for size, unit in ((3600, "hour"), (60, "minute"), (1, "second")):
        if seconds >= size and seconds % size == 0:
            count = int(seconds // size)
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds:g} seconds"


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


async def run_command(command: str) -> CommandResult:
    """Run ``command`` with /bin/sh, as root in the supervisor's container, as a custom task does.

    Deliberately a shell command run as root: administrators only, see the trust
    model at the top of this module. Its standard output and error go into one
    pipe, which keeps their order, and the run keeps the last MAX_OUTPUT_BYTES.
    It runs in a session of its own: when its shell exits, the processes it left
    running in that session are killed, and so is all of it after COMMAND_TIMEOUT
    seconds, or when the run is cancelled (ADR-0267). The event loop waits for it,
    no worker thread. Raises OSError when the shell cannot start.
    """
    read_end, write_end = os.pipe()
    try:
        process = await asyncio.create_subprocess_shell(
            command, stdin=subprocess.DEVNULL, stdout=write_end, stderr=write_end, start_new_session=True
        )
    except BaseException:
        os.close(read_end)
        raise
    finally:
        os.close(write_end)
    output = _OutputTail()
    reading = asyncio.create_task(_read_output(read_end, output))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            timed_out = True
    finally:
        # What it left running, or all of it at the time limit (or when the run is cancelled):
        # the shell leads the session (start_new_session), whose id is its process id.
        await _end_session(process.pid)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), STOP_WAIT)
        # The rest of the output; a process that left the session may still hold the pipe.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(reading, STOP_WAIT)
    code = process.returncode
    if timed_out:
        return CommandResult(None, output.text(),
                             f"The command did not end within {_duration(COMMAND_TIMEOUT)} and was stopped")
    if code < 0:
        return CommandResult(None, output.text(), f"The command was ended by {_signal_name(-code)}")
    return CommandResult(code, output.text(), None if code == 0 else f"The command exited with status {code}")


async def _run_custom_task(task: ScheduledTask, run: TaskRun) -> bool:
    """Run the command of a custom task: its output and exit status, or why it failed, go into ``run``."""
    # Checked again: the tasks file may have been written before the checks or by hand.
    problem = _command_problem(task.command)
    if problem:
        run.error = problem
        return False
    try:
        result = await run_command(task.command)
    except Exception as e:  # as execute_task: the run is recorded as failed, never left running
        run.error = f"The command could not run: {e}"
        return False
    run.output, run.exit_code, run.error = result.output or None, result.exit_code, result.error
    return result.exit_code == 0


def _record_last_runs(ended: list[TaskRun]) -> None:
    """Record with each task the run of it that ``ended`` names last, as its last run and status.

    Read again: an edit may have saved a task during its run. A task deleted
    meanwhile is not written back, and nothing is written when no task changes.
    """
    last = {run.task_id: run for run in ended}
    tasks = load_tasks()
    changed = False
    for t in tasks:
        run = last.get(t.id or "")
        if run is not None:
            t.last_run, t.last_status = run.started_at, run.status
            changed = True
    if changed:
        save_tasks(tasks)


def _record_end(run: TaskRun, success: bool) -> None:
    run.finished_at = _utc_now().isoformat()
    run.status = "success" if success else "failed"
    save_run(run)
    try:
        _record_last_runs([run])
    except OSError as exc:  # the tasks' file cannot be read or written (ADR-0324)
        logger.warning("Could not record the last run of the task %s: %s", run.task_name, exc)


async def run_task(task: ScheduledTask) -> None:
    """Run a task and record the run, and its result with the task."""
    started = _utc_now()
    run = TaskRun(
        # Random suffix: two runs that start in the same microsecond must not share an id.
        id=f"run_{started.strftime('%Y%m%d%H%M%S%f')}_{secrets.token_hex(4)}",
        task_id=task.id or "",
        task_name=task.name,
        started_at=started.isoformat(),
        status="running",
    )
    save_run(run)

    try:
        if task.task_type == TaskType.CUSTOM:
            success = await _run_custom_task(task, run)
        else:
            success, message = await execute_task(task)
            if success:
                run.output = message
            else:
                run.error = message
    except asyncio.CancelledError:
        # The supervisor stops (stop_scheduler, or the end of its event loop); a command's
        # processes have been killed (run_command). Recorded, never left "running" (ADR-0268).
        run.error = STOPPED_DURING_RUN
        _record_end(run, False)
        raise
    _record_end(run, success)


def start_run(task: ScheduledTask) -> asyncio.Task:
    """Start a run of ``task`` beside the caller, kept in _runs until it ends and named after the task,
    so that the power schedule sees which tasks run (running_task_names, ADR-0407)."""
    run = asyncio.create_task(run_task(task), name=RUN_PREFIX + task.name)
    _runs.add(run)
    run.add_done_callback(_runs.discard)
    return run


def running_task_names() -> list[str]:
    """The names of the tasks whose runs have not ended, by name: a running job holds a scheduled
    shutdown and keeps the host from idling (ADR-0407)."""
    return sorted(run.get_name().removeprefix(RUN_PREFIX) for run in list(_runs) if not run.done())


def finish_interrupted_runs() -> None:
    """Record the runs that a stop of the supervisor interrupted as failed, with their tasks (ADR-0268).

    A run is saved as "running" when it starts. One that is still "running" when the
    supervisor starts was cut off by its stop (a deploy, an update, a reboot, a crash),
    before it could be recorded: it fails with STOPPED_DURING_RUN, ended at this start, the
    first time the supervisor could see that it no longer runs. Writes only when there is
    such a run. Call it before the first run of the supervisor starts.
    """
    runs = load_runs()
    interrupted = [run for run in runs if run.status == "running"]
    if not interrupted:
        return
    now = _utc_now().isoformat()
    for run in interrupted:
        run.status, run.error, run.finished_at = "failed", STOPPED_DURING_RUN, now
    _save_runs(runs)
    # In the order the runs started: the last of a task's is its last run.
    _record_last_runs(interrupted)


def _seconds_to_next_pass(now: datetime) -> float:
    """Seconds from ``now`` to one second after the next minute begins."""
    return 61 - now.timestamp() % 60


async def scheduler_loop():
    """Start the runs of the enabled tasks when their time comes (ADR-0266).

    A pass, a second after each minute begins, starts the tasks with a time in the
    minutes since the pass before, by the clock of the host's time zone, which it
    reads again: a pass that comes late misses no task, and none runs twice.
    """
    checked = _utc_now()
    while _scheduler_running:
        await _sleep(_seconds_to_next_pass(_utc_now()))
        now = _utc_now()
        try:
            zone = await asyncio.to_thread(schedule_zone)
            # A clock set back gives an empty window, and the next one starts at its time.
            for task in due_tasks(load_tasks(), max(checked, now - CATCH_UP), now, zone):
                start_run(task)
        except Exception:
            logger.exception("The scheduler could not start the tasks that were due")
        checked = now


class ZoneSeen(NamedTuple):
    """The time zone of the host that the checks of the tasks saw last, and since when (ADR-0332)."""

    zone: str
    # None for the zone of the first check after the start, which the schedules may have followed
    # while the supervisor was stopped.
    since: datetime | None


def _instant(text: str | None) -> datetime | None:
    """An instant of the tasks' or the runs' file (ISO 8601; one without an offset is UTC); None for none."""
    if not text:
        return None
    try:
        at = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def task_problems(
    tasks: list[ScheduledTask],
    runs: list[TaskRun],
    zone: tzinfo,
    now: datetime,
    zone_since: datetime | None = None,
) -> dict[str, problems.Problem]:
    """The problems of the enabled tasks, by key (ADR-0332).

    A task whose last run failed is a warning. So is a task that missed a run: a time of its
    schedule, on the clock of ``zone``, that came MISSED_AFTER ago or longer, after the task's
    last start (also of a run that still runs, from ``runs``: a task's last run is recorded when
    the run ends) and after its schedule_since, when it was created, enabled again or given
    another schedule. So the times while it was disabled, before it existed or under another
    schedule are no runs it missed, and neither are the times before ``zone_since``, when the
    checks saw the host's time zone change; a time that fell while the supervisor was stopped is
    one, since runs are not made up after a stop (ADR-0266). A task without a start or a
    schedule_since, and one whose schedule has no next time, miss nothing.
    """
    found: dict[str, problems.Problem] = {}
    starts: dict[str, list[datetime]] = {}
    last_runs: dict[str, TaskRun] = {}
    for run in runs:  # oldest first
        last_runs[run.task_id] = run
        if (start := _instant(run.started_at)) is not None:
            starts.setdefault(run.task_id, []).append(start)
    for task in tasks:
        if not task.enabled or not task.id:
            continue
        if task.last_status == "failed":
            run = last_runs.get(task.id)
            error = run.error if run is not None and run.status == "failed" else None
            found[f"task_failed:{task.id}"] = problems.Problem(
                key=f"task_failed:{task.id}", severity="warning", kind="task_failed", subject=task.name,
                title=f"Task '{task.name}' failed",
                detail=f"The last run of the scheduled task {task.name} failed"
                + (f": {error}" if error else ". System > Scheduler shows its runs."),
            )
        known = (_instant(task.last_run), *starts.get(task.id, ()), _instant(task.schedule_since))
        since = max((at for at in known if at is not None), default=None)
        if since is None:
            continue
        if zone_since is not None:
            since = max(since, zone_since)
        try:
            due = next_run_after(task, since, zone)
        except Exception:  # no next time, as due_tasks finds too
            continue
        if due <= now - MISSED_AFTER:
            found[f"task_missed:{task.id}"] = problems.Problem(
                key=f"task_missed:{task.id}", severity="warning", kind="task_missed", subject=task.name,
                title=f"Task '{task.name}' missed its run",
                detail=f"The scheduled task {task.name} was due at {due.astimezone(zone):%Y-%m-%d %H:%M %Z} "
                "and did not run: a run whose time falls while the supervisor is stopped is not made up. "
                "Run it now from System > Scheduler, or it runs at its next time.",
            )
    return found


def _tasks_runs_and_zone() -> tuple[list[ScheduledTask], list[TaskRun], tzinfo]:
    """The tasks, their runs and the host's time zone (blocking: the files, and a host command)."""
    return load_tasks(), load_runs(), schedule_zone()


async def check_tasks(seen: ZoneSeen | None) -> ZoneSeen:
    """One check of the tasks: report their problems (task_problems), and return the zone it saw
    for the next check (None: this is the first). Raises when the tasks cannot be read; the
    problems found before stay."""
    tasks, runs, zone = await asyncio.to_thread(_tasks_runs_and_zone)
    now = _utc_now()
    if seen is None or seen.zone != str(zone):
        seen = ZoneSeen(str(zone), None if seen is None else now)
    await problems.update(TASK_PROBLEMS, task_problems(tasks, runs, zone, now, seen.since))
    return seen


async def task_check_loop():
    """Check the tasks for their problems at the start and every TASK_CHECK_INTERVAL seconds (ADR-0332)."""
    seen: ZoneSeen | None = None
    while _scheduler_running:
        try:
            seen = await check_tasks(seen)
        except Exception:
            logger.exception("Could not check the scheduled tasks for their problems")
        await _sleep(TASK_CHECK_INTERVAL)


async def start_scheduler():
    """Start the scheduler on app startup, once the runs that a stop interrupted are recorded,
    and the checks of the tasks for their problems (ADR-0332)."""
    global _scheduler_running, _scheduler_task, _task_check_task
    try:
        await asyncio.to_thread(finish_interrupted_runs)
    except Exception:  # a state file that cannot be read or written must not stop the supervisor
        logger.exception("The scheduler could not record the runs that a stop interrupted")
    _scheduler_running = True
    _scheduler_task = asyncio.create_task(scheduler_loop())
    _task_check_task = asyncio.create_task(task_check_loop())


async def stop_scheduler():
    """Stop the scheduler and its checks on app shutdown, and end the runs that it and Run now
    started: each is recorded as failed, with the processes of a command killed (run_task, ADR-0268)."""
    global _scheduler_running, _scheduler_task, _task_check_task
    _scheduler_running = False
    stopping = [t for t in (_scheduler_task, _task_check_task, *_runs) if t is not None]
    for t in stopping:
        t.cancel()
    await asyncio.gather(*stopping, return_exceptions=True)
    _scheduler_task = _task_check_task = None


def _task_info(task: ScheduledTask, has_key: bool | None, zone: tzinfo, now: datetime) -> ScheduledTaskInfo:
    """``task`` as the API answers with it, with its next run in ``zone`` after ``now``.

    ``has_key`` (whether it has a key) counts for a backup task only.
    """
    has_backup_key = has_key if task.task_type == TaskType.BACKUP else None
    return ScheduledTaskInfo(**task.model_dump(), has_backup_key=has_backup_key, next_run=get_next_run(task, zone, now))


# The task of a route's path (ADR-0417).
TaskId = Annotated[str, PathParameter(description="The task's id, as GET /scheduler/tasks lists it.")]

# What POST and PUT /tasks answer 400 and 500 for (validate_task, new_task_key, the keys file).
_BAD_TASK = (
    "The task is not valid: its name, schedule, keep count, pool, disk or command, or a backup task's "
    "passphrase, missing while the task has no key or shorter than 8 characters."
)
_KEYS_OR_DISKS = (
    "The keys of the backup tasks cannot be read, or the host's disks cannot be listed for a SMART test task."
)
_NO_TASK = "No task has that id."
_TASK_ID_EXAMPLE = "task_20261118090000_1a2b3c4d"


@router.get(
    "/timezone",
    summary="Read the time zone of the schedules",
    responses=answer({"timezone": "Europe/Berlin"},
                     "The name of the zone: the host's, or UTC when the host names none that the supervisor can use."),
)
async def get_schedule_timezone(_: dict = Depends(verify_token)):
    """The time zone whose clock the schedules follow: the host's, read on the host for each request,
    or UTC when the host names no zone that the supervisor can use. While the host cannot be asked,
    the zone it named last.
    \f
    schedule_zone, ADR-0266.
    """
    return {"timezone": str(await asyncio.to_thread(schedule_zone))}


@router.get("/tasks", response_model=list[ScheduledTaskInfo], summary="List the scheduled tasks")
async def list_tasks(_: dict = Depends(verify_token)):
    """Every scheduled task with its next run; a backup task says whether it has its key
    (has_backup_key), never its passphrase."""
    tasks = load_tasks()
    try:
        keys = load_backup_keys()
    except HTTPException:
        # The key file cannot be read: the edits and runs of the backup tasks
        # fail with the reason, but the tasks are still listed.
        keys = None
    zone = await asyncio.to_thread(schedule_zone)
    now = _utc_now()
    return [_task_info(task, None if keys is None else task.id in keys, zone, now) for task in tasks]


# The fields of a task that an edit changes; the others are the server's (ADR-0175, ADR-0332), and
# the passphrase is never stored (ADR-0325).
_EDITED_FIELDS = tuple(
    name for name in ScheduledTask.model_fields
    if name not in ("id", "last_run", "last_status", "schedule_since", "passphrase")
)


def _changed_fields(before: ScheduledTask, after: ScheduledTask) -> list[str]:
    """What an edit changed, as the audit entry names it (ADR-0351): the fields, the old name with a new one."""
    changed = []
    for name in _EDITED_FIELDS:
        if getattr(before, name) != getattr(after, name):
            changed.append(f"name (was {before.name})" if name == "name" else name)
    return changed


def _named(task: ScheduledTask) -> str:
    return f"the {task.task_type.value} task {task.name}"


async def _audit_task(request: Request, user, action: AuditAction, task: ScheduledTask, details: str) -> None:
    """The audit entry of a change of ``task`` or of a run started by hand (ADR-0351)."""
    await audit_change(action, request, user.username, resource_type="task", resource_id=task.name,
                       details=details)


@router.post(
    "/tasks", response_model=ScheduledTaskInfo, summary="Create a scheduled task",
    responses=errors(BODY, {400: _BAD_TASK, 500: _KEYS_OR_DISKS}),
)
async def create_task(request: Request, task: ScheduledTask, user=Depends(verify_token)):
    """Create a scheduled task, with an id that the server gives it; a backup task needs a passphrase.
    An enabled task first runs at the next time of its schedule. The creation is written to the audit log."""
    # In a worker thread: the disk of a SMART task is looked up on the host,
    # and the key of a backup task is derived from its passphrase.
    await asyncio.to_thread(validate_task, task)
    key = await asyncio.to_thread(new_task_key, task, has_key=False)

    # Random suffix: tasks created in the same second must not share an id.
    task.id = f"task_{_utc_now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    task.schedule_since = _utc_now().isoformat()  # it missed no run before it existed (ADR-0332)

    # The key first: a task that is then not saved leaves only an unused key.
    if key is not None:
        await asyncio.to_thread(set_backup_key, task.id, key)
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    await _audit_task(request, user, AuditAction.TASK_CREATED, task, f"Created {_named(task)}")

    return _task_info(task, key is not None, await asyncio.to_thread(schedule_zone), _utc_now())


@router.put(
    "/tasks/{task_id}", response_model=ScheduledTaskInfo, summary="Change a scheduled task",
    responses=errors(BODY, {400: _BAD_TASK, 404: _NO_TASK, 500: _KEYS_OR_DISKS}),
)
async def update_task(request: Request, task_id: TaskId, task: ScheduledTask, user=Depends(verify_token)):
    """Replace a scheduled task's settings with the ones sent. Its last run and last status stay; a
    backup task without a passphrase keeps the one it has. An edit that changes nothing is neither
    stored nor written to the audit log.
    \f
    ADR-0214, ADR-0351.
    """
    stored = next((t for t in load_tasks() if t.id == task_id), None)
    kept_disk = stored.target_disk if stored and stored.task_type == TaskType.SMART_TEST else None
    await asyncio.to_thread(validate_task, task, kept_disk=kept_disk)
    has_key = task_id in load_backup_keys()
    key = await asyncio.to_thread(new_task_key, task, has_key=has_key)
    # A new key, or none for a task that is no longer a backup task; written
    # before the task, as when a task is created.
    if stored is not None and (task.task_type != TaskType.BACKUP or key is not None):
        await asyncio.to_thread(set_backup_key, task_id, key)
    # Read again: a run may have saved its result while the disk was checked.
    tasks = load_tasks()

    for i, t in enumerate(tasks):
        if t.id == task_id:
            task.id = task_id
            # Results of the runs, not of the edit (ADR-0175).
            task.last_run, task.last_status = t.last_run, t.last_status
            # With a new schedule, or enabled again, it misses runs from now on only (ADR-0332).
            kept = (get_cron_expression(task), task.enabled) == (get_cron_expression(t), t.enabled)
            task.schedule_since = t.schedule_since if kept else _utc_now().isoformat()
            changed = _changed_fields(t, task) + (["passphrase"] if key is not None else [])
            if changed:
                tasks[i] = task
                save_tasks(tasks)
                await _audit_task(request, user, AuditAction.TASK_UPDATED, task,
                                  f"Changed {_named(task)}: {', '.join(changed)}")
            # A backup task has the new key now, or kept the one it had.
            return _task_info(task, key is not None or has_key, await asyncio.to_thread(schedule_zone), _utc_now())

    raise HTTPException(404, f"Task not found: {task_id}")


@router.delete(
    "/tasks/{task_id}",
    summary="Delete a scheduled task",
    responses={
        **answer({"status": "deleted", "id": _TASK_ID_EXAMPLE},
                 'Status "deleted" and the id sent, also for an id that names no task.'),
        **errors({500: "The keys of the backup tasks cannot be read."}),
    },
)
async def delete_task(request: Request, task_id: TaskId, user=Depends(verify_token)):
    """Delete a scheduled task, and first the key of a backup task; its runs stay in the history.
    An id that names no task is answered as deleted; only a deletion is stored and written to the
    audit log.
    \f
    ADR-0351.
    """
    await asyncio.to_thread(set_backup_key, task_id, None)
    tasks = load_tasks()
    deleted = next((t for t in tasks if t.id == task_id), None)
    if deleted is not None:
        save_tasks([t for t in tasks if t.id != task_id])
        await _audit_task(request, user, AuditAction.TASK_DELETED, deleted, f"Deleted {_named(deleted)}")
    return {"status": "deleted", "id": task_id}


@router.post(
    "/tasks/{task_id}/run",
    summary="Run a scheduled task now",
    responses={
        **answer({"status": "started", "task_id": _TASK_ID_EXAMPLE},
                 'Status "started" and the task\'s id: the run goes on after the answer; GET /scheduler/runs '
                 "shows it."),
        **errors({404: _NO_TASK}),
    },
)
async def run_task_now(request: Request, task_id: TaskId, user=Depends(verify_token)):
    """Start a run of the task at once, enabled or not, as the scheduler runs it; the answer does not
    wait for the run, whose result goes into the runs and the task. The start is written to the
    audit log.
    \f
    The run goes on beside the request, as the scheduler runs its tasks: stop_scheduler ends the run
    and records it. (A background task of Starlette runs after the answer, and uvicorn waits for it
    at its stop without a limit: an off-device copy can take days.)
    """
    tasks = load_tasks()

    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")

    start_run(task)
    await _audit_task(request, user, AuditAction.TASK_RUN, task, f"Started a run of {_named(task)}")
    return {"status": "started", "task_id": task_id}


@router.post(
    "/tasks/{task_id}/toggle",
    summary="Enable or disable a scheduled task",
    responses={
        **answer({"status": "toggled", "enabled": False}, 'Status "toggled" and whether the task is enabled now.'),
        **errors({404: _NO_TASK}),
    },
)
async def toggle_task(request: Request, task_id: TaskId, user=Depends(verify_token)):
    """Enable a disabled task, or disable an enabled one. A task enabled again misses no run of the
    time it was disabled. The change is written to the audit log.
    \f
    ADR-0332.
    """
    tasks = load_tasks()

    for task in tasks:
        if task.id == task_id:
            task.enabled = not task.enabled
            if task.enabled:  # the runs of the time it was disabled are none it missed (ADR-0332)
                task.schedule_since = _utc_now().isoformat()
            save_tasks(tasks)
            await _audit_task(request, user, AuditAction.TASK_UPDATED, task,
                              f"{'Enabled' if task.enabled else 'Disabled'} {_named(task)}")
            return {"status": "toggled", "enabled": task.enabled}

    raise HTTPException(404, f"Task not found: {task_id}")


@router.get("/runs", response_model=list[TaskRun], summary="List the runs of the scheduled tasks")
async def list_runs(_: dict = Depends(verify_token)):
    """The last 100 runs of the tasks, oldest first, those that still run included."""
    return load_runs()


@router.get("/runs/{task_id}", response_model=list[TaskRun], summary="List the runs of a scheduled task")
async def list_task_runs(task_id: TaskId, _: dict = Depends(verify_token)):
    """The task's runs among the last 100 runs, oldest first; none for an id without runs, such as
    one that names no task (the runs of a deleted task stay)."""
    runs = load_runs()
    return [r for r in runs if r.task_id == task_id]

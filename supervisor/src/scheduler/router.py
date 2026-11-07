"""Scheduled tasks (snapshots, scrubs, SMART tests, backups, custom commands).

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

Backup tasks encrypt their backups (ADR-0240). Their passphrase is never
stored: BACKUP_KEYS_FILE (0600) keeps the key derived from it, apart from
TASKS_FILE, which the API returns and every scheduled backup contains.

Schedules follow the clock of the host's time zone, read on the host for each
pass of the scheduler loop and each answer (ADR-0247). A custom task's run
records the command's output and exit status; the command is stopped after
COMMAND_TIMEOUT, and the processes it leaves running end with it (ADR-0248).
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
import tempfile
import threading
import asyncio
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from croniter import croniter

from .. import host
from ..auth.utils import require_admin, verify_token
from ..backup.router import (
    BACKUP_KEY_SIZE,
    ENCRYPTED_EXTENSION,
    backup_temp_file,
    copy_database,
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
from ..snapshots.router import delete_snapshot_by_id, make_snapshot, utc_stamp
from ..storage import disks as storage_disks
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
SMART_TEST_TIMEOUT = 60
# Seconds `btrfs scrub start` may take: it returns once the scrub runs in the
# background, unless the filesystem hangs (as storage.pools.SCRUB_COMMAND_TIMEOUT).
SCRUB_START_TIMEOUT = 60
# Seconds the command of a custom task may run; then it is stopped, with what it
# started (ADR-0248). Long enough for a copy of a pool to another disk.
COMMAND_TIMEOUT = 24 * 3600
# What a run keeps of a command's output: its last part, so that RUNS_FILE, which
# is rewritten on the system disk at each run, stays small.
MAX_OUTPUT_BYTES = 16 * 1024
OUTPUT_CUT = "[Only the last 16 KiB of the output are kept.]\n"
# Seconds to wait, once the processes of a command were killed, for them to end
# and for the rest of their output: a process in the kernel's uninterruptible
# sleep ends only when the kernel lets it.
STOP_WAIT = 5
# How far back a pass of the scheduler loop looks for the times it has not
# handled yet: a pass that comes late still starts the runs of the minutes
# since the pass before, but a clock set forward by hours (a resume from
# suspend, NTP after a boot without a real-time clock) does not start every task
# of the hours it skipped (ADR-0247).
CATCH_UP = timedelta(minutes=5)
MINUTE = timedelta(minutes=1)

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
# The runs the loop started and that have not ended: the event loop keeps only
# weak references to tasks.
_runs: set[asyncio.Task] = set()
# The warnings already logged: each is logged once, not at every pass.
_reported: set[str] = set()
_sleep = asyncio.sleep  # replaced by the tests, as _utc_now


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _warn_once(message: str) -> None:
    if message not in _reported:
        _reported.add(message)
        logger.warning(message)


def load_tasks() -> list[ScheduledTask]:
    """Load scheduled tasks from file."""
    path = Path(TASKS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [ScheduledTask(**t) for t in data]
    except Exception:
        return []


def save_tasks(tasks: list[ScheduledTask]) -> None:
    """Save scheduled tasks to file."""
    path = Path(TASKS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([t.model_dump() for t in tasks], indent=2))


def load_runs() -> list[TaskRun]:
    """Load task runs from file."""
    path = Path(RUNS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [TaskRun(**r) for r in data[-100:]]  # Keep last 100
    except Exception:
        return []


def save_run(run: TaskRun) -> None:
    """Save a task run."""
    runs = load_runs()
    # Update existing or append
    for i, r in enumerate(runs):
        if r.id == run.id:
            runs[i] = run
            break
    else:
        runs.append(run)
    runs = runs[-100:]  # Keep last 100
    path = Path(RUNS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump() for r in runs], indent=2))


def load_backup_keys() -> dict[str, bytes]:
    """The keys of the backup tasks by task id; an entry that is not a key is left out.

    A file that is not JSON counts as empty, since none of its keys can be
    used: the runs of the backup tasks fail with NO_BACKUP_KEY, and the next
    passphrase that is set replaces the file. A file that cannot be read is
    HTTPException(500), so that no write replaces the keys it may still hold.
    """
    try:
        raw = Path(BACKUP_KEYS_FILE).read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise HTTPException(500, f"The keys of the backup tasks cannot be read: {e}") from e
    try:
        data = json.loads(raw)
    except ValueError as e:  # also not UTF-8 text
        logger.warning("%s is not JSON (%s): no backup task has a key", BACKUP_KEYS_FILE, e)
        return {}
    keys = {}
    for task_id, value in data.items() if isinstance(data, dict) else ():
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
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({task_id: base64.b64encode(key).decode() for task_id, key in keys.items()}, indent=2)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
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


def schedule_zone() -> tzinfo:
    """The host's time zone, whose clock the schedules follow; UTC when it cannot be read.

    Read on the host each time (host.time_zone_name), so that the schedules follow a
    change of the zone at once. The zone's rules come from the supervisor's own
    time zone database. Blocking (a host command): call it in a worker thread.
    """
    try:
        name = host.time_zone_name()
        if name is None:
            raise ValueError("/etc/localtime is not a link into a zoneinfo folder")
        return ZoneInfo(name)
    except (OSError, subprocess.SubprocessError, ValueError, ZoneInfoNotFoundError) as e:
        _warn_once(f"The time zone of the host cannot be read ({e}): the schedules follow UTC")
        return timezone.utc


def _first_instant(wall: datetime, zone: tzinfo) -> datetime:
    """The first instant (UTC) at which the clock of ``zone`` shows the naive time ``wall`` or a later one.

    That is the first of the two in the hour that the clock shows twice when daylight
    saving time ends, and for a time that the clock skips when it begins, the end of
    the skip: 02:30 comes at 03:00 in central Europe (ADR-0247).
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


def _run(cmd: list[str] | str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _start_scrub(pool: Path) -> subprocess.CompletedProcess:
    """Run `btrfs scrub start` on ``pool``, within SCRUB_START_TIMEOUT; stderr: its error output.

    btrfs forks the process that runs the scrub and returns at once, but
    that process keeps the standard output and error of btrfs until the
    scrub ends, hours later on a large pool. Read through pipes
    (capture_output), they would end only then, and the run would time out
    while the scrub runs: the error output goes to a temporary file, the
    output (a line saying that the scrub started) nowhere.
    """
    with tempfile.TemporaryFile() as stderr:
        result = subprocess.run(
            ["btrfs", "scrub", "start", str(pool)],
            stdout=subprocess.DEVNULL, stderr=stderr, timeout=SCRUB_START_TIMEOUT,
        )
        stderr.seek(0)
        result.stderr = stderr.read().decode(errors="replace")
    return result


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
                "created": datetime.now().isoformat(),
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
                copy_database(db_path, tmpdir_path / "unos.db")

            # Backup SMB config
            smb_conf = Path("/etc/samba/smb.conf")
            if smb_conf.exists():
                shutil.copy2(smb_conf, tmpdir_path / "smb.conf")

            # Backup fstab
            fstab = Path("/etc/fstab")
            if fstab.exists():
                shutil.copy2(fstab, tmpdir_path / "fstab")

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
    (delete_snapshot_by_id, ADR-0242); one that fails them is kept.
    """
    pool = pool_path(pool_name)
    snapshots_dir = _snapshot_dir(pool)
    if snapshots_dir is None:
        return ""

    # Get all auto-created snapshots (prefixed with "auto_"); never follow symlinks.
    auto_snapshots = []
    for snap in snapshots_dir.iterdir():
        if _real_dir(snap) and snap.name.startswith("auto_"):
            # Get creation time from btrfs
            result = _run(["btrfs", "subvolume", "show", str(snap)])
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
    name = f"auto_{utc_stamp()}"
    make_snapshot(pool, name)
    return True, name


async def execute_task(task: ScheduledTask) -> tuple[bool, str]:
    """Execute a snapshot, scrub, SMART test or backup task: whether it succeeded, and its output or error.

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
                cleanup_result = await cleanup_old_snapshots(
                    task.target_pool, task.keep_count
                )
                if cleanup_result:
                    return True, f"Snapshot {name} created. {cleanup_result}"

            return True, f"Snapshot {name} created"

        elif task.task_type == TaskType.SCRUB:
            pool = pool_path(task.target_pool)
            # Not mounted, the path is a folder on the root filesystem: btrfs
            # would scrub that filesystem if it is btrfs.
            if not await asyncio.to_thread(_pool_is_mounted, pool):
                return False, f"Pool {task.target_pool} is not mounted at {pool}"
            # Without a timeout, a hung filesystem would block a worker thread
            # for good and leave the run "running".
            try:
                result = await asyncio.to_thread(_start_scrub, pool)
            except subprocess.TimeoutExpired:
                return False, (
                    f"btrfs scrub start timed out: {task.target_pool} did not answer within "
                    f"{SCRUB_START_TIMEOUT} seconds. The pool may not be responding."
                )
            if result.returncode != 0:
                return False, result.stderr
            return True, "Scrub started"

        elif task.task_type == TaskType.SMART_TEST:
            # Accepts "sda" and "/dev/sda" (what the UI sends); checks the host's disks.
            device = await asyncio.to_thread(disk_device, task.target_disk)
            # Without a timeout, a hung disk or USB bridge would block a worker
            # thread for good and leave the run "running".
            try:
                result = await asyncio.to_thread(
                    _run, ["smartctl", "-t", "short", device], timeout=SMART_TEST_TIMEOUT
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
            # Encrypted with the key of the task (ADR-0240); never unencrypted.
            key = load_backup_keys().get(task.id or "")
            if key is None:
                return False, NO_BACKUP_KEY
            return await create_scheduled_backup(task.keep_count, key)

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


def _kill_process_group(pgid: int) -> None:
    """Kill the processes left in the process group ``pgid``, if any."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


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
    It runs in a process group of its own: when its shell exits, the processes
    it left running there are killed, and so is all of it after COMMAND_TIMEOUT
    seconds (ADR-0248). The event loop waits for it, no worker thread. Raises
    OSError when the shell cannot start.
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
        # What it left running, or all of it at the time limit (or when the run is cancelled).
        # The shell may have been reaped already. Its group's id stays taken while a process
        # is left in the group, and the id of an empty group is not handed out again at once:
        # Linux gives out process ids in turn, up to pid_max. So the kill reaches no other group.
        _kill_process_group(process.pid)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), STOP_WAIT)
        # The rest of the output; a process that left the group may still hold the pipe.
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

    if task.task_type == TaskType.CUSTOM:
        success = await _run_custom_task(task, run)
    else:
        success, message = await execute_task(task)
        if success:
            run.output = message
        else:
            run.error = message

    run.finished_at = _utc_now().isoformat()
    run.status = "success" if success else "failed"
    save_run(run)

    # Read again: an edit may have saved the task during the run. A task deleted
    # meanwhile is not written back.
    tasks = load_tasks()
    for t in tasks:
        if t.id == task.id:
            t.last_run = run.started_at
            t.last_status = run.status
            save_tasks(tasks)
            break


def _seconds_to_next_pass(now: datetime) -> float:
    """Seconds from ``now`` to one second after the next minute begins."""
    return 61 - now.timestamp() % 60


async def scheduler_loop():
    """Start the runs of the enabled tasks when their time comes (ADR-0247).

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
                run = asyncio.create_task(run_task(task))
                _runs.add(run)
                run.add_done_callback(_runs.discard)
        except Exception:
            logger.exception("The scheduler could not start the tasks that were due")
        checked = now


async def start_scheduler():
    """Start the scheduler on app startup."""
    global _scheduler_running, _scheduler_task
    _scheduler_running = True
    _scheduler_task = asyncio.create_task(scheduler_loop())


async def stop_scheduler():
    """Stop the scheduler on app shutdown."""
    global _scheduler_running, _scheduler_task
    _scheduler_running = False
    if _scheduler_task:
        _scheduler_task.cancel()


def _task_info(task: ScheduledTask, has_key: bool | None, zone: tzinfo, now: datetime) -> ScheduledTaskInfo:
    """``task`` as the API answers with it, with its next run in ``zone`` after ``now``.

    ``has_key`` (whether it has a key) counts for a backup task only.
    """
    has_backup_key = has_key if task.task_type == TaskType.BACKUP else None
    return ScheduledTaskInfo(**task.model_dump(), has_backup_key=has_backup_key, next_run=get_next_run(task, zone, now))


@router.get("/timezone")
async def get_schedule_timezone(_: dict = Depends(verify_token)):
    """The time zone whose clock the schedules follow: the host's, or UTC when it cannot be read."""
    return {"timezone": str(await asyncio.to_thread(schedule_zone))}


@router.get("/tasks", response_model=list[ScheduledTaskInfo])
async def list_tasks(_: dict = Depends(verify_token)):
    """List all scheduled tasks; a backup task says whether it has a key (has_backup_key)."""
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


@router.post("/tasks", response_model=ScheduledTaskInfo)
async def create_task(task: ScheduledTask, _: dict = Depends(verify_token)):
    """Create a new scheduled task; a backup task needs a passphrase."""
    # In a worker thread: the disk of a SMART task is looked up on the host,
    # and the key of a backup task is derived from its passphrase.
    await asyncio.to_thread(validate_task, task)
    key = await asyncio.to_thread(new_task_key, task, has_key=False)

    # Random suffix: tasks created in the same second must not share an id.
    task.id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"

    # The key first: a task that is then not saved leaves only an unused key.
    if key is not None:
        await asyncio.to_thread(set_backup_key, task.id, key)
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)

    return _task_info(task, key is not None, await asyncio.to_thread(schedule_zone), _utc_now())


@router.put("/tasks/{task_id}", response_model=ScheduledTaskInfo)
async def update_task(task_id: str, task: ScheduledTask, _: dict = Depends(verify_token)):
    """Update a scheduled task. Its last run and last status stay; a backup task
    without a passphrase keeps the one it has."""
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
            tasks[i] = task
            save_tasks(tasks)
            # A backup task has the new key now, or kept the one it had.
            return _task_info(task, key is not None or has_key, await asyncio.to_thread(schedule_zone), _utc_now())

    raise HTTPException(404, f"Task not found: {task_id}")


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, _: dict = Depends(verify_token)):
    """Delete a scheduled task, and first the key of a backup task."""
    await asyncio.to_thread(set_backup_key, task_id, None)
    tasks = load_tasks()
    tasks = [t for t in tasks if t.id != task_id]
    save_tasks(tasks)
    return {"status": "deleted", "id": task_id}


@router.post("/tasks/{task_id}/run")
async def run_task_now(task_id: str, background_tasks: BackgroundTasks, _: dict = Depends(verify_token)):
    """Run a task immediately."""
    tasks = load_tasks()

    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")

    background_tasks.add_task(run_task, task)
    return {"status": "started", "task_id": task_id}


@router.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, _: dict = Depends(verify_token)):
    """Enable/disable a task."""
    tasks = load_tasks()

    for task in tasks:
        if task.id == task_id:
            task.enabled = not task.enabled
            save_tasks(tasks)
            return {"status": "toggled", "enabled": task.enabled}

    raise HTTPException(404, f"Task not found: {task_id}")


@router.get("/runs", response_model=list[TaskRun])
async def list_runs(_: dict = Depends(verify_token)):
    """List recent task runs."""
    return load_runs()


@router.get("/runs/{task_id}", response_model=list[TaskRun])
async def list_task_runs(task_id: str, _: dict = Depends(verify_token)):
    """List runs for a specific task."""
    runs = load_runs()
    return [r for r in runs if r.task_id == task_id]

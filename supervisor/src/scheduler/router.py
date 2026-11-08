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
"""

import base64
import io
import json
import logging
import os
import secrets
import stat
import subprocess
import tempfile
import threading
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
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
# Both commands are killed at their timeout and not waited for (ADR-0261).
SMART_TEST_TIMEOUT = 60
# Seconds `btrfs scrub start` may take: it returns once the scrub runs in the
# background, unless the filesystem hangs (as storage.pools.SCRUB_COMMAND_TIMEOUT).
SCRUB_START_TIMEOUT = 60
# Seconds the `btrfs subvolume show` of a snapshot may take when old snapshots
# are cleaned up: it reads the creation time, unless the filesystem hangs.
SUBVOLUME_SHOW_TIMEOUT = 60

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


def get_next_run(task: ScheduledTask) -> str:
    """Calculate next run time for a task."""
    cron = get_cron_expression(task)
    try:
        iter = croniter(cron, datetime.now())
        return iter.get_next(datetime).isoformat()
    except Exception:
        return ""


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
        command = task.command or ""
        if not command.strip():
            raise _bad_task("No command specified")
        if "\x00" in command or len(command) > MAX_COMMAND_LENGTH:
            raise _bad_task(f"Command must be at most {MAX_COMMAND_LENGTH} characters without NUL bytes")

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
        result = host.run_in_container(
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
    (delete_snapshot_by_id, ADR-0242); one that fails them is kept. When btrfs
    does not answer, nothing is deleted, and the result says why.
    """
    pool = pool_path(pool_name)
    snapshots_dir = _snapshot_dir(pool)
    if snapshots_dir is None:
        return ""

    # Get all auto-created snapshots (prefixed with "auto_"); never follow symlinks.
    auto_snapshots = []
    for snap in snapshots_dir.iterdir():
        if _real_dir(snap) and snap.name.startswith("auto_"):
            # Get creation time from btrfs; killed at the timeout and not
            # waited for (ADR-0261).
            try:
                result = host.run_in_container(
                    ["btrfs", "subvolume", "show", str(snap)], timeout=SUBVOLUME_SHOW_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                return (
                    f"Old snapshots were not cleaned up: btrfs did not answer within "
                    f"{SUBVOLUME_SHOW_TIMEOUT} seconds. The pool may not be responding."
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
    name = f"auto_{utc_stamp()}"
    make_snapshot(pool, name)
    return True, name


async def execute_task(task: ScheduledTask) -> tuple[bool, str]:
    """Execute a scheduled task.

    Targets are validated again here: the tasks file may contain entries
    written before validation existed or edited by hand.
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
            # Killed at its timeout and not waited for (ADR-0261): a hung
            # filesystem neither holds a worker thread nor leaves the run "running".
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
            # Killed at its timeout and not waited for (ADR-0261): a hung disk
            # or USB bridge neither holds a worker thread nor leaves the run "running".
            try:
                result = await asyncio.to_thread(
                    host.run_in_container, ["smartctl", "-t", "short", device], timeout=SMART_TEST_TIMEOUT
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

        elif task.task_type == TaskType.CUSTOM:
            if not task.command:
                return False, "No command specified"
            # Deliberately a shell command run as root: administrators only,
            # see the trust model at the top of this module.
            result = await asyncio.to_thread(_run, task.command, shell=True)
            return result.returncode == 0, result.stdout or result.stderr

        return False, "Unknown task type"

    except HTTPException as e:
        return False, str(e.detail)
    except Exception as e:
        return False, str(e)


async def run_task(task: ScheduledTask) -> None:
    """Run a task and save the result."""
    run_id = f"run_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    run = TaskRun(
        id=run_id,
        task_id=task.id or "",
        task_name=task.name,
        started_at=datetime.now().isoformat(),
        status="running"
    )
    save_run(run)

    success, output = await execute_task(task)

    run.finished_at = datetime.now().isoformat()
    run.status = "success" if success else "failed"
    if success:
        run.output = output
    else:
        run.error = output
    save_run(run)

    # Update task with last run info
    tasks = load_tasks()
    for t in tasks:
        if t.id == task.id:
            t.last_run = run.started_at
            t.last_status = run.status
            t.next_run = get_next_run(t)
            break
    save_tasks(tasks)


async def scheduler_loop():
    """Main scheduler loop - checks tasks every minute."""
    global _scheduler_running
    while _scheduler_running:
        try:
            tasks = load_tasks()
            now = datetime.now()

            for task in tasks:
                if not task.enabled:
                    continue

                cron = get_cron_expression(task)
                try:
                    iter = croniter(cron, now - timedelta(minutes=1))
                    next_time = iter.get_next(datetime)
                    # If next run is within this minute, execute
                    if next_time <= now < next_time + timedelta(minutes=1):
                        asyncio.create_task(run_task(task))
                except Exception:
                    pass

        except Exception as e:
            print(f"Scheduler error: {e}")

        await asyncio.sleep(60)  # Check every minute


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


def _task_info(task: ScheduledTask, has_key: bool | None) -> ScheduledTaskInfo:
    """``task`` as the API answers with it; ``has_key`` (whether it has a key) counts for a backup task only."""
    has_backup_key = has_key if task.task_type == TaskType.BACKUP else None
    return ScheduledTaskInfo(**task.model_dump(), has_backup_key=has_backup_key)


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
    # Update next_run for each task
    for task in tasks:
        task.next_run = get_next_run(task)
    return [_task_info(task, None if keys is None else task.id in keys) for task in tasks]


@router.post("/tasks", response_model=ScheduledTaskInfo)
async def create_task(task: ScheduledTask, _: dict = Depends(verify_token)):
    """Create a new scheduled task; a backup task needs a passphrase."""
    # In a worker thread: the disk of a SMART task is looked up on the host,
    # and the key of a backup task is derived from its passphrase.
    await asyncio.to_thread(validate_task, task)
    key = await asyncio.to_thread(new_task_key, task, has_key=False)

    # Random suffix: tasks created in the same second must not share an id.
    task.id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    task.next_run = get_next_run(task)

    # The key first: a task that is then not saved leaves only an unused key.
    if key is not None:
        await asyncio.to_thread(set_backup_key, task.id, key)
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)

    return _task_info(task, key is not None)


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
            task.next_run = get_next_run(task)
            tasks[i] = task
            save_tasks(tasks)
            # A backup task has the new key now, or kept the one it had.
            return _task_info(task, key is not None or has_key)

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

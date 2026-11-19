from pydantic import BaseModel, ConfigDict, Field
from enum import Enum
from typing import Optional

from ..times import UtcText


class TaskType(str, Enum):
    SNAPSHOT = "snapshot"
    SCRUB = "scrub"
    SMART_TEST = "smart_test"
    BACKUP = "backup"
    CUSTOM = "custom"
    # A copy to the off-device copy's repository (ADR-0337): no fields of its own.
    OFFSITE_COPY = "offsite_copy"


class ScheduleFrequency(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


class ScheduledTask(BaseModel):
    """A scheduled task: what it does and the times of its schedule, on the clock of the host's
    time zone."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "Nightly snapshot", "task_type": "snapshot", "enabled": True, "frequency": "daily",
        "hour": 3, "minute": 0, "target_pool": "data", "keep_count": 7,
    }]})

    id: Optional[str] = Field(None, description="The task's id, which the server gives it; a request's is ignored.")
    name: str = Field(description="The task's name: 1 to 100 printable characters, without spaces around them.")
    task_type: TaskType = Field(
        description="What the task does: snapshot or scrub of a pool, smart_test (a short SMART self-test) of a "
                    "disk, backup (an encrypted backup of the UNOS database), custom (a shell command) or "
                    "offsite_copy (an off-device copy)."
    )
    enabled: bool = Field(True, description="Whether the scheduler runs the task at the times of its schedule.")
    frequency: ScheduleFrequency = Field(
        description="hourly (at minute), daily (at hour and minute), weekly (also day_of_week), monthly (also "
                    "day_of_month), or custom (cron_expression)."
    )
    cron_expression: Optional[str] = Field(
        None, description="The cron expression of a custom frequency (minute, hour, day of the month, month, day "
                          "of the week); not used by other frequencies.",
    )  # For custom frequency
    hour: int = Field(0, description="The hour of a run, 0 to 23; used by daily, weekly and monthly tasks.")  # 0-23
    minute: int = Field(0, description="The minute of a run, 0 to 59; used by every frequency but custom.")  # 0-59
    day_of_week: Optional[int] = Field(
        None, description="The day of a weekly run, 0 (Sunday) to 6 (Saturday); null: Sunday.",
    )  # 0-6 for weekly
    day_of_month: Optional[int] = Field(
        None, description="The day of a monthly run, 1 to 31; null: the 1st. A month without that day has no run.",
    )  # 1-31 for monthly

    # Task-specific config
    target_pool: Optional[str] = Field(None, description="The pool of a snapshot or scrub task.")  # For snapshot/scrub
    target_disk: Optional[str] = Field(
        None, description="The disk of a SMART test task, sent as sda or /dev/sda and stored as /dev/sda; a whole "
                          "disk of the host.",
    )  # For SMART test
    command: Optional[str] = Field(
        None, description="The shell command of a custom task, at most 4096 characters, run by /bin/sh as root in "
                          "the supervisor's container and stopped after 24 hours.",
    )  # For custom tasks

    # Retention
    keep_count: Optional[int] = Field(
        None, description="How many automatic snapshots of the pool (snapshot task) or scheduled backups (backup "
                          "task) to keep, 1 to 1000: a run deletes the oldest beyond them. Null: all are kept.",
    )  # For snapshots - how many to keep

    # For backup tasks: the passphrase their backups are encrypted with.
    # Write-only (exclude): accepted in requests, but never in an answer or in
    # the tasks file; the task keeps a key derived from it (ADR-0325). An edit
    # without one keeps the stored key. Not in repr() or str() (repr=False),
    # so that a log line or an error text with a task in it never shows it.
    passphrase: Optional[str] = Field(
        default=None, exclude=True, repr=False,
        description="Backup tasks: the passphrase that their backups are encrypted with, at least 8 characters; "
                    "required for a new backup task, while an edit without one keeps the task's key. Never "
                    "answered or stored: the device keeps only a key derived from it.",
    )

    # The start of the task's last run (ISO 8601 in UTC, with its offset; one that the file holds
    # without an offset is UTC, ADR-0401) and its status.
    last_run: Optional[UtcText] = Field(
        None, description="When the task's last ended run started, in UTC; null before a run ended. Set by "
                          "the runs: a new task stores the value sent (send none), an edit keeps the stored one.",
    )
    last_status: Optional[str] = Field(
        None, description="How the task's last ended run ended: success or failed; null before a run ended. "
                          "Set by the runs: a new task stores the value sent (send none), an edit keeps the "
                          "stored one.",
    )
    # When the task was created, enabled again or given another schedule (ISO 8601 in UTC, with
    # its offset): a run it missed is a time of its schedule after this and after its last run
    # (ADR-0332). Set by the server, whatever a request sends.
    schedule_since: Optional[UtcText] = Field(
        None, description="When the task was created, enabled again or given another schedule, in UTC: the times "
                          "of its schedule before it are no runs it missed. Set by the server, whatever a request "
                          "sends.",
    )


class ScheduledTaskInfo(ScheduledTask):
    """A task as the API answers with it: with what is derived for the answer and never stored."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "task_20261118090000_1a2b3c4d", "name": "Nightly snapshot", "task_type": "snapshot",
        "enabled": True, "frequency": "daily", "cron_expression": None, "hour": 3, "minute": 0,
        "day_of_week": None, "day_of_month": None, "target_pool": "data", "target_disk": None, "command": None,
        "keep_count": 7, "last_run": "2026-11-19T02:00:01.104233+00:00", "last_status": "success",
        "schedule_since": "2026-11-18T09:00:00.512345+00:00", "has_backup_key": None,
        "next_run": "2026-11-20T03:00:00+01:00",
    }]})

    # For backup tasks: whether the device has the key of the task, which an
    # edit without a passphrase keeps (ADR-0325). None for other tasks, and
    # when the key file cannot be read.
    has_backup_key: Optional[bool] = Field(
        None, description="Backup tasks: whether the device has the task's key, made from its passphrase; null for "
                          "other tasks and when the keys cannot be read.",
    )
    # When the task runs next by the clock of the host's time zone, which its hour
    # and minute follow (ISO 8601 with that zone's offset, ADR-0266); None when its
    # schedule has no next time.
    next_run: Optional[str] = Field(
        None, description="When the task's schedule has its next time, also for a disabled task: on the clock of "
                          "the host's time zone, with that zone's offset. Null when it has none.",
    )


class TaskRun(BaseModel):
    """A run of a scheduled task, in the history of the last 100 runs."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "run_20261119020001104233_9f8e7d6c", "task_id": "task_20261118090000_1a2b3c4d",
        "task_name": "Nightly snapshot", "started_at": "2026-11-19T02:00:01.104233+00:00",
        "finished_at": "2026-11-19T02:00:02.318764+00:00", "status": "success",
        "output": "Snapshot auto_20261119_020001 created", "error": None, "exit_code": None,
    }]})

    id: str = Field(description="The run's id.")
    task_id: str = Field(description="The id of the task that ran.")
    task_name: str = Field(description="The task's name when the run started.")
    # ISO 8601 in UTC, with its offset, as finished_at (without one in older files: UTC)
    started_at: UtcText = Field(description="When the run started, in UTC.")
    # None while it runs. For a run that a stop of the supervisor cut off, when the
    # supervisor started again (ADR-0268).
    finished_at: Optional[UtcText] = Field(
        None, description="When the run ended, in UTC; null while it runs. For a run that a stop of the supervisor "
                          "cut off, when the supervisor started again.",
    )
    status: str = Field(description="running, success or failed.")  # running, success, failed
    # What a run of a custom task printed (its last 16 KiB), or what another task reports when it succeeded.
    output: Optional[str] = Field(
        None, description="What the command of a custom task printed, standard output and error together (its last "
                          "16 KiB), or what another task reports when it succeeded; null for none.",
    )
    # why the run failed
    error: Optional[str] = Field(None, description="Why the run failed; null while it runs and for a success.")
    # The exit status of the command of a custom task; None for other tasks, and for
    # a command that did not exit by itself (stopped at its time limit, or by a signal).
    exit_code: Optional[int] = Field(
        None, description="The exit status of a custom task's command; null for other tasks and for a command that "
                          "was stopped at its time limit or ended by a signal.",
    )

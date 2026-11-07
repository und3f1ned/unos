from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional


class TaskType(str, Enum):
    SNAPSHOT = "snapshot"
    SCRUB = "scrub"
    SMART_TEST = "smart_test"
    BACKUP = "backup"
    CUSTOM = "custom"


class ScheduleFrequency(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


class ScheduledTask(BaseModel):
    id: Optional[str] = None
    name: str
    task_type: TaskType
    enabled: bool = True
    frequency: ScheduleFrequency
    cron_expression: Optional[str] = None  # For custom frequency
    hour: int = 0  # 0-23
    minute: int = 0  # 0-59
    day_of_week: Optional[int] = None  # 0-6 for weekly
    day_of_month: Optional[int] = None  # 1-31 for monthly

    # Task-specific config
    target_pool: Optional[str] = None  # For snapshot/scrub
    target_disk: Optional[str] = None  # For SMART test
    command: Optional[str] = None  # For custom tasks

    # Retention
    keep_count: Optional[int] = None  # For snapshots - how many to keep

    # For backup tasks: the passphrase their backups are encrypted with.
    # Write-only (exclude): accepted in requests, but never in an answer or in
    # the tasks file; the task keeps a key derived from it (ADR-0240). An edit
    # without one keeps the stored key. Not in repr() or str() (repr=False),
    # so that a log line or an error text with a task in it never shows it.
    passphrase: Optional[str] = Field(default=None, exclude=True, repr=False)

    # The start of the task's last run (ISO 8601 in UTC, with its offset) and its status.
    last_run: Optional[str] = None
    last_status: Optional[str] = None


class ScheduledTaskInfo(ScheduledTask):
    """A task as the API answers with it: with what is derived for the answer and never stored."""

    # For backup tasks: whether the device has the key of the task, which an
    # edit without a passphrase keeps (ADR-0240). None for other tasks, and
    # when the key file cannot be read.
    has_backup_key: Optional[bool] = None
    # When the task runs next by the clock of the host's time zone, which its hour
    # and minute follow (ISO 8601 with that zone's offset, ADR-0249); None when its
    # schedule has no next time.
    next_run: Optional[str] = None


class TaskRun(BaseModel):
    id: str
    task_id: str
    task_name: str
    started_at: str  # ISO 8601 in UTC, with its offset, as finished_at
    # None while it runs. For a run that a stop of the supervisor cut off, when the
    # supervisor started again (ADR-0251).
    finished_at: Optional[str] = None
    status: str  # running, success, failed
    # What a run of a custom task printed (its last 16 KiB), or what another task reports when it succeeded.
    output: Optional[str] = None
    error: Optional[str] = None  # why the run failed
    # The exit status of the command of a custom task; None for other tasks, and for
    # a command that did not exit by itself (stopped at its time limit, or by a signal).
    exit_code: Optional[int] = None

"""Every path of the supervisor's data directory, which follows one setting: UNOS_DATA_DIR (ADR-0417).

The data directory holds what the supervisor keeps: the database and the JWT secret beside it, the
audit log, the state files (src/state_files.py), the stored certificates and the backups. It is the
volume /data of the supervisor's container, the default; the local E2E stand-in and the tests name
a folder of their own (scripts/standin/run_supervisor.py, supervisor/tests/conftest.py).

UNOS_DATA_DIR is read once, when this module is first imported, and the paths never change while
the process runs: the modules bind them to names of their own at their import, which tests move
with monkeypatch. A value that is not an absolute path of an existing directory stops the import,
and with it the start of the supervisor (DataDirError). Unset or empty, it is /data.

The paths of the host, and those of the container outside this volume, keep settings of their own
(src/config.py): smb.conf, the NFS export file, the pools' folder, /etc/fstab and the others.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_VARIABLE = "UNOS_DATA_DIR"
DEFAULT_DATA_DIR = "/data"


class DataDirError(RuntimeError):
    """UNOS_DATA_DIR names no directory that the supervisor can keep its data in: it refuses to start."""


def data_dir_from(value: str | None) -> Path:
    """The data directory that VALUE names, the value of UNOS_DATA_DIR: DEFAULT_DATA_DIR when it is
    unset or empty. DataDirError for a relative path, a missing one or one that is not a directory."""
    text = value or DEFAULT_DATA_DIR
    path = Path(text)
    if not path.is_absolute():
        raise DataDirError(f"{DATA_DIR_VARIABLE}={text} is not an absolute path")
    if not path.exists():
        raise DataDirError(f"{DATA_DIR_VARIABLE}={text} does not exist")
    if not path.is_dir():
        raise DataDirError(f"{DATA_DIR_VARIABLE}={text} is not a directory")
    return path


DATA_DIR = data_dir_from(os.environ.get(DATA_DIR_VARIABLE))

# The database (the default of UNOS_DB_PATH; config.py keeps the JWT secret beside it) and the app
# catalog's file (the default of UNOS_APP_CATALOG_PATH).
DATABASE = DATA_DIR / "unos.db"
APP_CATALOG = DATA_DIR / "app-catalog.json"
# The audit log and its rotated files (src/audit/router.py).
AUDIT_LOG = DATA_DIR / "audit" / "audit.jsonl"
# The backups, made by hand and by scheduled tasks (src/backup/router.py, src/scheduler/router.py).
BACKUPS = DATA_DIR / "backups"
# The uploaded and self-signed certificates (src/security/router.py, ADR-0404).
CERTIFICATES = DATA_DIR / "certs"
# The state files of the monitors (src/health/router.py, src/storage/volume_usage.py).
RAID_STATUS = DATA_DIR / "raid_status.json"
SMART_HISTORY = DATA_DIR / "smart_history.json"
VOLUME_USAGE = DATA_DIR / "volume_usage.json"
# The notification channels and the history of what was sent (src/notifications/router.py).
NOTIFICATIONS = DATA_DIR / "notifications.json"
NOTIFICATION_LOGS = DATA_DIR / "notification_logs.json"
# The off-device copy's settings, secrets and status (src/offsite/service.py, ADR-0337).
OFFSITE_SETTINGS = DATA_DIR / "offsite.json"
OFFSITE_SECRETS = DATA_DIR / "offsite_secrets.json"
OFFSITE_STATUS = DATA_DIR / "offsite_status.json"
# The power settings, the UPS's and Wake-on-LAN's (src/power/router.py).
POWER_CONFIG = DATA_DIR / "power_config.json"
UPS_CONFIG = DATA_DIR / "ups_config.json"
WOL_CONFIG = DATA_DIR / "wol_config.json"
# The scheduled tasks, their runs and the keys of the backup tasks (src/scheduler/router.py).
SCHEDULED_TASKS = DATA_DIR / "scheduled_tasks.json"
TASK_RUNS = DATA_DIR / "task_runs.json"
SCHEDULED_BACKUP_KEYS = DATA_DIR / "scheduled_backup_keys.json"
# The updater's state, history and settings (src/updates/updater.py).
UPDATE_STATE = DATA_DIR / "update_state.json"
UPDATE_HISTORY = DATA_DIR / "update_history.json"
UPDATE_CONFIG = DATA_DIR / "update_config.json"

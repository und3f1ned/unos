---
status: accepted
date: 2026-11-10
area: architecture
decided-by: agent
---

# ADR-0324: JSON state files are replaced through a rename, and one that cannot be read is kept as .bad

## Context

The supervisor keeps 14 JSON files of state in `/data`. Ten of them were rewritten in place (`write_text`): a supervisor killed during the write, or a full system disk, left an empty or cut file. Most loaders read a file that they could not parse as the defaults, so the next write made the loss final, without a word in the log. That held for the firewall rules, the scheduled tasks, the power and UPS settings and the update's settings (`architecture.atomic-state-files`). The SMART history and the notification files were already replaced through a synced rename (ADR-0235, ADR-0260), and so was the key file of the backup tasks (ADR-0240). The agent decided this under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation).

## Decision

- `supervisor/src/state_files.py`:
  - `replace_file(path, data, sync=..., mode=0o644)` writes the new content to `.<name>.<random>.tmp` next to the file. It creates the file exclusively, with the mode set by `fchmod`, so that the umask cannot narrow it, writes and flushes it, and renames it over the file. A failure removes the temporary file and leaves the file as it was.
  - Each caller says whether the write is synced before the rename (`sync`). Synced are the files whose loss on a power cut an ADR rules out: the SMART history (ADR-0235), the notification channels and history (ADR-0326) and the keys of the backup tasks (ADR-0325), all written in worker threads. The others are written from the event loop and its loops, unsynced as before, so that no write blocks the event loop on the system disk (ADR-0021). A kill leaves the old or the new content either way.
  - `write_json(path, value, sync=..., **dumps)` is `replace_file` for JSON, indented by 2 unless told otherwise.
  - `load_json(path, read, default, what=...)` answers `default()` without a file. A file may not be UTF-8, not be JSON, or be refused by `read`: a `ValueError` (pydantic's `ValidationError` among them) or a `TypeError`. Then it renames the file to `<name>.bad`, replacing an earlier one, logs a warning with `what`, both paths and the reason, and answers `default()`. Any other `OSError` of the read raises, and nothing is renamed.
  - The renames of one path take that path's lock. A reader renames a file to `.bad` only while the path still holds the file that it read, by its inode; a file that a writer put in place meanwhile is read instead.
- Every JSON state file of the supervisor is written through `replace_file` and read through `load_json`, by the loader and writer of its module:
  - the notification channels and history: `notifications.json`, `notification_logs.json`;
  - the power, UPS and Wake-on-LAN settings: `power_config.json`, `ups_config.json`, `wol_config.json`;
  - the firewall rules: `firewall_rules.json`;
  - the RAID status and the SMART history: `raid_status.json`, `smart_history.json`;
  - the update's progress, history and settings: `update_state.json`, `update_history.json`, `update_config.json`;
  - the scheduled tasks, their runs and the keys of the backup tasks: `scheduled_tasks.json`, `task_runs.json`, `scheduled_backup_keys.json`.

  The key file is written with mode 0600, and its folder is synced too (ADR-0325). `GET /api/v1/health/full` and `GET /api/v1/health/prometheus` read the RAID status and the UPS settings through their modules' loaders, not by their paths.
- What a file that cannot be opened answers depends on what it holds:
  - Histories and caches read as none, with a warning, so that the work that writes them goes on and the next write may replace them. These are the runs of the tasks, the RAID status that the RAID monitor compares with, the notification history and the Wake-on-LAN choices. The supervisor starts, a task runs, and the RAID check and its alerts go on. A run that cannot be recorded is a warning, and so is a task's last run.
  - Settings, rules and tasks raise, so that no write replaces what the file may hold. The routes that read them answer 500, and the loops of the scheduler and the UPS monitor log it and go on. The notification channels read as none with a warning (ADR-0326), the keys of the backup tasks answer 500 (ADR-0325), and the updater logs it and uses its defaults.
- Entries that do not validate, in a file that does, are left out where an ADR decided so: the notification history (ADR-0326), the Wake-on-LAN choices, the backup keys that are not keys (ADR-0325), and the RAID entries that are no objects.
- The SMART history endpoints still answer 500 for a damaged file (ADR-0235). The monitor's next pass keeps the file as `smart_history.json.bad` and starts a new history (`load_smart_history_or_start_anew` in `supervisor/src/health/router.py`). The history still loses the damaged file's snapshots, as ADR-0235 says; the `.bad` file keeps them for a look by hand.
- Tests: `supervisor/tests/test_state_files.py`.
  - A process killed with SIGKILL before and after the rename leaves the old file or the new one.
  - A write that fails leaves the file, and no temporary file beside it; only a write that asks for it syncs.
  - A file for secrets gets its mode under the umask 077.
  - The rules of `load_json`. A file that a writer puts in place between the read and the rename is read, not set aside.
  - `STATE_FILES` has one entry per state file. For each, a write replaces the file by a rename (a new inode, nothing beside it), and a damaged file is kept as `.bad` with a warning that names it. A scan finds every module-level path of a JSON state file in `supervisor/src` in the table. It finds no module that opens one of its state files' paths with `write_text`, `read_text`, `open` and the like, and no function that opens one by a literal path.
  - The scheduler starts, and a run goes on, while the runs' file cannot be opened; the RAID monitor reads such a status as none.
  - `supervisor/tests/test_health_monitor.py`: Prometheus keeps the RAID gauges at 0 for a status without pools.

## Consequences

- A damaged state file no longer becomes an empty one that the next write makes final. The firewall rules, the tasks and the settings stay in `<name>.bad`, for a look or a copy back by hand, and the warning names the file.
- No write gained a sync; a rename takes the place of the rewrite in place. After a power cut, a file written unsynced can come back with its earlier content.
- A kill between the write and the rename leaves a `.<name>.<random>.tmp` beside the file, which nothing removes, and the next write makes a new one. A `.bad` file is replaced by the next damaged file of its name.
- Where a settings, rules or tasks file that cannot be opened gave the defaults, its route now answers 500, and no write replaces the file. This holds for the power, UPS and firewall files and the tasks.
- `GET /api/v1/health/full` reports a damaged RAID status as "No RAID pools", where it said "unknown". After the rename the monitor's next check writes the status again. A damaged UPS settings file reads as "not_configured", where it said "unknown".
- The supervisor's settings (`config.py`) name `/data/app-catalog.json`, which no code reads. It is no state file of this decision.

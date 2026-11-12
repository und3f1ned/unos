---
status: accepted
date: 2026-11-12
area: backup
decided-by: agent
---

# ADR-0355: The backup API has no include_ options, and a backup's manifest names no options

## Context

ADR-0345 dropped `include_pools` and `include_network` from `BackupConfig` and left its four other
`include_` options to the ledger entry `backup.drop-unused-include-options`: `include_users`,
`include_apps`, `include_scheduler` and `include_notifications`, all true by default, selected
nothing, and only ended up in the `config` of the manifest. Without them that `config` would hold
only `encrypt`, which the manifest's `encrypted` already says. Working on through the features
under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation), the agent removed the options and the manifest's `config` with them.

## Decision

- `BackupConfig` of `supervisor/src/backup/router.py`, the body of `POST /api/v1/backup/create`,
  has only `encrypt` and `passphrase`, and so does the type of `webui/src/lib/api/backup.ts`
  (ADR-0207). A request that still sends an `include_` option is answered as one without it:
  pydantic ignores unknown fields of the body.
- The manifest that `_collect_backup_content` writes (Create Backup, encrypted backups through the
  API, the archive of the off-device copy of ADR-0337) holds `version`, `created`, `hostname` and
  `encrypted`, and no `config`. The manifest of a backup task's backup
  (`_create_scheduled_backup` of `supervisor/src/scheduler/router.py`) is unchanged: `version`,
  `created`, `hostname` and `type`. What a backup holds is unchanged (ADR-0345).
- The manifest of an older backup that names the options, in its `config`, still uploads and
  restores: the upload check and the restore require only that `manifest.json` is a JSON object
  (`_parse_manifest`), and the restore answers with the manifest as stored.
- The tasks of the scheduler never had these options. `ScheduledTask` of
  `supervisor/src/scheduler/schemas.py` keeps pydantic's default for unknown fields, which ignores
  them: a task stored with such keys loads and runs, and the next write of the tasks file leaves
  them out. No migration of the tasks file.

Test: `test_backups_have_no_include_options` of `supervisor/tests/test_backup_files.py`.

## Consequences

- A script that reads `config` from a manifest finds none in a new backup; the manifest's
  `encrypted` says what `config.encrypt` said.
- No option selects what a backup holds. A choice of what to back up would be a new decision with
  options that each select something.

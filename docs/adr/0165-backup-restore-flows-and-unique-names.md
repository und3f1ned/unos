---
status: accepted
date: 2026-11-06
area: backup
decided-by: agent
---

# ADR-0165: Backups restore from the tab, accept encrypted uploads and get unique names

## Context

Restoring a backup on the Backup & Restore tab always failed (HTTP 422): the API required a
passphrase also for unencrypted backups, and the page sent none. Encrypted backups could not
be uploaded. Backups made in the same second, by Create Backup or by scheduled tasks,
overwrote each other, and a backup was listed, and could be downloaded, while it was still
being written.

## Decision

- Unencrypted backups are restored without a passphrase; for an encrypted backup
  (`.tar.gz.enc`) the page asks for it, and the list marks encrypted backups.
- Encrypted backups can be uploaded. Without the passphrase only their length is checked,
  since the format has no header, and their content is checked when they are restored; an
  unencrypted backup uploaded under a `.tar.gz.enc` name is refused (400).
- Backups are written under a temporary name in the backup directory and get their final
  name only when they are complete and on disk (`publish_backup_file`: fsync, then a hard
  link, which never replaces a file), with `_1`, `_2` and so on before the extension when
  the name is taken. Temporary files that a killed backup or upload left behind are removed
  by the next backup or upload once they are a day old.
- The list shows exactly the regular files with a backup name (ASCII letters, digits, `.`,
  `_` and `-`, starting with a letter or digit, ending in `.tar.gz` or `.tar.gz.enc`),
  newest first; only those can be downloaded, restored or deleted.
- The restore dialog names what a restore overwrites: the database (user accounts with their
  passwords, two-factor authentication, API tokens and the login history) and the SMB share
  configuration. Other settings, such as notifications, scheduled tasks and power, are not
  restored.

Implemented in `supervisor/src/backup/router.py`, `supervisor/src/scheduler/router.py` (the
scheduled backups use `publish_backup_file` too),
`webui/src/routes/system/tabs/BackupTab.svelte` and `webui/src/lib/api/backup.ts`; tested in
`supervisor/tests/test_backup_files.py`, `supervisor/tests/test_backup_restore.py` and
`webui/tests/backup-restore.spec.ts`.

## Consequences

Backups never overwrite each other and are never listed half-written, and both kinds can be
restored from the UI. An encrypted upload that is not a valid backup is found out only when
it is restored.

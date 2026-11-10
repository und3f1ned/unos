---
status: superseded
superseded-by: ADR-0325
date: 2026-11-07
area: backup
decided-by: agent
---

# ADR-0240: Backup tasks encrypt every backup with a key derived from their passphrase and stored in a file of its own

## Context

Superseded by ADR-0325: a key file that cannot be read as keys is kept as scheduled_backup_keys.json.bad.

Scheduled backups were plain `.tar.gz` files that hold `unos.db` with the users' password hashes and two-factor secrets (ADR-0041, `backup.encrypted-scheduled-backups`). A backup task runs unattended, so what encrypts its backups must be on the device; but `/data/scheduled_tasks.json` is returned by `GET /api/v1/scheduler/tasks` and copied into every scheduled backup, and `unos.db` is in every backup, so neither may hold it.

## Decision

- A backup task needs a passphrase of at least 8 characters, as passwords and SMB passwords do: `POST /api/v1/scheduler/tasks` answers 400 without one, and so does a `PUT` that makes a task a backup task. `passphrase` is a write-only field of `ScheduledTask` (`Field(exclude=True, repr=False)` in `supervisor/src/scheduler/schemas.py`): no answer of the API, no tasks file and no `repr()` or `str()` of a task contains it. A `PUT` without it keeps the stored key; with it, the new key applies to the backups made from then on.
- The tasks in the answers of `GET`, `POST` and `PUT` (`ScheduledTaskInfo`) say with `has_backup_key` whether a backup task has a stored key, which a `PUT` without a passphrase keeps: derived from the key file for each answer and never stored, `null` for other tasks and when the key file cannot be read. A key file that cannot be read does not fail the task list.
- The passphrase itself is not stored either. When it is set, the supervisor derives a key from it as for an encrypted manual backup (PBKDF2-HMAC-SHA256 with 100,000 iterations and a new random 16-byte salt; `new_backup_key` in `supervisor/src/backup/router.py`) and stores salt and key by task id in `/data/scheduled_backup_keys.json`, mode 0600, always as a new file that replaces the old one, synced with its folder, in a worker thread and one write at a time (`save_backup_keys` and `set_backup_key` in `supervisor/src/scheduler/router.py`). Deleting the task, or making it another kind of task, removes its key. Neither the backups of System > Backup & Restore (`POST /api/v1/backup/create`) nor those of backup tasks contain this file. The copy of the data volume that `deploy/update.sh` makes before an update keeps it, next to the scheduled backups it decrypts: restoring the volume from that copy needs it.
- The key file is written before the tasks file, when a task is created, edited or deleted. A key file that cannot be read fails the request with 500 before anything is changed, and fails the runs of backup tasks: no write may replace keys that could not be read. A file that is not JSON counts as empty, since none of its keys can be used, and the next passphrase that is set replaces it.
- Each run encrypts the archive with AES-256-GCM, that key and a new nonce, in the format of an encrypted manual backup (salt, nonce, ciphertext and tag; `encrypt_with_key`), and stores it as `auto_backup_<date>_<time>.tar.gz.enc`. A restore asks for the passphrase as for any `.tar.gz.enc` file and refuses the backup without it or with another one. A backup task without a stored key fails its run with "No passphrase is set for this backup task: edit the task and set one" and stores nothing: scheduled backups are never unencrypted.
- Keep Count counts the `auto_backup_*.tar.gz.enc` files. This rewords step 2 of `backup.scheduled-with-retention`, which named `.tar.gz`.
- The Scheduler tab (`webui/src/routes/system/tabs/SchedulerTab.svelte`) asks for the passphrase twice, Passphrase and Confirm Passphrase, and sends it only when one was typed. An edit may leave it empty only for a backup task listed with `has_backup_key: true`.
- Tests: `supervisor/tests/test_scheduler_validation.py`, `supervisor/tests/test_backup_files.py`, `webui/tests/scheduler.spec.ts` and `webui/tests/backup-restore.spec.ts`.

## Consequences

- A forgotten passphrase makes the task's backups useless: UNOS can neither show it nor restore without it. The tab says so. A new passphrase does not reach the older backups, which need the one they were made with.
- Whoever is root on the device can decrypt the backups still on it with the stored key; such a person already has the live `unos.db`. The encryption protects the copies that leave the device, downloads and the off-device copies of `backup.off-device-copies`, as far as the passphrase withstands guessing at 100,000 PBKDF2 iterations per try.
- The backups of one task share its salt, so they show which backups were made with the same key.
- The archive is built and encrypted in memory, as an encrypted manual backup is: a run needs about three times the size of the compressed archive in RAM, a few MiB for a typical database.
- Backup tasks made before this change and plain `auto_backup_*.tar.gz` files get no migration (ADR-0207): such a task fails until a passphrase is set, and such files are not counted by Keep Count and are deleted by hand.
- The backup list does not say which task made a scheduled backup, nor which passphrase it needs: with several backup tasks, one passphrase for all of them avoids the question. Keep Count counts the scheduled backups of all tasks together, as before.
- A request that fails between the two writes leaves the key file ahead of the tasks file: a new task that was not saved leaves an unused key, and an edit answered with 500 may already have set the passphrase that was typed. Repeating the request makes both agree.
- A backup task without a key, made before this change or whose key was lost or cannot be used, is listed with `has_backup_key: false`, and its edit form asks for a passphrase as for a new task. While the key file cannot be read, the form asks for one for every backup task, and saving answers 500 with the reason.
- The copy of the data volume that `deploy/update.sh` keeps in `/var/lib/unos/backups` holds the key file together with the scheduled backups: whoever can read that copy can decrypt them, as they can forge sessions with the JWT signing secret it holds too. Only root can read it, and `docs/INSTALL.md` says what it contains.

## Considered options

- Storing the passphrase on the device: it would expose a passphrase that may be in use elsewhere too; the derived key opens only these backups.
- A field of the task in `/data/scheduled_tasks.json`: that file is returned by the API and copied into every scheduled backup.
- Public-key encryption, with only the public key on the device: backups already made would stay closed after a break-in, but a restore would need a private key, which neither the restore nor the format of encrypted manual backups provides. A later ADR can choose it for the off-device copies.
- Streaming the encryption to the file instead of encrypting in memory: less RAM, but a second way of writing `.tar.gz.enc` next to the one of manual backups.

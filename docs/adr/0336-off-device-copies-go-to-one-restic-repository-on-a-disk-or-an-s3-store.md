---
status: accepted
date: 2026-11-11
area: backup
decided-by: agent
supersedes: [ADR-0041]
---

# ADR-0336: Off-device copies go to one restic repository on a disk or an S3 store, and each copy is checked and its restore tested

## Context

Configuration backups live in `/data/backups` on the system disk, the dev device's SD card, which wears out, and the pools' data has no copy at all: one failed disk loses the configuration or the data (ADR-0041, `backup.off-device-copies`). ADR-0041 proposed a slice that would design btrfs send/receive to a USB disk or a second NAS and restic or rclone to the cloud. The agent decided this slice under the owner's standing instruction, "work on the features, ask no questions; I allow everything", with the goal "a safe, working MVP as soon as possible" (both in translation).

## Decision

- **restic**, Debian's package (0.18 in trixie, the base of `python:3.11-slim`), in `supervisor/Dockerfile` in a layer of its own after the pip layer, so that the apt-get and pip layers stay cached and a device downloads only restic. restic encrypts (AES-256 and Poly1305, scrypt for the password), deduplicates, keeps versioned snapshots, checks a share of the data, limits its bandwidth and writes to a folder or an S3 store. `supervisor/Dockerfile.test` has no restic: the test stack never runs it (below).
- **One copy target per installation, the copy repository** (`supervisor/src/offsite/service.py`):
  - `disk`: a folder strictly below `MOUNTS_BASE` (`/mnt`), absolute, normalized, without `..`, `.snapshots`, `.unos-trash` or the characters `*?[\` (it may become a pattern of `--exclude`), opened component by component without following symbolic links (`open_dir_nofollow`). Refused when its filesystem is that of `/mnt` (the root filesystem: a disk that is not mounted leaves its empty mount point there) or of the data volume `/data` (`on_system_disk`), and when it holds a folder that it copies. Saving makes the folder (0700) when only it is missing and its parent is not on the system disk; a folder that holds files but no repository is refused.
  - `s3`: an endpoint `https://` or `http://` with a host and port only (restic encrypts and authenticates every file, and SigV4 never sends the secret key), a bucket, an optional prefix and region, an access key ID and a secret access key.
  - SFTP and a REST server are left for later entries (`backup.off-device-copies-over-sftp`).
- **Settings and secrets.** `/data/offsite.json` holds the kind, the target without secrets, the sources, the limits (`limit_upload_kib`, `limit_download_kib`, KiB/s, 0 none, s3 only), the retention (`keep_daily` 7, `keep_weekly` 4, `keep_monthly` 12, at least one above 0) and `check_percent` (2, 0 to 100). `/data/offsite_secrets.json` holds the repository's password (at least 12 characters, typed twice on the page) and the S3 secret key, mode 0600. Both are written in a worker thread as new files renamed over the old ones, the secrets first, synced with their folder; a file that cannot be read is kept as `.bad` with a warning (ADR-0324). Neither is in the configuration backups (only `_FILE_MEMBERS` are). No answer holds a secret: they say `has_password` and `has_s3_secret_access_key`. An edit of the same repository keeps the stored password, and of the same endpoint and access key ID the stored secret key; a new repository needs a new password, and changing the password of one is not part of this decision.
- **restic's process** (`supervisor/src/offsite/restic.py`): an asyncio subprocess off the event loop, `nice -n 10 ionice -c 2 -n 7 restic ...`, in a process group of its own that is killed at its timeout and at the end of the lifespan (`offsite.service.stop`). Its environment is built for it, never the supervisor's (which holds `UNOS_JWT_SECRET`): `PATH`, `HOME` and `TMPDIR` (the tmpfs `/tmp`), `RESTIC_REPOSITORY` (`local:<folder>` or `s3:<endpoint>/<bucket>[/<prefix>]`), `RESTIC_PASSWORD` and, for s3, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION`. Secrets are never in its arguments (ADR-0125), and both secrets are cut out of its messages before an answer, a status or a log line gets them. Timeouts: init, cat config, unlock and snapshots 10 minutes, dump 1 hour, check 6 hours, backup, forget with prune and restore 7 days. Cache: `--cache-dir /tmp/restic-cache` for s3, rebuilt after a restart, `--no-cache` for a disk.
- **Saving tests the target** (`PUT /api/v1/offsite/settings`): `restic cat config` opens an existing repository with the password; exit status 10 (no repository) makes one with `restic init`, the only place that does; 12 (wrong password) and any other failure answer 400 with restic's message.
- **A copy** (`run_copy`), for the Scheduler's new task type `offsite_copy` (no fields of its own) and for Run now (`POST /api/v1/offsite/run`, 202, in the background):
  1. the settings and the folders: the target as above, without making anything, and each source inside the pools' base (`validate_pools_path`), present and not on the system disk, so that a pool that is not mounted fails the run instead of copying its empty mount point;
  2. `restic unlock` (stale locks only);
  3. the archive of Create Backup (`backup.router.configuration_archive`, built in memory) through `restic backup --stdin --stdin-filename unos-configuration.tar.gz --tag unos-configuration`;
  4. `restic backup --tag unos-data` of the sources, without `<pools>/*/.snapshots`, `<pools>/*/.unos-trash`, the same one level down (the volumes) and a disk target inside a source;
  5. one `restic forget --tag unos-configuration --tag unos-data` with the retention and `--prune` (restic groups by host and paths, so each kind keeps its own snapshots);
  6. `restic check --read-data-subset=<check_percent>%` (without it at 0);
  7. the restore test: `restic dump` of the new configuration snapshot into TMPDIR, its SHA-256 compared with the archive sent, and the archive checked as an upload is (`backup.router.check_archive`); nothing is restored.

  The first step that fails ends the run, and the run fails; exit status 3 of a backup (files that could not be read) is a failure. The result is the one write of `/data/offsite_status.json` per run (start, end, trigger, success, the step that failed with its error, the files new, changed and unmodified, the bytes added, the snapshot IDs, the check and the restore test), besides the Scheduler's record; a failed `offsite_copy` task is a task problem and reaches every channel (ADR-0332). A stop of the supervisor during a run records it as stopped.
- **One copy, restore or change of the settings at a time**, a lock in memory. A copy that finds one running ends at once as a success, "Skipped: the copy started at <time> UTC is still running", so that the runs of a task during a first copy that takes days raise no alert. Run now, the restores, saving and forgetting the settings answer 409 then; Run now and the restores also when nothing is set up.
- **Restores**, for administrators:
  - `GET /api/v1/offsite/snapshots`: `restic snapshots --json`, newest first, each with its kind (`configuration`, `data`) by its tag, paths, size and files.
  - `POST /api/v1/offsite/restore-configuration {snapshot_id}`: within the request, the snapshot's archive dumped into TMPDIR, checked as an upload is and stored in `/data/backups` as `offsite_<the snapshot's time in UTC>.tar.gz` (`backup.router.store_backup`, `publish_backup_file` with that time); it is restored there by the regular restore with its checks and its copy of the database.
  - `POST /api/v1/offsite/restore-files {snapshot_id, pool, path?}`: `restic restore <id>[:<path>] --target <pool's mount point>/restored-<UTC time>`, in the background, into a new folder (755) that this request makes and that is refused (409) when it exists, on a pool that is mounted; `GET /api/v1/offsite/status` reports it, and its result is written to the status file. Nothing is overwritten.
  - `DELETE /api/v1/offsite/settings` (Forget this target) removes the settings, the secrets and the state; the copies stay where they are.
- **Roles**: `supervisor/src/offsite/router.py` joins `_ADMIN_ONLY` of `main.py` and requires an administrator itself too; `supervisor/tests/test_authz.py` lists its routes.
- **The web UI**: the section Off-device copy of System > Backup & Restore (`webui/src/routes/system/tabs/OffsiteCopy.svelte`, texts in `catalog/offsite.ts`): the target (A disk or An S3-compatible store), the folders to copy (a checkbox per managed pool's mount point, by default all of them, and folders below a pool through a path field), the password twice with its warning, the retention and the check's share, Save and test, Forget this target; the state with Run now, the last copy and restore and Refresh, which reads it again (no polling, ADR-0006); "No scheduled task: copies run only on Run now" with a button that adds a daily `offsite_copy` task at 03:00; the snapshots with Restore configuration and Restore files (a pool and an optional folder). The Scheduler tab offers the type as Off-device Copy.
- **The local stand-in** (`scripts/standin/run_supervisor.py`) runs the fake restic of `supervisor/tests/fake_restic.py`, which the backend tests use as well, puts the targets below its state directory and has no system disk: one filesystem holds all of it. The test stack has no restic and its pools share the filesystem of its data volume, so `webui/tests/offsite.spec.ts` runs its main flow on the stand-in only.
- **The device check**: `scripts/dev/scenario.sh offsite`, with a source folder and a disk target in a folder of the pool, refuses to start on a device that has a copy set up and leaves none.
- Tests: `supervisor/tests/test_offsite.py`, `test_state_files.py`, `test_authz.py`, `test_standin.py`, `test_deploy_scripts.py`, `test_dev_scenarios.py`; `webui/tests/offsite.spec.ts`, `scheduler.spec.ts`, `mobile-layout.spec.ts`.

## Consequences

- The supervisor image grows by restic (about 30 MB), and its build on the device runs apt-get once more for that layer: the owner is told before that deploy, and its SD MiB are measured.
- A forgotten repository password makes every copy useless, and UNOS cannot show it. A copy on a disk that shares a filesystem with a source does not survive that disk; the page and the answer say so, and it is what lets the dev device, with one disk, be checked.
- Only one target; SFTP, REST, a second target and a change of the password are later entries. A restore of the whole installation from the copy alone (a new device: install UNOS, enter the target and the password, restore the configuration, then the files) works through these routes, step by step.
- Runs and restores write to the system disk only the status file, the Scheduler's record and a restored configuration archive; the pack files of restic go to TMPDIR, and its cache for s3 to `/tmp`.
- The state on the page is as fresh as its last Refresh.
- The checks open the target's folder without following symbolic links, but restic opens it again by its path: a disk of its own, which the page's hint names, is the target to choose, not a folder that others can change.
- Forget this target leaves the Scheduler's `offsite_copy` tasks, whose runs then fail and are alerted until a target is set up again.

## Considered options

- btrfs send/receive: it needs btrfs on the target, and gives neither encryption nor a cloud store.
- rsync: no encryption and no versions. rclone sync: no versions; it may become a back end of restic later.
- A run that makes a missing repository: an unmounted disk would get a new repository on its mount point, on the SD card.

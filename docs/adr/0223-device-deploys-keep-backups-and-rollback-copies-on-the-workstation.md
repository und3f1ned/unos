---
status: accepted
date: 2026-11-07
area: device
decided-by: agent
supersedes: [ADR-0051]
---

# ADR-0223: Device deploys keep backups and rollback copies on the workstation

## Context

ADR-0051 made deploys to the dev device scripted, measured and reversible. In Phase 0 and the slices the deploy scripts kept what a rollback needs (the host's fstab and smb.conf, the git state, an archive of the data volume, the containers' details) in a root-only directory on the device: every deploy wrote that archive to the SD card, which cannot be replaced (ADR-0214), and a failed card would take the backups with it. `scripts/dev/deploy.sh` replaced those scripts in the repository and keeps the copies on the workstation instead.

## Decision

- Deploys run from `scripts/dev/deploy.sh`, one step at a time, never as edits by hand: `status`, `sync [--dry-run]`, `build-supervisor`, `build-webui`, `backup`, `up [service]`, `rollback` and `all` (status, sync, both builds, up, then `scripts/dev/check.sh`). Every step holds the device lock (ADR-0210) and reports the MiB written to the system disk (ADR-0214).
- `status`, `sync --dry-run` and `backup` only read the device, in the read-only mount namespace (ADR-0221). The other steps change what runs there and run when the owner asks for a deploy.
- `sync` compares the working tree with the device's checkout by the sha256 of each file (`scripts/dev/tree.py`). It first saves the device's versions of the files it will replace or delete in `tmp/dev/rollback` on the workstation, then writes only the files that differ and checks the device's checkout against the working tree.
- `build-supervisor` and `build-webui` first tag the previous image `:rollback`. Only what must be built on the device is built there (ADR-0184), and checks start no container (ADR-0183).
- `up` runs `backup` first: the device's `/etc/fstab`, `/etc/samba/smb.conf` and the `unos-data` volume go to `tmp/dev/backup/<time>/` on the workstation, because the volume holds the JWT secret, the password hashes and the database. The directories have mode 0700 and the files 0600, nothing of a backup is printed, and a backup is written under another name and renamed when it is complete. `tmp/dev/backup` keeps the newest 3 backups, the one just made and the two newest others (`BACKUPS_KEPT` in `scripts/dev/deploy.sh`); older ones and what an interrupted backup left are deleted. Then the containers start (`docker compose up -d --no-build`), and the step waits until they are healthy (ADR-0176) and checks the images inside them.
- `rollback` restores the files of before the last sync, the `:rollback` images and the smb.conf of the latest backup, then starts the containers. Putting back the `unos-data` volume and fstab from that backup is an owner action; `rollback` names where they are.
- `tmp/dev/` is the main checkout's, which git ignores and its worktrees share.
- Tested in `supervisor/tests/test_dev_scripts.py` against fixture trees with a stub ssh (`test_status_sync_and_rollback_end_to_end`, `test_deploy_sh_status_and_dry_run_hold_the_lock_and_report`, and `test_deploy_sh_backup_is_private_quiet_and_keeps_the_newest_three` for the modes, the quiet output, the read-only device side and the retention).

## Consequences

- A deploy's backups cost the SD card nothing and outlive it, but they put the device's secrets on the workstation: they stay in the git-ignored `tmp/dev/`, readable only by the owner's user, are never shown or extracted except by `rollback`, and never go into a handoff, a log or a commit (ADR-0212). Only the newest 3 are kept, so older copies of those secrets do not pile up.
- The database comes back only from the backup's `unos-data` copy, restored by the owner; `deploy.sh rollback` leaves the volume alone.
- ADR-0051 is superseded; docs that describe how deploys work cite this ADR.

## Considered options

- Backups on the device, as in Phase 0 and the slices: they write the data volume's archive to the SD card at every deploy and are lost with the card.

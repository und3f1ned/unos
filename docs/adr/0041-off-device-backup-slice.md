---
status: superseded
date: 2026-11-05
area: backup
decided-by: agent
superseded-by: ADR-0337
---

# ADR-0041: Give off-device backup of user data its own slice

## Context

Superseded by ADR-0337: the slice copies the configuration and the pools' data with restic to one repository on a disk or an S3 store, and tests a restore after each copy.

Backups hold only the configuration (`unos.db`, `smb.conf` and a few files that are not restored) and are written to `/data/backups` in the `unos-data` volume, on the same system disk as everything else; the user data on the pools has no backup at all. Scheduled backups are not encrypted and contain the users' TOTP secrets, and removing the data volume (`docker compose down -v`, `deploy/uninstall.sh`) deletes the backups with it. This was too large for Phase 0 or slice 1.

## Decision

Proposed: a Phase 1 slice of its own for off-device backup, which designs:

- btrfs send/receive of the pools to a USB disk or a second NAS, and restic or rclone to the cloud;
- verification of the backups and a tested restore;
- a configuration backup kept off the system disk;
- the encryption of scheduled backups.

## Consequences

- Until that slice, a failing system disk takes the configuration backups with it, and a failing pool takes its data: UNOS must not hold the only copy of family data (ADR-0014).
- The slice's items are the `feature-list.json` entries `backup.off-device-copies` (the copies, their verification and restore, and configuration backups off the system disk) and `backup.encrypted-scheduled-backups` (ADR-0203).

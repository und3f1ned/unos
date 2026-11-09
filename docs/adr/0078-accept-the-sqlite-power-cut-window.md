---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0306
---

# ADR-0078: SQLite's power-cut rollback window is accepted, and the startup reconcile repairs the host

## Context

Superseded by ADR-0306: unos.db uses SQLite's write-ahead log with synchronous NORMAL, and the window it accepts covers the commits of the last 35 seconds, or of up to about 155 seconds while the log grows.

`unos.db` uses SQLite's defaults: the rollback journal (DELETE mode) with `synchronous=FULL`; the code sets no journal PRAGMA. SQLite does not fsync the directory after it deletes the journal, and the device's root filesystem commits its own journal every 120 seconds, so a commit followed closely by a power cut can roll back. An SMB flow changes the host (accounts, passdb, smb.conf) around its commits, so after such a rollback the host can be ahead of the database.

## Decision

- The window is accepted. The database stays authoritative, and the startup reconcile (ADR-0138) repairs the host from it: the passdb entries of rows that are not active are removed, marked host accounts that no row has are stale and removed (ADR-0108), and the open sessions of accounts that are not active end (ADR-0104).
- The files UNOS writes next to smb.conf are durable on their own: smb.conf, `smb.conf.unos-import-backup` and `smb.conf.unos-replaced` are written to a temporary file, fsynced, renamed and followed by an fsync of the directory (`_install` and `_write_file_durably` in `supervisor/src/shares/smb.py`). After every change of the host's account files, `/etc` is synced on the host (`flush_etc` in `supervisor/src/shares/accounts.py`).
- Test: `supervisor/tests/test_smb_reconcile.py` (`TestRollback`) rolls the database back after an activation and checks the repair.

## Consequences

- After a power cut, an SMB change of the last seconds can be lost from the database; the next start makes the host match what the database kept.
- A user whose activation was rolled back must turn SMB on again; the uid that the lost activation used is not handed out again (ADR-0109).

## Considered options

- `synchronous=EXTRA`, which adds the directory fsync after the journal is deleted: named, not adopted; no reason was recorded.

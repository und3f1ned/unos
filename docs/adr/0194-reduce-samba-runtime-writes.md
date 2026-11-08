---
status: superseded
date: 2026-11-07
area: shares
decided-by: agent
superseded-by: ADR-0262
---

# ADR-0194: Reduce Samba's remaining writes to the system disk

## Context

Superseded by ADR-0262: Samba keeps a password history of 3, and ADR-0261 puts its log file in /run/samba.

The SMB slice moved Samba's cache to RAM (`/run/samba`, after one restart of smbd and nmbd) and its logging to syslog at level 1, with no per-client log files (ADR-0094). Samba still writes to the system disk in two ways. smbd and nmbd always append their own messages to `/var/log/samba/log.smbd` and `log.nmbd`, whatever the logging setting says: on the ArmSoM dev device `/var/log` is in RAM (zram, synced daily), on other hosts it is on the system disk. And every failed SMB sign-in of an existing account writes `passdb.tdb` (ADR-0159), while SMB has no login limit (ADR-0102).

## Decision

Proposed, to be chosen after measuring:

- Logs: on hosts whose `/var/log` is on the system disk, `log file = /run/samba/log.%m` with a small `max log size` in UNOS's `[global]`.
- Failed sign-ins: a short password history in passdb (`pdbedit -P "password history" -C 3`), so that retries with the previous passwords skip the write, or a rate limit for TCP port 445 on the host.

## Consequences

- Until then, a client that keeps retrying an old password writes to the system disk at every attempt; users are told to update saved passwords on every client after a change (`docs/USER_GUIDE.md`).
- A rate limit on port 445 is a host firewall change, outside what UNOS manages today.

---
status: superseded
date: 2026-11-06
area: security
decided-by: agent
superseded-by: ADR-0224
---

# ADR-0066: Make the audit log append-only; until then it is rewritten as a whole

## Context

Superseded by ADR-0224: the audit log is append-only JSON lines with size-based rotation.

`log_action` in `supervisor/src/audit/router.py` reads `/data/audit_log.json`, appends the entry and writes the whole file again (`save_audit_log`, up to 10,000 entries, about 5 MB), synchronously on the event loop, for every entry. Each login and each audited change therefore rewrites the whole file on the device's system disk (ADR-0214), megabytes once the log is full (on the dev device a sign-in wrote about 0.19 MiB in November 2026), and a full system disk then blocks logins. `load_audit_log` returns an empty list when the file cannot be parsed, so one bad entry makes the next write drop the whole log.

## Decision

Proposed, not implemented: the audit log becomes append-only, as JSON lines or a SQLite table, with rotation, written off the event loop. Its loader skips an entry it cannot read instead of dropping the whole log. No older release has to read or keep the new log (ADR-0207).

Until then, slice 2 keeps the whole-file rewrite and limits the damage: entries are bounded (ADR-0065), and the SMB code writes entries only for real changes (ADR-0157).

## Consequences

- Until then each audit entry costs a rewrite of the whole file on the system disk.
- The choice between JSON lines and a SQLite table is open.
- The proposal first put the new log under a new file name, so that the loader of an older release, after a rollback, would not wipe it. Under ADR-0207 that case is legacy and the requirement is dropped; the `feature-list.json` entry `security.append-only-audit-log` tracks its implementation.

## Considered options

- Append-only JSON lines with rotation.
- A SQLite table.

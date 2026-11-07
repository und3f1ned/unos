---
status: accepted
date: 2026-11-07
area: security
decided-by: agent
supersedes: [ADR-0066]
---

# ADR-0224: The audit log is append-only JSON lines with size-based rotation

## Context

Until now `log_action` in `supervisor/src/audit/router.py` read `/data/audit_log.json`, appended the entry and wrote the whole file again (up to 10,000 entries, about 5 MB), on the event loop, for every entry (ADR-0066). The cost of an entry therefore grew with the log, up to megabytes per sign-in on the device's system disk (ADR-0214), and a file that could not be parsed was read as empty, so the next write dropped the whole log. ADR-0066 left open whether the log becomes JSON lines or a SQLite table.

On the dev device on 2026-11-07, with a small log of 16.8 KB, a sign-in through `scripts/dev/api.py` wrote 0.172 to 0.195 MiB to the SD card, and an API call with a cached token 0.020 MiB. Most of a sign-in's cost is the login history's SQLite commit (rollback journal, synchronous FULL), not the audit log.

## Decision

- The log is JSON lines: one entry per line in `/data/audit/audit.jsonl` (`AUDIT_FILE`). An entry is appended in append mode and nothing written before is rewritten.
- Rotation by size: before an append would grow the file past `MAX_FILE_BYTES` (1 MiB), the file is renamed to `audit.jsonl.1`, the older files move one number up and the oldest beyond `KEEP_ROTATED` (4) is deleted, so the log holds at most about 5 MiB.
- Writes, rotations, reads and clearing run in a worker thread (`asyncio.to_thread`) under one lock, never on the event loop, and a reader never sees a rotation halfway.
- An entry is not fsynced: the kernel writes appends out together. A power cut can lose the entries of up to about the last half minute, accepted to spare the system disk. An entry after a line that a power cut left without its newline starts on a line of its own.
- The reader goes through the old files and then the current one, and skips every line that is not a valid entry (damaged, cut, or an action UNOS no longer knows).
- Clearing (`DELETE /api/v1/audit/logs`) goes file by file, oldest first: a file whose entries are all older than `before_date` is deleted, a file with nothing to remove is left as it is, and only the file that holds both is rewritten (staged, then renamed over it). Nothing is deleted before what replaces it is in place; a failed clearing answers 500, keeps every entry that was to be kept and records the failure.
- The previous `/data/audit_log.json` is not read or converted (ADR-0207); it was deleted from the dev device's data volume at the deploy of this change.
- The feature-list entry `security.append-only-audit-log` gets a measurable step 5 in place of "one sign-in writes a few KiB to the SD card instead of about 0.19 MiB", which the login history's SQLite commit makes unreachable for this change: on the dev device, sign-ins append one line each to the log without rewriting it, and a sign-in's SD writes are measured before and after the deploy. The SQLite part is the entry `architecture.sqlite-write-cost`.

## Consequences

- A sign-in appends one line of about 270 bytes. Measured on the dev device after the deploy: three sign-ins appended three lines, the file kept its inode and its earlier bytes, and a sign-in wrote 0.156 to 0.168 MiB to the SD card (0.172 to 0.195 MiB before, with the small old log). The cost no longer grows with the log.
- Listing, counting, filtering and exporting read at most about 5 MiB of lines per request, in a worker thread.
- Tests read a log file with `supervisor/tests/audit_helpers.py`; the API reads the log through `load_audit_log()`. Tests: `supervisor/tests/test_audit_log.py` (appends only, the worker thread, damaged and cut lines, rotation with the production number of old files, clearing, the lock, no fsync).

## Considered options

- A SQLite table: indexed queries, but every insert is a SQLite transaction with its journal and fsyncs, several pages per entry on the system disk, and the audit log would share the database's write lock with every other change.
- Keeping one JSON document with atomic replacement: still rewrites the whole log per entry.
- An fsync per entry: durable, but every entry would cost its own journal commit on the system disk.

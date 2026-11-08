---
status: accepted
date: 2026-11-08
area: security
decided-by: agent
---

# ADR-0263: The audit log's lock holds across processes, through a file lock that the emergency command line takes too

## Context

ADR-0224 keeps the appends, rotations, reads and clearing of the audit log under one lock, a `threading.Lock` of the supervisor's process. The emergency command line (ADR-0261) writes the log from a process of its own, in the supervisor's container, while the supervisor runs, and a thread lock does not reach it: two rotations at once could lose an old file or fail, and a clearing that read the current file before the command's append and replaced it after would drop the reset's entries.

## Decision

- `_log_lock()` in `supervisor/src/audit/router.py` is the log's lock: the thread lock `_lock`, then an exclusive `flock` of `AUDIT_FILE.lock` (`/data/audit/audit.jsonl.lock`) beside the log. `_append_line`, `load_audit_log` and `_clear` take it, in the supervisor and in the command alike; the rest of ADR-0224 is unchanged.
- The lock file is created once, with mode 0600, and stays empty. Each operation opens it (`O_CLOEXEC`), takes the flock and closes it at the end, which ends the flock, also when the process dies; no flock is held between operations.
- Tests: `TestOtherProcesses` in `supervisor/tests/test_audit_log.py` (an append, a read and a clearing wait for the rotation of another process, and an append waits for its clearing, without an entry lost) and `TestBesideTheSupervisor` in `supervisor/tests/test_emergency.py` (the command's entries wait for a rotation of the supervisor).

## Consequences

- An operation on the log waits for one in the other process as it waits for one in another thread: an append or a rotation for milliseconds, a read of a full log of about 5 MiB a little longer.
- After its creation the lock file costs no writes to the system disk (ADR-0214): opening, locking and closing it write nothing.
- The lock is advisory. Everything that changes the log goes through `_append_line` and `_clear`, which take it.
- The stand-in's supervisor and `scripts/standin/emergency.py` share the lock file in the stand-in's `data/audit/`.

## Considered options

- An append from the command that never rotates: a clearing could still drop its entries.
- Writing the command's entries through the supervisor: no route of the API may reach the command (ADR-0261).

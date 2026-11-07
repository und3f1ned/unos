---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0079: The SQLite engine waits 15 s for locks and has an unbounded pool overflow

## Context

A share change holds SQLite's write lock while testparm checks the new smb.conf (ADR-0089), which can take longer than usual when a pool's disk has to spin up. Other writers waited sqlite3's default of 5 seconds and then failed with "database is locked". A request that holds `smb_lock` (ADR-0080) may need a second database connection while every request waiting for the lock holds one, and with a bounded pool it waited for a free connection and failed after the pool timeout.

## Decision

- The application's engine in `supervisor/src/database.py` is created with `engine_options(db_path)`: `connect_args={"timeout": 15}` (`BUSY_TIMEOUT_SECONDS`) and, for a database file, `max_overflow=-1`, a pool without a limit on extra connections. An in-memory database (tests) gets only the timeout.
- The staging order stays as it is: testparm still runs inside the transaction of a share change.
- Tests: `supervisor/tests/test_share_apply.py` (`TestDatabaseEngine`).

## Consequences

- Other writers wait up to 15 seconds for a share change instead of failing after 5.
- The number of open connections is bounded only by the number of concurrent requests.
- A testparm run longer than 15 seconds can still make another writer fail.

## Considered options

- Moving the staging and testparm out of the share change's transaction: not taken; the staging order stays.

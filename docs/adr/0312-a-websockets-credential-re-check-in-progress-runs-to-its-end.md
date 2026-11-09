---
status: accepted
date: 2026-11-09
area: auth
decided-by: agent
---

# ADR-0312: A WebSocket's credential re-check in progress runs to its end when the socket closes

## Context

While a socket is open, its token is checked again every 60 seconds (ADR-0031) by a task,
`_watch_credential` of `supervisor/src/websocket/events.py`, which the events socket and the
container logs socket cancel without waiting for it when they close (`_finish`). A cancellation
that lands while a check waits for its database query makes SQLAlchemy discard the connection and
close it in a task of its own, shielded, queued behind the query that aiosqlite's worker thread
still runs; when the task that waits for that close is cancelled again, SQLAlchemy stops the
worker thread at once, and if the close's own cancellation is handled after that stop, aiosqlite's
close waits for ever for the thread that has ended (aiosqlite 0.22.1, SQLAlchemy 2.1.8).
Starlette's TestClient makes that second cancellation, since its event loop's shutdown cancels
every task left when a `with client.websocket_connect(...)` block ends, and on 2026-11-09 it hung
`make check` in `supervisor/tests/test_ws_auth.py` (PROGRESS.md, Log); uvicorn waits for the
handlers' tasks before it cancels the rest, so in the supervisor only a close still queued behind
a long statement would meet it. Working on through the features under the owner's delegation
("work on through the features, ask no questions; I allow everything", in translation,
2026-11-09), the agent fixed the cause on UNOS's side.

## Decision

- `_watch_credential` runs each check in a task of its own and waits for it shielded from
  cancellation (`asyncio.shield`). A socket that closes ends the watch at once, as before, and a
  check in progress runs to its end: nothing waits for it, its outcome is dropped
  (`_consume_result`, so that a late error is not reported as never retrieved), and the module
  keeps a reference to it until it is done, since the event loop keeps only weak references to
  tasks. A watch cancelled in its pause between checks ends as before.
- Test: `supervisor/tests/test_ws_auth.py`,
  `test_a_socket_that_closes_lets_its_recheck_in_progress_run_to_its_end`: a re-check held in
  progress while the socket closes ends with the user, after the watch has ended; on the old
  code it ended with the cancellation.

## Consequences

- A check that started before its socket closed runs on after the close, usually for
  milliseconds; when its hourly commit of an API token's last use (ADR-0252) waits for SQLite's
  write lock, for up to the busy timeout of 15 seconds, holding a database connection. Its answer
  is not used: a socket that closed is not closed again.
- The handshake's check (`authenticate_websocket`) runs in the socket's handler itself, and other
  database work of the supervisor is not shielded: work cancelled in its query and then again in
  its close can still meet aiosqlite's fault, which stays in aiosqlite. A test that leaves
  `client.websocket_connect(...)` while the handshake's query runs gets that second cancellation
  from the TestClient, which cancels the handler and keeps cancelling it until it ends: tests wait
  for `auth_ok` or the close before they leave the block.

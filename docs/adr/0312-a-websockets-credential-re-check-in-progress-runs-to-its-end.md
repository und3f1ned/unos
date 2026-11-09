---
status: accepted
date: 2026-11-09
area: auth
decided-by: agent
---

# ADR-0312: A WebSocket's credential re-check in progress runs to its end when the socket closes

## Context

While a socket is open, its token is checked again every 60 seconds (ADR-0031): the events
socket and the container logs socket run the checks in a task, `_watch_credential` of
`supervisor/src/websocket/events.py`, and cancel that task without waiting for it when the socket
closes (`_finish`). A cancellation that lands while a check is in its database query ends the
query, and SQLAlchemy then discards the session's connection: it closes it in a task of its own,
shielded from the cancellation. When that close is cancelled as well, SQLAlchemy stops aiosqlite's
worker thread at once, and aiosqlite's own close, still running, then waits for ever for an
answer from the thread that has ended (aiosqlite 0.22.1, SQLAlchemy 2.1.8).

The tests meet the second cancellation: Starlette's TestClient ends its event loop when a
`with client.websocket_connect(...)` block ends and cancels every task left. On 2026-11-09
`make check` hung for more than two hours at the end of
`test_rechecks_of_an_api_token_do_not_commit_its_last_use_again` of
`supervisor/tests/test_ws_auth.py`, whose re-checks run every 0.1 seconds; run alone in a loop
that test hung after 106 and after 21 runs, and a dump of the asyncio tasks named the one left:
SQLAlchemy's close of the discarded connection, waiting in aiosqlite's close. In the supervisor,
the same cancellation discards a pooled connection, and the event loop's shutdown at a container
stop can hang the same way until Docker kills the container. Working on through the features
under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation, 2026-11-09), the agent fixed the cause on UNOS's side.

## Decision

- `_watch_credential` runs each check in a task of its own and waits for it shielded from
  cancellation (`asyncio.shield`). A socket that closes ends the watch at once, as before, and a
  check in progress runs to its end: nothing waits for it, its outcome is dropped
  (`_consume_result`), and the module keeps a reference to it until it is done, since the event
  loop keeps only weak references to tasks. A watch cancelled in its pause between checks ends as
  before.
- Test: `supervisor/tests/test_ws_auth.py`,
  `test_a_socket_that_closes_lets_its_recheck_in_progress_run_to_its_end`: a re-check held in
  progress while the socket closes ends with the user, after the watch has ended; on the old
  code it ended with the cancellation.

## Consequences

- A check that started before its socket closed takes its few milliseconds after the close, and
  its answer is not used: a socket that closed is not closed again.
- The handshake's check (`authenticate_websocket`) runs in the socket's handler itself, and other
  database work of the supervisor is not shielded: work cancelled in its query and then again in
  its close can still meet aiosqlite's fault, which this ADR leaves in aiosqlite.

---
status: accepted
date: 2026-01-25
area: architecture
decided-by: owner
---

# ADR-0006: Real-time data goes over WebSocket, never HTTP polling

## Context

The dashboard shows CPU, memory, network, disk I/O and process data that change every second, and container logs grow while they are watched. Polling HTTP endpoints with timers from every open page multiplies the requests and lets each page compute rates its own way. On 2026-01-25 the web UI moved to a WebSocket-first design.

## Decision

- Live data travels over the supervisor's WebSockets in `supervisor/src/websocket/events.py`: `/ws/v1/events` sends the system stats every 2 seconds (`STATS_INTERVAL_SECONDS`), with the rates computed on the server, and `/ws/v1/logs/{container_id}` streams a container's log.
- The web UI reads the stats from the store in `webui/src/lib/stores/stats.ts` and its derived stores, which keep one socket, authenticate it (ADR-0031) and reconnect with backoff. Pages do not poll `/api/v1/system/stats` with `setInterval` and do not compute rates themselves.
- A long operation answers when it is done instead of being polled: Repair permissions (`POST /api/v1/shares/smb/{name}/repair-permissions`) returns its result in the response.
- Tests: `supervisor/tests/test_ws_auth.py`, `webui/tests/websocket.spec.ts`.

## Consequences

- Live data has one channel, and rates are computed in one place.
- Not all of the web UI follows the rule yet: the container log view of `webui/src/lib/components/containers/ContainerDetailDrawer.svelte` refreshes over HTTP every 3 seconds instead of using `/ws/v1/logs`, and the scrub status on the Storage page, the auto-refresh of the Logs tab and the progress on the Updates tab poll HTTP endpoints with timers.

## Considered options

- HTTP polling with `setInterval`, as the Dashboard, the Monitor tab and the Network tab did before 2026-01-25: replaced by the socket.

---
status: accepted
date: 2026-11-10
area: security
decided-by: agent
---

# ADR-0333: The supervisor serves at most 128 open connections and 64 sockets, and Caddy refuses header blocks over 16 KB

## Context

Uvicorn accepted any number of connections, and Caddy, the only entry from the network (port 8080 is not published), took header blocks up to its default of 1 MB: idle or slow clients on the LAN could hold the file descriptors and the memory of the supervisor before any credentials were checked. Request bodies and WebSocket messages are limited already (ADR-0062).

## Decision

- Uvicorn runs with `--limit-concurrency 128` in `supervisor/Dockerfile` and `supervisor/Dockerfile.test`: while 128 connections are open it answers 503 to a new request, and serves again once they are gone. 128 leaves room for the live-data socket of every open tab and for Caddy's pooled connections to the supervisor.
- Uvicorn's limit never refuses a WebSocket upgrade, while every open socket counts toward it. So `src/websocket/events.py` holds at most 64 open sockets and 16 that wait for their auth message: past either, a socket is refused before it is accepted (HTTP 403), and sockets alone never fill the 128.
- Caddy's global `servers` block sets `max_header_size 16KB` (`webui/Caddyfile`): a larger header block gets 431. Caddy reads 16KB as 16,000 bytes, and Go adds 4,096 bytes of slack on HTTP/1.1, so a block up to about 20 KB still passes there. A browser's, with its session token, is under 2 KB. The stand-in's proxy (`scripts/standin/front.mjs`) has Node's default of 16 KB.
- The HTTP parser stays httptools (uvicorn's standard extras), so uploads keep the throughput they had.

## Consequences

- When the limit is reached, uvicorn answers 503 also to a new request on a connection that is already open; requests in progress and open WebSockets go on. 128 slow uploads, or idle connections held open, fill it too.
- The health checks of the compose file share the limit: while it is reached they fail, Docker marks the containers unhealthy (it restarts nothing for that), and an `update.sh` run then waits for health in vain and rolls back.
- A process in the host's network namespace that connects to port 8080 directly is not bound by Caddy's header limit; it is not a LAN client.

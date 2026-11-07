---
status: proposed
date: 2026-11-06
area: security
decided-by: agent
---

# ADR-0063: Limit uvicorn's request headers and connections after measuring the cost

## Context

The supervisor runs uvicorn with the httptools parser (`uvicorn[standard]` in `supervisor/requirements.txt`), which sets no limit on the size of a request's header block, and uvicorn sets no limit on the number of connections, so each unauthenticated connection can still make FastAPI hold up to 1 MiB of body (ADR-0062). Requests from the LAN pass through Caddy, which limits request headers to 1 MB, so only processes in the host's network namespace and on the compose network reach uvicorn's port directly.

## Decision

Proposed, not implemented:

- Limit the header block, either by running uvicorn with `--http h11`, after measuring the upload throughput of h11 on a 2.5 GbE link, or with a limit in front of uvicorn.
- Optionally limit concurrent connections (`--limit-concurrency`).

## Consequences

- Until then the header limit for clients on the LAN is Caddy's, local processes are not limited, and the number of request bodies held at the same time is not bounded.
- h11 is a pure-Python parser, so large uploads may be slower with it: hence the measurement first.

## Considered options

- `--http h11`: has a header limit; its cost on uploads is not measured yet.
- A limit in front of uvicorn: keeps httptools.
- `--limit-concurrency`: bounds the connections, not the size of one header block.

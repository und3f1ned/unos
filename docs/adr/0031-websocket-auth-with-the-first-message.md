---
status: accepted
date: 2026-11-05
area: auth
decided-by: agent
---

# ADR-0031: WebSocket clients authenticate with their first message, never with a token in the URL

## Context

The web UI put its JWT into the WebSocket URL (`?token=`), so the token ended up in the uvicorn access log, which the container logs exposed. An expired session looked like empty data instead of leading to the login page.

## Decision

- A client connects without query parameters and sends `{"type": "auth", "token": "<JWT or API token>"}` as its first message, within 10 seconds; the server answers `{"type": "auth_ok"}` (`authenticate_websocket` in `supervisor/src/websocket/events.py`).
- A `?token=` query parameter is refused with close code 4401, and so is a first message that is missing, late, malformed or carries an invalid token.
- The token is checked like an HTTP Bearer token (signature, expiry, the API token's state, the user still existing) at the handshake and again every 60 seconds. When it stops being valid the socket closes with 4401; a logs socket whose user is no longer an administrator closes with 4403.
- The container log stream `/ws/v1/logs/{container_id}` requires an administrator (4403 otherwise).
- The web UI (`webui/src/lib/stores/stats.ts`) sends the token first, ends an expired session once (on 401 or 4401) and reconnects the stats socket with exponential backoff and jitter.
- Tests: `supervisor/tests/test_ws_auth.py`, `webui/tests/websocket.spec.ts`.

## Consequences

- Tokens never appear in request URLs, so the access logs of uvicorn and Caddy hold none.
- The first message is read before the client is authenticated, so its size is limited before authentication (ADR-0062).

## Considered options

- A cookie for the WebSocket, as the audit suggested: it would need CSRF protection, which the Bearer-only API does not need (ADR-0009).

---
status: accepted
date: 2026-11-05
area: deploy
decided-by: agent
---

# ADR-0048: A fixed compose subnet lets uvicorn trust X-Forwarded-For only from the web UI

## Context

Every request reaches the supervisor through Caddy in the web UI container, so without proxy headers all clients had Caddy's address: one login rate-limit bucket for the whole family, so that one attacker could lock everybody out, and useless addresses in the login history and the audit log. Trusting `X-Forwarded-For` from anyone, on the other hand, would let a client choose its own address.

## Decision

- The compose network `unos-net` has the fixed subnet 172.31.254.0/24 (gateway .1) and fixed addresses: the web UI 172.31.254.2, the supervisor 172.31.254.3 (`deploy/docker-compose.yml`).
- uvicorn runs with `--proxy-headers` (`supervisor/Dockerfile`) and takes the client address from `X-Forwarded-For` only on connections from `FORWARDED_ALLOW_IPS=172.31.254.2`, the web UI container. Caddy replaces a client's own `X-Forwarded-For` with the real peer address.
- The supervisor's port 8080 is not published, and Caddy proxies only `/api` and `/ws` to it.
- Tests: `supervisor/tests/test_authz.py` (`TestRealClientAddress`).

## Consequences

- The login rate limit (ADR-0010), the login history and the audit log see the real client.
- On a host whose networks already use 172.31.254.0/24, every 172.31.254.x address in the compose file must change together, and the change must be committed, because `update.sh` refuses local changes (ADR-0049).
- Changing the network of an existing stack needs `docker compose down` once before `up -d`.

## Considered options

- `--forwarded-allow-ips *`, or the whole subnet: refused, because processes in the host's network namespace, host-network apps included, can connect to port 8080 directly from the gateway address and would then choose their own address and rate-limit bucket.

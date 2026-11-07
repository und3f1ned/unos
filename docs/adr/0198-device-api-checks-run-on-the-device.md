---
status: accepted
date: 2026-11-07
area: device
decided-by: agent
---

# ADR-0198: API checks of the device run on the device itself, over https://127.0.0.1

## Context

On 2026-11-07 the workstation's command-line tools (curl, python, nc) got no data back from the device's web ports, although the TCP connection was made; the likely cause is a network filter or a VPN extension on the workstation. SSH to the device kept working.

## Decision

- Device checks make their API calls on the device itself, against `https://127.0.0.1/api/v1`, through Caddy as a browser's request would go.
- `scripts/dev/api.py METHOD PATH [-] [--param k=v]` starts a short Python relay on the device through `scripts/dev/ssh.sh`. The request, the administrator's login (from `dev.env`) and the answer travel on SSH's standard input and output, never on a command line (ADR-0212), and the printed answer has its secrets redacted.
- Every login writes to the login history and the audit log on the device's system disk and counts against the login limit (ADR-0010), so the relay keeps the token in the device's RAM (`/dev/shm`, mode 600) and signs in again only when the API refuses it (ADR-0222).
- Tests: the tests of `api.py` in `supervisor/tests/test_dev_scripts.py` (secrets on standard input, redaction, HTTP and relay errors).

## Consequences

- API checks depend on SSH, and they test the API from inside the device, not over the LAN.
- The workstation's network filter is not worked around (ADR-0211).

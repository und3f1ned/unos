---
status: proposed
date: 2026-11-05
area: process
decided-by: agent
---

# ADR-0015: Carry the Phase 0 follow-ups into Phase 1

## Context

The Phase 0 pull request, merged on 2026-11-05, closed the audit's blockers and named the follow-ups that were outside its scope.

## Decision

Proposed: carry these follow-ups into Phase 1, and decide each one when its work is designed:

- a host command layer for network, logs and power; smartctl also still runs inside the supervisor container (ADR-0020);
- scopes for API tokens, and session revocation (ADR-0033);
- a time zone for the scheduler: the container runs in UTC, and schedules and times in the UI show no zone (ADR-0023);
- certificate edge cases;
- base images pinned by digest: `supervisor/Dockerfile` and `webui/Dockerfile` use the tags `python:3.11-slim`, `node:22-alpine` and `caddy:2-alpine`.

Done since: download, copy and move in the file manager, and Node.js 22 instead of the end-of-life Node 20 (slice 1, ADR-0058); SMB users with guest access off by default (slice 2, ADR-0059).

## Consequences

- The open items are `feature-list.json` entries that name this ADR (ADR-0203): `architecture.host-command-layer` (with the entries of the actions it serves, such as `system.network`, `system.power` and `system.logs-viewer`), `auth.api-token-scopes`, `auth.session-revocation`, `system.scheduler-time-zone` and `deploy.digest-pinned-base-images`, except the certificate edge cases, which get an entry when their work is designed.

---
status: superseded
date: 2026-11-07
area: auth
decided-by: agent
superseded-by: ADR-0252
---

# ADR-0191: Throttle the commit of an API token's last use

## Context

Superseded by ADR-0252: the last use is committed at most once an hour.

Every request authenticated with an API token sets the token's `last_used_at` and commits it to the database (`verify_api_token` in `supervisor/src/auth/utils.py`). The database is on the system disk, which cannot be replaced (ADR-0214), so an automation that calls the API every few seconds writes to it request by request. This was left out of the SMB slice (ADR-0164).

## Decision

Proposed: commit `last_used_at` only when the stored value is older than a fixed interval, for example an hour, and keep answering the request either way.

## Consequences

- The token list shows the last use only to within that interval.
- An automation then costs at most one database write per token and interval.
- Sessions of the web UI are not affected: they use JWTs, which `authenticate_token` checks with a read only.

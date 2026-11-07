---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0075: Each share grants each user none, read or write, and new users get no access

## Context

The goal of slice 2 is SMB access for UNOS users with read and write permissions per share and safe defaults (ADR-0059): no user should get access that nobody granted.

## Decision

- Each share stores an explicit level per UNOS user: a row of `smb_share_access` with `read` or `write` (a CHECK constraint), or no row, which means no access (`supervisor/src/shares/models.py`). The API takes the complete list as `access` entries of user id, username and level (`supervisor/src/shares/schemas.py`).
- A new user has no access to any share until an administrator grants it.
- An entry counts only while the user's SMB account is active (allowed by an administrator and turned on with a password, ADR-0118): only active accounts are rendered into `valid users` and `write list` (`build_render_input` in `supervisor/src/shares/service.py`).
- The Shares page shows the entries that do not count struck through, with the reason as text (`webui/src/lib/components/shares/ShareCard.svelte`).
- Tests: `supervisor/tests/test_share_api.py`, `supervisor/tests/test_share_apply.py`, `webui/tests/shares.spec.ts`.

## Consequences

- Access is granted user by user; groups are not principals yet (ADR-0076).
- An entry survives while the account is off, so turning SMB on again restores the access that was granted.
- Guests are a separate switch of the share and can only read (ADR-0096).

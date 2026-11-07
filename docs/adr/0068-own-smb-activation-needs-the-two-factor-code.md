---
status: accepted
date: 2026-11-06
area: auth
decided-by: agent
---

# ADR-0068: Turning on one's own SMB access needs the two-factor code

## Context

A regular user turns SMB on with the current UNOS password on the Account page (ADR-0174). SMB has no second factor and no login rate limit (ADR-0102), so turning it on opens a permanent channel that needs only the password. For a user with 2FA, the password alone must not be enough to open it.

## Decision

- `POST /api/v1/auth/me/smb/enable` in `supervisor/src/auth/router.py` requires `totp_code` when the user has 2FA turned on: 400 "Two-factor code required" when it is missing, 400 "Invalid two-factor code" when it is wrong. The code is checked like a login's, single-use (ADR-0067), after the current password, on the user as reloaded under `smb_lock` (ADR-0081).
- `GET /api/v1/auth/me/smb` answers `totp_required`, and the SMB card of the Account page (`webui/src/lib/components/account/SmbAccessCard.svelte`) shows a code field exactly when it is true; the page refreshes the card after 2FA is turned on or off.
- The endpoint is also rate limited (5 requests per minute).
- Tests: `supervisor/tests/test_smb_accounts.py`, `supervisor/tests/test_totp_once.py`, `webui/tests/account-smb.spec.ts`.

## Consequences

- Turning SMB on needs what the web login needs.
- The code of the login cannot be reused for it within the same 30 seconds: the user waits for the next code.
- An administrator's activation of a user's SMB access (with a password the administrator sets) asks for no code of the user.

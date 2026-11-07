---
status: accepted
date: 2026-11-06
area: webui
decided-by: agent
---

# ADR-0174: Regular users manage their own SMB access on the Account page

## Context

SMB is opt-in per user: an administrator allows it, and the account becomes active only when Samba gets the user's current password (ADR-0118). Turning it on oneself keeps the password with its user, so users need a place to turn their access on and off and to see which shares they can use. The card must also stay correct when two-factor authentication, the allowance or the password rules change while the page is open.

## Decision

- The Account page (System > Security, labelled Account in the navigation) shows `SmbAccessCard` (`webui/src/lib/components/account/SmbAccessCard.svelte`) after Change Password, in `webui/src/routes/system/tabs/SecurityTab.svelte`. It reads `GET /api/v1/auth/me/smb`: the state of the account, why SMB is unavailable or how to get it, the shares the user can use with their level, and how to connect.
- "Turn on SMB" sends the current password, plus a two-factor code when the user has 2FA (`POST /api/v1/auth/me/smb/enable`, ADR-0068); the login's code cannot be used again (ADR-0067). "Turn off SMB" asks for confirmation (`POST /api/v1/auth/me/smb/disable`).
- `refresh()` reads the state again after 2FA is turned on or off above the card and after any failed "Turn on SMB", so that the code field follows the server. The error alert "SMB was not turned on" sits outside the form and stays when the state changed meanwhile, for example when the allowance was withdrawn.
- After a refusal whose detail starts with "SMB passwords must", the card adds: "Change your password above (Change Password) so that it meets this rule, then turn SMB on." Change Password shows the warnings of its answer until they are dismissed.
- The server's texts (the refusal while UNOS's smb.conf is not installed, ADR-0130, and the fixed texts of ADR-0131) are shown as worded.
- Tests: `webui/tests/account-smb.spec.ts`.

## Consequences

- The password reaches Samba only from its owner or from an administrator who sets it; plain logins never set it (ADR-0119).
- Users with 2FA wait for the next code after signing in before they can turn SMB on.
- The hint depends on the server's wording: the texts of `smb_password_problem` in `supervisor/src/shares/constants.py` all start with "SMB passwords must", and the backend tests pin them.

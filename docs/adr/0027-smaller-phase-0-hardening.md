---
status: accepted
date: 2026-11-05
area: security
decided-by: agent
---

# ADR-0027: QR codes for 2FA are drawn locally, logs show as text, and token lifetimes are bounded

## Context

Next to the big holes, the audit's review of HTTPS and sessions found smaller ones: the 2FA setup sent the TOTP secret to an outside service (`api.qrserver.com`) to draw the QR code, the log viewer rendered log text as HTML (an XSS hole), and a very long API token lifetime caused a server error.

## Decision

- The 2FA QR code is drawn in the browser with the `qrcode` package (`webui/src/routes/system/tabs/SecurityTab.svelte`); the TOTP secret goes to no outside service.
- Log text is shown as text, never as HTML; the web UI uses no `{@html}`.
- An API token's lifetime is 1 to 3650 days, or no expiry when none is given (`expires_in_days` in `supervisor/src/auth/schemas.py`).
- Tests: `webui/tests/roles.spec.ts` (the QR code is drawn in the browser), `supervisor/tests/test_authz.py` (`TestApiTokenExpiry`).

## Consequences

- The Content Security Policy (ADR-0025) allows images only from the page itself and from `data:` and `blob:` URLs, so an outside QR service would not work anyway.

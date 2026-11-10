---
status: accepted
date: 2026-11-05
area: security
decided-by: agent
---

# ADR-0025: The web UI is served over HTTPS from Caddy's local CA, with a CSP and without HSTS

## Context

In 1.0.0 Caddy listened only on port 80: passwords, tokens and TOTP codes crossed the network in clear text, and port 443 was published with nothing answering TLS on it. A NAS on a home network has no public name that a public CA could certify, and users reach it by IP address or a local name.

## Decision

- Caddy serves the UI and the API over HTTPS on port 443 with certificates from its own local CA (`local_certs`, `tls internal`) for `UNOS_HOSTNAME` (default `unos`); clients that connect by IP address or by another name get that certificate too (`webui/Caddyfile`).
- Plain HTTP on port 80 only redirects to HTTPS (308), except `/api/v1/health`, which health checks and scripts call over HTTP.
- Every HTTPS response carries a Content Security Policy, besides the hash-based policy that SvelteKit writes for its inline bootstrap script (`kit.csp` in `webui/svelte.config.js`), and `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and a restrictive `Permissions-Policy`; the redirect and the health check on port 80 carry none of these headers.
- There is no `Strict-Transport-Security` header.
- Tests: `webui/tests/https.spec.ts`; `supervisor/tests/test_standin.py` keeps the local stand-in's headers equal to the Caddyfile's.

## Consequences

- Browsers warn about the certificate until the local CA's root certificate, kept in the `caddy-data` volume, is trusted on the client; scripts must use `https://` and trust that CA or skip the check.
- Without HSTS a user can always click through the certificate warning, also after the CA is recreated (a lost volume, a reinstall), which HSTS would turn into a lockout; the price is HSTS's protection against a downgrade to plain HTTP.

## Considered options

- HSTS: rejected, because browsers do not let users past a certificate error on an HSTS host, so a recreated local CA would lock every browser out until its HSTS entry expires.
- A certificate from a public CA: the device has no public name.

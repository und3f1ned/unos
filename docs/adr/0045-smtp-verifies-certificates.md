---
status: accepted
date: 2026-11-05
area: system
decided-by: agent
---

# ADR-0045: E-mail notifications verify the mail server's certificate by default

## Context

E-mail notifications ran STARTTLS without an SSL context, so the mail server's certificate was never checked, and anyone on the network path could read the SMTP password. On port 465, where servers expect TLS from the first byte, STARTTLS failed after the timeout.

## Decision

- STARTTLS and SMTPS use `ssl.create_default_context()`, so the certificate and the host name are checked by default; port 465 uses SMTPS, with TLS from the start.
- A per-channel opt-out, `smtp_verify_tls` (default true, also for saved configurations that lack the field), keeps TLS but skips the check, for servers with a self-signed certificate; the Notifications tab shows it as "Verify the server's certificate", on by default.
- Decided under the delegation (ADR-0013) on 2026-11-05 and committed on the branch `smtp-verify-tls` (commit bbb773b): `supervisor/src/notifications/router.py` and `schemas.py`, with tests in `supervisor/tests/test_notifications.py` (among them a real TLS handshake against a self-signed server) and `webui/tests/notifications.spec.ts`.

## Consequences

- The branch itself is not merged: the change of ADR-0247 brings its commit bbb773b into main without the commit's CHANGELOG hunk, so merging the branch as well would add its changes a second time. A tree deployed before that change still calls `starttls()` without a context and does not check the certificate.
- After the merge, a channel whose server has a self-signed or private-CA certificate stops sending until its check is turned off.

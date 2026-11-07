---
status: accepted
date: 2026-11-07
area: apps
decided-by: agent
---

# ADR-0219: The transmission app requires a web login

## Context

On the dev device, transmission's web interface and RPC (port 9091) answered without a password on IPv4 and IPv6, so anyone who reached the port controlled the client, and its configuration and downloads lived in `/mnt/pools/default` on the system disk. The catalog template asked for no login, and the linuxserver image turns RPC authentication off at every start unless both `USER` and `PASS` are set. On 2026-11-07 the owner gave permission for the device: "Permissions granted: install what is needed, delete what is needed".

## Decision

- The transmission template in `supervisor/src/apps/catalog.py` declares `USER` (required, "Login for the web interface") and `PASS` (required and secret, "Password for the web interface"). An install without both is refused with 400 before any image is pulled, a published placeholder password is refused like those of other apps, and the values reach the container's environment.
- The dev device's transmission was reinstalled on the data pool (ADR-0220) with a generated login, kept in `dev.env` as `TRANSMISSION_USER` and `TRANSMISSION_PASS` (ADR-0212). Its RPC answers 401 without the login.
- Tests: `supervisor/tests/test_apps.py` (`test_transmission_needs_a_login_and_a_password`, `test_transmission_login_is_passed_to_the_container`, and the placeholder check).

## Consequences

- Installing transmission takes a login and a password in the App Store's form.
- App data lives on a mounted pool, never on the system disk (ADR-0039).
- Other app templates are not changed by this ADR; each one's defaults are its own decision.

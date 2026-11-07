---
status: proposed
date: 2026-11-05
area: system
decided-by: agent
---

# ADR-0046: Deliver Telegram alerts over Docker IPv6, or send them by e-mail

## Context

From the dev device's network, IPv4 traffic to Telegram's API (`api.telegram.org`) does not get through, and the UNOS containers have no IPv6, so Telegram alerts cannot be delivered from the device. A change of the Docker network was outside Phase 0's scope.

## Decision

Proposed, to be decided in Phase 1 together with the alert design (ADR-0047): either enable IPv6 for the UNOS compose network, so that the supervisor reaches Telegram over IPv6, or send the alerts by e-mail instead.

## Consequences

- Until then a Telegram channel on the device fails, and the supervisor logs each failed alert.
- Enabling IPv6 changes the compose network whose fixed addresses the trusted-proxy setting depends on (ADR-0048).

---
status: superseded
date: 2026-11-07
area: device
decided-by: agent
superseded-by: ADR-0301
---

# ADR-0227: No inbound IPv6 filter on the dev device for now; each service protects itself

## Context

Superseded by ADR-0301: at the owner's request the dev device admits inbound IPv6 to its services only from the LAN.

The dev device has global IPv6 addresses from a Hurricane Electric tunnel prefix, and its services listen on every IPv6 address: SSH (22), the web UI (80, 443), Samba (139, 445), transmission (9091, 51413) and LLMNR (5355). The device itself filters nothing (ip6tables INPUT policy ACCEPT). Whether the router filters inbound IPv6 could not be checked from here: there is no access to the router and no outside host to probe from. Checking the router's inbound IPv6 filter was an owner action (ADR-0024, ADR-0095).

## Decision

- No inbound IPv6 filter is added on the device for now: a filter could cut off remote IPv6 access that the owner may use through the tunnel.
- Each service protects itself instead:
  - SSH takes keys only (ADR-0226).
  - The web UI and transmission require their new passwords (ADR-0219, ADR-0226).
  - Samba admits only private, unique-local and link-local addresses (`hosts allow` of ADR-0095), so it refuses the global ones.
  - The BitTorrent port 51413 is meant to be public.
- The feature-list entry `device.ipv6-inbound-filter` holds the filter as an option for the owner: allow LAN, unique-local and link-local sources to the service ports, keep 51413 open, and take Docker's published ports into account.

## Consequences

- If the router passes inbound IPv6, the web UI's and transmission's sign-in pages are reachable from outside, behind their passwords and the login rate limit (ADR-0010).
- A check from outside (for example `curl -6` to the device's address from another network) settles whether the router filters.

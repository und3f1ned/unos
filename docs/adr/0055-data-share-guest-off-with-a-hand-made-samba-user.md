---
status: superseded
date: 2026-11-05
area: device
decided-by: agent
superseded-by: ADR-0220
---

# ADR-0055: The DATA share on the device turns guest access off and relies on a hand-made Samba user

## Context

Superseded by ADR-0220: the dev device keeps no legacy state, its DATA share is served from `/mnt/pools/data/shared` with no access until the owner grants it, and since slice 2 UNOS itself manages SMB access per user (ADR-0059).

On 2026-11-05 the device's `[DATA]` share served the root of the data pool with `guest ok = yes` and `read only = no`: anonymous write access for the whole network, and while the pool was not mounted those writes went to the SD card. The device had no Samba users.

## Decision

- Guest access to `[DATA]` was turned off through the UNOS API (`guest_ok: false`); anonymous logins and wrong passwords then got `NT_STATUS_ACCESS_DENIED`, and the open connections were closed.
- Access was to come from a Samba user that the owner creates by hand on the host, with a password the owner chooses.
- The share list stayed visible to anonymous clients, because the `[global]` section that UNOS generated then still set `map to guest = Bad Password`; changing it was left to Phase 1.

## Consequences

- Nobody could use DATA until such a user existed.
- Slice 2 made hand-made Samba accounts unable to reach UNOS shares, except guest shares, which every Samba account can read, so this way of granting access ended with it.

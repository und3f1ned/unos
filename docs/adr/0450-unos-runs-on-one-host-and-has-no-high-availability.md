---
status: accepted
date: 2026-11-22
area: architecture
decided-by: agent
---

# ADR-0450: UNOS runs on one host and has no high availability

## Context

The feature-list entry `architecture.high-availability` asked that two UNOS hosts share one
configuration and that one take over the shares and apps when the other fails; its notes said that
it needs an ADR first and is out of scope while UNOS has one dev device. UNOS turns one existing
host into a NAS (ADR-0002): its pools are that host's disks, its database is one SQLite file
(ADR-0306) and it changes that host alone (ADR-0020). The owner delegated the decision
("decide everything that can be decided without me; do it", 2026-11-22).

## Decision

- UNOS is a NAS of one host: no cluster, no configuration shared between hosts and no failover.
- A host that fails is repaired or replaced, and the replacement gets its data back from what
  UNOS already has: its pools' disks, imported on the new host (ADR-0283), the backup of the
  database and settings, and the off-device copies.
- The entry `architecture.high-availability` is removed from `feature-list.json`.

## Consequences

- A failed host means downtime until it is repaired or replaced and restored.
- A takeover would need the pools on shared or replicated storage, a quorum, fencing and an
  address that moves, none of which one dev device can test; a later request comes as a new entry
  and a new ADR.

## Considered options

- Active and passive hosts with btrfs send and receive between their pools and an address that
  moves (keepalived): the second host's copy lags behind by one send, SMB sessions and the apps'
  containers start again on the other host, and a split brain needs fencing that UNOS does not
  have.
- Two hosts on one shared block device: btrfs is no cluster filesystem, so only one host may
  mount it at a time, and the shared device stays the single point of failure.

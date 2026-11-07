---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0127
---

# ADR-0126: Accounts without a password get disabled passdb entries

## Context

Superseded by ADR-0127: an account that is not active has no passdb entry at all.

An SMB account can exist before Samba has its password (allowed, "password needed"), and a
restore can bring back accounts whose passwords Samba may not hold.

## Decision

The first design of slice 2 created accounts without a password as disabled passdb entries
("password needed") and, after a restore, disabled every SMB account in passdb.

## Consequences

A disabled entry keeps a stale password equivalent, the NT hash, on the system disk, and
`smbpasswd -e` would revive it; ADR-0127 deletes the entry instead.

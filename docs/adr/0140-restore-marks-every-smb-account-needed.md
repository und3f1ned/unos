---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0141
---

# ADR-0140: After a restore every SMB account needs its password again

## Context

Superseded by ADR-0141: an account stays active after a restore when its password is proven.

Samba keeps its passdb outside the database, which a restore replaces, so the restored
bcrypt hashes may not match the passwords Samba holds.

## Decision

The first design of slice 2 marked every SMB account "password needed" after a restore and
disabled it in passdb.

## Consequences

A restore made only to roll back shares would have logged every user out of SMB, although
equal bcrypt strings prove that Samba holds the restored password; ADR-0141 replaced the
rule.

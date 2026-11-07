---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0098
---

# ADR-0097: A guest share sets restrict anonymous = 0 and map to guest = Bad User

## Context

Superseded by ADR-0098: with a guest share, `restrict anonymous` is 1, not 0.

Guest shares need `[global]` settings that let a client without a password in, and the rendered `[global]` (ADR-0087) should keep anonymous access closed whenever no guest share needs it.

## Decision

The first design of slice 2 rendered `map to guest = Never` and `restrict anonymous = 2` unless a guest share existed, and `map to guest = Bad User` with `restrict anonymous = 0` while at least one guest share existed. No reason for the value 0 was recorded.

## Consequences

- With `restrict anonymous = 0`, anyone on the network could list the account names through an anonymous connection, which helps password guessing against SMB, which has no login limit (ADR-0102). ADR-0098 keeps that list closed with the value 1.

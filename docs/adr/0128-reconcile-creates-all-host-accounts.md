---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0129
---

# ADR-0128: The reconcile creates host accounts for every SMB-enabled user

## Context

Superseded by ADR-0129: host accounts are created at the first activation.

Every SMB account needs a host account (ADR-0105), and the first design of slice 2 had to
decide when the startup and post-restore reconcile create the host accounts of users whose
SMB was allowed.

## Decision

After a restore and at every start, the reconcile created the missing host accounts of every
SMB-enabled user, without a password (disabled in passdb, "password needed").

## Consequences

Such accounts cannot sign in, and creating them only writes /etc on the system disk;
ADR-0129 replaced the rule.

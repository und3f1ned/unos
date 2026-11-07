---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0121
---

# ADR-0120: The SMB password is set wherever the plaintext is available

## Context

Superseded by ADR-0121: a password change reaches Samba only for active accounts.

SMB accounts use the UNOS password (ADR-0119), and Samba can get it only from a request that
carries the plaintext.

## Decision

The first design of slice 2 set the SMB password wherever the plaintext was available: at
user creation, at every administrator password reset, at every self password change, and at
an explicit "enable SMB" that asked for the password.

## Consequences

A password change would have turned SMB on again for a user who had turned it off, which is
why ADR-0121 replaced the rule.

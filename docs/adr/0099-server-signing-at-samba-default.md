---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0100
---

# ADR-0099: Server signing stays at Samba's default

## Context

Superseded by ADR-0100: server signing is mandatory unless a guest share exists.

The hardened `[global]` (ADR-0087) had to choose values for SMB signing and encryption.

## Decision

The first design of slice 2 left `server signing` and encryption at Samba's defaults ("server signing/encryption default").

## Consequences

- With signing at its default, clients may connect without signing, which leaves NTLM relay possible, although every SMB2 or newer client can sign. ADR-0100 makes signing mandatory whenever no guest share exists.
- Encryption stays at its default (`server smb encrypt = default`).

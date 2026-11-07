---
status: superseded
date: 2026-11-07
area: shares
decided-by: agent
superseded-by: ADR-0249
---

# ADR-0195: Handle the SMB follow-ups left open after slice 2

## Context

Superseded by ADR-0249: the follow-ups still open, without the two that ADR-0247 and ADR-0248 decided.

Phase 1 slice 2 delivered per-user SMB access (ADR-0059) and left known gaps, smaller design items and checks that could not be run from the workstation. They are recorded here so that later slices pick them up instead of finding them again; two of them, the SAMR password change and the single copy of `smb.conf.unos-replaced`, are also among the follow-ups of ADR-0164.

## Decision

Proposed, not implemented:

- SMB users can change their own Samba password through Samba's password-change protocol (SAMR). That bypasses the 8-character rule of ADR-0122 and the Account page, and UNOS's password hash no longer matches Samba's until the next UNOS password change.
- A crafted backup archive can hold two SMB account rows with one uid; the second activation is then refused while the first account exists.
- A foreign archive can still give a restored account the uid of a user deleted before the backup, because such uids are not in the restore's snapshot (ADR-0112); an installation id in `smb_state` would close that.
- `GET /api/v1/shares/smb-status` names no reason when smb.conf was replaced after a successful apply (activations do answer 409 with the reason, ADR-0130).
- Unmounting or forgetting a pool does not apply smb.conf again: its shares keep their sections until the next change or start (the mount point is empty and immutable, ADR-0034).
- `smb.conf.unos-replaced` keeps only the latest copy (ADR-0092); a second event overwrites the first one's text.
- Not yet checked for real: `systemctl try-restart smbd` ending sessions at uninstall (ADR-0180), the testparm tolerance on Samba older than 4.22 (ADR-0091), the ipv6-literal.net hint on a Windows client (ADR-0196), and a share folder of another account inside a sticky folder (ADR-0193).
- Later product items: groups as share principals (ADR-0076), discovery with WS-Discovery and Bonjour (ADR-0158), binding Samba to interfaces, per-share encryption, Time Machine and Previous Versions (ADR-0161).

## Consequences

- Until the SAMR item is fixed, a user can give their SMB access a password shorter than UNOS allows.
- Each fix needs a test that fails on the code before it (ADR-0186).

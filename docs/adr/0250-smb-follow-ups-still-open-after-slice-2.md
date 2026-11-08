---
status: superseded
date: 2026-11-07
area: shares
decided-by: agent
supersedes: [ADR-0195]
superseded-by: ADR-0262
---

# ADR-0250: Handle the SMB follow-ups still open after slice 2

## Context

Superseded by ADR-0262: the follow-ups still open, without network discovery, which ADR-0261 decided.

ADR-0195 recorded the known gaps, smaller design items and unchecked points that phase 1 slice 2
(ADR-0059) left open, so that later slices pick them up instead of finding them again. Two of them
are decided since, by ADR-0248 and ADR-0249, and this ADR restates the others. Two of those, the
SAMR password change and the single copy of `smb.conf.unos-replaced`, are also among the follow-ups
of ADR-0164.

## Decision

Proposed, not implemented:

- SMB users can change their own Samba password through Samba's password-change protocol (SAMR).
  That bypasses the 8-character rule of ADR-0122 and the Account page, and UNOS's password hash no
  longer matches Samba's until the next UNOS password change
  (`shares.password-changes-only-through-unos`).
- A crafted backup archive can hold two SMB account rows with one uid; the second activation is
  then refused while the first account exists (`shares.restore-uid-guards`).
- A foreign archive can still give a restored account the uid of a user deleted before the
  backup, because such uids are not in the restore's snapshot (ADR-0112); an installation id in
  `smb_state` would close that (`shares.restore-uid-guards`).
- `smb.conf.unos-replaced` keeps only the latest copy (ADR-0092); a second event overwrites the
  first one's text (`shares.keep-every-replaced-copy`).
- Not yet checked for real: `systemctl try-restart smbd` ending sessions at uninstall (ADR-0180),
  the testparm tolerance on Samba older than 4.22 (ADR-0091), the ipv6-literal.net hint on a
  Windows client (ADR-0196), and a share folder of another account inside a sticky folder
  (ADR-0193) (`testing.smb-real-world-checks`).
- Later product items: groups as share principals (ADR-0076), discovery with WS-Discovery and
  Bonjour (ADR-0158), binding Samba to interfaces, per-share encryption, Time Machine and Previous
  Versions (ADR-0161).

Decided since ADR-0195, and no longer open:

- `GET /api/v1/shares/smb-status` names an smb.conf that was replaced or removed after UNOS wrote
  it (ADR-0249, `shares.status-names-foreign-smb-conf`).
- Creating, mounting, unmounting and forgetting a pool apply smb.conf again, so the shares of an
  unmounted or forgotten pool leave it at once (ADR-0248, `shares.reapply-on-pool-unmount`).

## Consequences

- Until the SAMR item is fixed, a user can give their SMB access a password shorter than UNOS
  allows.
- Each fix needs a test that fails on the code before it (ADR-0186).
- The ADR that decides one of these items supersedes this one and restates the items still open,
  as this one does for ADR-0195.

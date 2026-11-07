---
status: superseded
date: 2026-11-06
area: deploy
decided-by: agent
superseded-by: ADR-0207
---

# ADR-0181: Rolling back to the release before SMB accounts is documented, not engineered

## Context

Superseded by ADR-0207: UNOS keeps no backward compatibility, so there is no downgrade guidance any more.

`deploy/update.sh` rolls back to the previous release when the new one does not become healthy (ADR-0049), and an administrator can start the previous images by hand. The release before the SMB slice knows nothing about the SMB tables or the Samba accounts that UNOS manages: it re-renders shares without the new access lines, and the shares it creates are dropped at the next upgrade.

## Decision

Rollback was documented, not engineered: the upgrade notes of `CHANGELOG.md` ("Downgrading and automatic rollback") and the update chapter of `docs/INSTALL.md` said that

- after an automatic rollback or a manual downgrade the generated smb.conf stays, and the older release shows only the shares that this file serves (none while a pool-root share such as DATA has no folder);
- users, shares and backups must not be deleted, edited or restored in the older release until the newer one is installed again: shares created there are dropped at the next upgrade, kept in `smb.conf.unos-replaced` and listed on the Shares page until UNOS restarts;
- copying `smb.conf.unos-import-backup` back is safe only if no account had SMB turned on under the newer release, or after `pdbedit -x -u NAME` for each such account, because the old file may give every Samba account access to its shares (for example write access to a whole pool) and brings back the old, unhardened `[global]`.

## Consequences

- No code supported a downgrade; the safety of a rollback depended on the administrator reading and following the notes.
- The older release re-renders the shares without the newer access lines, so the notes also had to say which state of the host keeps a share without access closed there.

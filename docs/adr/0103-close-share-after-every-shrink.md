---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0103: Open connections to a share are closed after every shrink of its access

## Context

Samba checks a user's access to a share when the client connects to it (a tree connect), and an open connection keeps the access it was granted after a reload. `smbcontrol smbd close-denied-share` compares only the connection's cached access with the user lists and looks neither at `available` nor at `guest ok` (checked in the Samba 4.22.6 source): when a share loses its last principal, everyone stays connected, writers included.

## Decision

- After the reload (ADR-0090), UNOS runs `smbcontrol smbd close-share <name>` for every share whose grants shrank between the old and the new smb.conf (`shrunk_shares` in `supervisor/src/shares/smbconf.py`): a share removed, renamed (the old name), moved to another folder, made unavailable or no longer served; a user or guests who lost read or write access; an unknown section whose text changed.
- Additions close nothing: a new principal, read raised to write, guest access turned on.
- A name passed to smbcontrol must fullmatch the share-name rule (ADR-0038); other names, for example from a hand-edited file, are skipped with a warning (`_close_share` in `supervisor/src/shares/smb.py`).
- `close-denied-share` is never used. A failed close stays pending and is retried at the next apply (ADR-0090).
- The Shares page (`shares.form.accessChangeNote` in `webui/src/lib/i18n/catalog/shares.ts`) and `docs/USER_GUIDE.md` say that removing or lowering access, or turning guest access off, disconnects the open connections to that share, that clients reconnect, and that adding access disconnects nobody.
- Tests: `supervisor/tests/test_smbconf.py` (`TestShrunkShares`), `supervisor/tests/test_share_apply.py` (`TestClosedShares`, `TestReloadAndClose`).

## Consequences

- close-share ends every tree connect of the share, also of users who keep their access; their clients reconnect under the new file.
- A session itself stays open and can connect to the shares still granted; ending the sessions of an account is ADR-0104.

## Considered options

- `close-denied-share`, as the first design used it: rejected, because it leaves every connection open when a share loses its last principal, and its stated reason was wrong in mechanism.
- Keeping `close-denied-share` for sections with `valid users`: rejected for one rule for every shrink.

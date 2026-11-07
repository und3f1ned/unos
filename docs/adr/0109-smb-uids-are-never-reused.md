---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0109: SMB uids are never handed out twice, also across reinstalls

## Context

Files on the pools are owned by uid. When a deleted user's uid goes to a new user, the new user owns every file the deleted one created over SMB. The database is not enough to remember used uids: `deploy/uninstall.sh` deletes the Docker volumes, and with them `unos.db` and its `next_uid`.

## Decision

- A row keeps its valid uid for good. A new uid (`_allocate_uid` in `supervisor/src/shares/accounts.py`) is the first one at or above both `smb_state.next_uid` and one more than the highest uid of any row, skipping uids that host accounts or rows hold; `next_uid` only grows.
- The row's uid and `next_uid` are committed before `useradd` runs, so a crash after `useradd` leaves a row that matches its host account.
- The reconcile raises `next_uid` above every uid it sees, of the rows and of the marked host accounts (`_uid_floor`, written only when it changes). A restore sets the floor in the extracted database before the swap (`prepare_restored_accounts`, ADR-0139): the highest of the restored value, the live value before the restore and one more than every uid seen in either; values that are not integers or lie outside the range are replaced.
- Across an uninstall: `deploy/uninstall.sh` keeps the marked host accounts (ADR-0180), so the first reconcile of a new installation raises `next_uid` above their uids before it removes them as stale. The uninstall's last text lists the `userdel` commands for the case that UNOS will not be installed again.
- Tests: `supervisor/tests/test_smb_reconcile.py` (`TestNextUid`), `supervisor/tests/test_restore_accounts.py` (`TestNextUid`), `supervisor/tests/test_deploy_scripts.py`.

## Consequences

- A uid passes to no other user, also after a crash or a new installation on the same host.
- Limit: an administrator who removes the kept accounts by hand and installs UNOS again gives their uids, and the files they own on the pools, to the new installation's first SMB users.
- Limit: the uids of users that were deleted before a backup are not known to a restore of that backup (ADR-0195).

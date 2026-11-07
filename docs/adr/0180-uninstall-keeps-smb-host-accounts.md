---
status: accepted
date: 2026-11-06
area: deploy
decided-by: agent
supersedes: [ADR-0179]
---

# ADR-0180: Uninstall keeps the SMB host accounts and removes only their exact passdb entries

## Context

Removing UNOS's SMB host accounts at uninstall (ADR-0179) let a later installation hand their uids, and the files on the pools that belong to them, to someone else (ADR-0109). tdbsam deletes an entry by its lower-cased name, so `pdbedit -x` for a name can remove a foreign entry stored in another letter case (ADR-0108). The generated smb.conf keeps serving its shares after an uninstall, guest shares included, and a copy that failed under `set -e` ended the uninstallation before its last message, which tells the administrator what is left.

## Decision

In `deploy/uninstall.sh`, the step `remove_samba_passwords` runs after the containers, volumes, service and files are removed and before the Samba configuration is restored:

- It keeps the marked host accounts (no password, a nologin shell) and the group `unos-smb`: no `userdel`, no `groupdel`. It considers only marked accounts whose names pass the rules of `linux_name_problem` in `supervisor/src/shares/constants.py`: a bash list of reserved names equal to `RESERVED_ACCOUNT_NAMES`, no `systemd-*` and no `s-` followed by a digit (ADR-0114); other marked accounts are left alone with a warning.
- It runs `pdbedit -L` once and `pdbedit -x -u NAME` only for an entry stored as exactly NAME; an entry equal only after case folding is kept, with a warning.
- It runs `systemctl try-restart smbd` once, after the last removal, when it removed at least one entry, so that open connections end; it starts no stopped smbd.
- Every failure of the script's Samba steps (`pdbedit -L`, `pdbedit -x`, the restart, the copy of `smb.conf.original`) gives a warning, and the uninstallation goes on to its completion text. That text names the accounts whose Samba password may be left, with exact-name instructions; the kept accounts with one `userdel NAME` each, then `groupdel unos-smb`; and the Samba configuration: restored from `smb.conf.original` (then smbd and nmbd restart), or kept, still serving its shares (guest shares to anyone), naming `smb.conf.unos-import-backup` when it exists and saying to check it before copying it back.
- Tests: `TestUninstallScript` in `supervisor/tests/test_deploy_scripts.py`, with stubs of getent, pdbedit (deleting by the lower-cased name, as tdbsam does) and systemctl, and stubs of userdel, usermod, groupdel, groupmod, deluser and delgroup that must never be called; a test keeps the bash list and the name rule equal to `constants.py`.

## Consequences

- The host keeps UNOS's accounts and group after an uninstall until the administrator removes them with the printed commands. While they exist, a new installation gives its SMB accounts other uids, and its first start removes them as stale accounts (ADR-0109).
- A Samba password whose removal failed can still sign in to the shares that smb.conf gives it; the completion text says so.
- When `smb.conf.original` exists and a password was removed, smbd restarts twice (ADR-0175).

## Considered options

- Remove every marked account with `pdbedit -x` and `userdel` (ADR-0179): frees the uids for reuse and can remove a foreign passdb entry.

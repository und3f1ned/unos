---
status: accepted
date: 2026-11-10
area: deploy
decided-by: agent
supersedes: [ADR-0180]
---

# ADR-0322: Uninstall keeps the SMB host accounts, removes only their exact passdb entries, restarts smbd once and restores smb.conf through a temporary file

## Context

Removing UNOS's SMB host accounts at uninstall (ADR-0179) let a later installation hand their uids, and the files on the pools that belong to them, to someone else (ADR-0109). tdbsam deletes an entry by its lower-cased name, so `pdbedit -x` for a name can remove a foreign entry stored in another letter case (ADR-0108). ADR-0180 kept the accounts and removed only exact entries, but when a password was removed and `smb.conf.original` existed, smbd restarted twice, once after the removals and once after the restore, and the restore copied `smb.conf.original` straight over smb.conf, so that a copy that stopped halfway left a part of a file (ADR-0175, `deploy.uninstall-samba-restore`). Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent restated ADR-0180 with one restart and a restore through a temporary file.

## Decision

In `deploy/uninstall.sh`, the step `remove_samba_passwords` runs after the containers, volumes, service and files are removed and before the Samba configuration is restored (`restore_samba_config`):

- It keeps the marked host accounts (no password, a nologin shell) and the group `unos-smb`: no `userdel`, no `groupdel`. It considers only marked accounts whose names pass the rules of `linux_name_problem` in `supervisor/src/shares/constants.py`: a bash list of reserved names equal to `RESERVED_ACCOUNT_NAMES`, no `systemd-*` and no `s-` followed by a digit (ADR-0114); other marked accounts are left alone with a warning.
- It runs `pdbedit -L` once and `pdbedit -x -u NAME` only for an entry stored as exactly NAME; an entry equal only after case folding is kept, with a warning.
- Open connections end with one restart of smbd once at least one entry was removed. Without `smb.conf.original`, `remove_samba_passwords` runs `systemctl try-restart smbd` after the last removal, which starts no stopped smbd. With it, the restart is the one that `restore_samba_config` makes with the restored file (`systemctl restart smbd nmbd`); when the restore fails, `restore_samba_config` runs that `systemctl try-restart smbd` instead.
- `restore_samba_config` copies `smb.conf.original` with its mode, owner and times (`cp -p`) to `.smb.conf.unos-restore.unos-tmp` in `/etc/samba`, a name of the supervisor's temporary files there (`remove_stale_temp_files` of `supervisor/src/shares/smb.py`), and renames it over smb.conf (`mv -f`): a copy or rename that fails (a read-only or full /etc, an immutable smb.conf) leaves smb.conf as UNOS wrote it, and the temporary file is removed, a removal that fails included without ending the script. After a restore, nmbd is enabled again (ADR-0275), and smbd and nmbd restart (`systemctl restart smbd nmbd`); a restart that fails is a warning that Samba may still use UNOS's smb.conf and keep open connections until they are restarted.
- Every failure of the script's Samba steps (`pdbedit -L`, `pdbedit -x`, a restart, the restore of `smb.conf.original`) gives a warning, and the uninstallation goes on to its completion text. That text names the accounts whose Samba password may be left, with exact-name instructions; the kept accounts with one `userdel NAME` each, then `groupdel unos-smb`; and the Samba configuration: restored from `smb.conf.original`, not restored (still the file UNOS wrote, with the commands that copy it back and restart Samba), or kept, still serving its shares (guest shares to anyone), naming `smb.conf.unos-import-backup` when it exists and saying to check it before copying it back.
- Tests: `TestUninstallScript` in `supervisor/tests/test_deploy_scripts.py`, with stubs of getent, pdbedit (deleting by the lower-cased name, as tdbsam does) and systemctl, and stubs of userdel, usermod, groupdel, groupmod, deluser and delgroup that must never be called; a test keeps the bash list and the name rule equal to `constants.py`. `test_with_smb_conf_original_smbd_restarts_once`, `test_a_restart_that_fails_after_the_restore_is_reported` and `test_a_copy_that_fails_halfway_leaves_smb_conf_whole` failed on the script of ADR-0180; `test_the_restore_copies_next_to_smb_conf_and_renames_before_samba_restarts`, `test_a_rename_that_fails_leaves_smb_conf_whole` and `test_a_temporary_file_that_cannot_go_does_not_stop_the_uninstallation` hold the copy, the rename and their failures.

## Consequences

- The host keeps UNOS's accounts and group after an uninstall until the administrator removes them with the printed commands. While they exist, a new installation gives its SMB accounts other uids, and its first start removes them as stale accounts (ADR-0109).
- A Samba password whose removal failed can still sign in to the shares that smb.conf gives it; the completion text says so.
- After a restore that failed, Samba keeps serving the shares of UNOS's smb.conf until the administrator copies the file back; a temporary file left by an uninstallation that was killed in the restore is overwritten by the next run, or removed by the supervisor of a new installation.
- The restored smb.conf is a new file with the mode, owner and times of `smb.conf.original`, not UNOS's file rewritten in place: an smb.conf that is a symbolic link becomes a regular file, and the link's target keeps UNOS's text. The supervisor replaces the link the same way when it writes smb.conf (ADR-0089).
- The restart after a restore is `systemctl restart smbd nmbd`: it starts smbd also when it was stopped, as it did under ADR-0180.

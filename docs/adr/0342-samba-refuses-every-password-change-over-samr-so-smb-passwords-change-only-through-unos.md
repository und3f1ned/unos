---
status: accepted
date: 2026-11-11
area: shares
decided-by: agent
supersedes: [ADR-0276]
---

# ADR-0342: Samba refuses every password change over SAMR with a check password script that always fails, so SMB passwords change only through UNOS

## Context

ADR-0276 restated the SMB follow-ups left open since slice 2, among them that an SMB user who
knows the old password can change it through Samba's password-change protocol (SAMR: Windows'
Change a password, `smbpasswd -r`): the new password escapes the rules of ADR-0279, and UNOS's hash
no longer matches Samba's until the next change through UNOS (`shares.password-changes-only-through-unos`,
ADR-0164). UNOS sets every Samba password as root on the host with `smbpasswd -s -a`
(`accounts.set_samba_password`), Samba's local path, which must keep working. The owner delegated
the choice (ADR-0013).

## Decision

- UNOS's `[global]` gets `check password script = /bin/false`, after `obey pam restrictions = no`,
  in both variants (`global_settings` in `supervisor/src/shares/smbconf.py`), and so does the copy
  that `deploy/install.sh` writes before the supervisor's first start (`unos_smb_conf`, which
  `supervisor/tests/test_deploy_scripts.py` compares with `render_config([])`). The line joins the
  pinned `[global]` of ADR-0281.
- What Samba then does (source3/smbd/chgpasswd.c, source3/rpc_server/samr/srv_samr_nt.c): a change
  that a client asks for over SAMR, once the old password is checked, reaches `change_oem_password`,
  whose `check_password_complexity` runs the script with the new password on its standard input and
  refuses the change with NT_STATUS_PASSWORD_RESTRICTION when the script exits with another status
  than 0, before anything is stored. Samba's file server does not implement the old
  SamrChangePasswordUser, which carries no plain text, and the LANMAN change needs SMB1, which is
  off (ADR-0281). Root's local `smbpasswd -s -a` (`local_password_change`) and `pdbedit` run no
  script: activations, UNOS's password changes, the restore steps and the password that goes back
  after a failed change (ADR-0124) work as before.
- A refused change writes passdb.tdb only where a sign-in would: a wrong old password counts as a
  failed sign-in (ADR-0334), and the right one sets a count of failures back to 0.
- The device check is a step of the smb scenario check (`scripts/dev/scenario.py`, ADR-0304), after
  the user's new password signs in and before the steps with wrong passwords: `smbpasswd -r
  127.0.0.1 -s -U <user>` on the device stands in for Windows' Change a password, which asks for the
  same SAMR change. It runs with the check's client configuration in RAM, in the read-only namespace,
  with the old password and the new one twice on its standard input only, and must answer
  NT_STATUS_PASSWORD_RESTRICTION (smbpasswd prints Samba's text for it, "Password restriction"),
  leave passdb.tdb's time as it was and leave the password signing in. A Windows client stays among
  the checks by hand of `testing.smb-real-world-checks`.
- Tests: `supervisor/tests/test_smbconf.py` (TestGlobal, both variants),
  `supervisor/tests/test_deploy_scripts.py` (the copy of `install.sh`),
  `supervisor/tests/test_dev_scenarios.py` (the step against FakeNas, its defect
  `samr-change-accepted`, the SMB helper's change mode against a stand-in smbpasswd).

Still open from ADR-0276, restated here:

- A crafted backup archive can hold two SMB account rows with one uid; the second activation is
  then refused while the first account exists (`shares.restore-uid-guards`).
- A foreign archive can still give a restored account the uid of a user deleted before the
  backup, because such uids are not in the restore's snapshot (ADR-0112); an installation id in
  `smb_state` would close that (`shares.restore-uid-guards`).
- `smb.conf.unos-replaced` keeps only the latest copy (ADR-0092); a second event overwrites the
  first one's text (`shares.keep-every-replaced-copy`).
- Not yet checked for real: `systemctl try-restart smbd` ending sessions at uninstall (ADR-0180),
  the testparm tolerance on Samba older than 4.22 (ADR-0091), the ipv6-literal.net hint on a
  Windows client (ADR-0196), a share folder of another account inside a sticky folder
  (ADR-0193), and Windows' Change a password aimed at the NAS (this ADR)
  (`testing.smb-real-world-checks`).
- Later product items: groups as share principals (ADR-0076), binding Samba to interfaces,
  per-share encryption, Time Machine and Previous Versions (ADR-0161).

## Consequences

- No SMB client can change an SMB password: Windows answers Change a password aimed at the NAS
  with its message for a password that does not meet the requirements, whatever the new password.
  The Account page, and an administrator on the Users page, stay the way to change it, with the
  rules of ADR-0279; `docs/USER_GUIDE.md` says so.
- The statements of ADR-0195, ADR-0250, ADR-0276 and of ADR-0279's consequences that Samba's own
  password change takes any password no longer hold.
- The line holds for every account of Samba's passdb: an account that UNOS did not make (ADR-0108)
  cannot change its password over SAMR either; root on the host still can.
- UNOS sets no maximum password age, so Samba never asks a client for a new password; an expired
  password could not be changed over SAMR either.
- An smb.conf of the host with a script of its own loses it when UNOS writes its own; the import
  report names it among the replaced `[global]` settings (ADR-0086).
- Each refused change runs `/bin/false` once on the host; nothing is written for it.

## Considered options

- The account policy `minimum password age` set very high, which Samba checks in the same
  function: it answers NT_STATUS_ACCOUNT_RESTRICTION while the time of the last set plus the age
  lies ahead, but an account whose time of the last set is 0 skips that test, the policy needs the
  startup reconcile to keep it in account_policy.tdb (ADR-0334), and Windows tells the user to wait
  instead of refusing.
- A "cannot change the password" time for each account (`pdbedit --pwd-can-change-time`): a write
  of every passdb entry at each activation and restore, and an account that UNOS did not make keeps
  the change.

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
the choice (ADR-0013); the agent made it, and the choices below, while working on through the
features under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation).

## Decision

- UNOS's `[global]` gets `check password script = /bin/false`, after `obey pam restrictions = no`,
  in both variants (`global_settings` in `supervisor/src/shares/smbconf.py`), and so does the copy
  that `deploy/install.sh` writes before the supervisor's first start (`unos_smb_conf`, which
  `supervisor/tests/test_deploy_scripts.py` compares with `render_config([])`). The line joins the
  pinned `[global]` of ADR-0281.
- What Samba 4.22 then does (source3/rpc_server/samr/srv_samr_chgpasswd.c and
  source3/rpc_server/samr/srv_samr_nt.c): a change that a client asks for over SAMR, once the old
  password is checked, reaches `change_oem_password`, whose `check_password_complexity` runs the
  script with the new password on its standard input and
  refuses the change with NT_STATUS_PASSWORD_RESTRICTION when the script exits with another status
  than 0, before anything is stored. Samba's file server does not implement the old
  SamrChangePasswordUser, which carries no plain text, and the LANMAN change needs SMB1, which is
  off (ADR-0281). Root's local `smbpasswd -s -a` (`local_password_change`) and `pdbedit` run no
  script: activations, UNOS's password changes, the restore steps and the password that goes back
  after a failed change (ADR-0124) work as before.
- SAMR's SetUserInfo can also set a password (levels 18, 21, 23 to 26, 31 and 32). It runs no
  script, but it needs the right to set the account's password (SAMR_USER_ACCESS_SET_PASSWORD),
  which only root, `BUILTIN\Administrators` and `BUILTIN\Account Operators` have. UNOS maps no
  group to those two, and `invalid users = root` refuses root over SMB (ADR-0281), so no SMB user
  reaches it. A later mapping of a group of administrators must keep it out of their reach.
- A refused change writes passdb.tdb only where a sign-in would: a wrong old password counts as a
  failed sign-in (ADR-0334), and the right one sets a count of failures back to 0.
- The device check is a step of the smb scenario check (`scripts/dev/scenario.py`, ADR-0304), after
  the user's new password signs in and before the steps with wrong passwords: `smbpasswd -r
  127.0.0.1 -s -U <user>` on the device stands in for Windows' Change a password, which asks for the
  same SAMR change. It runs with the check's client configuration in RAM, in the read-only namespace,
  with the old password and the new one twice on its standard input only, and must answer
  NT_STATUS_PASSWORD_RESTRICTION, leave passdb.tdb's time as it was and leave the password signing
  in. Samba 4.22's smbpasswd names the status by its description in MS-ERREF ("machine 127.0.0.1
  rejected to change the password with error: When trying to update a password, this status
  indicates that some password update rule has been violated. For example, the password might not
  meet length criteria."), older releases by a short text ("Error was : Password restriction.");
  the check reads both, without regard to case, and reports any other text with smbpasswd's output.
- Step 1 of `shares.password-changes-only-through-unos` is checked on the device with
  `smbpasswd -r` only. No Windows client has tried Change a password yet: that part stays open in
  this entry, whose `passes` stays false until one is tried. Trying it is an owner action: the
  agent can reach no Windows machine.
- Tests: `supervisor/tests/test_smbconf.py` (TestGlobal, both variants),
  `supervisor/tests/test_deploy_scripts.py` (the copy of `install.sh`),
  `supervisor/tests/test_dev_scenarios.py` (the step against FakeNas; its defects
  `samr-change-accepted`, `samr-refusal-writes` and `samr-refusal-changes-password`, each of which
  fails one expectation of the step; how smbpasswd's answer is read, Samba 4.22's text and the older
  one included; the SMB helper's change mode against a stand-in smbpasswd).

ADR-0276's item that a crafted backup archive can hold two SMB account rows with one uid is
closed: ADR-0349 refuses the restore of such a backup before anything changes (step 1 of
`shares.restore-uid-guards`).

Still open from ADR-0276, restated here:

- A foreign archive can still give a restored account the uid of a user deleted before the
  backup, because such uids are not in the restore's snapshot (ADR-0112); an installation id in
  `smb_state` would close that (step 2 of `shares.restore-uid-guards`).
- `smb.conf.unos-replaced` keeps only the latest copy (ADR-0092); a second event overwrites the
  first one's text (`shares.keep-every-replaced-copy`).
- Not yet checked for real: `systemctl try-restart smbd` ending sessions at uninstall (ADR-0180),
  the testparm tolerance on Samba older than 4.22 (ADR-0091), the ipv6-literal.net hint on a
  Windows client (ADR-0196), and a share folder of another account inside a sticky folder
  (ADR-0193) (`testing.smb-real-world-checks`).
- Later product items: groups as share principals (ADR-0076), binding Samba to interfaces,
  per-share encryption, Time Machine and Previous Versions (ADR-0161).

## Consequences

- No SMB client can change an SMB password, whatever the new password. Windows is expected to
  answer Change a password aimed at the NAS with its message for a password that does not meet the
  requirements, since that is what NT_STATUS_PASSWORD_RESTRICTION means; this is expected, not
  observed, until a Windows client is tried. The Account page, and an administrator on the Users
  page, stay the way to change it, with the rules of ADR-0279; `docs/USER_GUIDE.md` says so.
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

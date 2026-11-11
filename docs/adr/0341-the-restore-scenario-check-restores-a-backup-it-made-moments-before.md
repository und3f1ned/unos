---
status: accepted
date: 2026-11-11
area: device
decided-by: agent
---

# ADR-0341: The restore scenario check restores a backup it made moments before

## Context

`shares.restore-keeps-smb-with-proof` and the device part of `backup.no-old-backup-import` need
restores on the dev device, with Samba, which the stand-in lacks. On 2026-11-11 two restores ran
there through scripts of that session (PROGRESS.md, Log); the owner asked for an area of
`scripts/dev/scenario.py` (ADR-0304) that runs them again. A restore puts the device's whole
database back as it was at the backup and ends every session but the restoring administrator's
(ADR-0329), so the API relay may sign in again.

## Decision

- `scripts/dev/scenario.sh restore` (`restore_check` of `scripts/dev/scenario.py`) restores only
  a backup made by hand by the check itself after its baseline, and only on a device where no share
  that is not served has access or guest access, which the post-restore step removes (ADR-0145).
  The baseline holds the users with their SMB state and uid, the shares with their access, the
  API tokens of the relay's administrator (`GET /auth/tokens` lists only the caller's), smb.conf's digest, the SMB host accounts, Samba's accounts, the account files, the
  backups, the copies of the database from before a restore, the Scheduler's tasks and the pool's
  top folder.
- Each restore keeps a copy of the database from before it, `unos.db.pre-restore-<time>` in the
  data volume, and `supervisor/src/backup/router.py` keeps the newest 5 (`PRE_RESTORE_COPIES`):
  the check starts only on a device with at most 3, so that its two restores push out none that was
  there before, and its clean-up removes the copies that are new since its baseline, also that of
  a restore whose answer was lost (`REMOTE_DB_COPIES`, which removes only names of such copies,
  without the read-only namespace: the data volume is on the root filesystem). More than 3 copies
  are the owner's to thin out.
- The clean-up first waits for a restore that may still run, one whose answer was lost: the
  restore holds smb_lock to the end of its reconcile, and a change of a share that does not exist
  waits for it, answers 404 and changes nothing (`wait_for_smb_lock`, as after a restart of the
  supervisor). Only then does it list the copies, or look for the accounts of the dropped user.
- The copies stay, and the clean-up fails naming them, when what is not the check's changed: the
  users, shares, API tokens of the relay's administrator, SMB host accounts or Samba's accounts
  whose entries do not name the run, compared around each restore that answered, and at the
  clean-up with the baseline and with how they were right before each restore, also one whose
  answer was lost. Those copies hold that data from before the restore, and removing them is the
  owner's decision. A change of the check's own users or of smb.conf alone loses nothing of
  anyone's, so their copies go.
- Where the device has a guest share, `map to guest = Bad User` lets Samba take a user without a
  Samba account as a guest (ADR-0339), so the step whose sign-ins of `...c` must be refused says
  so instead (as the shares check does, ADR-0340).
- Its steps: two users `unoscheck<6 hex>s` and `...c` with SMB on and a share `unoscheck<6 hex>`
  with write access for both; the backup by hand and the backup of Run now of a disabled backup
  task without `keep_count`, whose members hold no smb.conf (read by `ARCHIVE_MEMBERS` in the
  supervisor's running container with `docker exec`, ADR-0183: python decrypts the scheduled
  backup in memory with the task's passphrase on standard input and writes nothing); the scheduled
  backup and its task deleted; a restore with nothing changed since the backup, after which the
  users, the shares, the API tokens, smb.conf, the host accounts and Samba's accounts are as
  before; then a new password for `...c`, which signs in, and a user `...d` with SMB on and access
  to the share, and the same restore again: the users and the share as at the backup, `...s`
  active with its uid and `...c` waiting for its password with its uid (the restore's warning
  "SMB must be turned on again for ..." names `...c` alone, ADR-0141), smb.conf with UNOS's header
  and `...s` alone in the share's section, which testparm takes, the host account and Samba
  account of `...d` removed (stale, ADR-0110), a signed session of `...s` with its password of the
  backup that writes, both passwords of `...c` refused with NT_STATUS_LOGON_FAILURE, SMB on again
  for `...c` with a password set by an administrator, under its uid, and a user `...f` made after
  the restore with a uid above that of `...d`, which the backup's next uid would have given again
  (ADR-0109); no password of the check in an answer or a log; the deletes.
- What a restore drops from the database after the check made it is the user `...d`: the
  post-restore reconcile removes its host account and Samba account. When they are still there,
  the clean-up restarts the supervisor, whose startup reconcile removes them (as the smbconf check
  restarts it, ADR-0309), and the comparison with the baseline names what is left.
- A restored row with the uid of a live account of another name (ADR-0112) is not built on the
  device: one installation hands out each uid once, also across restores (ADR-0109), so only a
  backup of another installation or a crafted archive has one. Making either there means
  restoring a database rewritten outside UNOS, a copy of the device's own with its password
  hashes, or another installation's, after which the relay could not sign in with `dev.env`'s
  administrator to put the device back. `supervisor/tests/test_restore_accounts.py` shows that
  case (`TestLiveUids`, `TestWholePath`).
- The feature-pass skill's table names this check for both entries. Tests:
  `supervisor/tests/test_dev_scenarios.py`, against the fake NAS, whose restore applies the proof,
  the uid floor, the post-restore reconcile and the copies, `REMOTE_ARCHIVE` with a fake docker on
  a plain and an encrypted backup that `supervisor/src/backup/router.py` writes, and
  `REMOTE_DB_COPIES` on a temporary folder.

## Consequences

- Each run ends the device's web sessions twice, the owner's in a browser included, writes two
  backups and two copies of the database and removes them again, and leaves the run of its task in
  the Scheduler's history. What the device's database gains during the check from others, such as
  a login or a setting saved in the web UI, is lost with the restores; when that is a user, a share,
  an API token of the relay's administrator or an account, the check fails and keeps the copies that
  hold it. Another user's API token is not listed to the relay, so its loss goes unseen. A copy that someone
  else's restore made meanwhile would be removed as the check's: the check holds the device lock,
  which other checks and deploys wait for, and runs on the owner's request.
- The check cannot show a restore of a backup of another installation or of an older one; the
  backend tests do.

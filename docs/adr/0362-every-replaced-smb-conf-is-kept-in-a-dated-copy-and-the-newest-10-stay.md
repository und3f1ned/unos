---
status: accepted
date: 2026-11-12
area: shares
decided-by: agent
supersedes: [ADR-0085, ADR-0092, ADR-0309]
---

# ADR-0362: Every replaced smb.conf is kept in a copy named with its UTC time, and the newest 10 of each kind stay

## Context

UNOS kept one copy of an smb.conf with share sections it did not render, `smb.conf.unos-replaced`,
which the next such file overwrote (ADR-0092), and one import backup, `smb.conf.unos-import-backup`,
which was never replaced, so a later file that UNOS imported was lost once UNOS wrote its own
(ADR-0085; ADR-0164 and ADR-0342 list both as `shares.keep-every-replaced-copy`). The copies lie on
the system disk (ADR-0214), so their number needs a bound. Working on through the features under the
owner's delegation ("work on through the features, ask no questions; I allow everything", in
translation), the agent chose the names and the bound.

The smbconf scenario check (ADR-0309) shows the copy on the device: it edits smb.conf by hand,
lets an apply copy it, and refused to start beside `smb.conf.unos-replaced`, which an apply would
have replaced. Its refusal, its copy and its clean-up change with the dated copies, and step 1 of
`shares.keep-every-replaced-copy`, a second such file whose copy leaves the first as it was, takes
a second hand edit. ADR-0309 moved these checks from a session's script into
`scripts/dev/scenario.py` on the owner's answer ("Yes, move them", in translation); this ADR
supersedes it and restates its rules that stay.

## Decision

- Two kinds of copies next to smb.conf, each named with the UTC time of the copy as
  `YYYYMMDD_HHMMSS_ffffff`, the form of the database copies before a restore
  (`supervisor/src/backup/router.py`): `<smb.conf>.unos-replaced-<time>` and
  `<smb.conf>.unos-import-backup-<time>`, for example
  `smb.conf.unos-replaced-20261112_101530_123456` (`keep_copy`, `kept_copies` and `COPIES_KEPT` in
  `supervisor/src/shares/smb.py`).
- When, as before: a replaced copy before an apply writes a new smb.conf whose live file has share
  sections that are no database row and lack UNOS's six fixed lines (`is_unos_section`, ADR-0088;
  `[homes]`, `[printers]` and `[print$]` do not count, nor sections that UNOS rendered itself, also
  of a share deleted a moment ago; `_prepare_file` in `supervisor/src/shares/service.py`); an import
  backup whenever the import reads a live smb.conf that does not start with `smbconf.HEADER`
  (`_read_live_source`), in the order copy, commit of the import, apply. A copy that cannot be
  written refuses the apply ("smb.conf has shares that UNOS did not create (NAMES), and they could
  not be copied to <smb.conf>.unos-replaced-<UTC time>: REASON") or fails the import, and smb.conf
  stays as it is. `deploy/install.sh` keeps the host's earlier file as `smb.conf.original`.
- A copy is a temporary file (mode 600), fsynced, then hard-linked to its name, which fails instead
  of replacing a file, the temporary file removed, and one fsync of the directory. No copy is
  written when one of the newest 10 copies of its kind, by the time in their names, has exactly
  these bytes, also one named before the others by a clock set back: an apply or import retried
  after a failure writes nothing. A copy is read only when it is a regular file of exactly that
  size, opened without following a symbolic link or waiting on a FIFO (`O_NOFOLLOW`, `O_NONBLOCK`)
  and checked with fstat, so the comparison makes at most 10 reads of the size of smb.conf; a copy
  that cannot be read holds nothing, and a new copy is written.
- After a new copy, the copies of its kind beyond the newest 10 are deleted, oldest first, never
  the new copy, also when a clock set back named it before the others. Nothing is refused for
  their number; a copy that cannot be deleted is logged and stays. Only regular files (lstat) with
  names of exactly this form count: a directory, a symbolic link or a FIFO with such a name, and
  other files of `/etc/samba`, the undated names of the earlier single copies among them, are
  neither listed, read nor deleted.
- `GET /api/v1/shares/smb-status` lists, until the supervisor restarts, "smb.conf had shares that
  UNOS did not create (NAMES); they are kept in COPY: create them again on the Shares page",
  followed by "; earlier copies (UNOS keeps the newest 10): A, B" when there are other copies,
  newest first by name. The import report says "Imported N share(s) from PATH; the previous file
  is kept as COPY", with "; earlier import backups (UNOS keeps the newest 10): ..." in the same
  way, also when an import backup held these bytes already.
- `deploy/uninstall.sh` lists the import backups, oldest first, where it named the single one.
- The smbconf scenario check, restated from ADR-0309 with the copies of this ADR:
  `scripts/dev/scenario.sh smbconf` (also in `all`, after `smb`) runs the area `smbconf` of
  `scripts/dev/scenario.py`, on the pool of the other checks, with the first regular user of the
  device for an access entry (a temporary user without SMB only when there is none, which saves
  its writes): smb.conf edited by hand (UNOS's header replaced by a comment, a share section
  `[unoscheck<6 hex digits>hand]` with `available = no` appended, so that Samba serves nothing
  new), which the Shares page names before an apply (ADR-0249); a new share whose apply copies
  that file byte for byte to a new `smb.conf.unos-replaced-<time>`, keeps the earlier copies and
  installs UNOS's file, and the copy and the earlier ones named on the Shares page; a comment
  saved with the expected version and a stale one refused (409); smb.conf edited by hand again in
  the same way, which the next comment's apply copies byte for byte to a second copy, while the
  first keeps its bytes and the Shares page names it as an earlier copy beside the others (step 1
  of `shares.keep-every-replaced-copy`); the share's folder replaced by a symbolic link, so that
  the share is not served, with access refused (400) without a new folder and saved with one,
  which UNOS makes 2770 root:unos-smb; Delete keeping the folder with its files, and smb.conf byte
  for byte as found; then, with the check's two copies deleted, a restart of the supervisor that
  writes no file in `/etc/samba` and leaves no problem on the Shares page.
- The check refuses to start when smb.conf is not UNOS's, holds a share section without UNOS's
  fixed lines (one that UNOS did not write, which the status cannot name while UNOS's header is
  there, ADR-0249, and which an apply would copy into the check's copy), when `/etc/samba` keeps 9
  or more copies `smb.conf.unos-replaced-<time>` (the check's two copies would make UNOS delete the
  oldest, which the check did not make) or when the Shares page reports a problem. After each
  apply it takes the one new name as its copy and expects the earlier copies to stay.
- Its edits run without the read-only namespace of the checks' commands (ADR-0221), through
  `REMOTE_HAND_EDIT`: `edit` and `again` write only `/etc/samba/smb.conf`, after comparing its
  digest with the one the check read; `edit` keeps the original in RAM (`/dev/shm`) and refuses
  beside 9 copies, `again` keeps that original and refuses beside 10, the check's first among
  them. `restore` deletes only the copies that hold exactly one of the check's edited files, and
  puts the original back when smb.conf does not start with UNOS's header; once an apply has
  written UNOS's rendering, that stays.
- The symbolic link is made and removed by `REMOTE_UNSERVE`, which touches only
  `<pools>/<pool>/unoscheck<6 hex digits>` and its `-moved` folder, in the read-only namespace
  (the pool is writable there).
- The restart is `docker restart unos-supervisor`, through Docker's socket in the read-only
  namespace, followed by a wait for the container's health check (at most 180 seconds) and then
  for the startup reconcile: it holds smb_lock from before the first request (`src/main.py`), so
  a PATCH of a share that does not exist waits for it, answers 404 and writes nothing. Only then
  does the check read `/etc/samba` and the Shares page. This restart is an exception to ADR-0183:
  it starts the container that exists, creates none, and its writes are in the step's SD MiB.
  The clean-up restarts the supervisor again only when the Shares page still names the check's
  section, that is, when the steps ended between an apply and the restart.
- The feature-pass skill's table names `scripts/dev/scenario.sh smbconf` for
  `shares.smb-conf-and-status` and `shares.edit-and-delete`; the smb row does not list them.
- Tests: `supervisor/tests/test_share_apply.py` (TestReplaced, TestSmbHelpers),
  `supervisor/tests/test_share_import.py` (TestCopy), `supervisor/tests/test_share_api.py`
  (TestStatus), `supervisor/tests/test_deploy_scripts.py` (the uninstall's completion text) and
  `supervisor/tests/test_dev_scenarios.py`, which runs the smbconf area against the stand-in of the
  device, whose smb.conf is a file of its own with UNOS's six fixed lines in each section (the
  same as `src/shares/smbconf.py`'s), an apply that copies sections without them unless a copy
  holds the file, a status that names a file that is not UNOS's only once an apply has run, links
  that are not served, and a restart after which the API answers before the reconcile has applied;
  the defects the area exists for fail it, among them an apply that deletes earlier copies
  (`deletes-earlier-copies`) or writes over the first (`overwrites-the-first-copy`); the hand edit
  and the link run here against temporary folders and refuse what is not a check's own.

## Consequences

- Each kind takes at most 10 times the size of an smb.conf, and a copy is written only when a file
  that UNOS did not write appears (ADR-0214).
- The first import backup, the file from before UNOS, goes after 10 more imports of files that
  UNOS did not write; each such import needs a new database or the restore of one that never
  imported (ADR-0084).
- A copy named with a clock set back, as on a board without a clock of its own before NTP has set
  it, sorts before older copies: it stays when it is written, but is the first one deleted later;
  while it is among the newest 10, a file that it holds is not copied again.
- After a restart the Shares page names no replaced copy (the import report names its backup until
  it is dismissed); `/etc/samba` lists them, and `docs/USER_GUIDE.md` says where. The earlier single copies keep their undated names and are
  named by neither the Shares page nor the uninstall (ADR-0207).
- It closes the item of `smb.conf.unos-replaced` that ADR-0342 restates as still open, and the
  two items of ADR-0164 on the copies.
- The re-check of `shares.smb-conf-and-status`, `shares.edit-and-delete` and step 1 of
  `shares.keep-every-replaced-copy` after a reboot is a scenario check. Its second edit and apply
  add one copy and one smb.conf of a few KiB each to its SD writes, and the owner's copies may be
  at most 8 when it starts.
- `all` restarts the supervisor once: its API is away for six to eight seconds, and the restart's
  step wrote 1.277 MiB to the SD card on 2026-11-09. ADR-0304's list of what the scripts leave
  out no longer holds for a supervisor restart.
- The status panel itself stays a matter of the E2E specs on the stand-in (ADR-0215); the check
  shows what the API answers it on the device.
- A run that loses its connection can leave smb.conf as it edited it, the original in
  `/dev/shm`, copies `smb.conf.unos-replaced-<time>` with the check's section, or the link: the
  next run refuses to start, and the device-checks skill says how to put them back.

## Considered options

- A bound on the copies' total size: it would have to refuse or drop the copy of a large file,
  and the number already ties the copies to the size of smb.conf.
- A list of the copies of its own in the status and on the Shares page: a new API field, texts
  in two languages and an E2E spec for files that an administrator opens on the host anyway.
- Comparing only with the newest copy by name, as first built: while a clock stays set back, a
  file would be copied again at every apply, each copy pushing an older one out.

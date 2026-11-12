---
status: accepted
date: 2026-11-12
area: shares
decided-by: agent
supersedes: [ADR-0085, ADR-0092]
---

# ADR-0360: Every replaced smb.conf is kept in a copy named with its UTC time, and the newest 10 of each kind stay

## Context

UNOS kept one copy of an smb.conf with share sections it did not render, `smb.conf.unos-replaced`,
which the next such file overwrote (ADR-0092), and one import backup, `smb.conf.unos-import-backup`,
which was never replaced, so a later file that UNOS imported was lost once UNOS wrote its own
(ADR-0085; ADR-0164 and ADR-0342 list both as `shares.keep-every-replaced-copy`). The copies lie on
the system disk (ADR-0214), so their number needs a bound. Working on through the features under the
owner's delegation ("work on through the features, ask no questions; I allow everything", in
translation), the agent chose the names and the bound.

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
  written when the newest copy of its kind, by the time in its name, has exactly these bytes: an
  apply or import retried after a failure writes nothing.
- After a new copy, the copies of its kind beyond the newest 10 are deleted, oldest first, never
  the new copy, also when a clock set back named it before the others. Nothing is refused for
  their number; a copy that cannot be deleted is logged and stays. Only names of exactly this form
  count: other files of `/etc/samba`, the undated names of the earlier single copies among them,
  are neither listed nor deleted.
- `GET /api/v1/shares/smb-status` lists, until the supervisor restarts, "smb.conf had shares that
  UNOS did not create (NAMES); they are kept in COPY: create them again on the Shares page",
  followed by "; earlier copies (UNOS keeps the newest 10): A, B" when there are any, newest first.
  The import report says "Imported N share(s) from PATH; the previous file is kept as COPY", with
  "; earlier import backups (UNOS keeps the newest 10): ..." in the same way, also when the newest
  import backup held these bytes already.
- `deploy/uninstall.sh` lists the import backups, oldest first, where it named the single one.
- The smbconf scenario check (ADR-0309, `scripts/dev/scenario.py`) refuses to start beside 10
  replaced copies, since its own copy would make UNOS delete the oldest; it takes the one new name
  as its copy, expects the earlier copies to stay and to be named on the Shares page, and its
  clean-up deletes only the copies that hold exactly its edited file.
- Tests: `supervisor/tests/test_share_apply.py` (TestReplaced, TestSmbHelpers),
  `supervisor/tests/test_share_import.py` (TestCopy), `supervisor/tests/test_share_api.py`
  (TestStatus), `supervisor/tests/test_deploy_scripts.py` (the uninstall's completion text),
  `supervisor/tests/test_dev_scenarios.py` (the smbconf check against its fake, which deletes
  earlier copies under the defect `deletes-earlier-copies`, and the hand edit helper run here).

## Consequences

- Each kind takes at most 10 times the size of an smb.conf, and a copy is written only when a file
  that UNOS did not write appears (ADR-0214).
- The first import backup, the file from before UNOS, goes after 10 more imports of files that
  UNOS did not write; each such import needs a new database or the restore of one that never
  imported (ADR-0084).
- A copy named with a clock set back, as on a board without a clock of its own before NTP has set
  it, sorts before older copies: it stays when it is written, but is the first one deleted later,
  and the next file is compared with the copy whose name is newest.
- After a restart the Shares page names no replaced copy (the import report names its backup until
  it is dismissed); `/etc/samba` lists them, and `docs/USER_GUIDE.md` says where. The earlier single copies keep their undated names and are
  named by neither the Shares page nor the uninstall (ADR-0207).
- It closes the item of `smb.conf.unos-replaced` that ADR-0342 restates as still open, and the
  two items of ADR-0164 on the copies.

## Considered options

- A bound on the copies' total size: it would have to refuse or drop the copy of a large file,
  and the number already ties the copies to the size of smb.conf.
- A list of the copies of its own in the status and on the Shares page: a new API field, texts
  in two languages and an E2E spec for files that an administrator opens on the host anyway.

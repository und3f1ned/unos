---
status: accepted
date: 2026-11-09
area: device
decided-by: agent
---

# ADR-0309: The smbconf scenario check edits smb.conf by hand and restarts the supervisor

## Context

The steps of `shares.smb-conf-and-status` (a share section that UNOS did not write, copied to
`smb.conf.unos-replaced`, and the status panel's problems) and of `shares.edit-and-delete` (a
share that is not served gets access only with a new folder) that `scripts/dev/scenario.sh smb`
leaves out were checked by hand on 2026-11-09, before and after a reboot, with a script of the
session. A check by hand that is repeated becomes a step or an area of `scripts/dev/scenario.py`
(ADR-0304); asked whether to move these checks there, the owner answered "Yes, move them", and
then "work on through the features, ask no questions; I allow everything" (in translation).

Two of the check's actions are outside what ADR-0304 allows a scenario check. It writes
`/etc/samba/smb.conf` by hand, which the read-only namespace of the checks' commands (ADR-0221)
refuses. And the problem of a copied section stays on the Shares page until the supervisor
restarts (ADR-0092), so a check that leaves the device as it found it ends with a restart, which
ADR-0183 rules out for device checks (it writes to the system disk: 1.277 MiB measured on
2026-11-09) and which ADR-0304 lists as not checked by the scripts. The restart also shows step 3
of `shares.smb-conf-and-status` (a restart changes no file when nothing changed), a step that
the smb check leaves out.

## Decision

- `scripts/dev/scenario.sh smbconf` (also in `all`, after `smb`) runs the area `smbconf` of
  `scripts/dev/scenario.py`, on the pool of the other checks, with the first regular user of the
  device for an access entry (a temporary user without SMB only when there is none, which
  saves its writes): smb.conf edited by hand (UNOS's header replaced by a comment, a share section
  `[unoscheck<6 hex digits>hand]` with `available = no` appended, so that Samba serves nothing
  new), which the Shares page names before an apply (ADR-0249); a new share whose apply copies
  that file byte for byte to `smb.conf.unos-replaced` and installs UNOS's file, and the copy named
  on the Shares page; a comment saved with the expected version and a stale one refused (409);
  the share's folder replaced by a symbolic link, so that the share is not served, with access
  refused (400) without a new folder and saved with one, which UNOS makes 2770 root:unos-smb;
  Delete keeping the folder with its files, and smb.conf byte for byte as found; then, with the
  check's copy deleted, a restart of the supervisor that writes no file in `/etc/samba` and leaves
  no problem on the Shares page.
- The check refuses to start when smb.conf is not UNOS's, holds a share section without UNOS's
  fixed lines (one that UNOS did not write, which the status cannot name while UNOS's header is
  there, ADR-0249, and which the apply would copy), when `smb.conf.unos-replaced` exists (an
  apply would replace it) or when the Shares page reports a problem.
- Its edit runs without the read-only namespace, through `REMOTE_HAND_EDIT`: it writes only
  `/etc/samba/smb.conf`, after comparing its digest with the one the check read, keeps the
  original in RAM (`/dev/shm`), and deletes `smb.conf.unos-replaced` only when the copy holds
  exactly the bytes of the check's edited file. The clean-up puts the original back when smb.conf
  does not start with UNOS's header; once an apply has written UNOS's rendering, that stays.
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
  section, that is, when the steps ended between the apply and the restart.
- The feature-pass skill's table names `scripts/dev/scenario.sh smbconf` for both entries; the
  smb row no longer lists them.
- Tests: `supervisor/tests/test_dev_scenarios.py` runs the area against the stand-in of the
  device, whose smb.conf is a file of its own with UNOS's six fixed lines in each section (the
  same as `src/shares/smbconf.py`'s), an apply that copies sections without them, a status that
  names a file that is not UNOS's only once an apply has run, links that are not served, and a
  restart after which the API answers before the reconcile has applied; the defects the area
  exists for fail it; the hand edit and the link run here against temporary folders and refuse
  what is not a check's own.

## Consequences

- The re-check of both entries after a reboot is a scenario check: nothing of them is checked by
  hand any more.
- `all` restarts the supervisor once: its API is away for six to eight seconds, and the restart's
  step wrote 1.277 MiB to the SD card on 2026-11-09. ADR-0304's list of what the scripts leave
  out no longer holds for a supervisor restart.
- The status panel itself stays a matter of the E2E specs on the stand-in (ADR-0215); the check
  shows what the API answers it on the device.
- A run that loses its connection can leave smb.conf as it edited it, the original in
  `/dev/shm`, a `smb.conf.unos-replaced` with the check's section, or the link: the next run
  refuses to start, and the device-checks skill says how to put them back.

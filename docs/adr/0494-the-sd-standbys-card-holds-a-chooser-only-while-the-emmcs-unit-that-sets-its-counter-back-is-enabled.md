---
status: accepted
date: 2026-11-24
area: system
decided-by: agent
---

# ADR-0494: The SD standby's card holds a chooser only while the eMMC's unit that sets its counter back is enabled

## Context

The card's chooser (ADR-0488, ADR-0489) counts one try in the card's counter sector at every boot of
the eMMC, and only the eMMC's `unos-standby-boot-ok.service` sets the counter back to 0 once the
system came up; at 3 tries the chooser boots the card's root. Remove the standby unmounted the card,
dropped UNOS's fstab lines, took away the eMMC's units and only then wiped the card. A Remove that
stopped after the units went but before the wipe (a power cut, a crash, a failed command) left a card
whose chooser still ran: the next three boots each counted a try that nothing reset (the counter at 1,
2 and 3, each booting the eMMC), and the fourth, and every boot after it, booted the card's root, an
old copy of the system, with `unos.reason=tries`. Set up and a sync had the same window the other way
round: Set up wrote the chooser before it wrote and enabled the units, and a sync wrote a missing
chooser before missing units (PROGRESS.md, Log of 2026-11-24; ADR-0489's consequences).

## Decision

The card holds a chooser that a boot runs only while `unos-standby-boot-ok.service` is on the eMMC
and enabled (`supervisor/src/standby/card.py`, `generation.py`, `service.py`):
- **Remove** (`card.remove`) unmounts the card and wipes it first (`card.wipe_card`: the F3's boot
  info, the filesystems' and the table's signatures, the first 16 MiB; then `card.reread_partitions`),
  so that its chooser no longer runs; then it disables and deletes the units, their scripts and the watchdog's
  drop-in, removes the mount points and folders, and drops UNOS's fstab lines last, followed by one
  `systemctl daemon-reload`. The fstab lines are the anchor of a standby that is set up
  (`card.is_armed`): a Remove that stopped before them shows as set up, with its card missing
  (`standby_card_missing`, alerted) once the card is wiped, and the tab's Remove runs again to its
  end, without a model to type when the card no longer holds the standby. A Remove that fails reads
  the state again at once (`service.remove`), so that the tab shows that state in the same run, and
  `standby_card_missing` names that second Remove beside putting the card back.
- **Remove without the card**: with no SD card in the slot, Remove cannot wipe the card, which may
  come back with its chooser, so it keeps `unos-standby-boot-ok.service` and its script, enabled and
  inert (they run only with `unos.root=emmc`, which only a chooser passes); a later Remove that wipes
  the card takes them away. An SD card in the slot that no longer holds the standby counts as the
  standby's card that a Remove that stopped wiped: then the unit goes.
- **Set up** (`card.prepare`) writes the chooser `boot.scr` after it wrote and enabled the units, and
  the F3's loader still last of all (ADR-0489).
- **A sync** (`generation._refresh_card_files`) writes and enables the units before it writes a
  chooser that differs, and enables `unos-standby-boot-ok.service` also when it is there but not
  enabled (`systemctl is-enabled`, one read per sync); the watch is enabled only when it was missing,
  so that an administrator's `disable` of it stays.
- **A card left with a chooser** (a card set up by another installation, or one whose Remove ran
  without it): when this boot came through a card's chooser (`unos.root=emmc`) while no standby is
  set up and a card holds the standby's labels, the problem `standby_leftover` (error, alerted and on
  the banner, linked to System > Standby) says that the card counts the boots and Remove wipes it.
  The supervisor reads the cards for it at its start, after each action and, while a leftover card
  is known, at each check of the card (5 minutes), so that a card pulled out ends the problem. The
  status's `leftover` covers it as before.

Nothing resumes a Remove at the supervisor's start: in no state left by a stop is there a counter
that nothing resets, and a wipe needs the administrator's typed model (ADR-0471).

Tests: `supervisor/tests/test_standby.py` (`TestAStopMidway`, `TestALeftoverCard`,
`TestARemovalWithoutTheCard`) and `supervisor/tests/test_standby_bpi_f3.py` stop Set up and Remove (and,
on the W3, a sync) before each of their changes on the fake hosts of `supervisor/tests/standby_fake.py`
(`PowerCut`, `stopped_at_each_step`, `counts_tries_unanswered`; the fake tracks `systemctl enable`
and `disable` and wipes one filesystem's label at a time) and run Remove again to its end; the order
tests of Set up pin the chooser after the units, and Remove's dry run the wipe before the units and
the fstab lines last.

## Consequences

- The writes to the eMMC are the same as before, in another order, but for a sync that enables the
  boot-ok unit found disabled (its link and systemd's reload) and the unit and script that a Remove
  without the card keeps.
- A Remove stopped after the wipe leaves the eMMC's units in place until it runs again; they are inert,
  since they run only with `unos.root=emmc`, which no chooser passes once the card is wiped. A stop
  between the card's last signature and the end of its first 16 MiB leaves the loader's sectors and
  the counter on a card that no longer holds a standby: it is offered as a free card, and Set up wipes
  it again.
- A Remove without the card leaves the boot-ok unit on the eMMC for good when the card never comes
  back; Set up writes it again. A card from another installation, which no unit here answers, counts
  its boots until Remove wipes it: `standby_leftover` reports it from the first boot through its
  chooser, but a board that reboots unattended until the counter reaches 3 boots the card's root.

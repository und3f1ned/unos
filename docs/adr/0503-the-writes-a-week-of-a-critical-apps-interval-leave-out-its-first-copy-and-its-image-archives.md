---
status: accepted
date: 2026-11-25
area: system
decided-by: agent
---

# ADR-0503: The writes a week of a critical app's interval leave out its first copy and its image archives

## Context

Before an interval is saved, the critical mark's dialog shows the writes to the card a week that it
costs: the app's average MiB a sync over its recent syncs (`RECENT_SYNCS`, 8) times the syncs a week
(ADR-0500, `system.sd-standby-critical-apps` step 3). Each sync recorded every sector it wrote to
the card. The first sync copies the app's whole folder and archives its images; on the dev device on
2026-11-25 Uptime Kuma's first sync wrote 588.7 MiB, 586 of them its image's archive, so the dialog
offered an interval of 1 hour at 98902 MiB a week, while its later syncs of a changed database wrote
about 1 MiB each. An image's archive is written once an image, at the first sync and after an update,
whatever the interval.

## Decision

This refines what ADR-0500 keeps in `state.json` as "the MiB of the last 8 syncs that wrote". The MiB that `standby/apps.py` records for the writes a week (the app record's `mib`) leave out:

- the first sync of an app's copy (no earlier generation), which copies its whole folder: the dialog
  can tell the writes a week from its second sync that wrote on; the first is not measured at all;
- the sectors written while a later sync removes archives of images no longer used and writes the
  archives of new ones: the card is flushed (`sync -f`, at most `FLUSH_TIMEOUT`, 600 s) before and
  after that step and its sectors are read at both ends, only when there is an archive to remove or
  write.

The sync's own answer and log (`AppOutcome.mib`, the Standby tab's last sync) still count every
sector it wrote. The dialog's text says that the archive of a new image is written once after an
update, besides, and the Standby tab's average names "a sync of its changes".

## Consequences

- The estimate appears after the second sync that wrote: at the daily interval about a day after
  the mark; until then the dialog says it cannot be told yet.
- A sample can still span more than one interval (the first sync after a pool return, or after the
  pool's disks slept), and samples taken at one interval are multiplied for another, as before.
- A record kept from before this change holds its first sync's MiB until 8 newer samples replace it
  or the app is marked again.
- A sync that writes an archive flushes the card twice more; a flush writes only what the sync
  wrote already.
- An update's archive is not in any interval's estimate: the dialog names it in words, with the size
  of the app's images that it shows already.

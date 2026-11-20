---
status: accepted
date: 2026-11-20
area: webui
decided-by: agent
---

# ADR-0432: The dashboard counts every issue it knows, and the data pages keep one primary action each, with the rarer ones behind More

## Context

The owner found the redesign careless ("the design is slapdash, the UX limps") and asked to remove
every duplicate of every menu; the decisions were delegated, safety first. A UX audit of the web UI
found on the data pages (Dashboard, Storage, Snapshots, Files, Trash): a dashboard that showed
"No problems" beside a "Replace soon" alert and pools in error, a header time that ticked every
2 seconds, a pool card of seven buttons and up to three alerts (about 900 px on a phone), actions
disabled without a reason, names broken one letter per line on a phone, Title Case labels, raw
values ("raid1", "N/A", lowercase states), an "About" wall of rules, and the Trash in no
navigation. The entry is `webui.ux-data`; the shared components (ActionMenu, BulkBar, ConfirmDialog,
LoadState, `nasTime`, Modal sizes) come from the design-system work that runs beside it.

## Decision

- **One list of issues, counted once.** The dashboard draws the problems banner
  (`webui/src/lib/components/ProblemsBanner.svelte`, ADR-0332) under its header, and the layout
  draws it above every other page. On the dashboard the banner adds what the page itself knows
  (`webui/src/lib/components/dashboard/issues.ts`): each pool that is not healthy or is mounted
  read-only and whose health no problem of the socket already reports (a low-space problem hides
  nothing), each disk whose SMART trend says replace soon (ADR-0235) with the server's reasons as
  a second line unless the socket already reports it failing, a failed read of the pools and a
  failed check of the disks. Each issue is a
  link to the page where it is dealt with (pools to Storage, disks to Disk Health, tasks to the
  Scheduler, apps to Containers, shutdowns to Power). The header's chip counts the same list ("3
  issues", red when one is an error, a link to the banner); "No problems" shows only when the
  socket has said there are none and the dashboard knows of none. `ReplaceDisksAlert` is gone.
- **Nothing ticks.** The header says "Live" in a muted line, with the time of the last stats in its
  tooltip; chips are for issues only. Dates on Snapshots and Trash are to the minute
  (`DATE_TIME_MINUTES` of `webui/src/lib/format.ts`); a trash checkbox's label keeps the seconds,
  which tell two items of one name apart (ADR-0197).
- **Counts add up.** The Containers widget shows Running, Stopped and, when there are any, Other,
  which add up to the total; its rows show a state badge and no play or stop icon, which are the
  Containers page's buttons. The Network tile names Received and Sent, and a missing temperature is
  a quiet "No reading".
- **One primary action per page, in the header; rarer actions behind More.** A pool card has its
  name and badges on the left and its actions on the right, always: Volumes and Scrub (Mount when
  it is not mounted) as secondary buttons, and Balance…, Add disk…, Remove disk…, Unmount… and
  Remove pool… behind More (`webui/src/lib/components/storage/PoolActions.svelte`), which the
  design system's ActionMenu replaces. An action that cannot run says why: a tooltip, a text for
  screen readers and a line under the actions (`blockedReason` of
  `webui/src/lib/components/storage/pools.ts`); so do Create pool when the disks could not be read
  and Create in its dialog. A running scrub, balance or disk removal shows its progress right under
  the pool's header with its own Pause, Resume or Cancel as secondary buttons; how the last one
  ended stays at the foot. What is wrong with a pool is one alert, a line per fact, the worst
  colour; RAID 5/6's warning is the pool's, not the page's. Solid red is only a confirmation's
  button: Files' and the Trash's Delete and Empty trash are `ghost-danger`.
- **Plain words.** Sentence case for every title, label and button of the four catalogs; an action
  that opens a dialog ends in "…"; profiles read "RAID 1 (2 copies)", "Single"; disks are "Awake"
  or "Asleep" with "Idle timeout Off ✎"; fact labels have no colon (`.kv`, with `data-field` for
  the specs); the system disk's status is neutral; "Found on the disks" is a compact list after the
  pools and disks, with a secondary "Import…" each.
- **Dialogs say it once and scroll as a whole.** Create pool lists only the disks a pool can take,
  the others folded behind "N disks not available" with their reasons; the field for the typed name
  starts empty and its label names the value. Remove pool is one step: what UNOS does, as three
  consequences, then the name typed. A server's raw error shows under a plain title, once per page
  (a card whose cause a card above names already does not repeat it).
- **Tables fit a phone.** Snapshot and trash names keep their words and end in an ellipsis with the
  full path in the tooltip; the card names the snapshots' folder once; dates and the trash's folder
  move under the name on a phone. The snapshot picker is in the page header, Restore is a word, and
  the About card is gone: its rules are where they apply (the subtitle, the create and restore
  dialogs, a "Schedule snapshots →" link to the Scheduler).
- **The Trash is in the navigation, under Files** (`webui/src/lib/components/layout/NavMenu.svelte`),
  in the order the command palette already listed it; the sidebar marks the most specific link, so
  Files is not marked on its Trash. Files' header has no second link to it, the Trash page no link
  back (ADR-0431). Files' header has Upload as its primary and New folder in words; the selection's
  actions show only while something is selected, in a row that keeps its height.
- **An empty state says what to do, and repeats no header button**: "No pools yet" counts the disks
  free for one, an empty snapshot list names Create snapshot and the Scheduler, the dashboard's
  storage card says to create one on the Storage page, which its Manage link opens.

`webui/tests/dashboard.spec.ts`, `redesign.spec.ts`, `storage.spec.ts`, `snapshots.spec.ts`,
`files.spec.ts`, `navigation.spec.ts` and `mobile-layout.spec.ts` check these.

## Consequences

- The banner on the dashboard is the same component and the same region as on every page; a spec
  that counts it on the dashboard of the stand-in fakes the predictions, which the stand-in cannot
  read.
- More, the selection bar, Remove pool's dialog, the empty states and the disabled reasons are
  built so that the design system's ActionMenu, BulkBar, ConfirmDialog, EmptyState action and
  Button `disabledReason` replace them in one line each. Snapshot names are in UTC while their
  Created column is in the browser's zone until the NAS's time zone (`nasTime`) exists.
- A folder's size cell stays empty (it is not known without walking the folder) instead of "-".
  Items left for later: sorting and a filter in Files; a link from Snapshots to a pool's volumes,
  which have no page of their own.

## Considered options

- A breadcrumb "Files / Trash" on the Trash page: it would bring back the link to Files that
  ADR-0431 removed as a duplicate of the sidebar's.
- A Create button in each empty state: a second place for the header's primary action.

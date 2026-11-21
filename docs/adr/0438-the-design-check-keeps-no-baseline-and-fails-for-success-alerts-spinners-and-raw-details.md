---
status: accepted
date: 2026-11-21
area: webui
decided-by: owner
supersedes: [ADR-0430, ADR-0432]
---

# ADR-0438: The design check keeps no baseline and fails for a success alert, a hand-made spinner and a raw details element, and no line shows the live connection

## Context

ADR-0432 let `webui/scripts/design-check.mjs` admit the raw checkboxes and radios and the filled
primary buttons in cards that pages had when its rules came, by file counts in
`webui/scripts/design-baseline.json`, until the UX work's chunks 2 to 5 moved the pages onto
`webui/src/lib/components/ui`. They did, and the baseline is empty. The owner asked to lock that in,
so that the hand-made patterns cannot come back: also the success alerts after an action, the
spinners and the disclosures that the pages made by hand. The same pass found the live connection
said twice, on the dashboard and in the sidebar (against ADR-0431's one place per fact).

## Decision

- **No baseline.** `design-baseline.json` and the counting of `design-check.mjs` are gone: a raw
  checkbox or radio and a filled primary `Button` in a card's header or a table's row fail like
  every other rule of ADR-0432, unless a `design-ignore: <reason>` comment stands above them.
- **A success `Alert` fails** outside `ui`: `variant="success"`, or a `'success'` literal in the
  variant's expression. What an action did is a `toast` (ADR-0432). An outcome to read before going
  on keeps its Alert with a reason: an app's install, update or rollback end on its job's card
  (`AppInstallJobs.svelte`), where the Trash and Snapshots restores put the items, and the end of
  the two-factor setup, which leads on to recovery codes (`mandatory-two-factor.spec.ts`).
- **A hand-made spinner fails** outside `ui`: the class `animate-spin`, with any variant, or
  `app.css`'s `loading-spinner`, which applies it. A wait is Button's `loading`, `LoadState` or
  `Skeleton` (ADR-0430); a job that runs (a scrub, a balance, a disk's removal) is a `StatusDot`
  with `pulse`, which gained the `info` status for it.
- **A raw `<details>` fails** outside `ui`: the disclosure is `TechDetails`, whose `text` is
  technical text with Copy and whose `children` snippet takes a list (an update's containers and
  images, a disk's risk factors). Two stay with a reason, since their specs read them: an NFS
  export's client options (`nfs-exports.spec.ts`) and the smb.conf import's report inside its alert
  (`shares.spec.ts`).
- The tooltip of a disabled button's reason (`.tooltip` of `webui/src/app.css`, Button's) is
  `content-inverse` on the new background token `surface-inverse` (`#dde1e7` dark, `#22262e` light),
  not on the text token `content`, so that `webui/tests/theme.spec.ts` needs no exception for it;
  `supervisor/tests/test_webui_theme.py` checks the pair at 4.5:1 in both themes.
- `supervisor/tests/test_webui_design_system.py` tests each rule, its exemption and that a file of
  counts beside the check admits nothing.
- **No connection line.** The owner, on the dashboard's Live / Disconnected line, which repeated the
  sidebar's Connected / Not connected line: "remove it", then "remove this line from everywhere".
  Both are gone (`DashboardHeader.svelte`, `SidebarUser.svelte`), with their catalog keys and the
  dashboard's time of the last stats; no page or header says whether the live connection is up.
  What a lost connection does otherwise stays: a stats card waits as a skeleton only while the
  socket may still answer, and the stores `wsConnected`, `wsForbidden` and `wsSettled` keep
  steering the pages and the boot screen (ADR-0430). The specs' assertions on the two lines are
  deleted with the owner's yes.

## Consequences

A page that brings back one of these patterns fails `make check` at once, with the file, the line
and the component to use; a kept use is visible in review by its reason. A running job no longer
spins: its dot pulses, and stops under reduced motion like the skeletons. ADR-0432's other rules
stay in force. ADR-0430's dashboard status chip and sidebar line (first ADR-0428's) are gone; its other rules stay.

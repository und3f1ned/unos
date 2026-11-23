---
status: accepted
date: 2026-11-23
area: webui
decided-by: agent
---

# ADR-0466: A shared component that no page uses is removed, and card headers' links and empty states' next steps use CardAction and EmptyState's action

## Context

ADR-0432 built the web UI's shared components for the UX work's chunks 2 to 5 (`webui.ux-data`,
`webui.ux-sharing`, `webui.ux-system`, `webui.ux-apps`) to adopt, and its entry
`webui.design-foundations` left open that `StatusSummary`, `BulkBar`, `Tabs`, `Segmented`,
`CardAction` with `href` and `EmptyState`'s `action` had no page. The chunks are built. `BulkBar`
(Files, Trash, Containers, SMB shares), `Tabs` (Shares, App Store, an app's details, Containers and
its drawer, Network, Notifications) and `Segmented` (Containers, Notifications' history, System Logs,
Resource Monitor) are used. `StatusSummary` has no page; four card headers drew their link to
another page by hand, three with a typed "→" in the text, and six empty states put their next step
in as children: four text links to Storage and two buttons of the default size. The owner delegated
the decisions, asking above all that they be safe.

## Decision

- **`StatusSummary` is removed**, with its classes in `webui/src/app.css` and its texts `ui.status.*`
  (no backward compatibility, ADR-0207). This changes ADR-0432 in part, whose rule that a page's
  several states are one `StatusSummary` ends here; `webui/AGENTS.md` no longer states it. No page
  needs it: the NAS's problems are the health banner's (`ProblemsBanner`, ADR-0332), which on the
  dashboard adds the dashboard's own issues (ADR-0434) and lists three at once, each with its link,
  and a page's own state is said on its object (a share's on its card, ADR-0435; Power's Status
  card, ADR-0436). The stacks of alerts that remain stay alerts, each by its object: on Shares the
  SMB service's (`SmbStatusAlerts.svelte`, whose smb.conf import report needs its list and its
  Dismiss, which a summary's line cannot hold) and the NFS service's (`NfsStatusAlerts.svelte`), and
  the outcomes of a save (`ShareOutcomeAlerts.svelte`), which are read before going on (ADR-0432);
  on System, the firewall's, the virtual interfaces' and remote access's alerts inside their own
  cards.
- **A card header's link to where its things are managed is a `CardAction` with `href`**, the
  arrow its icon: Manage of the dashboard's Storage pools and Containers cards, View all of Recent
  activity, Schedule snapshots of the Snapshots list. Their texts lose the typed arrow
  (`dashboard.manage`, `snapshots.schedule`); `.card-action-link` keeps the link on one line, so that
  a narrow header wraps it whole below the title.
- **An empty state's next step is `EmptyState`'s `action`**, a small secondary button or a link
  drawn as one, and the component takes no `children`, so that a page cannot put a control of its
  own there: Clear filters of Containers and of the App Store's catalog, Go to Storage of the SMB and
  NFS lists without a pool, of the iSCSI list without a volume and of Snapshots without a mounted
  pool (`shares.toStorage`, `snapshots.storagePage`: no arrow, both "Go to Storage").
- **Tests.** `webui/tests/ui.ts` gives the specs `expectCardLink` (the link in a card's header, its
  name without an arrow, the arrow an icon, its `href`) and `expectNextStep` (the empty state's
  control, small and secondary). `dashboard.spec.ts` checks the three widgets' links,
  `snapshots.spec.ts` the schedule's link and the way to Storage in English and Russian,
  `containers.spec.ts` and `appstore.spec.ts` Clear filters, `shares.spec.ts` and `iscsi.spec.ts` Go
  to Storage without a pool or a volume.

## Consequences

- The shared components are those that pages use; a new one comes with its page and its spec.
- The links to Storage of the empty Shares tabs and Snapshots are small secondary buttons where
  they were text links. A card header's link is not checked by the design check: review keeps it on
  `CardAction`, and `expectCardLink` checks its class where a spec names it.
- A link to another page in a sentence or a form keeps its typed arrow, since it is neither a card
  header's action nor an empty state's step: Backup's "Schedule backups →" in its tab's subtitle,
  and the access table's "Add a user →", "Create a group →" and "Turn SMB on in Users →" inside
  the share dialog, which open in a new tab so that the form is kept (ADR-0435).

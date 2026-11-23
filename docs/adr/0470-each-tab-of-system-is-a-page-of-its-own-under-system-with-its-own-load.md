---
status: accepted
date: 2026-11-23
area: webui
decided-by: agent
---

# ADR-0470: Each tab of System is a page of its own under /system, with its own load

## Context

ADR-0469 moves every page's reads into a SvelteKit load, so that a navigation keeps the open page
until the next one has its data and a page visited before appears at once from the tab's cache; the
owner chose it for all pages. System's twelve tabs were one route that showed the tab its URL's hash
named (ADR-0431, feature `webui.system-tabs`), and each tab and its cards read their data in
`onMount` behind skeletons. A load cannot see the hash (SvelteKit hides `url.hash` from it, and a
change of the hash alone runs no load), and one load for every tab would read twelve tabs' data,
Disk Health's smartctl on every disk among them, to show one. The owner delegated the decisions.

## Decision

- **The tab is in the path.** General is `/system`, the others `/system/<id>` (`network`, `remote`,
  `security`, `notifications`, `monitor`, `logs`, `health`, `updates`, `backup`, `scheduler`,
  `power`): each a route of `webui/src/routes/system/` whose `+page.ts` is
  `readPage(event, <tab>Reader)` of ADR-0469, with its reader in
  `webui/src/lib/pages/system/<tab>.ts` keyed `system:<tab>`. Every link to a tab names its path
  (the palette, the problems banner, the dashboard, Snapshots, Backup's and the off-device copy's
  links to the Scheduler); a link with the old hash opens General (no redirect, ADR-0207). This
  changes ADR-0431 in part (each tab has a hash of its own) for System only, and the first step of
  `webui.system-tabs` with it: the Account page keeps its hashes. `.gitignore`'s `logs/` no longer
  hides `webui/src/routes/system/logs/`.
- **The layout holds the page, the route the tab.** `webui/src/routes/system/+layout.svelte` draws
  the title and the groups of tabs, whose entries are links (`PageTabs` takes an `href` per tab,
  marked `aria-current="page"`), and the open tab from the path. A switch of tabs is a navigation:
  the open tab and its marked link stay until the next tab has its data, a tab visited before
  appears at once with its last data, Back and Forward go from tab to tab, and the sidebar marks
  System for every tab.
- **What each tab reads, and the areas whose changes forget it** (ADR-0469's invalidation by the
  first part of a changed path): General the system's facts and the host's name, time zone and
  zones (system, network: a saved host name also forgets the Dashboard's, Shares' and Network's);
  Network the interfaces, DNS, routes, sockets and the bonds, VLANs and bridges (network, apps,
  containers); Remote Access the tunnel (network, security: ufw's rule for its port); Security the
  administrator's two-factor status, the requirement, the firewall and the certificates' status,
  then the stored ones (auth, security, network: remote access and the apps' names add and remove
  firewall rules); Notifications the history and the channels (notifications); Resource Monitor the
  hour of history with the moment it answered (monitor); System Logs the sources, then the 200
  newest entries of the journal (logs); Disk Health every disk's SMART data and the predictions
  (health, storage); Updates the version and the updater's progress, history and settings
  (updates); Backup & Restore the backups and the off-device copy's settings, state and tasks
  (backup, offsite, scheduler, storage); Scheduler the tasks, pools and disks (scheduler, storage,
  system: the next runs follow the time zone); Power Management the pending action, the settings,
  the plan and Wake-on-LAN (power, system).
- **Notifications is never cached** (`cache: false`): `GET /notifications/configs` answers the
  channels with their passwords, tokens and webhook addresses as stored.
- **A restored configuration forgets every page**: Backup & Restore calls `changedEverything` of
  `webui/src/lib/pages/cache.ts` after a restore, which empties the cache and makes every read that
  started before it old, since the restored database replaces what any page read.
- **Slow parts that are not the tab's point** are `later` parts that the load does not wait for,
  their place waiting (`data-later`): Updates' check for the newest release (it asks GitHub),
  Network's local names of apps (they ask Docker for the apps' ports), the off-device copy's
  snapshots (restic lists them from the copy's target), System Logs' entries (journalctl can
  take seconds on a large journal: the tab shows its sources and filters at once, and a source
  picked meanwhile is the page's own read), and the Scheduler's pools and disks, which only its
  task form offers (the form says they are on their way until they come). A change or a check of the card's own
  outranks a later part still on its way.
- **The cards take what the load read.** A card that read in `onMount` (the firewall, the
  two-factor requirement, the certificates, the tunnel, the bonds, the apps' names, the host's
  name and zone, the power plan, Wake-on-LAN) takes the load's value as a prop or through its
  class's `apply`, at once and again for the fresh read, and keeps its own reads after its changes,
  its Retry and its countdowns. A form a user has changed keeps what was typed when a fresh read
  comes (General's fields, the power settings, the tunnel's settings and the off-device copy's
  form, which are filled again only when the stored settings differ). System Logs takes the load's
  entries only while it has asked for none of its own (a filter, Refresh, Auto); Updates takes the
  load's check only until it checked itself. Disk Health's Refresh runs its load again
  (`refreshPage`).
- **Countdowns count from their read.** A change of the firewall, of the bonds or a disabled
  interface that waits to be kept is undone `revert_in` seconds after the read that said so: the
  snapshots carry when those reads answered (`firewallAt`, `interfacesAt`, `virtualAt`), a countdown
  found in a read counts from then and a newer read corrects it, and only a countdown of the card's
  own change keeps its deadline. A revisit from the cache thus never offers Keep after the host has
  undone the change.
- **Live parts stay**: Resource Monitor's and the dashboard's stats from `/ws/v1/events`, System
  Logs' Auto over the journal socket (ADR-0366), the update job's progress (`updateProgress`), and
  the countdowns of Power Management (absolute times), the firewall, the bonds and a disabled
  interface.
- **Preloading** (ADR-0469): the tabs' links take `preloadOf` like the sidebar's. Notifications,
  whose reader asks only `$lib/api/notifications` (UNOS's own files on the system disk, in
  `HOVER_SAFE_API`), is in `HOVER_PAGES` (`/system/notifications`); every other tab preloads on the
  press of its link: Disk Health runs smartctl on every disk, Scheduler lists the pools and disks,
  and the others ask the host or modules not yet checked for `HOVER_SAFE_API`.
- **Tests.** `webui/tests/system.spec.ts`: the tabs are links at their paths; a walk through all
  twelve tabs keeps the open tab, its marked link and `<main aria-busy>` until the next tab has its
  data, with no frame of a skeleton (but the `data-later` places) or of a page without its tab; a
  revisit of General appears at once from the cache and the fresh read replaces it; a hover reads
  nothing but for Notifications, and the press of Disk Health starts its read of the disks.
  `webui/tests/system-general.spec.ts`: a saved host name makes General and the Dashboard wait for
  their fresh reads on the next visit. `webui/tests/page-loads.spec.ts` takes System into its loop
  of sidebar navigations; `supervisor/tests/test_webui_page_loads.py` checks that Disk Health and
  Scheduler could not preload on hover.

## Consequences

- A first visit to a tab waits as long as its slowest read, with the open tab up meanwhile, instead
  of drawing skeletons; a revisit in the same session is instant, and a change makes the next visit
  to the tabs of its area a first visit again. Notifications reads afresh on every visit.
- Each tab is its own chunk of code, as the `{#await import()}` of each tab was.
- Bookmarks and links with `/system#<tab>` open General.
- A tab's first page (a reload, a link from outside) shows its skeleton after 300 ms, as every
  converted page does; Security and Remote Access got one, since their cards drew their own.
- Wide areas (a remote-access change forgets the Network tab and the Dashboard, a container removed
  forgets Network) cost a first visit. A change that a tab's areas do not name (one made in another
  browser, or on the host) shows there only with the fresh read, as on every converted page.
- A fresh snapshot can still land after the Network tab's own Refresh and show the interfaces of a
  moment before it; the next read corrects them.

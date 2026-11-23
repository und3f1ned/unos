---
status: accepted
date: 2026-11-23
area: webui
decided-by: owner
---

# ADR-0469: The web UI's pages read their data in load functions, and a navigation keeps the open page until the next one has its data

## Context

Every page of the SPA read its data in `onMount` and showed the skeleton of ADR-0430 meanwhile, so
every navigation drew a skeleton: on the stand-in with the BPI-W3's API times, the data came 248 ms
(Storage), 423 ms (Shares) and 464 ms (Dashboard) after the click, at the p50. The spike
`spike.rendering` (not merged) measured the SPA with SvelteKit's universal load functions against
server rendering with `@sveltejs/adapter-node`: with loads the old page stays until the data are in,
a first visit takes about as long, a visit from a cache 31-32 ms and a first visit preloaded 200 ms
before the click 36-51 ms, never with a skeleton. Server rendering was ruled out: a page request
carries the session cookie but not the tab's session key (ADR-0397), so the server could render no
signed-in page, and Node costs 66-100 MiB per board. The slow part is the supervisor
(`/containers?all=true` 417 ms, `/shares/paths` 181, `/shares/smb-interfaces` 130). The owner chose
the loads for all 23 pages ("yes, all pages"); this change builds the shared part and Dashboard,
Storage and Shares, and `webui/AGENTS.md` ("Page loads") holds the recipe for the other 20.

## Decision

- **Universal loads, no server.** A converted page reads in its `+page.ts`, `load = (event) =>
  readPage(event, <page>Reader)` of `webui/src/lib/pages/load.ts`; the static adapter stays, and
  `webui/src/routes/+layout.ts` sets `ssr = false`, so that the loads run in the browser only, also
  under `vite dev`. SvelteKit keeps the open page while the load runs. A page reader
  (`src/lib/pages/dashboard.ts`, `storage.ts`, `shares.ts`) makes the page's API reads through
  `$lib/api` as one snapshot that holds each read's value or its failure (`settle` of
  `src/lib/pages/read.ts`), in the order the page had (Storage's disks alone first, ADR-0378).
  `readPage` reads for the signed-in account (the store's, or `/auth/me` once for every caller,
  which then starts the live connection, as the root layout would; a tab that finds no session
  key knows it has none), reads nothing for no account, an account that can only set up two-factor
  authentication or, for an administrators' reader, another account (ADR-0030), and returns
  `readFor` (the session's count, the account's id and role): the root layout shows a page only
  with data read for the session and account of now, and runs the load again
  (`invalidate('unos:account')`) when another account signed in meanwhile. A read that answers after
  the session or the account changed is nobody's: it is neither kept nor shown.
- **A stale-while-revalidate cache** (`src/lib/pages/cache.ts`): per tab, in memory only, an entry
  per reader `key` (the page and what it reads by its URL), for the session it was read in; emptied
  whenever the account or its role changes, a sign-out included. A page visited before appears at once with its
  entry, and the fresh read replaces it when it answers (`followPage` of
  `src/lib/pages/follow.svelte.ts`); without one the navigation waits for the read. It holds what
  the pages show; a reader whose snapshot holds a secret sets `cache: false`.
- **Changes.** `apiFetch` tells `onApiChange` listeners of every request but GET and HEAD, answered
  or failed; the cache takes the first part of its path as the area that changed and forgets every
  entry whose reader names that area (`areas`: the first part of each path it reads and of what
  those answers show; Dashboard's system, network, updates, storage, files, snapshots, containers,
  apps, notifications, health and auth; Storage's storage, files and snapshots; Shares' shares,
  storage, snapshots, auth, groups and network). A read that started before its areas' last change
  is old: it is not kept; a navigation reads again (three times at most); a page does not show an
  old fresh snapshot, since the page reads again itself after its own change, and runs its load
  again (`invalidate('unos:page:<key>')`) for an old snapshot with nothing fresh behind it, and when
  it has shown nothing and its fresh read failed. The pages keep their own reloads after their
  changes; a fresh snapshot comes second to one of them that started later (Storage's lists) and
  waits until a dialog closes; Refresh is `refreshPage`. A snapshot read longer than 5 s before the
  page shows it without a fresh one (a pressed link that was clicked later) is shown, and read
  again.
- **The tab's first page** shows its skeleton only when its reads take longer than 300 ms
  (`FIRST_LOAD_SKELETON_DELAY_MS`), under the boot screen as before; after the first navigation
  (`afterNavigate`) no load shows a skeleton. This supersedes ADR-0430 in part (its rule that each
  page's first load shows its skeleton): ADR-0430's other rules stay, and the pages not converted
  yet follow it. The root layout no longer hides the open page while it navigates to a page of
  another gate: SvelteKit 3 gives it the next page and its URL in one update.
- **The navigation's progress**: while SvelteKit navigates, the root layout draws
  `.navigation-progress` (`src/app.css`), a 3 px bar across the top in the brand colour that
  appears after 200 ms, so that a fast navigation shows nothing, and breathes under reduced motion;
  it has no text and no role, and `<main>` is `aria-busy="true"` meanwhile.
- **Slow parts that are not the page's point** are `later(...)` parts that the load does not wait
  for: the dashboard's containers, whose card waits in its place (`ContainersWidget`'s `pending`).
- **Preloading** (`data-sveltekit-preload-data`): `app.html`'s body preloads data on tap (the
  press of the pointer, a touch) and code on hover. A page preloads its data on hover only when its
  load reads no data disk and answers at once (`HOVER_PAGES` of `src/lib/pages/preload.ts`, which
  the sidebar's links take): a pointer passing over a link must never wake a sleeping disk
  (ADR-0378). None of the three pages qualifies, and they preload on tap; the list is empty: the
  dashboard lists the pools, which scans every disk, and waits for Docker; Storage reads every disk;
  Shares lists the volumes of the mounted pools (ADR-0468 reads them without a scan) and waits for
  smbd's status and the network's interfaces (100-180 ms on the BPI-W3).
- **Shares' interfaces card** takes its interfaces from the page, which reads them in its load and
  takes the card's answers, so that the card shows them again at once after another tab.
- **Tests.** `webui/tests/page-loads.spec.ts`: a navigation between the three pages keeps the open
  page (its URL, its marked link, `<main>` busy) until the next has its data, with no frame of a
  skeleton or of an empty page and the bar shown; a revisit appears at once while its fresh read is
  held, and the fresh answer replaces it; a hover over each sidebar link reads nothing, and the
  press of the Storage link starts its reads, the disks first; a dashboard layout changed and left
  is not shown older on the next visit; a regular user signed in after an administrator never sees
  the administrator's dashboard. `webui/tests/loading.spec.ts` keeps the first page's skeleton when
  the API is slow and checks that a fast first page draws none; the page specs check the
  containers card waiting and the revisits. `supervisor/tests/test_webui_page_loads.py`: every
  `+page.ts` loads through `readPage`, no page has a server load, the body preloads on tap, and a
  page of `HOVER_PAGES` reads only `HOVER_SAFE_API` modules (the database's: auth, groups, audit,
  notifications, dashboard, theme), which the three pages do not.

## Consequences

- Measured on the stand-in with the BPI-W3's API times (the spike's `nav.mjs`, 6 sessions of 3
  rounds, p50 from the press of the link to the page with its data): before, Storage 248 ms,
  Shares 405 ms, Dashboard 466 ms, each behind a skeleton every time; after, a first visit 244 ms
  (Storage) and 244 ms (Shares) with the old page up, a revisit 31-32 ms, the Dashboard 30 ms (it
  is cached from the sign-in), and no skeleton in any navigation.
- A revisit without a change in between is instant; the first visit to a page waits as long as its
  slowest read before the page shows, with the old page and the bar meanwhile, and a hung read keeps
  the old page until the user goes elsewhere; a reader that throws fails the navigation with
  SvelteKit's error page. A change makes the next visit to the pages of its area a first visit
  again, and an area a reader does not name shows its change only after the fresh read.
- "Tap" is SvelteKit's press of the pointer or a touch: a scroll of the phone's drawer that starts
  on a link starts that page's reads too.
- After another account signed in elsewhere, the page waits for its reads for that account under
  the shell's placeholder, as it waited for the account before.
- On the tab's first page the account is read by the load, before the layout is drawn: a 401 there
  reloads to the sign-in page instead of a navigation.
- The 20 pages not converted keep their `onMount` reads and their skeletons until each follows the
  recipe of `webui/AGENTS.md`; a page's data in memory grows with the pages visited, and goes with
  the tab.

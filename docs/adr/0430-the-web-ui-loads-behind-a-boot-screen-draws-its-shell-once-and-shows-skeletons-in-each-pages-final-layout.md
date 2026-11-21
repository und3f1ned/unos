---
status: superseded
superseded-by: ADR-0438
date: 2026-11-20
area: webui
decided-by: agent
supersedes: [ADR-0327]
---

# ADR-0430: The web UI loads behind a boot screen, draws its shell once and shows skeletons in each page's final layout

## Context

Superseded by ADR-0438: only in part, the dashboard's status chip and the sidebar's connection line, which say "Connecting…" until the first attempt has ended and are gone now; every other rule here stays in force.

The owner asked, in translation, "why does the interface flicker while it loads? why is there no
proper loader?". The causes, measured on the stand-in with a filmstrip of each main page and its
cumulative layout shift (CLS): `src/app.html` had no loader, so the page was empty until the app
ran; the root layout drew the sidebar before the account was known (its links and the Search
button came a frame later, the menu jumped) and said "Loading…" until the account came; then each
page fetched its own data and said "Loading…" or showed nothing, and its blocks arrived one by
one: the dashboard's host name, subtitle, Customize button, alert and widgets (CLS 0.09 with the
API 400 ms slow), System > General's three cards (0.085), the Users page's groups under its users,
the App Store's error above its catalog. A refresh of some pages blanked them again (Containers,
Disk Health, the installed apps). The entry is `webui.loading-without-flicker`.

## Decision

- **A boot screen** (`#boot` in `webui/src/app.html`, `.boot` in `src/app.css`): the page's
  background, the sidebar's logo larger and, after 400 ms, a thin indeterminate bar. It is drawn
  by the HTML and the style sheet alone from the first frame (no script: the CSP allows only
  SvelteKit's own), in the theme that `static/theme-init.js` set; the product's name is generated
  content, and the element is `aria-hidden` with no role, so that screen readers and the specs read
  the page under it. The root layout calls `finishBoot` of `src/lib/boot.ts` once the page is
  drawn, its skeleton at least: for the account, for nobody (the sign-in and first-run pages, at
  once), or with the error of the account. A signed-in page also waits, 1 s at most, for the
  account's theme (`loadAccountTheme`), so that it is not drawn in the browser's theme first, and an
  administrator's for the problems banner, which comes with the events socket's first message
  (`problemsKnown`, or the socket's first attempt failed or was refused), so that the banner does
  not push the page down. `finishBoot` marks it done: it fades out in 160 ms, no longer takes the
  pointer, and is removed. Under reduced motion it goes at once and its bar breathes instead of
  sliding.
- **The shell is drawn once, with the role.** Under the boot screen the account is known before
  anything shows; while it is not (a session that changed in another tab), the sidebar draws the
  shape of its links, of the Search button and of the user block as skeletons, and the page's
  place a page skeleton, instead of an empty menu and "Loading…".
- **`Skeleton`** in `src/lib/components/ui`: `line` (lines as tall as a text size's line:
  `eyebrow`, `xs` to `xl`, `title`, `stat`, `control`), `block`, `circle`, `row` (rows of a table,
  as tall as its rows), `tile` (the tallest stat tile's structure), `card` and `table` (a flush card
  with its head and rows). Every skeleton is `aria-hidden` with `data-skeleton`, carries no text and
  no role, and is made of `div`s and `span`s, not of a table's elements, which a spec's CSS
  (`tbody tr`) or role could find; the region that loads has `aria-busy="true"`. Its bones are `--skeleton-bone` and one light
  `--skeleton-shine` sweeps over every bone of the page at once (a gradient fixed to the window);
  both are existing tokens per theme, no new colour. Under reduced motion they stand still.
- **Each page's first load** shows its skeleton in the page's final layout in place of everything
  that load brings, and swaps it for the data at once, so that no part of it stays to be pushed
  down: Dashboard (the default layout of the account's widgets; the header's name and system as
  lines), Storage (the disks before the pools, ADR-0378, behind one skeleton), Shares, Users,
  Containers, the App Store, Audit Log, System > General (its four cards), Snapshots, the System
  tabs' cards, Files (the breadcrumbs, the selection's buttons and the list, until both the folder
  and the uploads' leftovers, whose notice sits above them, have answered), the trash. A part that
  loads on its own afterwards sits last (Shares' interfaces card) or keeps its place (the App
  Store's subtitle line, the badges of the firewall's and the certificates' cards). No "Loading…" text is left; the keys that said it are gone. A dialog that says what it
  reads ("Reading what the update changes…", "Checking the disks…") keeps its sentence.
- **A refresh keeps what is shown**: a page tracks its first load (`loaded`, `drawn`) and shows the
  skeleton only then; the Refresh button spins. Containers, Images, the volumes' dialog, the
  snapshots' lists and the login history keep their list, and their error until a load succeeds;
  the App Store keeps its counts from the last load that succeeded (`catalogKnown`,
  `installedKnown`); a snapshots' list shows its skeleton only for another pool or volume.
- **What a page needs at once is fetched in parallel**: the Users page loads its groups with its
  users (`GroupsCard` takes the page's `GroupDirectory`, and loads again only with a new list of
  users); the dashboard, the App Store and System > General already did.
- **The live connection's first attempt** (`wsSettled` of `$lib/stores/stats`: the socket was up,
  refused, or closed before it was up; false again after a sign-out). Until it has ended, the
  dashboard's status chip and System > Resource Monitor's badge say "Connecting…", not
  "Disconnected" (ADR-0428's chips gain this state before the first), and so does the sidebar's
  line, which ADR-0428 had say "Connecting…" until the socket had been up once, also when it never
  came up: it says "Not connected" once the first attempt failed; a stats card of the dashboard and the Monitor's cards wait as skeletons of
  their tiles, in their places, while the attempt runs or the socket is up without its first
  stats, and are left out (the Monitor says it waits) once it ended without them or for an
  account the socket refuses. ADR-0395's stats card still shows with the live stats.
- **The dashboard**: Customize shows while the page loads, disabled, and goes only when the layout
  could not be read; the header's host name and system are lines of its height until they come.
- **The App Store (supersedes ADR-0327 in part)**: on its first load the catalog waits for the
  installed apps' answer up to 1 s after its own, so that the page is drawn once; a Docker that
  takes longer no longer holds it (ADR-0327 otherwise stays: each list loads on its own, a count
  only from a load that succeeded, a failed load says so). The two lists' errors sit under the
  tabs, drawn with the lists.
- **Tests.** `webui/tests/loading.spec.ts` delays every API request by 700 ms and samples every
  frame of the dashboard and each main page: the first frame is the boot screen, no frame shows a
  visible "Loading" or nothing, the navigation has its ten links and Search from the first frame
  after the boot screen (ten for an administrator, two for a regular user's dashboard), and the
  CLS stays below 0.05; the boot screen is marked done with the page's skeleton drawn, fades out
  in under 200 ms and is removed; the sign-in page drops it at once; an account that cannot be
  loaded shows its error; a socket that does not answer holds the page a second at most, says
  "Connecting…", then "Disconnected from the NAS" and, in the sidebar, "Not connected", without
  waiting cards; a refresh of the
  dashboard and of the file browser keeps them. `supervisor/tests/test_webui_loading.py` checks the boot
  screen's markup and style, the skeleton's attributes and that no catalog text says "Loading".

## Consequences

- The first frames show the logo instead of a half-drawn page; an administrator's first page can
  wait up to 1 s more for the socket. If the app's script fails to run, the boot screen stays,
  where the page was empty before.
- A new page or card follows the same rule (`webui/AGENTS.md`): a skeleton of its final layout on
  the first load, swapped at once, and no blanking on a refresh; `loading.spec.ts` covers the main
  pages only.
- Skeletons approximate: a page whose data are taller or shorter than its skeleton changes height
  once, at the swap, which moves nothing that was drawn before.

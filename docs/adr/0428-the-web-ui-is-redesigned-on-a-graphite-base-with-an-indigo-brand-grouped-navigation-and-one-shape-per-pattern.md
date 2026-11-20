---
status: superseded
superseded-by: ADR-0430
date: 2026-11-19
area: webui
decided-by: agent
supersedes: [ADR-0302, ADR-0393, ADR-0400, ADR-0405]
---

# ADR-0428: The web UI is redesigned on a graphite base with an indigo brand, grouped navigation and one shape per pattern

## Context

Superseded by ADR-0430: only in part, the System page's groups (Notifications is a setting) and its view for regular accounts, the two runs of Security, the user block's link (now `/account`), the Quick Actions grid, and the rules of ADR-0393 that this ADR kept for the palette's own language and Logout actions, General's entry and a regular user's Account at `/system#security`; every other rule here stays in force.

The owner asked, in translation, to "refactor and redesign the interface" together with the move
to Svelte 5; ADR-0427 did the refactor and kept the look. The look had grown by page: ten flat
links beside twelve System tabs in a second sidebar, a System page without an `h1`, four shapes of
page header, card titles and tab bars that differ by page, a brand blue that was also the info
blue, green that also meant "download", focus rings that painted the page colour around buttons on
cards, and on a phone twelve tabs wrapping into four or five rows before the content. A written
proposal with a mockup was drafted; the owner delegated its choices, and the lead took its
recommendations: an indigo brand, a visible search button on desktop, System's tabs in three
groups, the system's own fonts. The entry is `webui.redesign`.

## Decision

- **Tokens** (`webui/src/app.css`, both themes; the names and roles of ADR-0405 stay). A neutral
  graphite base: dark page `#0f1115`, cards `#171a20`, fields and secondary buttons `#22262e`;
  light page `#f3f4f7`, cards white, fields `#eceef2`. The brand is indigo `#4a55d6` (hover
  `#3d47c0`) in both themes, its ring `#8f98ff` dark and `#4a55d6` light, apart from the blue of
  `status-info`. The six accents are six hues (CPU blue, memory violet, network lime, storage pink,
  container cyan, temperature orange) and colour only chart series and a stat tile's icon chip;
  other icons are `content-secondary`. New tokens: `content-link` (text links, which used
  `status-info`; `#a9b1ff` / `#3a44b8`) and `shadow`, the colour of the three shadows
  `--shadow-raised`, `--shadow-popover` and `--shadow-dialog` of each theme block (Tailwind's
  `shadow-raised`, `shadow-popover`, `shadow-dialog`), so that `app.css` writes no colour. The
  scrim is 60 % dark and 45 % light (`--scrim-alpha`). The values of each token are in `app.css`.
- **Contrast.** `supervisor/tests/test_webui_theme.py` adds the pairs that the redesign uses:
  `content` on each alert's 10 % tint (an alert's body is `content`, its title and icon the status
  colour), `content-link` on the four backgrounds, on `surface-hover` and on the brand 20 % tint,
  `content-secondary` on `surface-hover` and on the brand tint, `brand-ring` against the brand tint
  (the active item's bar) and `surface-hover`, `border-strong` against `surface-hover`, and each
  accent against its 20 % tint (3:1, the icon chips). Text and icons sit only on 10 % and 20 %
  tints; the other alphas are borders (35 %), hovers (60 %) and the fields' focus halo (25 %).
- **Type, shape and motion.** The system font stack (`ui-sans-serif, system-ui, ...` and
  `ui-monospace, ...`, `tailwind.config.js`), no web font: every one carries Cyrillic, and a
  self-hosted font would cost 100 to 300 KB a weight. Body text 14 px; `2xs` 11 px; page titles
  24/600, card titles 15/600, the new `.eyebrow` (11 px, 600, upper case) for group labels; stat
  values, table cells, sizes and times in tabular figures. Radii 6 px for controls, badges and
  tabs (`rounded`), 10 px for cards and menus (`rounded-card`), 12 px for dialogs
  (`rounded-dialog`). Cards are set apart by a 1 px `border`, not by shadow. One focus style for
  every control: `:focus-visible` draws a 2 px `brand-ring` outline 2 px out, nothing on a mouse
  click and no ring offset in the page colour; a field gets a `brand-ring` border, a 1 px ring and
  a halo.
  Transitions of 120, 180 and 240 ms stop under `prefers-reduced-motion: reduce`, and so does
  `animate-pulse`; spinners turn at half speed.
- **Components of `ui`** keep their props; new ones are optional. `Button`: heights 32, 36 and
  44 px, `secondary` with a 1 px border, a new `ghost-danger` variant (an icon button that deletes),
  `loading` sets `aria-busy`, and under `pointer: coarse` no `Button` is below 40 px. `Card`:
  `title`, `description`, `headingLevel` (an `h3` by default) and an `actions` snippet draw its
  header. `Badge`: `dot`. `Input`: `icon`, and `aria-invalid` with `aria-describedby` naming its
  hint or error. `Alert`: `size="sm"`. `EmptyState`: `compact`. `StatusDot`: `label` for screen
  readers. `Modal`: a 32 px ghost close button, a footer that is right-aligned from `sm` and shares
  the row below it, and a danger dialog's warning icon in a tinted circle with the title in
  `content`; its stack, Escape, focus and inert logic stay (ADR-0361).
- **Patterns of `app.css`**: `.table` cells with side padding, `.num`, `.table-compact`,
  `.table-actions` (always visible, a touch screen has no hover) and `.card-flush`; page tabs are
  `.tab-underline`, `.tab-pill` is the segmented control of filters and views; `.nav-item` for the
  sidebar, and System's tabs in its look, the active one a brand 20 % tint with a 3 px `brand-ring`
  bar; `.kv`
  (label over value, `div > span`, so that the specs' `div[span="Mount:"]` still finds a field),
  `.toolbar` with `.toolbar-group`, `.page-subtitle`, `.content-narrow` (960 px, for the System
  tabs that are forms). Every page header is `h1.page-title`, an optional subtitle and the actions,
  the primary one last, with no icon tile.
- **Navigation.** The sidebar keeps its ten links, their texts, `href`s and order (the command
  palette lists them in that order), with Dashboard alone on top and three group labels (Data:
  Storage, Snapshots, Shares, Files; Applications: Containers, App Store; Administration: Users,
  Audit Log, System), each a `<p>` naming its `<ul>` by `aria-labelledby`; a regular user's two
  links get no labels. The `nav` is labelled "Main". Under the logo, from `md`, a "Search" button
  with the key hint (`kbd`, hidden from screen readers, `aria-keyshortcuts`) opens the command
  palette; its name, "Search pages and actions", starts with the word it shows (WCAG 2.5.3) and is
  not the exact name of System > Logs' Search button. Above the user block, on every page, a
  line says whether the live connection is up ("Connecting…" until it has been up once, then
  "Connected" or "Not connected", and "No live data" for an account the socket refuses; a polite
  live region). For an administrator the user block is a link to `/system#security`, whose name
  ends in "Your account" (a text for screen readers; a `title` for the mouse). Below `md` the
  drawer slides in. The problems banner lists three problems and puts the rest behind "Show all N".
- **The System page** gets an `h1`, "System" or "Account", and its twelve tabs (ids, hashes and
  labels unchanged, General the default) come in three groups in this order: Settings (General,
  Network, Remote Access, Security), Monitoring (Resource Monitor, System Logs, Disk Health,
  Notifications) and Maintenance (Updates, Backup & Restore, Scheduler, Power Management). The
  palette lists them in the same order. From `xl` the groups are a 220 px column beside the tab;
  below it they are labelled rows of chips above it, so that from `md` to `xl` the tab keeps the
  width. A regular user sees the one Security tab without labels. Inside Security the cards come in
  two runs, for administrators under divider labels, "Your account" and "This NAS" (the 2FA
  requirement, the firewall, the certificates); a regular user's tab is the first run alone. Splitting Security and moving Disk Health to the
  Storage page are not part of this decision.
- **The dashboard** (ADR-0395 stays): a status chip in the header, by priority "Live stats are not
  available for this account", "Disconnected from the NAS" (the key `dashboard.disconnected`, which
  said "WebSocket disconnected") or "Updated <time>", and for an administrator without problems a
  "No problems" chip; stat tiles with an icon chip in their accent, the value in `content` in the
  normal range and in the status colour above the thresholds (the classes `text-success`,
  `text-warning`, `text-error` stay), rates with a plain arrow; Quick Actions as a two by two grid;
  with every widget hidden, an empty state that points to Customize. "No problems" waits for the
  socket's problems message (`problemsKnown` of `$lib/stores/problems`).
- **The E2E specs** keep their hooks (routes, hashes, texts, headings, classes and the DOM shapes
  they walk). Three change, for the reasons above: `SYSTEM_TABS` of `command-palette.spec.ts` takes
  the new order, `websocket.spec.ts` checks that the new disconnected text is absent, and
  `theme.spec.ts` expects the active navigation item's text in `content` on its brand tint (it
  was white on a solid brand). New checks: the Search button opens the palette from `md`
  (`command-palette.spec.ts`, `mobile-layout.spec.ts`), the group labels of the sidebar and of
  System (`navigation.spec.ts`), and the new states (`redesign.spec.ts`). Where a spec reads a
  class that the look no longer needs, the class stays: the App Store's tiles keep `rounded-xl`,
  which `appstore.spec.ts` counts. The connection line is a polite `aria-live` region, not
  `role="status"`: the specs read a page's own status by that role.
- **Superseded in part.** ADR-0405: only its listed values of the light theme and the dark values
  it moved, now those of this ADR. ADR-0400: only its line that the new tokens have the hex values
  of the Tailwind colours they replace. ADR-0393: only "From md up there is no such button, and the
  sidebar's first button is still Logout": the Search button is there, and the header bar's button
  "Open the command palette" stays below `md` only. ADR-0302: only that from `md` System's tabs are
  a sidebar beside the tab and that Logout is the first button of a page from `md`. Every other rule
  of the four stays in force.

## Consequences

- Every page changes its look at once through the tokens, `app.css` and `ui`; markup changes where
  a page had its own shape. The screenshots of both themes and of a 375 px phone are the owner's
  review; no test sees a look.
- Body text is smaller (14 px, was 16 px) where a page did not size it.
- A page's first button from `md` is the Search button; specs that take `button` first only check
  that it is visible.
- A new sidebar link joins a group, and a new System tab joins a group and the palette's list in
  the same place; a new tint beyond 10 % and 20 % needs its pairs in `test_webui_theme.py`.
- The web UI's image is rebuilt once on the dev device; the supervisor does not change.

## Considered options

- Keeping the blue brand `#2563eb`: brand and info stay alike, so a selected tab looks like an
  info alert. A teal brand: close to the success green and the container cyan.
- A self-hosted Inter: 100 to 300 KB a weight with Cyrillic, for a look the system fonts nearly
  give.
- A top bar on desktop: 56 px less of every page; the page header is the header.

---
status: accepted
date: 2026-11-20
area: webui
decided-by: agent
supersedes: [ADR-0384, ADR-0431]
---

# ADR-0437: Containers and the App Store get one filter, quiet actions and plain states, and an app's container is removed only with its app

## Context

The owner called the redesign careless ("the design is slapdash, the UX limps") and asked to check
and remove every duplicate of every menu; the decisions were delegated, safety first. A UX audit of
the web UI found on the Containers page counts that did not add up (Running counted `running`,
Stopped `exited`, so paused or restarting containers were in neither), count cards and a native
state menu doing one job, row buttons that wrapped into two lines, a table whose state and buttons
were off a phone's screen, a raw one-line Docker error, lowercase Docker words as states, a drawer of
one card per fact that showed the state twice, and Title Case labels; in the App Store a filled
Install button on each of 69 cards (inside a card that is itself a button), publishers in status
colours, Install enabled while Docker was down, details that led with image digests, "6g" memory,
count tiles that repeated the tabs, an empty band above the title, orange Stop and red Uninstall on
every installed app, "not read yet" four times per card, Docker's "layers" on the install's card and
an update dialog that opened with digest arrows. The entry is `webui.ux-apps`, chunk 4 of the UX
work; chunk 1 (the design-system foundations) builds the shared components at the same time.

## Decision

- **Containers: one filter that adds up.** The state filter is one segmented control, **All ·
  Running · Stopped · Other**, each with its count (`groupOf` and `countByGroup` of
  `webui/src/lib/components/containers/labels.ts`): Running is `running`, Stopped is `exited`
  (as the dashboard counts it), Other every other state, shown only while a container is in it.
  The counts follow the search and add up to All. It replaces the count cards and the native state
  menu (`ContainerToolbar.svelte`; `ContainerStats.svelte` is removed): one place for the counts and
  the filter, as ADR-0431 asks of every fact. This takes the place of ADR-0431's Containers item
  ("the state select; the counts stay"): the segments are both.
- **Rows and cards.** A row's buttons sit on one line in a column as narrow as they are, each in a
  fixed slot (Open in browser, Start or Stop, Restart, Remove), as ghost icons with their names as
  tooltips and for screen readers; no status colours on a routine action. Remove, which deletes,
  is the quiet red of every delete icon (`ghost-danger`, `webui/AGENTS.md`), with no fill. A container that keeps
  restarting offers Stop and Restart, as a running one, since Start does nothing to it. The name keeps at least 16
  characters, the image is cut with its full name on hover, Select all shown is the table head's
  checkbox. Below 640 px the list is cards unless the user chose the table (`MediaQuery` in
  `webui/src/routes/containers/+page.svelte`); the cards of a row are as tall as the tallest.
- **The selection's bar shows only with a selection** (`BulkToolbar.svelte`): the count, Start,
  Stop and Restart selected and Clear selection, after the list and sticking to the window's foot, so
  that it pushes no row from under the pointer. Without a selection there is no bar of disabled
  buttons.
- **An app's container is removed only with its app.** A container with the label `unos.app` names
  its app as a link to `/apps#installed` (which opens the Installed tab) and has no Remove: removing
  one container leaves the app incomplete (ADR-0381), and Uninstall on the App Store is the one place
  that removes an app's containers. Start, Stop and Restart stay, as the server allows them.
- **Plain states and errors.** Docker's states are shown in sentence case, `exited` as Stopped, the
  filter's word; a stopped container is neutral, not red. When Docker does not answer (its 503,
  ADR-0323), the page says "Docker is not answering", what that means, Docker's reason in small type
  and Retry; the App Store does the same. Any other failed load says plainly that the containers or
  the installed apps could not be loaded, with the reason and Retry. While the installed apps are not
  known (not answered within the first load's moment, or failed: `installedKnown`) no card says
  Install and the details' Install is disabled with the reason as its description (`installBlocked`
  of `AppDetailModal.svelte`). Dates of the drawer and the Images tab have no seconds.
- **The drawer** shows its facts as labels over values (ID with Copy, created, image, app), the state
  once in its header, the name wrapped, Docker's labels behind a disclosure, and a Ports tab as a
  table. The create dialog has short sentence-case labels, a labelled field each for the host port,
  the container port and the protocol (`Select`), a variable's fields are Variable and Value (not a
  second Name), and it says under Name and Image what is missing instead of an error banner.
- **The catalog's card is the one control.** It has no Install button of its own; its foot says
  "Install ›" in the link colour (or the installed state, Installing, or why it is not available).
  Sources and categories are neutral badges, official and verified ones with a check. The category
  list takes the sidebar's selected look from `lg` and is a `Select` below it; the search is `Input`.
- **An app's details**: the app's name and icon are the dialog's title; the Overview leads with the
  recommended and minimum memory in the page's units (`memoryBytes` of `detail.ts` reads Docker's
  "6g"), the system disk the images take and the number of containers, then where the data goes, and
  ends with the containers and their pinned images; no count tiles (the tabs carry the counts), and
  Environment is called Variables. An installed app's details close with Close, not a disabled
  "Already Installed".
- **Installed apps**: the buttons are secondary and small (Update to, Open, Start or Stop, Restart)
  and Uninstall, which removes every container of the app, is a small quiet-red button
  (`ghost-danger`) with its word, a glyph of its own beside Remove earlier version's bin, and the
  app in its name ("Uninstall immich"); Roll back and Remove earlier version are neutral and say why they
  wait. The facts (Port, Pool, Group, Containers) are labels over values. Resources is a table in all
  but its markup: a line per container with Memory and CPU in columns and Limits at its end, and the
  use not read yet said once.
- **Progress and updates.** An install's card shows how many images are on the NAS and one bar of
  the whole download in which each image is an equal share: one on the NAS whole, one that
  downloads its part of the layers reported so far, any other none; and no layers. The bytes are
  not added up across the images: `bytes_total` counts only the layers that Docker reported so far
  (ADR-0384), so a sum would near 100% and drop as each next image starts. The rest of ADR-0384
  stays. The update dialog opens with the versions,
  how many containers get a new image, the download and the system disk, the database copy and the
  safety net; each container's images are in a disclosure.
- **Skeletons at every width.** The first load's skeletons take the layout of the width they are
  drawn at (ADR-0430): the App Store's the category menu beside the search below `lg`, and the
  list of categories from it; the Containers page's on a phone the filter's row and Select all
  shown above the cards. The filter's segments share a phone's row on one line, also in Russian
  with Other.
- **Left for chunk 1's components**, each a one-line swap: `confirm()` for the `window.confirm` of
  the App Store and the Containers page (left as they are), `ActionMenu` for Restart and Uninstall
  and the containers' Restart and Remove, `BulkBar`, `Segmented`/`Tabs`, `ErrorState`/`LoadState`,
  `Button disabledReason`, `Sheet` for the create and install dialogs, `Toast` for the Images tab's
  notices, `Checkbox` for the containers' selection boxes and `FieldGroup` for the create dialog's
  port row.

## Consequences

- `webui/tests/containers.spec.ts`, `apps*.spec.ts`, `appstore.spec.ts` and `mobile-layout.spec.ts`
  select the new names: the segments, "Create container", "Variables (n)", the facts by
  `data-field`, and the cards' "Install" text instead of a button.
- A container of an app can no longer be removed on the Containers page; an incomplete app is still
  shown and uninstalled on the App Store.
- The Containers page keeps no count that its filter does not show, and the dashboard's Stopped
  count (exited) matches the page's.

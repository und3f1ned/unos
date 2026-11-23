---
status: accepted
date: 2026-11-23
area: webui
decided-by: owner
supersedes: [ADR-0483]
---

# ADR-0484: The dashboard's widgets take their columns from the width of their grid, three wide ones to a row from 900 px

## Context

ADR-0483 put the dashboard's wide widgets (Storage pools, Containers, Recent activity) three to a
row from a 1536 px window. The owner's screenshot was 2000 physical pixels of a Retina screen: a
window of about 1250 CSS px, whose widgets' grid is about 970 px wide, so the owner still saw two
columns there. The owner asked to lower the threshold, with three wide cards in a row on that
screen, and the coordinator asked for a container query on the grid in place of a window
breakpoint. This changes the steps of `webui.dashboard-three-columns`.

## Decision

- **The columns follow the grid's width, not the window's.** The grid's wrapper is a query
  container (Tailwind 4's `@container`) and the grid's columns are container queries
  (`@min-[...]:`), so the sidebar, the page's padding and its 1440 px cap are counted once, where
  the width is measured. `WIDGET_CONTAINER`, `WIDGET_GRID` and `WIDGET_SPAN` of
  `webui/src/lib/dashboard-layout.ts` serve the widgets and the first load's skeleton alike
  (ADR-0430).
- **The rule, by the grid's width:** below 480 px (a phone) 2 columns, small cards two to a row
  and wide ones the full width; from 480 px (a tablet beside the sidebar) 6 columns, small cards
  three to a row and wide ones the full width; from 640 px wide ones two to a row; from 900 px 15
  columns, five small cards (3 each) or three wide ones (5 each) to a row. A window with the
  sidebar reaches 900 px at 1196 px: the owner's 1250 px, a 1280 px and a 1440 px laptop show three
  wide cards (289 to 469 px each), a window of about 1100 px two.
- **The stats cards are five to a row from 900 px** (167 px at the threshold, as on a phone),
  three from 480 px.
- **The rest of ADR-0395 and ADR-0483 holds**: the user's order is the visual and the keyboard
  order, a hidden widget leaves no place, the cards of a row are as tall as its tallest, and
  arranging works in every column count.
- **Tests**: `webui/tests/dashboard-layout.spec.ts` ("Dashboard columns by the width of the
  screen": three to a row at 1196, 1280, 1440, 1920 and 2560 px, two at 1195, 1100 and 1024 px, a
  768 px tablet and a 375 px phone as before, no sideways scroll, arranging at 1280 px) and
  `webui/tests/loading.spec.ts` (the skeleton in three columns at 1280 px, no layout shift).

## Consequences

- A window narrower than its old breakpoint takes the columns its grid has room for: a phone in
  landscape (667 px, no sidebar, a grid of 635 px) shows three stats cards to a row where it had
  two, and from a grid of 640 px (a window of 912 px beside the sidebar, a 1024 px one) the wide
  cards are two to a row where they were one, so a third card can sit alone in its row, half of
  it empty, below 900 px.
- The query container contains its inline size: the wrapper's width never comes from its content,
  which suits a block that fills the page.

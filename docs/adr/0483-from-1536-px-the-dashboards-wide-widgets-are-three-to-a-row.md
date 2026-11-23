---
status: accepted
date: 2026-11-23
area: webui
decided-by: agent
---

# ADR-0483: From 1536 px the dashboard's wide widgets are three to a row

## Context

ADR-0395 made the dashboard's wide widgets (Storage pools, Containers, Recent activity) fill the
row below `xl` and two to a row from it. On a screen about 2000 px wide the owner saw two cards in
one row and Recent activity alone in the next, half of that row empty, and said (in translation):
"it looks like this would fit three columns instead of two." The page is at most 1440 px wide
(`page-container`), beside the 248 px sidebar and 32 px of padding a side, so it has its full
width from a 1752 px window on and 1128 px at a laptop's 1440 px.

## Decision

- **From `2xl` (1536 px, Tailwind's breakpoint of `webui/src/app.css`) the wide widgets are
  three to a row**: the grid has 15 columns there, a wide widget takes 5 (the `xl:col-span-5` it
  already had) and a small card 3, so that five small cards or three wide ones fill a row. Each
  wide card is then 397 px (at 1536 px) to 469 px (from 1752 px) wide, more than the 343 px of a
  phone where the same cards read in one column.
- **No ADR is superseded, and ADR-0395 keeps its text.** This ADR changes one rule of ADR-0395,
  its widths, from `2xl` up; ADR-0395 is already superseded in part by ADR-0431, and an ADR names
  one successor (`supervisor/tests/test_adrs.py`), so the change is recorded here alone. The
  widths of ADR-0395 below `2xl`, and its other rules, stay in force.
- **Below `2xl` nothing changes** (ADR-0395): small cards two to a row on a phone, three from `md`
  and five from `xl`; wide ones fill the row below `xl` and are two to a row from it. A 1440 px
  laptop keeps its two columns: three would be 365 px each, and the owner's complaint was a wide
  screen.
- **One place for the columns.** `WIDGET_GRID` and `WIDGET_SPAN` of
  `webui/src/lib/dashboard-layout.ts` give the grid and each size's span to the widgets and to the
  first load's skeleton alike (ADR-0430), so that the cards come where their skeletons were.
- **The rest of ADR-0395 holds**: the user's order is the visual and the keyboard order, a hidden
  widget leaves no place, the cards of a row are as tall as its tallest, and arranging (drag, Move
  up and Move down, Hide and Show) works in three columns as in two.
- **Tests**: `webui/tests/dashboard-layout.spec.ts` ("Dashboard columns by the width of the
  screen": three to a row at 1536, 1920 and 2560 px, two at 1440 and 1535 px, the phone's one
  column, no sideways scroll, arranging, order and hidden widgets at 1920 px) and
  `webui/tests/loading.spec.ts` ("Loading without flicker on a wide screen": the skeleton in three
  columns, each card in its column, no layout shift).

## Consequences

- On a wide screen the default dashboard is two rows, the stats cards and the three wide cards,
  without an empty half row.
- With one wide widget hidden, or the widgets in an order that mixes the sizes, a row can still
  end with an empty place: a third of the row with one wide widget hidden, up to two thirds when
  the user's order leaves a wide card alone after small ones (ADR-0395's consequence: no dense
  packing, so the visual order stays the keyboard order).
- The cards of a row keep one height (ADR-0395), so a card with less in it than the tallest of
  its row keeps the room below its content, as before: with the default layout the row is filled
  and no half row stays empty, but a Storage pools card of one or two pools beside five
  containers is about a third empty, and Recent activity has some room below its five lines.
- The breakpoint is the browser window's width in CSS pixels: a 2000-pixel screen scaled to 150%
  is a 1333 px window and keeps two columns.
- Each card is narrower from 1536 px than in two columns: a long line of Recent activity is cut
  sooner (it was one line already).

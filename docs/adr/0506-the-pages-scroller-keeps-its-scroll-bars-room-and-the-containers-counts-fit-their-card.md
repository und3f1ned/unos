---
status: accepted
date: 2026-11-25
area: webui
decided-by: agent
---

# ADR-0506: The pages' scroller keeps its scroll bar's room, and the Containers counts fit their card

## Context

The dashboard's widgets take their columns from the width of their grid (ADR-0484), which the page's
scroller, `<main>` of `webui/src/routes/+layout.svelte`, gives them. A review of ADR-0484 on
2026-11-25 found two things. With a classic scroll bar (Windows, Linux; macOS hides it), the first
load's short skeleton has no scroll bar and the taller loaded page has one, so a window of 1196 to
about 1207 px gets three wide columns for the skeleton and two for the page, and every card moves.
And in the narrowest third (a card of about 289 px at a 1196 px window) the Containers card's three
counts cannot hold the Russian label of Stopped, which spilled out of its box. The review also
found ADR-0484's consequences wrong in two facts: the wide cards are 289 to 365 px at windows of
1250 to 1440 px (not up to 469 px), and windows of about 672 to 767 px, which have no sidebar and a
grid of at least 640 px, put the wide cards two to a row.

## Decision

- `<main>` keeps the room of its scroll bar whether it scrolls or not (`scrollbar-gutter: stable`),
  so that the grid has one width before and after the page grows.
- The Containers card's counts are as many to a row as fit at 6rem each, the room of the longest
  label (`grid-cols-[repeat(auto-fit,minmax(6rem,1fr))]`): in the narrowest third, Other goes below.
- ADR-0484 stands, with those two facts of its consequences as this ADR states them.

## Consequences

- With a classic scroll bar every page keeps its thin gutter (6 px in Chromium and Safari, the thin
  width in Firefox) on the right also when it does not scroll; the columns change at a window of
  about 1202 px there instead of 1196 px.
- `dashboard-layout.spec.ts` checks the counts at 1196 px in Russian; Playwright's Chromium hides
  scroll bars, so no spec sees the gutter.

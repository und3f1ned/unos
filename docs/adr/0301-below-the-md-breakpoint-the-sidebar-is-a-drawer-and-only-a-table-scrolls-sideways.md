---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0301: Below the md breakpoint the sidebar is a drawer, and on a phone only a table scrolls sideways

## Context

The web UI was desktop-only (ADR-0023): the `w-56` sidebar of `webui/src/routes/+layout.svelte`
stayed beside the page and left it about 150 px of a 375 px phone, and System's tab sidebar took
as much again. The feature `webui.mobile-layout` asks that every page is usable on a 375 px wide
screen without horizontal scrolling and that the navigation folds into a menu.

## Decision

- Below Tailwind's `md` breakpoint (768 px) the logo row of the sidebar of `+layout.svelte` is a
  header bar across the top, with a menu button before the logo (its label "Menu" from the
  `layout` catalog, `aria-expanded`, `aria-controls="layout-sidebar"`). The button opens the rest
  of the sidebar, `#layout-sidebar` with the links, the signed-in user and Logout, as a drawer
  under the bar, with a backdrop over the rest of the page; the logo stays one element and the
  drawer stays in the `aside`. The menu is a disclosure, not a modal dialog: the focus stays on
  the button and the links follow it in the tab order. Escape and a click beside the drawer close
  it and give the focus back to the button; a link closes it, and the navigation moves the focus
  to the new page as SvelteKit does; leaving the drawer with Tab closes it, so that it never
  covers the element with the focus; every navigation, Back included, closes it. From `md` up the
  layout is the one it was, in the DOM too: the button exists only while
  `matchMedia('(max-width: 767.98px)')` matches, so a page's first button there is still Logout,
  which desktop specs pick as `page.locator('button').first()`.
- `main` has `p-4` below `md`, `p-6` from it. Below `md` System's tabs wrap above the open tab and
  the page scrolls as a whole; from `md` they are the sidebar beside it as before. Below `md` the
  Files page has no padding of its own besides `main`'s, and below `sm` its list leaves out the
  Modified column and System > Logs stacks each entry: its time, level and program on a line, the
  message below at the full width.
- At 375 px no page scrolls sideways. A table that can be wider than the screen sits in a frame
  of the class `table-frame` of `webui/src/app.css` (`relative overflow-x-auto`) and scrolls
  sideways inside it; `relative` keeps what the table places absolutely, the texts of its buttons
  for screen readers, inside the frame. Rows of a title and buttons wrap (`.page-header`,
  `.card-header`, `flex-wrap` on the others), long names and paths break (`break-words`, and
  `break-all` for code). Dialogs keep the `p-4` and `max-w-*` of Modal and scroll on their own.
- `webui/tests/mobile-layout.spec.ts` proves it at 375 x 812, in English and in Russian, whose
  texts are longer: every page of the navigation, the views behind the tabs of Shares, Containers,
  the App Store and System > Network, every tab of System, the main dialogs, a regular user's
  Account page, sign-in and first run, with route fakes for the data that the stand-in lacks. On
  each, the document does not scroll sideways, no element does but a `table-frame`, and no element
  cuts off its content at the side without an ellipsis. It also drives the menu with the mouse and
  the keyboard, and checks the sidebar at 768 px.

## Consequences

- New markup follows the same rules, and the spec fails for a page that scrolls sideways on a
  phone: a wide table goes into a `table-frame`, a row of buttons wraps.
- Tables with many columns (disks, containers, users) need a sideways swipe inside their frame on
  a phone; every page keeps one markup for all widths.
- From `md` up nothing changed, also where it is narrow: between 768 px and about 1000 px the two
  sidebars of System leave its tab little width, as before.

## Considered options

- Cards instead of tables on a phone: a second markup per table to keep in step with the first;
  the frame does it with one class.
- A sideways scrolling strip for System's tabs: it hides the tabs beyond the edge, while the
  wrapped tabs show all eleven at once.
- A modal drawer with a focus trap (`role="dialog"`): more code than the disclosure that a menu
  button with `aria-expanded` and `aria-controls` names, made safe by closing on focus-out.

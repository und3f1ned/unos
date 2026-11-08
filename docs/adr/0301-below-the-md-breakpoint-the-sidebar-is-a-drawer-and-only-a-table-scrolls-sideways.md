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
  it and give the focus back to the button. That click lands on the backdrop, which listens for
  it itself, as Modal's does: iOS Safari makes a tap a click only on an element that listens for
  clicks (or has `cursor: pointer`), and a listener on the window does not count; the window's
  listener closes the menu on the other clicks outside the drawer, such as on the header bar. A
  link closes it, and the navigation moves the focus to the new page as SvelteKit does; leaving
  the drawer with Tab closes it, so that it never covers the element with the focus; every
  navigation, Back included, closes it (`afterNavigate`), and so do Back and Forward between two
  places of one page, such as System's `#updates` and `#general`, which change the URL without a
  navigation of SvelteKit (`popstate`; the focus goes back to the button if the drawer had it).
  From `md` up the layout is the one it was, in the DOM too: the button exists only while
  `matchMedia('(max-width: 767.98px)')` matches, so a page's first button there is still Logout,
  which desktop specs pick as `page.locator('button').first()`.
- `main` has `p-4` below `md`, `p-6` from it. Below `md` System's tabs wrap above the open tab and
  the page scrolls as a whole; from `md` they are the sidebar beside it as before. Below `md` the
  Files page has no padding of its own besides `main`'s and grows with its list, so that `main`
  scrolls it as a whole too; from `md` the list fills the rest of the window and scrolls inside
  it. No part of a page takes its height from the window's (the list's former
  `max-height: calc(100vh - 280px)` was taller than the room left to it and cut off the last
  entries, on a phone and at 1280 x 720 alike). Below `sm` the Files list leaves out the Modified
  column and System > Logs stacks each entry: its time, level and program on a line, the message
  below at the full width.
- At 375 px no page scrolls sideways. A table that can be wider than the screen sits in a frame
  of the class `table-frame` of `webui/src/app.css` (`relative overflow-x-auto`) and scrolls
  sideways inside it; `relative` keeps what the table places absolutely, the texts of its buttons
  for screen readers, inside the frame. Rows of a title and buttons wrap (`.page-header`,
  `.card-header`, `flex-wrap` on the others), and so do rows of a field and its buttons, such as
  the File Browser's New Folder; long names and paths break (`break-words`, and `break-all` for
  code, such as a Docker image's name, and for user names and IPv6 addresses in the sign-in
  history), also in the text of an Alert (`min-w-0 break-words`), which names files. An element
  placed absolutely inside a part that scrolls on its own, such as a text for screen readers,
  sits in a positioned box inside that part (`relative`, as in `table-frame` and SmbStateBadge):
  else it belongs to the scroller outside and makes it scroll on past its end. A dialog keeps the
  `p-4` and `max-w-*` of Modal, and one taller than the window scrolls with its overlay, so that
  its title and its buttons stay reachable: Modal's overlay is `modal-overlay items-start
  overflow-y-auto p-4`, with `my-auto` on the panel to center it while it fits. The dialogs
  written by hand on System's Security tab (two-factor authentication on and off, a firewall
  rule, a certificate) and its Backup tab (restore) take the same classes; those of the Scheduler
  scroll inside their panel (`max-h-[90vh] overflow-y-auto`).
- `webui/tests/mobile-layout.spec.ts` proves it at 375 x 812, in English and in Russian, whose
  texts are longer: every page of the navigation, the views behind the tabs of Shares,
  Containers, the App Store and System > Network, every tab of System, the File Browser's New
  Folder row and a folder of 40 entries, the alert of a failed download on System > Backup, the
  sign-in history on System > Security, 16 dialogs, a regular user's Dashboard and Account page,
  sign-in and first run, with route fakes for the data that the stand-in lacks. On each, the
  document does not scroll sideways, no element does but a `table-frame`, no element cuts off its
  content at the side without an ellipsis, and an open dialog's overlay scrolls no further than
  the dialog. Each dialog shows its title whole as it opens, and its last button (or, without one
  there, the last of its content) once scrolled to as a finger can: by the elements that scroll
  and the document, never by an element that cuts off its content, which no swipe moves; so does
  the long folder its last entry. The dialogs and the long folder are checked so also on a phone
  held sideways, 667 x 375, which is still below `md`, where the menu reaches its last link and
  Logout, and the folder at 768 x 1024 and at 1280 x 720. The spec drives the menu with the
  mouse, the keyboard, a click that reaches only the backdrop, a link and Back, also Back within
  one page, and checks the sidebar at 768 px. It checks the first view of each page and the
  listed states, not every state of every page, and it runs Chromium, not a phone's browser.

## Consequences

- New markup follows the same rules, and the spec fails for a page that scrolls sideways on a
  phone: a wide table goes into a `table-frame`, a row of buttons wraps, a dialog is a Modal or
  takes its overlay's classes.
- Tables with many columns (disks, containers, users) need a sideways swipe inside their frame on
  a phone; every page keeps one markup for all widths.
- From `md` up nothing changed but the File Browser's list, which now ends at the bottom of the
  window instead of below it, and the dialogs written by hand, which scroll in a short window;
  also where it is narrow: between 768 px and about 1000 px the two sidebars of System leave its
  tab little width, as before.

## Considered options

- Cards instead of tables on a phone: a second markup per table to keep in step with the first;
  the frame does it with one class.
- A sideways scrolling strip for System's tabs: it hides the tabs beyond the edge, while the
  wrapped tabs show all eleven at once.
- A modal drawer with a focus trap (`role="dialog"`): more code than the disclosure that a menu
  button with `aria-expanded` and `aria-controls` names, made safe by closing on focus-out.

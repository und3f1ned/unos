---
status: accepted
date: 2026-11-17
area: webui
decided-by: agent
---

# ADR-0395: Each user arranges, hides and shows the dashboard's widgets, and the server keeps the layout

## Context

The entry `webui.dashboard-layout` says that a user arranges, hides and shows the dashboard's
widgets, and that a dragged widget stays where it was put after a reload. The dashboard was a fixed
page of one grid of stats cards, an alert, and two rows of two sections. Where the layout lives,
what a widget is, how it is arranged without a mouse and on a phone, and what a regular user may
arrange were open; the owner delegated the decisions, asking above all that they be safe.

## Decision

- **Where it is kept: per user, on the server.** A new table `dashboard_layouts`
  (`supervisor/src/auth/models.py`; new tables only, existing ones are never altered, ADR-0319):
  `user_id` (primary key), `layout` (JSON text) and `updated_at`. The layout therefore follows the
  user to another browser, a reinstalled browser and a phone, which the browser's storage cannot do,
  and it needs no key of the browser that holds a token. No row means the default layout.
  Deleting a user deletes the row in the same transaction (SQLite enforces no foreign keys, and ids
  are reused); a rename keeps it. A restore from a backup that lacks the table gets it from
  `create_all`.
- **The API: `GET`, `PUT` and `DELETE /api/v1/auth/me/dashboard-layout`** (`src/auth/dashboard.py`,
  included in the auth router, so no new router, token area or role list: they are listed in
  `USER_ALLOWED` of `tests/test_authz.py`). The body is `{"widgets": [{"id", "hidden"}]}`: the
  widgets in the user's order, each with whether it is hidden. Ids match `^[a-z0-9][a-z0-9-]{0,31}$`,
  each at most once, 1 to 64 entries, `hidden` a real boolean, no other field: anything else is 422
  and stores nothing. The server knows no widget catalog, so a widget added or removed later needs no
  change to it. `DELETE` is the reset to the default.
- **A write only for a real change (ADR-0214).** A `PUT` of the layout that is stored, and a `DELETE`
  with nothing stored, write nothing and are not audited; the web UI sends a request only when the
  user changes something, one per change, and drops a drop on the widget's own place. Nothing polls
  (ADR-0365): the layout is read with the page's other data when it opens and on Refresh.
- **Audit (ADR-0320).** Each real change, and a reset that found a layout, is one entry of the new
  action `dashboard_layout_changed`, by the user, about their own dashboard (resource
  `dashboard_layout`), written after the commit; an entry that cannot be written is logged and the
  change stands, as for the other own-account entries (ADR-0251).
- **Roles (ADR-0030).** Every signed-in user, administrator or not, has a layout of their own and
  sees nobody else's. A regular user arranges the cards they see (the stats cards); the widgets of
  administrators are not offered to them and are dropped from what the page shows. A full API token
  acts as its user; a limited one may read the layout and never changes it (it is an area `auth`
  change, ADR-0392).
- **What a widget is.** The dashboard's cards and sections as they are, one widget each
  (`webui/src/lib/dashboard-layout.ts`): `cpu`, `memory`, `network`, `disk-io` (the Disk I/O card of
  ADR-0388), `temperature`, then for administrators `storage`, `containers`, `quick-actions` and
  `recent-activity`. The stats card of a widget needs the live stats and shows with them. Not
  widgets, always shown: the header, the alert of the disks to replace (a warning must not be hidden
  by a preference), the health banner, and a regular user's note on who they are. The default order
  is the one above. Small cards are two to a row on a phone, three from `md` and five from `xl`;
  wide ones fill the row below `xl` and are two to a row from it (the page's two-column rows began
  at `lg`).
- **Arranging.** A **Customize** button in the header turns arranging on and off (`aria-pressed`;
  it says Done meanwhile). While it is on, each widget has a toolbar: a handle to drag with a mouse
  (HTML5 drag and drop: the dragged widget takes the place of the one it is dropped on), and the
  buttons **Move up**, **Move down** and **Hide** named with the widget (`Move Memory down`), made
  of the design system's `Button` and `Card`. The move buttons are the alternative to dragging for
  the keyboard (ADR-0361) and for a finger on a phone, where dragging does not work reliably
  (ADR-0302); they skip a hidden widget, keep the keyboard focus on the button that was pressed, are
  disabled at the ends, and a live region says where the widget went (and that a save failed). Hide moves the focus to the widget's Show button, Show to the widget's toolbar and Reset to the Customize button, and Refresh waits while a save runs. A hidden widget keeps its place
  in the list, so **Show** puts it back where it was; the hidden ones are listed in a region **Hidden
  widgets** with a button each. **Reset to default** is enabled while the layout differs from the
  default and asks no confirmation: it only restores a layout the user can arrange again.
- **Unknown or removed ids are ignored.** The page takes the stored widgets that exist and that the
  user may see, in the stored order, and puts the others at their default place after them; a
  repeated id counts once. A stored value that is not a valid layout (a restored database is
  untrusted, ADR-0144) reads as the default, never as an error. The next change saves a clean list.
- **Failures.** A change that cannot be saved is shown again as it was, with the reason in an
  alert. A layout that cannot be loaded shows the default and offers no arranging, so that a guess
  never overwrites the stored one. A change is sent one at a time: the controls are disabled while
  one is saved.
- **Texts** in English and Russian (`dashboard.layout.*`, `audit.action.dashboardLayoutChanged`,
  ADR-0287).
- **Tests:** `supervisor/tests/test_dashboard_layout.py` (roles, tokens, validation, per-user
  storage, no write for no change, audit, untrusted rows, user deletion and id reuse),
  `test_authz.py`, `test_smb_models.py`; `webui/tests/dashboard-layout.spec.ts` (drag and reload,
  the move buttons and the focus, hide and show, reset, unknown ids, save and load failures, no
  request but the user's changes, Russian, a regular user in two browsers) and the phone view in
  `webui/tests/mobile-layout.spec.ts`.

## Consequences

- A layout survives reloads, browsers and reboots, and costs one small row and one audit line per
  change the user makes; reading costs a request when the dashboard opens or is refreshed.
- A widget whose card is missing from a user's stored list appears last, so a new widget never
  stays hidden by accident, and a user who is promoted gets the administrators' widgets at their
  default places.
- Widgets of different sizes in one flowing grid can leave a gap in a row (for example a wide card
  that does not fit beside four small ones). The visual order is the order of the page, which keeps
  the keyboard order equal to what is seen; dense packing would not.
- Dragging with a finger is not supported; the buttons are the way on a touch screen.
- A regular user's layout of administrators' widgets, kept before a demotion, is dropped at their next
  change.

## Considered options

- The browser's storage: nothing on the server, but the layout would not follow the user to another
  browser and would be lost with the browser's data.
- A server-side catalog of widgets that validates ids: it would reject the saves of a tab that is a
  version behind and need a server change for every widget; the page ignores what it does not know.
- A column on `users`: existing tables are never altered (ADR-0319).
- A pointer-event drag that works on touch: much more code for what the buttons already give.

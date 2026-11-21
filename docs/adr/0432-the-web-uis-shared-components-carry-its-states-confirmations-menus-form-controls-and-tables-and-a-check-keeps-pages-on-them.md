---
status: superseded
superseded-by: ADR-0438
date: 2026-11-20
area: webui
decided-by: agent
supersedes: [ADR-0266, ADR-0401]
---

# ADR-0432: The web UI's shared components carry its states, confirmations, menus, form controls and tables, and a check keeps pages on them

## Context

Superseded by ADR-0438: only in part, the baseline of the design check (`webui/scripts/design-baseline.json`), which is gone, so that a raw checkbox or radio and a filled primary button in a card's header or a table's row fail outright; every other rule here stays in force.

The owner found the redesign careless: "the design is slapdash, the UX limps". A UX audit of every
page found the same faults on many of them: a failed load drawn as an empty list, eight shapes of a
destructive confirmation (`window.confirm()` among them) and `alert()` for errors, no feedback after
an action, filled primary buttons in card headers and rows, row actions that wrap, tables that break
on a phone, raw checkboxes, unaligned number fields without units, buttons disabled without a
reason, badges in upper case, two time zones on one page. This is the first of five chunks of that
work (entry `webui.design-foundations`): the shared components and the rules, and the replacements
that only a shared component makes consistent; the pages' own redesign is chunks 2 to 5, which
adopt them. The owner delegated the decisions, asking above all that they be safe.

## Decision

- **States** (`webui/src/lib/components/ui`): `LoadState` (on ADR-0430's `Skeleton`: the skeleton
  until the first answer under `aria-busy`, then `ErrorState` when the load failed, never the empty
  state, then the empty state or the content; `keepOnError` keeps an earlier load's content under
  the error). `ErrorState`: a catalog title, the server's message (`messageOf`) and Retry; a message
  that `isTechnical` of `src/lib/errors.ts` finds technical (an errno, an exception's name, a
  traceback) is folded into `TechDetails` (a disclosure with
  Copy) behind a plain sentence. `Alert` gets a `details` snippet behind "Show details". `HelpText`
  is one line with a `more` snippet behind "Details". `StatusSummary` collapses a page's states into
  one bar of the worst tone that opens to list each with its action. `EmptyState` gets an `action`.
- **Feedback and confirmations.** `toast` and `showToast` (`ui/toast.ts`) and `ToastRegion` in the
  root layout: at most three, bottom right (along the bottom on a phone), success and information
  in a polite live region for 5 s, errors and warnings in an assertive one for 10 s, the time held
  while hovered or focused, an optional action; no `role="status"` or `alert`, which the specs read
  as the page's own. `confirm(options)` (`ui/confirm.ts`) returns a promise answered by
  `ConfirmHost` in the root layout, one question at a time, in `ConfirmDialog`: a small `Modal` with
  the question as its title, a sentence, an optional list of consequences, Cancel and the action
  named by its verb and object; `tone` danger (the one solid danger button, the warning icon in a
  red tint) or warning; `typeToConfirm` shows the expected text above an empty field and keeps the
  action until it matches. Escape and Cancel answer no and give the focus back; a navigation
  (another page, a sign-out) answers every pending question no and drops the toasts that carry an
  action, which belong to the page that goes. Every
  `window.confirm` and `alert()` became one of them: the App Store's rollback, removal of an
  earlier version, cancel and uninstall, a container's removal, a backup's deletion, the off-device
  copy's new key pair and forget, a task's deletion, a firewall rule's deletion and reset, a
  certificate's deletion; the Scheduler's errors are toasts or inline errors. Toasts replace the
  inline success texts on Backup & Restore and the Scheduler only; the other pages' own success
  alerts move to toasts with their chunk.
- **Actions.** `Menu` (WAI-ARIA's menu button: arrows, Home, End, a first letter, Escape back to the
  button, Tab out; destructive items last after a separator; the list in the top layer as a manual
  popover, placed by `ui/float.ts`, closed by a press outside; a scroll or a resize moves it with
  its button and closes it once the button has left the window) and
  `ActionMenu`, an object's "⋯" named "Actions for <name>". `BulkBar`: the count, and the actions
  and Clear only while something is selected; it keeps its height and sticks while active. `Card`
  gets a `status` snippet beside its title and `.card-header` a 32 px minimum height; `CardAction`
  is a header's action, a link with an arrow with `href`, else a small secondary button. `Button`
  gets `disabledReason`: the button stays focusable with `aria-disabled`, a press does nothing, and
  the reason is a tooltip (`role="tooltip"`, fixed, placed by `float.ts`) on hover and focus and the
  button's description. `Tabs` (WAI-ARIA tabs with roving focus) and `Segmented` (toggle buttons)
  are the two shapes of a set of choices.
- **Form controls.** `Input`: `prefix` and `suffix` inside its frame (the unit is part of its
  description), `width` `xs`, `sm`, `md`, `min`, `max` and `step` (the bounds are its hint unless it
  has one) and `steppers`. `FieldGroup`: a fieldset with a legend and a line of help whose fields sit
  on a CSS subgrid, so that labels, fields and hints of a row line up whatever the labels' length.
  `Switch` (a checkbox with `role="switch"`) and `SwitchRow` (title and help left, the switch right,
  the setting's fields below only while it is on), `Checkbox` (18 px, `indeterminate`, a bound
  `group`) and `RadioGroup` (a fieldset, an icon and a line per option). `Modal` gets `size` sm
  420 px, md 560 px (the default), lg 720 px, xl 880 px, a `description`, a `status` snippet, a
  `tone`, and a header and footer that stick to the edges of what scrolls with a shadow while
  content is under them; `Sheet` is a `Modal` at the right edge as tall as the window, the whole
  screen of a phone, for a form of more than six fields, a list or an object's details. `Badge` is
  sentence case and gets `outline`, a category or a feature, which is no state.
- **Tables.** `.table-actions` never wraps and its cell is as narrow as its buttons; at most edit
  and delete stay icons and the rest go into an `ActionMenu`. `.table-stack` turns a table into
  cards below `sm`: the head for screen readers only, the first cell and the actions on the first
  line, every other cell on a line of its own after its column's name (`data-label`).
- **The NAS's clock.** `nasTimeZone` of `src/lib/stores/nasTime.ts` reads the zone whose clock the
  schedules follow, `GET /api/v1/scheduler/timezone`, each time a page starts to show it (General
  tells it of a change it saved), and `formatDateTime` and `formatTimeOfDay` of `src/lib/time.ts` take
  `zone`. A page shows the times of what the NAS runs by its clock on that clock and names the zone
  once: System > Scheduler shows a task's next run (date and minutes) and its runs on the NAS's clock,
  where ADR-0266 showed them in the browser's zone (this supersedes that rule of ADR-0266 and the
  rule of ADR-0401 that every page shows the server's times in the browser's zone, for such times
  only; every other rule of both stays).
- **The design check** (`webui/scripts/design-check.mjs`, ADR-0400's, which ADR-0428 superseded in
  part; its exemption of checkboxes and radios ends here) also fails for `window.confirm`, `alert`
  and `prompt`, called bare (unless the file declares the name) or on `window`, `globalThis` or
  `self`, in a component or a `.ts` module outside `ui`; for the class `uppercase` (the eyebrow of
  `app.css` stays); and, against `webui/scripts/design-baseline.json`, for a raw checkbox or radio
  (`raw-choice`) and for a filled primary `Button` in a card's header (Card's `actions`, a
  `card-header`, a `justify-between` box with an `h3` to `h6` or a `card-title`) or a table's row
  (`tr`, `td`, `.table-actions`) (`primary-in-card`). The baseline holds each file's count when the
  rules came: a file may have up to its count, a new file none, and a count higher than the file's
  use fails until it is lowered, so that it only shrinks. It names only files of chunks 2 to 5; the
  account's and Security's cards were moved to `CardAction`, `RadioGroup` and `Checkbox` here.
- **Pages carried over** to show the components in use: System > Backup & Restore (the list a
  `table-stack` table with Delete and a ⋯ menu of Restore and Download, a toast after a deletion,
  a technical error folded), Scheduler (`LoadState`, a `Switch` per task, Edit, Delete and a ⋯ menu
  of Run now and View history, toasts, the zone as `HelpText`, the history a `Sheet`, a `SwitchRow`
  in the task form), the off-device copy's form (a `RadioGroup`, `Checkbox`es, `FieldGroup`s of
  retention and check with steppers and units, bandwidth in KiB/s, Save with its reason), Security
  and the Account page (headers' `CardAction`s and secondary buttons, the firewall's rule action a
  `Badge`, the theme and a new API token's choices).
- **Texts** in English and Russian in `src/lib/i18n/catalog/ui.ts` and the areas' catalogs
  (ADR-0287). **Tests:** `webui/tests/design-foundations.spec.ts` (the confirmation and Escape, the
  toast and its region, in Russian too, the ⋯ menu by keyboard, the actions on one line and the
  cards at 375 px, `LoadState` with the folded reason and Retry, the `Switch`, an error toast,
  `HelpText`, the `Sheet`, `FieldGroup` alignment, steppers, units and bounds, `disabledReason`,
  `CardAction`, badges' case); `webui/tests/ui.ts` gives the specs the menu and the confirmations;
  `mobile-layout.spec.ts` adds the confirmation and the Sheet;
  `supervisor/tests/test_webui_design_system.py` tests the new rules and the baseline.

## Consequences

- Chunks 2 to 5 replace their pages' alerts, raw controls, header primaries and hand-made dialogs
  with these components and empty the baseline; a page's own layout and wording stay theirs.
- Dialogs are wider (md 448 to 560 px); a disabled button with a reason is focusable, and
  Playwright's `toBeDisabled` sees `aria-disabled`. A confirmation is part of the page: the specs
  answer it in the page instead of the browser's dialog event.
- A browser without the Popover API places a menu by its fixed position and z-index alone.
- No new dependency: the components are Svelte 5 and Tailwind of the existing toolchain. Only the
  web UI's image rebuilds on the dev device; the supervisor does not change.

## Considered options

- A component library (Bits UI, Melt UI): a new dependency and a second styling system beside the
  tokens of ADR-0428.
- A `design-ignore` comment on each existing use instead of a baseline: forty comments in other
  chunks' files, which nothing would make anyone remove.
- Native `title` tooltips for a disabled button's reason: a disabled button gets no focus and no
  hover in some browsers, and a touch screen never shows a title.

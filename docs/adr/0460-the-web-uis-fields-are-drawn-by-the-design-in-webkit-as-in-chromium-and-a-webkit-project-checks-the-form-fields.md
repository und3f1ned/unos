---
status: accepted
date: 2026-11-22
area: webui
decided-by: agent
---

# ADR-0460: The web UI's fields are drawn by the design in WebKit as in Chromium, and a WebKit project of the E2E specs checks the form fields

## Context

The owner sent a screenshot of the Scheduler's Create task dialog in Safari on the Mac and asked,
in translation, "why are the fields of different heights and not in the design?": the selects
were macOS's short pop-up buttons with a double arrow, and Copies to keep showed WebKit's spin
buttons. WebKit draws a `select` with its native look (`appearance: auto`), which ignores padding
and height (22 px against the design's 36), and a number field with its spin buttons, which
`webui/src/app.css` hid only inside Input's frame. Measured in Playwright's WebKit 26 against
Chromium, a date or time field was also 38 px in WebKit, and in both browsers Select's trigger and
a field framed with a unit or steppers (`.form-affix`) were 38 px beside 36 px fields. The E2E specs
ran only in Chromium (`webui/playwright.config.ts`), so nothing saw it. The entry is
`webui.fields-in-safari`; the owner delegated its decisions, above all safe.

## Decision

- **Selects.** `.form-select` of `webui/src/app.css` takes `appearance: none` and ends in the
  chevron of Select's trigger, 8 by 4 px, 16 px from the field's end, in `content-secondary`: two
  strokes of 1.5 px that two linear gradients draw, so that its colour is the theme's token in
  light and dark; `pr-9` keeps the text off it. Height, padding, border, background and focus ring
  stay `.form-input`'s, which it shares. The list of options stays the browser's own.
- **Number fields** show no spin buttons in any browser: the base layer of `app.css` gives every
  `input[type='number']` `appearance: textfield` and hides `::-webkit-inner-spin-button` and
  `::-webkit-outer-spin-button` (it was only `.form-affix`'s). A count to keep steps by one with
  Input's own `steppers` (ADR-0432): the Scheduler's Copies to keep (Input, `min` 1, `max` 100,
  width `sm`) joins the off-device copy's counts and its check's percent. Ports, IDs, an MTU, sizes,
  limits and numbers of days, hours or minutes over a wide range are typed; the arrow keys step
  every number field.
- **Date and time fields** are one line of text high: the parts of their value
  (`::-webkit-datetime-edit` and its fields and wrapper) lose WebKit's padding above and below.
  ADR-0456 put the browser's parts back; this takes back only that padding, which made a date or
  time field 38 px in Safari and does not change Chromium's 36. ADR-0456 is not marked superseded
  for it: it names ADR-0458 as its successor already, and an ADR names one; its other rules stay.
- **One height.** Select's chevron (lucide's svg, which the component's scoped style did not reach)
  is styled through `:global`, so that it is 16 px and the trigger 36 px; the input inside
  `.form-affix` takes the frame's height (`min-h-0`), so that a field with a unit or steppers is
  36 px too. Checkboxes, radios and switches are the design's already (`appearance: none`), a file
  field is hidden behind a Button, a search field is drawn as a text field, no page has a range
  field, and a textarea measured the same in both browsers: they stay.
- **The design check** (`webui/scripts/design-check.mjs`, ADR-0438) also fails, outside
  `src/lib/components/ui`, for a raw field off the class of its kind (a `<select>` without
  `form-select`, an `<input>` or `<textarea>` without `form-input`) and for a class or style that
  sets a field's `appearance` (`appearance-auto`, `[-webkit-appearance:auto]`, `style:appearance`):
  either would bring the browser's own look back. It found the off-device restore's pool select
  with `form-input` (`OffsiteSnapshots.svelte`), now `form-select`.
- **A WebKit project.** `webui/playwright.config.ts` adds the project `webkit` (Playwright's
  Desktop Safari) that runs `webui/tests/form-fields.spec.ts` alone; the spec runs in Chromium too.
  It opens the Create task dialog, the certificate dialog, the off-device copy's settings with a
  REST server, the audit log's filters and the power schedule at 1440 and 375 px, and asserts that
  every one-line field is 36 px with the fields of a row on one line, that every select has no
  native appearance and the chevron, and that pressing where the browser draws spin buttons does
  not change a number. `make e2e-local` and `scripts/standin/e2e.sh` run both projects; every other
  spec stays Chromium's. `make setup` (`scripts/setup.sh`) installs WebKit beside Chromium
  (`playwright install chromium webkit`), and so does `scripts/test-cycle.sh`; the compose test
  stack's Playwright image carries both. CI's frontend job runs no E2E spec (ADR-0230 keeps CI
  off); the bootstrap job of `audit.yml` installs both browsers through `make setup`.
- Tests: `webui/tests/form-fields.spec.ts`, `supervisor/tests/test_webui_design_system.py` (the
  new rule), `test_standin.py` (the projects of the configuration), `test_repo_tooling.py`
  (setup.sh's browsers) and `test_deploy_scripts.py` (test-cycle.sh's).

## Consequences

- Every select of the pages looks like Select's trigger in every browser, and a page's fields
  share one height; Select and the framed number fields are 2 px lower everywhere than before.
- WebKit adds about 275 MB to Playwright's browser cache on each machine that runs `make setup`,
  and `make e2e-local` about 10 s. A form that a page adds is checked in WebKit only once it joins
  `form-fields.spec.ts`; the design check covers the classes.
- A new field type whose look the browser draws (a colour or range field) is checked in WebKit
  before it ships.

## Considered options

- A chevron as an SVG data URI: its colour would be written in `app.css`, the same in both themes,
  outside the tokens.
- Select for every raw select: a larger change of pages whose fields need a native select's
  `bind:value` of numbers and its keyboard; the native list stays the OS's own either way.
- The whole suite in WebKit as well: twice the run, for differences that no spec of a page asks
  about.

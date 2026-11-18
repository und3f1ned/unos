---
status: accepted
date: 2026-11-18
area: webui
decided-by: agent
---

# ADR-0405: Each user chooses a dark, light or system theme, and the colour tokens have a value per theme

## Context

The entry `webui.light-theme` (ADR-0023 listed the dark-only web UI as debt) says that the Account
page offers Dark, Light and System, that the choice stays across sign-ins, and that every page uses
the tokens of the chosen theme. ADR-0400 moved the pages to the colour tokens of
`webui/tailwind.config.js`, but the tokens were fixed hex values, and `app.css`, `app.html` and the
components of `ui` still used palette colours. Where the choice lives, what System means before a
sign-in, how the page avoids a flash of the wrong theme and how contrast is kept were open; the
owner delegated the decisions, asking above all that they be safe.

## Decision

- **Tokens per theme.** Every colour token is a CSS variable `--color-<token>` of
  `webui/src/app.css` holding red, green and blue, with one value in `:root, [data-theme='dark']`
  and one in `[data-theme='light']`; `tailwind.config.js` maps each token to
  `rgb(var(--color-<token>) / <alpha-value>)` (`surface-muted` and `border-muted` are half
  transparent) and writes no colour of its own. A border or ring without a colour takes `border`
  and `brand-ring`; checkboxes and radio buttons take `brand-ring` as their accent colour. `app.css`,
  `app.html` and the components of `ui` use only tokens: new tokens `solid-success`,
  `solid-warning`, `solid-error` and their `-hover` for the fills of `Button`'s variants, whose
  focus ring is `brand-ring` like every button's, and `scrim` for the class `scrim`. The badges
  purple, cyan and orange take the accents memory, container and temperature. Each theme sets
  `color-scheme`, and nothing else does (a date field had `color-scheme: dark`).
- **The light theme** is slate on white: the page `#f1f5f9`, cards white, fields, secondary buttons
  and rows under the pointer `#e2e8f0`, text `#0f172a` to `#56657a`, the field border `#64748b`, the
  brand `#2563eb`, the scrim `#020617`, and the status and accent colours in shades of 700 to 900,
  dark enough to read on their own tints.
- **WCAG AA in both themes.** `supervisor/tests/test_webui_theme.py` reads both themes from
  `app.css` and fails when a pair that text uses is below 4.5:1: each text colour, status colour
  and accent on the page, a card, a nested card and `surface-overlay` (a row, field or card under
  the pointer, a secondary button), each status colour on its badge's 20 % and its alert's 10 %
  tint over each of them, the neutral badge, the purple, cyan and orange badges, a selected row
  (`brand` at 20 %), `content` on `surface-hover`, and `content-on-brand` on every solid fill; and
  when field borders, the focus ring, status colours and accents (status dots, progress bars,
  icons, chart lines) are below 3:1 against those backgrounds. The dark theme met neither, so its
  values move: `surface-overlay` `#2c384c` (was `#334155`), `content-secondary` `#a9b6c8`,
  `content-muted` `#97a5b9`, `border-strong` `#728297` (field borders, which were `border`, become
  `border-strong`), `status-success` `#53e086`, `status-error` `#fdb4b4`, `status-info` `#a5ccfd`,
  `status-neutral` `#748297`, `accent-memory` `#dcbafe`, `accent-container` `#3fd9f0`,
  `accent-temperature` `#fcb880`; the warning and success buttons are the 700 shades in both
  themes. Markup that broke a pair changes with it: the search match of System > Logs is a 20 %
  tint (30 % did not reach 4.5:1), the count of the selected category of the App Store is
  `brand-hover` (white on a white tint of the brand did not), and the restore dialog of System >
  Backup names the file in `content` (it was `content-on-brand`, white on white in the light theme).
- **The choice: Dark, Light or System, per user on the server.** A new table `user_themes`
  (`supervisor/src/auth/models.py`; new tables only, ADR-0319): `user_id` (primary key), `theme`
  and `updated_at`; no row is System, the default, so that a user who never chose sees what the
  device prefers, as the sign-in page does. `GET` and `PUT /api/v1/auth/me/theme`
  (`supervisor/src/auth/theme.py`, in the auth router): `{"theme": "dark" | "light" | "system"}`,
  anything else 422 and nothing stored. Every signed-in user has their own (listed in `USER_ALLOWED`
  of `tests/test_authz.py`); a limited API token reads it and never changes it (ADR-0392). A stored
  value that is not one of the three reads as System (ADR-0144). Deleting a user deletes the row in
  the same transaction; a rename keeps it. An account that can only set up two-factor
  authentication cannot reach it and keeps the theme of the browser.
- **A write and an audit entry only for a real change** (ADR-0214, ADR-0320): a `PUT` of the theme
  the user has, System without a row included, writes nothing; each real change is one entry
  `theme_changed`, by the user, resource `theme`, "Changed the theme from light to dark"; an entry
  that cannot be written is logged and the change stands (ADR-0251). Reading writes nothing.
- **Applying it without a flash.** The theme is the attribute `data-theme` of `<html>`. `app.html`
  loads `webui/static/theme-init.js`, a file in the head (the CSP allows no inline script but
  SvelteKit's own), which sets it before the body is drawn from the browser's cache of the last
  choice, `localStorage['unos-theme']`, or for System and without one from
  `prefers-color-scheme`; it also sets the inline `color-scheme`, so that the canvas the browser
  paints before the style sheet has come is of the theme too. The cache is no secret (it names no
  account, and the session stays in its cookie, ADR-0397); it stays after a sign-out, so the sign-in
  and first-run pages follow it or, in a new browser, the device. `$lib/stores/theme` keeps both
  from then on: System follows `prefers-color-scheme` as it changes, a choice made in another tab
  of the browser follows through the `storage` event, and once the account is known the root
  layout takes the user's choice from the server, once per sign-in and page load, and caches it. An
  answer that comes after a choice made here or in another tab is dropped. A failed read keeps the
  theme shown; the Account page asks again when it opens and says when it could not.
- **The Account page** (System > Security, a regular user's Account) has a card **Theme** with
  three radio buttons, Dark, Light and System (`ThemeCard.svelte`, texts `theme.*` in English and
  Russian, ADR-0287). A choice applies at once and is sent; the theme already chosen sends nothing.
  The buttons stay enabled while a choice is sent, so that the arrow keys go on through them: a
  choice made meanwhile waits, and only the last one is sent next. One the server refuses brings
  back the theme it keeps, with the reason.
- **Tests:** `supervisor/tests/test_theme.py` (choices, validation, roles, tokens, per user, no write
  for no change, audit, untrusted rows, deletion, id reuse, rename), `test_webui_theme.py` (the
  pairs, both themes' tokens, no colour in the config, `app.css`, `app.html` and `ui` on tokens,
  `color-scheme` only from the themes, the script before the body), `test_authz.py`,
  `test_smb_models.py`; `webui/tests/theme.spec.ts` (the three choices, Light across a sign-out, a
  sign-in and another browser, System following an emulated colour scheme, the theme and the
  canvas's scheme before the body is drawn, nothing sent for the same choice, a refused save, the
  arrow keys while a choice is sent, a failed read, Russian; every page of the navigation and every
  tab of System in each theme with no text, background or border colour that is not a token of
  that theme for what it colours, and the key colours of the Account page, a dashboard card, a
  badge of the Storage page and the Add user dialog with its field and backdrop),
  `first-run.spec.ts` (the setup page follows the device), and `mobile-layout.spec.ts` with the
  card on the Account page.

## Consequences

- A page that uses the tokens follows the theme with no code of its own; a colour outside them is
  caught by `design-check.mjs` (pages), `test_webui_theme.py` (`app.css`, `app.html`, `ui`) and the
  walk of `theme.spec.ts` (what the browser computes, without opening every dialog or menu). Colours
  that a `.ts` module returns or that code puts together stay for review (ADR-0400), and so do
  combinations of tokens that the test does not list.
- The dark theme looks a little different: rows under the pointer, fields and secondary buttons are
  a little darker, secondary and muted text lighter (and close to each other), field borders
  visible, the status and accent colours lighter (shades near 300), and the warning and success
  buttons darker.
- The E2E specs run in the light theme, as Playwright's browser prefers light and System is the
  default; a user who never chose sees the light theme on a device that prefers it.
- One small row per user who chose a theme, and one audit line per change; one request per sign-in
  and page load reads it. A browser shared by several users shows the last user's theme on the
  sign-in page.
- The theme of a page opened before the account is known is the cached one; when another browser
  changed the choice, the page switches once after the sign-in or the first load.

## Considered options

- The browser's storage alone (as the language, ADR-0287): the entry asks that the choice stay
  across sign-ins and so follow the account; the browser keeps only a cache of it.
- A column on `users`: existing tables are never altered (ADR-0319).
- An inline script in `app.html`: the CSP's hash covers only SvelteKit's own script.
- Dark as the default for users who never chose: the sign-in page before a sign-in follows the
  device, and a different default after the sign-in would switch the theme at every first sign-in.

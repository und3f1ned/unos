---
status: accepted
date: 2026-11-22
area: testing
decided-by: agent
---

# ADR-0454: A spec listens for the file chooser before the page can open it, not just before the key press

## Context

In about two of five full runs of `make e2e-local`, `webui/tests/files.spec.ts` 'uploads files
from the keyboard' waited in vain for the file chooser after Enter on the focused Upload button
(`testing.keyboard-upload-opens-the-file-chooser`). Playwright reports a file chooser only while
a `filechooser` listener is registered, and the first listener turns the browser's interception
of choosers on without waiting for the browser to apply it. The spec added the listener
(`page.waitForEvent('filechooser')`) right before `page.keyboard.press`, a raw key event with no
round trip to the page before it, and Enter clicks a button on its keypress, within the same
key-down command: under load the click on the hidden file input sometimes ran before the
interception, the headless browser opened its own chooser, which nothing reports, and the folder
stayed empty. Space clicks on its keyup, one round trip later, and never lost that race.

## Decision

- A spec that waits for a file chooser registers its `filechooser` listener (`page.on`) before
  it opens the page that opens the chooser, keeps it for the test, and takes each chooser from
  what the listener has collected; `page.waitForEvent('filechooser')` just before a raw
  `page.keyboard.press` or `page.mouse` event is not used. 'uploads files from the keyboard' in
  `webui/tests/files.spec.ts` does so, waits for exactly one chooser per key, and checks that no
  chooser is left over at its end.
- The Files page stays as it is: Enter and Space on Upload click its hidden file input with the
  key's user activation.

## Consequences

- The key press is made once and the spec waits only for the event that it causes: no retry,
  as the E2E specs take none (`webui/AGENTS.md`).
- The evidence, from a throwaway spec on the stand-in under load, 300 attempts per variant: with
  the listener added just before the key, 4 of 300 attempts with the cached Chromium headless
  shell build 1234 and 3 of 300 with build 1200, the one that `@playwright/test` 1.57.0 expects,
  got no chooser, all on Enter, while the input was clicked every time and no user-activation
  warning came; with one `page.evaluate` round trip between the listener and the key, and with
  one listener for the whole run, none failed. The browser build is not the cause.
- `page.click`, `locator.click` and `locator.press` go to the page before their input event
  (scrolling, focus, hit checks), so `waitForEvent('filechooser')` before one of them should not
  be affected; this follows from Playwright's code and was not measured.

## Considered options

- A `page.evaluate` round trip between `waitForEvent` and the key press: it also measured 0 of
  300, but it works through the order of the browser's protocol messages and reads as a no-op.
- A change of the page (opening the chooser on keydown, `showPicker()`): the page is not at fault,
  and a keyboard user's Enter and Space already open the chooser.

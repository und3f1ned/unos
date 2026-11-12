---
status: accepted
date: 2026-11-12
area: webui
decided-by: agent
---

# ADR-0365: A check of webui/src fails for a setInterval whose callback reaches the API

## Context

ADR-0006 rules out polling the API from pages, but nothing enforced it: System > Logs' Auto asked
`GET /api/v1/logs/entries` every 5 seconds and the Storage page asked for a running scrub's status
every 2 seconds, both from `setInterval` (webui.no-http-polling). Step 2 of that entry asks for a
test that fails when a component fetches the API from `setInterval`: a static check over
`webui/src` or a spec that fakes timers. Working on through the features under the owner's
delegation ("work on through the features, ask no questions; I allow everything", in translation),
the agent chose the static check.

## Decision

- `webui/scripts/polling-check.mjs` reads the `<script>` blocks of every `.svelte` file and every
  `.ts` module of `webui/src` with TypeScript's parser, and fails for each `setInterval` (also
  `window.`, `globalThis.` and `self.setInterval`) whose callback reaches the API:
  - the API is `fetch` and every value a file imports, not as a type, from `src/lib/api/`
    (`$lib/api/...` or a relative path that leads there): `getLogEntries`, `api.get`; and a
    function that a `.ts` module of `src/` exports (`$lib/...` or a relative path), when it
    reaches the API, through the modules that module imports in turn (`export function`, a
    variable whose value is a function, an export list, a re-export, `export *`, a default
    export; a namespace import of a module that exports one);
  - the callback reaches what its body names, and the bodies of the functions of its file that it
    names, again and again (a `function`, a variable or a `$:` statement whose value is a
    function); a function literal inside a body counts as run, since a `.map()` or a `.forEach()`
    runs it at once and the check cannot tell one that runs later;
  - a callback that is neither a function literal nor the name of such a function fails too.
- In a component, a callback also fails when it assigns a variable (also `name.field = ...`,
  `name++` or a destructuring) that a `$:` statement reaching the API reads, since Svelte runs that
  statement again; and so when the variable is read by a `$:` statement that assigns what such a
  statement reads, and so on. A `$:` statement that only defines a function
  (`$: load = () => ...`) runs nothing by itself.
- A callback fails when it changes a store, in a component or a module: `name.set(...)`,
  `name.update(...)` or `$name = ...`, where `name` is declared with `writable`, `readable` or
  `derived` in its file or in the `.ts` module it is imported from, or is read as `$name` in its
  file. What reads a store can be in any file, so the check does not look for it; a timer that
  changes a store whose readers reach no API takes an exemption with its reason.
- A comment `polling-ignore: <reason>` above the statement of the `setInterval` exempts it; the
  summary counts the exempted timers, and review reads the reasons. The one exemption is the
  countdown of `webui/src/routes/system/tabs/NetworkTab.svelte`, which reads the interfaces once
  when a disable's time has run out (ADR-0246): no polling.
- `supervisor/tests/test_webui_polling.py` runs the check on the web UI, so `make test` and
  `make check` fail for a page that polls, and tests the check on small web UIs in `tmp_path`.

## Consequences

- Every component is checked, including states that no spec reaches, such as a scrub that runs on
  the Storage page or Auto on the Logs tab; the specs still show that each page updates over the
  WebSocket (`webui/tests/logs.spec.ts`, `webui/tests/storage.spec.ts`).
- A loop of `setTimeout` calls, or a timer given an object's method, is out of the check's reach
  or fails it: review looks for the first, and the second takes a function literal.
- Also out of its reach, for review: the markup of a component (an `{#await}` or a `{#key}` on a
  variable that a timer changes); a function of another module that changes a store; an object or
  a store that a module exports with methods that reach the API (only exported functions are
  followed); a function that reaches the API only through a module of an import cycle, which is
  taken as reaching nothing while the cycle is read; and a store whose `set` a timer reaches
  through another name. These came from the review of `webui.no-http-polling`; the cases the check
  covers are tested in `supervisor/tests/test_webui_polling.py` (`TestOtherModules`,
  `TestWhatATimerChanges`), each of which failed on the check before them.
- Like the text check of ADR-0287, it needs `webui/node_modules` (make setup); without node the
  test skips.

## Considered options

- A spec that fakes timers (`page.clock`) and counts the API requests while time passes: it shows
  the behaviour of the pages it visits, in the states it can bring about, and only in
  `make e2e-local`, which the commit hook does not run; a new component that polls would pass
  until a spec visits it.

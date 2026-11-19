---
status: accepted
date: 2026-11-19
area: webui
decided-by: agent
supersedes: [ADR-0231]
---

# ADR-0425: The web UI runs on Svelte 5 in runes mode only, and its pages are composed of area components

## Context

The web UI was written for Svelte 4 (`export let`, `$:`, slots, `createEventDispatcher`), and
ADR-0231 left the new majors of its toolchain to two feature entries, `webui.svelte-5` ("Svelte 5
with SvelteKit 3") and `webui.toolchain-major-versions`. SvelteKit 3 accepts TypeScript 6 only,
which is the other entry's. Svelte 5 still compiles a component of Svelte 4 in a legacy mode, so
a move that only bumps the version keeps the old reactivity, whose implicit dependencies of `$:`
hid bugs. The owner asked, in translation, "together with Svelte, refactor and redesign the
interface": the redesign comes in a later step on top of this one, so this one keeps what a user
sees. Route files had grown to 2,500 lines (the Storage page) with tabs, tables, dialogs and
their forms in one file. The owner delegated the decisions of this entry, asking above all that
it be safe.

## Decision

- Versions (`webui/package.json`, `webui/package-lock.json`): svelte ^5.58, @sveltejs/kit ^2.70
  (SvelteKit 2 supports Svelte 5), @sveltejs/vite-plugin-svelte ^4.0.4 (the last major for
  Vite 5), svelte-check 4.7.8 (exact, as before), @sveltejs/adapter-static ^3.0.11, and
  lucide-svelte ^0.321.0, the lowest release whose peer range takes Svelte 5 (0.300 to 0.320 take
  `<5`, which `npm ci` refuses). SvelteKit 3, @sveltejs/vite-plugin-svelte 5 and later, Vite 8,
  Tailwind CSS 4, TypeScript 6 or 7, adapter-static 4 and lucide-svelte 1 stay with
  `webui.toolchain-major-versions`. lucide-svelte 0.x ships class components of Svelte 4: they are
  a library's and keep their mode, and pages type an icon as `IconComponent` of
  `$lib/components/ui`.
- Runes mode only. `webui/svelte.config.js` compiles every file of `webui/src` with
  `runes: true` (`vitePlugin.dynamicCompileOptions`; the files under `node_modules` keep theirs),
  so `export let`, `$:` and `$$props` fail the build. `webui/scripts/runes-check.mjs`, which
  `npm run check` runs after svelte-check (`make frontend`, `make check`), compiles every
  component in runes mode and fails for the syntax of Svelte 4 that runes mode still takes:
  `on:` directives, `<slot>`, `$$slots`, a `slot` attribute, `<svelte:fragment>`,
  `<svelte:component>`, `<svelte:self>`, `<script context="module">`, and the imports of
  `svelte/legacy`, `createEventDispatcher` and `$app/stores` (also in `.ts` modules).
  `supervisor/tests/test_webui_runes.py` tests it and runs it on the web UI.
- Components: props are `$props()` with a `Props` type; a prop a parent binds is `$bindable()`;
  events are element attributes (`onclick`) and callback props named `on` and the event in lower
  case (`onclose`, `onsaved`), whose argument is what the event's `detail` was; content passed in
  is a snippet (`children`, `footer`, Modal's `titleIcon`). `Select`'s `onchange` gets the value.
- State: `$state` for what markup, a `$derived` or an `$effect` reads, `$state.raw` for what is
  only replaced whole, a plain `let` for the rest. A value computed from others is `$derived`. An
  effect is the last resort, for what follows a store or a prop: it reads its dependencies first
  and runs its body in `untrack(...)`, because Svelte 5 tracks every read of a run, also inside
  the functions it calls, where Svelte 4 re-ran a `$:` only for the names in the statement.
  Stores (`$lib/stores`, `$t`) stay stores.
- The checks of ADR-0287, ADR-0365 and ADR-0400 read Svelte 5: they walk the parser's tree with
  `webui/scripts/svelte-walk.mjs` (Svelte 5 no longer exports Svelte 4's walk); the text check
  also reads `{@render}` and the defaults of a `{#snippet}`'s parameters; the polling check
  follows `$effect`, `$effect.pre`, `$derived` and `$derived.by` instead of `$:`, with the state
  of `$state`, `$derived` and `$props()` and without what `untrack` reads.
- Pages are composed of area components. A route file holds the page's state and loads and
  passes data down and callbacks up; each card, table, tab and dialog with its form is a component
  of `webui/src/lib/components/<area>/`, with typed props. Logic that files share moves into
  modules beside them: pure functions into `.ts`, state with its loading and error into a
  `.svelte.ts` module with runes; the sizes of every area are scaled and written by
  `webui/src/lib/format.ts`. Duplicated markup becomes a component or a snippet. A file above
  about 400 lines is one concern or is split. The refactor changes no DOM, class, text or
  behaviour: the redesign is a step of its own, and the E2E specs pass unchanged.
- Kept from ADR-0231: Dependabot's npm updates ignore semver-major versions of every dependency
  and `@playwright/test`; its Docker updates ignore the image `playwright`; new majors of the
  toolchain are feature work; Playwright moves by hand in the monthly maintenance pass
  (`docs/maintenance.md`) with the image tag of `webui/Dockerfile.test`.

## Consequences

- `webui/node_modules` shrinks from 129 MiB to 107 MiB (no svelte-preprocess, svelte-hmr or
  the svelte-check 3 toolchain); the web UI image's npm layer is rebuilt once.
- A component of Svelte 4's syntax, copied from older code or a tutorial, fails `make check`.
- lucide-svelte 0.x keeps compiling in legacy mode inside the build until lucide-svelte 1
  (`webui.toolchain-major-versions`); `IconComponent` is the one place to change then.
- The polling check cannot see a `$state` field of a class in a `.svelte.ts` module that a timer
  changes: review reads those.

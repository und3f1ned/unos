---
status: accepted
date: 2026-11-21
area: ci
decided-by: agent
---

# ADR-0448: ESLint and Prettier check webui/src in make frontend, with the recommended rules and the code's own style

## Context

The web UI's code had svelte-check and the checks of ADR-0287, ADR-0400 and ADR-0427, but no
linter and no formatter: its files mixed tabs and spaces and double and single quotes. The
feature-list entry `ci.webui-eslint` asks for ESLint with the Svelte plugin and Prettier's check
on `webui/src` in `make frontend`, and for CI's failure on a finding. The owner left the packages
to the agent ("do it yourself", in translation); the web UI's behaviour stays as it was.

## Decision

- Exact dev dependencies of `webui/package.json`, in `webui/package-lock.json`: eslint 10.15.0,
  @eslint/js 10.0.1, typescript-eslint 8.72.2, eslint-plugin-svelte 3.24.1, prettier 3.10.1 and
  prettier-plugin-svelte 4.1.1. They and every package they bring are releases at least a week
  old: npm took minimatch 11.0.9 and ignore 7.0.16, of the last five days, which the lockfile
  holds at 11.0.8 and 7.0.14. The plugin's dependencies moved postcss, which the build also uses,
  from 8.5.6 to 8.5.33, and nanoid, which postcss uses, from 3.3.11 to 3.3.20.
- `webui/eslint.config.js`: the recommended rules of ESLint, typescript-eslint and
  eslint-plugin-svelte; the scripts of components are parsed by typescript-eslint's parser with
  its project service, in runes mode (ADR-0427). `npm run lint` is `eslint --max-warnings 0 src`
  and `reportUnusedDisableDirectives` is `error`: a warning (`$inspect`, `{@debug}`) and an
  exemption that exempts nothing fail it too. Five rules are off,
  each with its reason in the file: `no-undef` (TypeScript reports an undefined name, as
  typescript-eslint advises); `svelte/require-each-key` (a key changes which rows Svelte keeps, and
  a duplicate key throws, as two equal warnings of a list would); `svelte/no-navigation-without-resolve`
  (no `kit.paths.base`, so `resolve()` returns every path as it is); `svelte/prefer-svelte-reactivity`
  (a Set or a Map is replaced whole, `webui/AGENTS.md`); `svelte/no-useless-mustaches` (Svelte 5
  drops whitespace at the edges of an element, a block and an iteration, so `{' '}` and `{' · '}`
  carry it). A line exempted later says `// eslint-disable-next-line <rule> -- <reason>`; that it
  gives a reason is for review, as for ruff's `noqa` (ADR-0425).
- The findings of the base tree, 172 in 93 files, are resolved without a change of behaviour:
  144 by the four Svelte rules above; 15 `any` (a catch variable of `unknown`, read as an `Error`
  where the code read `e.message`), 11 unused names (six imports, an index, two items of
  `{#each Array(n), i}`, the never-read `loading` of the Backup tab, a rest pattern and a catch
  variable), and two counters of `<script module>` that `no-useless-assignment` misread,
  rewritten. The reformat broke one `[index]` onto a line of its own (`no-unexpected-multiline`):
  the list is a variable now.
- `webui/prettier.config.js`: the style most of the code had, `useTabs`, `singleQuote`,
  `trailingComma: 'none'`, `printWidth: 120` (a tab counts as Prettier's `tabWidth`, 2), with
  prettier-plugin-svelte and its defaults (`htmlWhitespaceSensitivity: 'css'`). Of the widths 100
  to 140, 120 changed the fewest files. `npm run format` wrote it over `src/` once, in a commit of
  its own (275 of 424 files). Svelte's compiler gave the same output for every component before and
  after it, but two texts whose line break was re-indented; two places where Prettier would put a
  line break after `{' '}`, a second space in Svelte 5's text, keep their source with
  `<!-- prettier-ignore -->` and the reason (`snapshots/DeleteDialog.svelte`,
  `storage/DiskTable.svelte`). The `.ts` modules have the same syntax tree and comments.
- `make frontend` runs `frontend-lint` (`npm run lint`) and `frontend-format`
  (`npm run format-check`: `prettier --check src`) after its other parts, each also when another
  fails (ADR-0190); the Frontend job of `.github/workflows/test.yml` runs `make frontend`. CI is
  off on GitHub (ADR-0230): a finding fails `make check`, and the job once the workflow runs again.
- Dependabot leaves the six packages alone: a new release can report more or format otherwise,
  which its pull request would have to fix. The monthly maintenance pass moves them, with the
  fixes and the new format in the same change (`docs/maintenance.md`, `docs/TESTING.md`).
- `supervisor/tests/test_webui_lint.py` lints and checks a small web UI in `tmp_path` with the
  web UI's configuration: a finding of each set of rules fails, so do a warning and an unused
  exemption, the rules turned off do not report, a file that Prettier would change fails, and
  Prettier writes the settings above;
  `test_deploy_scripts.py` checks the exact pins and the Frontend job's commands,
  `test_repo_tooling.py` the make targets and Dependabot's rules.

## Consequences

- `webui/node_modules` grows by about 40 MiB, so the builder stage of `webui/Dockerfile`, which
  `deploy/install.sh` runs and the dev device does not (ADR-0184), installs that much more.
- A new file is written with `npm run format`; a finding is fixed, or turned off with its reason.
- Keys of `{#each}` and `resolve()` stay for features that need them, with their tests.
- The reformat touches nearly every component: a branch made before it merges with conflicts that
  `npm run format` on its side shrinks.

# webui/: the SvelteKit web UI and the E2E specs

SvelteKit 3 with Svelte 5 in runes mode only (ADR-0427), Vite 8, `<script lang="ts">`, the static
adapter, strict TypeScript 6 and Tailwind CSS 4 (ADR-0012, ADR-0456). SvelteKit 3 takes its
configuration in its Vite plugin, `sveltekit({...})` of `vite.config.ts`: there is no
`svelte.config.js`, and `$lib` is the alias of that configuration. Caddy serves the built files
and proxies `/api/*` and `/ws/*` to the supervisor (`webui/Caddyfile`).

## Components and state (ADR-0427)

- Every component is in runes mode: `vite.config.ts` compiles `src/` with `runes: true`
  (`export let` and `$:` fail the build), and `scripts/runes-check.mjs` (`npm run runes`, run by
  `make frontend`, tested by `supervisor/tests/test_webui_runes.py`) fails for
  `<svelte:options runes={false} />` and the syntax of Svelte 4: `on:`, `let:`, `<slot>`,
  `$$slots`, `slot=`, `<svelte:fragment>`, `<svelte:component>`, `<svelte:self>`,
  `<script context="module">`, the options accessors and immutable, `svelte/legacy`,
  `createEventDispatcher`, `$app/stores` (use `$app/state`), and for a plain `let` that the
  markup shows and that changes (it is no state in runes mode). The icons of lucide-svelte, a
  library's, are the one part compiled in legacy mode.
- Props: `type Props = { ... }` and `let { a = 1, class: className = '', children, onsaved }: Props
  = $props();`; `$bindable()` only for a value the parent binds (a form field). Events are
  attributes (`onclick={...}`, a modifier is code: `e.preventDefault()`) and callback props `on` +
  the event in lower case, whose argument is the value (`onsaved(share)`, Select's
  `onchange(value)`). Markup passed in is a snippet: `children`, Modal's `footer` and
  `titleIcon`. An icon prop is an `IconComponent` of `$lib/components/ui`.
- State: `$state` for what the markup, a `$derived` or an `$effect` reads (a deep proxy: copy it
  with `$state.snapshot`, compare by id), `$state.raw` for what is only replaced whole, a plain
  `let` for the rest. A computed value is `$derived`; a user's action is handled in its handler.
  An `$effect` (or `$effect.pre`, before the DOM update) only follows a store or a prop: it reads
  its dependencies first and runs its body in `untrack(...)`, since Svelte 5 tracks every read
  of a run, also in the functions it calls. A `Set` or an array is replaced, not mutated and
  assigned to itself. Stores (`$lib/stores`, `$t`) stay stores.
- Whitespace: Svelte 5 keeps the line break and indentation between two expressions of a text
  as they are written (`{a}` and `{b}` on two lines), where Svelte 4 wrote one space, and drops
  the whitespace between the iterations of an `{#each}`. Neither shows on the page, but a spec
  reads `textContent`: a text that a spec matches with a regular expression keeps its parts on
  one line, and a cell whose items a spec reads as one text keeps a space between them.
- Svelte 5 delegates `onclick`, `onkeydown`, `oninput` and their kin to the app's root: a
  listener that `addEventListener` puts on an element of the page runs before them, one on
  `document` or `window` after them, and `stopPropagation` in a delegated handler stops the
  delegated ones above it and those of `document` and `window`.
- A page is composed of area components: the route file holds the page's state and loads and
  passes data down and callbacks up; each card, table, tab, and dialog with its form is a
  component of `src/lib/components/<area>/` with typed props. Pure helpers of an area are `.ts`
  files beside its components; state with its loading and error that several components share
  is a `.svelte.ts` module with runes; every number, size, rate, percentage, temperature, duration
  and age goes through `src/lib/format.ts` and every date and time through `src/lib/time.ts`, in the
  format of `$intlLocale` with the words of the catalog `format` (ADR-0459), the message of a
  failed request through `messageOf` of `src/lib/errors.ts`, and a helper that needs texts takes
  `$t` as a `Translate` of `$lib/i18n` (its keys are checked, its other literals are not: keep
  texts in the components). A file above about 400 lines is one concern or is split; duplicated
  markup becomes a component or a snippet.
- A backdrop or scrim that closes on a click listens for it itself,
  `{@attach (node) => on(node, 'click', close)}` with `on` of `svelte/events`, as `Modal` and
  the phone drawer do: iOS Safari makes a tap a click only where an element listens, and a
  delegated `onclick` never sees a click that does not bubble.

## Design system (ADR-0012)

- Pages are built from `$lib/components/ui`: `Button`, `Card`, `CardAction`, `Badge`, `Input`,
  `Select`, `Checkbox`, `RadioGroup`, `Switch`, `SwitchRow`, `FieldGroup`, `Modal`, `Sheet`,
  `ConfirmDialog`, `Alert`, `HelpText`, `TechDetails`, `ProgressBar`, `EmptyState`, `ErrorState`,
  `LoadState`, `StatusDot`, `Skeleton`, `Menu`, `ActionMenu`, `BulkBar`, `Tabs`, `Segmented`, and
  `confirm()` and `toast` (ADR-0432); a component that no page uses is removed (ADR-0466). `Input`
  and `Select` take a `label`, and dialogs, lists and hints work with the keyboard and screen
  readers (ADR-0361).
  A dialog asked from a dialog opens over it, which stays open: `Modal` keeps the stack of open
  dialogs, and Escape closes only the top one, whatever the markup order.
- Colours are the tokens of the `@theme` blocks of `src/app.css` (Tailwind CSS 4: no
  `tailwind.config.js`, no PostCSS): `surface`, `surface-elevated`,
  `surface-overlay`, `surface-muted`; `content`, `content-secondary`, `content-muted`; `border`;
  `status-success`, `status-warning`, `status-error`, `status-info`. Each token is a CSS variable of
  `src/app.css` with a value for the dark and the light theme, which `data-theme` on `<html>`
  selects (`$lib/stores/theme`, ADR-0405): no colour is written anywhere else, `app.css` and the
  components of `ui` included, and `supervisor/tests/test_webui_theme.py` fails for a token without
  a value in both themes or a pair that text or a control uses below WCAG AA (4.5:1, 3:1). Icons take the classes of
  `src/app.css`: `icon-cpu`, `icon-memory`, `icon-network`, `icon-storage`, `icon-container`,
  `icon-temperature`.
- Layout uses the classes of `src/app.css` (the shapes of ADR-0428): `page-container`,
  `page-header` (`page-title`, `page-subtitle`, `page-actions`), `section-title`, `card`,
  `card-header`, `card-title`, `card-nested`, `card-flush` (a table to the card's edges; `Card
  flush`), `table` with `num`, `table-compact` and `table-actions`, `table-header`, `table-row`,
  `table-frame`; `kv` (a label over its value, `div > span.kv-label + span.kv-value`), `toolbar`
  of `toolbar-group`s, `chip`, `eyebrow` (group labels), `content-narrow` (forms), `link` (a text
  link, `content-link`). Page tabs are `tab-bar` with `tab-underline`; filters and views a
  `segmented` control of `tab-pill`s. Focus is the one `:focus-visible` ring of `app.css`: do not
  remove an outline without a ring of its own. A delete icon button is `Button variant="ghost-danger"`.
- Every page fits a 375 px phone (ADR-0302): below `md` the sidebar is the drawer of the header
  bar's menu button. The page never scrolls sideways: a table that can be wider than the screen
  sits in a `table-frame` and scrolls inside it, rows of buttons and fields wrap (`flex-wrap`),
  long names and paths break. A dialog is a `Modal`, or its overlay takes Modal's classes
  (`modal-overlay items-start overflow-y-auto p-4`, `my-auto` on the panel), so that one taller
  than the screen scrolls; no height comes from the window's (`calc(100vh - ...)`). A text for
  screen readers (`sr-only`) in a part that scrolls on its own sits in a `relative` box there.
  `tests/mobile-layout.spec.ts` walks every page, tab and main dialog at 375 x 812 in both
  languages, and the dialogs at 667 x 375 too: a new page, tab or dialog joins its `VIEWS`, a
  dialog with its `start` (title) and `end` (last button).
- Buttons are `Button` (`variant="plain"` for a tab, with `tab-pill` or `tab-underline`, or a
  clickable card or row; `href` for a link that looks like a button), fields are `Input` and
  `Select`, or a raw field with a `form-input` or `form-select` class when it needs an attribute
  they lack. No `.svelte` file outside `ui` uses a palette colour (`bg-slate-700`, `text-red-400`, `text-white`; the classes a `.ts` module returns are for review): the
  tokens above are the colours, and `brand`, `brand-hover`, `brand-ring`, `brand-disabled`,
  `surface-hover`, `surface-inverse` (a tooltip, under `content-inverse`), `border-strong`, `content-soft`,
  `content-on-brand`, `content-link` and `status-neutral` cover the rest; a dialog's backdrop is the class `scrim`. `npm run design` (`scripts/design-check.mjs`,
  run by `make frontend`, tested by `supervisor/tests/test_webui_design_system.py`) fails for a
  raw `<button>`, a raw `<a>` styled as a button, a raw text field, select or textarea without the
  `form-*` class of its kind (`form-select` for a select, `form-input` for the others) or with a
  class or style that sets its `appearance` (ADR-0460: Safari would draw it its own way), and a raw palette class in a class attribute, a `class:` directive or a string
  literal, outside `src/lib/components/ui`, and names the token to use (ADR-0400; the mapping is
  in the ADR). It also fails for `window.confirm`, `alert` and `prompt` (also in a `.ts` module),
  the class `uppercase`, a raw checkbox or radio and a filled primary `Button` in a card's header or
  a table's row (ADR-0432), and for a success `Alert` (a success after an action is a `toast`), a
  hand-made spinner (`animate-spin`, `loading-spinner`: use Button's `loading`, `LoadState`,
  `Skeleton`, or a `StatusDot` with `pulse` for a job that runs) and a raw `<details>` (use
  `TechDetails`, whose `children` take a list) (ADR-0438). In every file of `src/`, `ui`'s
  included, but `src/lib/format.ts` and `src/lib/time.ts`, it fails for a number or a date written by
  hand: `toFixed`, `toPrecision`, `toLocaleString` and its date and time kin, Intl's number, date,
  relative-time and duration formats, a date's getters, `padStart(2, '0')` and a percentage or a
  unit of a size put together after a value (`value + ' MB'`, `{value}%` between tags), and in a
  catalog for `'{value}%'` and a Russian `%` without its no-break space (ADR-0459).
  `// design-ignore: <reason>` (an HTML comment above an element) exempts a use: an outcome to read
  before going on keeps its success `Alert` so, with its reason; an SVG path's coordinates keep
  their `toFixed`.

## Patterns (ADR-0432)

- A load: `LoadState` with the page's skeleton, its `ErrorState` (a catalog title, the server's
  message, Retry; never the empty state after a failure) and its `EmptyState` with its next step
  as its `action`, the one control it takes (ADR-0466). A technical message folds into `TechDetails`
  (`isTechnical` of `$lib/errors`); an `Alert` takes a `details` snippet, and help is one line of
  `HelpText`. The NAS's problems are the health banner's (`ProblemsBanner`, ADR-0332); a page's own
  state is said on its object.
- A destructive action asks with `await confirm({ title, message, consequences, confirmLabel })`:
  the title is the question, the button a verb and its object. What an action did, or why it failed,
  is a `toast` (`toast.success`, `toast.error(message, { title })`); an outcome to read before going
  on stays on the page, by its object. The specs answer a confirmation with `tests/ui.ts`.
- One filled primary button per view, in the page header. A card's header action is a `CardAction`
  (with `href` a link to where its things are managed, the arrow its icon and not in the text; else
  a small secondary button), its badges Card's `status`. A row shows at most edit and delete as
  icons, in a `table-actions` cell that never wraps, and the rest in an `ActionMenu`; a table that
  must read on a phone takes `table-stack` and a `data-label` per cell. A selection's actions are a
  `BulkBar`. A disabled button says why (`disabledReason`).
- A number field has its unit (`suffix`), bounds (`min`, `max`) and a width, and no browser's spin
  buttons (`app.css`); a count to keep steps with Input's `steppers` (ADR-0460); related fields are a
  `FieldGroup`; an on/off setting is a `Switch` or a `SwitchRow`, an item to select a `Checkbox`,
  one of several a `RadioGroup`; a page section a `Tabs`, a filter a `Segmented`. A dialog takes a
  `Modal` `size`; more than six fields or a list is a `Sheet`. A badge is sentence case and readable
  alone; a category is `outline`.
- A time of what the NAS runs by its clock is shown on it (`formatDateTime(v, $intlLocale, { zone:
  $nasTimeZone })`), the zone named once on the page.

## Loading (ADR-0430)

- The boot screen of `src/app.html` covers the page until the root layout calls `finishBoot` of
  `$lib/boot.ts`: once the account is known (or the page needs none) and the page has drawn. The
  shell is drawn with the account's role; while the account is unknown the sidebar draws skeletons.
- A page's first load shows a `Skeleton` of its final layout (`line`, `block`, `circle`, `row`,
  `tile`, `card`, `table`) in place of everything that load brings, under `aria-busy="true"`, and
  swaps it for the data at once: nothing drawn before is pushed down. No "Loading…" text. A part
  that loads on its own later sits last or keeps its height. A refresh, or a reload after an
  action, keeps what is shown (a `loaded` flag; the skeleton only before the first answer). What a
  page needs at once is fetched in parallel. A skeleton has no text and no role, so that the specs'
  roles and texts find only the page. `tests/loading.spec.ts` checks the main pages with the API
  slowed: no "Loading", no empty frame, the navigation drawn once, a layout shift below 0.05.

## Data

- Live data comes from the store `stats` of `$lib/stores/stats`, with `wsConnected` and
  `wsForbidden`, which `/ws/v1/events` feeds every 2 seconds (ADR-0363); a page reads the objects
  of the message it needs (`$stats.cpu`, `$stats.network.interfaces`). The same socket fills the
  stores `problems`, `repairJob` (the running or last repair of a share's permissions) and
  `appInstalls` (the running install, update or rollback of an app and the last of each, ADR-0384,
  ADR-0408) for administrators. Rates come from the server; a page writes sizes and rates with
  `formatSize` and `formatRate` of `src/lib/format.ts` (ADR-0459).
- No page polls the API: other live data comes over a WebSocket of its own, as a container's log
  (`streamContainerLogs` of `$lib/api/containers.ts`, ADR-0255) and the journal of System > Logs'
  Auto do (`streamLogEntries` of `$lib/api/logs.ts`, ADR-0366), or as a message of
  `/ws/v1/events` into a store, as the scrubs that run do (`$lib/stores/scrubs`, ADR-0367).
  `scripts/polling-check.mjs`, which `supervisor/tests/test_webui_polling.py` runs, fails for a
  `setInterval` whose callback reaches the API, also through an exported function of another
  module under `src/` or a method of a class of a `.ts` module (`lists.load()`, by name), or
  changes a store or a variable of `$state`, `$derived` or `$props()` that an API-reaching
  `$effect` or `$derived` reads, also through a `$derived` and outside `untrack` (ADR-0427);
  `// polling-ignore: <reason>` above it exempts a countdown that asks once when its time has run
  out (ADR-0365).
- The session is in the HttpOnly session cookie, which no script reads (ADR-0397): the page never
  holds a token, only the session key, which `$lib/api/client.ts` keeps in the tab and sends with
  every request; the server takes the cookie only from the web UI's own origin and with the key.
  Sign-ins and sign-outs go through `login` of `$lib/api/auth` and `signOut` of
  `$lib/stores/auth`, which tell the other tabs (`BroadcastChannel` `unos-session`).
- A WebSocket authenticates with its first message, `{"type": "auth", "key": currentSessionKey()}`,
  with the cookie of the handshake; no credential in URLs (ADR-0031). Close code 4401 ends the
  session, 4403 means forbidden.
- HTTP goes through `$lib/api/client.ts`, one module per API area in `$lib/api/`: `api.get`,
  `post`, `put`, `patch`, `delete` for JSON, `apiFetch` for other answers, `downloadAs` for files.
  It ends the session once on a 401 of a request sent in the current session and throws
  `ApiError` with the server's message.
- Regular users see Dashboard and Account only (ADR-0030): every other page is for administrators.

## Texts (ADR-0287)

- Every text comes from a catalog in English and Russian: `src/lib/i18n/catalog/<area>.ts`
  exports `en` and `ru: Translation<typeof en>`, keys start with `<area>.`. Markup and scripts use
  `$t('area.key')`, `$t('area.key', { name })` for `{name}`, and `$t('area.key', { n })` for a
  text with plural forms (`{ one, other }` in English, `{ one, few, many }` in Russian).
  `$lib/i18n` collects every catalog file: an area adds its own file and edits no shared one;
  `common.ts` has the words that many areas use, `format.ts` the units of the numbers. Numbers and
  dates take `$intlLocale` through `src/lib/format.ts` and `src/lib/time.ts`. A value that the
  server answers as a word (an action, a state, a type) is shown through a map from the value to
  a key, `{ allow: 'firewall.allow' }`, never as it comes. System > Network keeps what
  ip and ss print in its tables (link types, protocols, socket states) as it comes (ADR-0295).
- English stays word for word what the specs select: `playwright.config.ts` runs an en-US
  browser, and `tests/language.spec.ts` switches to Russian. The server's messages (the
  `ApiError` texts) stay English, like the fakes' copies of them (ADR-0189).
- `npm run i18n` (`scripts/i18n-check.mjs`) fails for a text of a `.svelte` file that does not
  come from `$t`: text between tags, a user-facing attribute or prop, and every string literal of
  a script or of a markup expression that is not code (compared or looked up, a key, a class, a
  name, an id, a path); for a key that no catalog has, also in a map of keys; and for a catalog
  whose halves differ. A literal that is the same in every language (a command, a browser's name)
  gets `// i18n-ignore: <reason>` above its statement. The check cannot see a text that the code
  puts together from data, a single lower-case word in a script, or a `.ts` module: translate
  those too. `scripts/i18n-untranslated.txt`
  lists the files not translated yet; a translated file leaves it. The empty list is not enough:
  the feature passes after a walk through every page in Russian (ADR-0287).

## Checks and build

- `make frontend` runs `npm run check` (svelte-kit sync, then svelte-check `--threshold error`:
  errors fail, warnings do not), `npm run runes` (the runes check above), `npm run i18n` (the
  texts, above, and the keys that the `.ts` modules name),
  `npm run design` (the design system, above), `npm run build` (into `webui/build`),
  `npm run lint` and `npm run format-check` (below). The checks of this repository read
  components with Svelte's parser and walk its tree with `scripts/svelte-walk.mjs`.
- ESLint (`eslint.config.js`: the recommended rules of ESLint, typescript-eslint and
  eslint-plugin-svelte) and Prettier (`prettier.config.js`: tabs, single quotes, no trailing
  commas, 120 columns) check `src/` (ADR-0449). Run `npm run format` before `make frontend`. A
  finding is fixed; a rule turned off in `eslint.config.js`, or a line exempted with
  `// eslint-disable-next-line <rule> -- <reason>`, gives its reason. Prettier breaks a long line
  at a space, and Svelte 5 keeps a line break between `{' '}` and a block element as a second
  space: such an element keeps its source with `<!-- prettier-ignore -->` above it, where the
  comment adds no space (between table cells, at the start of a block), and its reason.
- Tailwind CSS 4 (ADR-0456): `src/app.css` imports its theme and preflight, holds the tokens in
  `@theme` blocks, and puts the utilities in the layer `components` after its own classes, as
  Tailwind CSS 3 ordered them (a more specific class of `app.css` wins over a utility). It keeps 3's
  line heights, `hover:` on every device, 3's `space-y-*` and `divide-y` (before every child but
  the first), and in its base layer what 3's preflight gave fields, buttons and table cells.
  `translate-x-*` and `rotate-*` set `translate` and `rotate`: a transition names those, not
  `transform`. `transition-colors` also fades `outline-color`, so a focus ring fades in: the
  web UI writes 3's list (`transition-[color,background-color,...]`). `@apply` takes utilities only
  (`@utility` of `app.css` included): a class of `app.css` is written out. A component's `<style>`
  names no layer (its sheet can load before `app.css`, and its first layer would set the order of
  all), so it wins over every layer; with `@apply` it starts with `@reference` to `src/app.css`.
  Breakpoints are in px. A colour with an
  opacity is a `color-mix()`, which the browser reports as `oklab(...)` (`tests/theme.spec.ts`
  reads it). The web UI's build tools have no riscv64 binaries and none for ARMv7 with musl: such
  a host takes the image from another machine.
- `webui/node_modules` comes from `make setup` (`npm ci` from `package-lock.json`). A new
  dependency is the owner's call: it needs the network and a lockfile change.
- The Node.js version of `webui/Dockerfile`, of `engines` in `package.json` and of CI agree;
  `supervisor/tests/test_deploy_scripts.py` checks them.
- `webui/Caddyfile` is the image's Caddy configuration and its only copy
  (`deploy.one-caddyfile`), and `scripts/standin/front.mjs` sends the same security headers
  (`supervisor/tests/test_standin.py`).

## E2E specs (`webui/tests/`)

- They run only against local targets, never the dev device (ADR-0215): the stand-in by default
  (`make e2e-local`, or `scripts/standin/e2e.sh tests/<name>.spec.ts` for one spec: the
  standin-e2e skill), or the compose test stack (`docs/TESTING.md`), which sets
  `E2E_STAND_IN=1` too and takes the same skips. `tests/target.ts` refuses a `BASE_URL` or
  `HTTP_URL` whose host is not local.
- What the stand-in cannot provide (real disks, btrfs, Docker, the host's network, the journal,
  Linux's /proc) is a skip that gives its reason:
  `test.skip(process.env.E2E_STAND_IN === '1', 'The stand-in has no ...' + TEST_STACK_TOO)`;
  `supervisor/tests/test_standin.py` checks every such skip. A failing spec gets fixed: no list of
  known failures, no added retries.
- Specs that must not change real disks, shares or accounts answer the page's requests with the
  route fakes of `tests/storage-fakes.ts` (`fakeStorageApi`), `tests/shares-fakes.ts`
  (`fakeSmbApi`) and `tests/health-fakes.ts` (`fakeHealthApi`: SMART data and failure
  predictions), which abort unknown requests. Their texts are the server's, word for word:
  change the fake with the server (ADR-0189).
- Signed-in specs use the `authenticatedPage` fixture of `tests/fixtures.ts`. No spec reads a
  token from the page's storage: `tests/session.ts` reads the session cookie from the browser
  context, signs a page in (`signInPage`), puts a session of the API into the cookie
  (`useSessionCookie`) or acts in the browser's session (`browserSession`); other API calls send
  a Bearer token (`apiLogin` of `tests/accounts.ts`). `E2E_POOLS_BASE` is
  the stand-in's pools folder; `E2E_STANDIN_STATE` and `E2E_STANDIN_PYTHON` are its state directory
  and Python, with which `scripts/standin/emergency.py` runs the supervisor's emergency command
  line against the stand-in's data (`tests/emergency-reset.spec.ts`). Restore specs run only on a
  local target or with `E2E_ALLOW_RESTORE=1` (`tests/backup-restore.spec.ts`).
- A spec that waits for a file chooser registers `page.on('filechooser')` before it opens the
  page and takes the choosers its listener collects, as 'uploads files from the keyboard' does:
  Playwright turns the interception on with the first listener without waiting for the browser,
  so a raw `page.keyboard.press('Enter')` right after `waitForEvent('filechooser')` can open a
  chooser that no event reports (ADR-0454).
- The specs run in Chromium; `tests/form-fields.spec.ts` runs in WebKit too, the project `webkit` of
  `playwright.config.ts`, and checks that the fields of a form are one height, a select is the
  design's and no number field shows spin buttons (ADR-0460). A form with a select, a number, date
  or time field that the other forms do not show joins it.
- `@playwright/test` is one exact version in `package.json` and the lock, the version of
  `Dockerfile.test`'s image too, and a release that downloads its browsers for the Ubuntu of
  GitHub's ubuntu-latest, where the bootstrap job runs `make setup` (ADR-0461). Move it with the
  monthly pass of `docs/maintenance.md`, which says how, and run the full `make e2e-local`.
- Every feature's user flow has a spec (ADR-0186).
- A change runs the specs of its area: those of the pages and APIs it changes, plus every spec
  citing a test id, label or catalog key it changed (`git grep` in `tests/`). A change to
  `src/lib/components/ui/`, `src/app.css` or `src/routes/+layout.svelte` runs the full
  `make e2e-local`, which otherwise runs once per batch, on the batch branch after the merges
  (ADR-0433).

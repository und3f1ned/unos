# webui/: the SvelteKit web UI and the E2E specs

SvelteKit 2 with Svelte 4 (`<script lang="ts">`, props through `export let`, `$:` statements),
the static adapter, strict TypeScript and TailwindCSS (ADR-0012). Caddy serves the built files
and proxies `/api/*` and `/ws/*` to the supervisor (`webui/Caddyfile`).

## Design system (ADR-0012)

- Pages are built from `$lib/components/ui`: `Button`, `Card`, `Badge`, `Input`, `Select`,
  `Modal`, `Alert`, `ProgressBar`, `EmptyState`, `StatusDot`. `Input` and `Select` take a
  `label`, and dialogs, lists and hints work with the keyboard and screen readers (ADR-0361).
  A dialog asked from a dialog opens over it, which stays open: `Modal` keeps the stack of open
  dialogs, and Escape closes only the top one, whatever the markup order.
- Colours are the tokens of `tailwind.config.js`: `surface`, `surface-elevated`,
  `surface-overlay`, `surface-muted`; `content`, `content-secondary`, `content-muted`; `border`;
  `status-success`, `status-warning`, `status-error`, `status-info`. Icons take the classes of
  `src/app.css`: `icon-cpu`, `icon-memory`, `icon-network`, `icon-storage`, `icon-container`,
  `icon-temperature`.
- Layout uses the classes of `src/app.css`: `page-container`, `page-header`, `page-title`,
  `section-title`, `card`, `card-header`, `card-title`, `card-nested`, `table`, `table-header`,
  `table-row`, `table-frame`.
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
  `surface-hover`, `border-strong`, `content-soft`, `content-on-brand` and `status-neutral` cover
  the rest; a dialog's backdrop is the class `scrim`. `npm run design` (`scripts/design-check.mjs`,
  run by `make frontend`, tested by `supervisor/tests/test_webui_design_system.py`) fails for a
  raw `<button>`, a raw `<a>` styled as a button, a raw text field, select or textarea without a
  `form-*` class, and a raw palette class in a class attribute, a `class:` directive or a string
  literal, outside `src/lib/components/ui`, and names the token to use (ADR-0398; the mapping is
  in the ADR). `// design-ignore: <reason>` (an HTML comment above an element) exempts a use.

## Data

- Live data comes from the store `stats` of `$lib/stores/stats`, with `wsConnected` and
  `wsForbidden`, which `/ws/v1/events` feeds every 2 seconds (ADR-0363); a page reads the objects
  of the message it needs (`$stats.cpu`, `$stats.network.interfaces`). The same socket fills the
  stores `problems`, `repairJob` (the running or last repair of a share's permissions) and
  `appInstalls` (the running install of an app and the last of each, ADR-0384) for
  administrators. Rates come from the server; a page writes sizes and rates with the units of its
  catalog, as `NetworkTab.svelte` and `MonitorTab.svelte` do (ADR-0287).
- No page polls the API: other live data comes over a WebSocket of its own, as a container's log
  (`streamContainerLogs` of `$lib/api/containers.ts`, ADR-0255) and the journal of System > Logs'
  Auto do (`streamLogEntries` of `$lib/api/logs.ts`, ADR-0366), or as a message of
  `/ws/v1/events` into a store, as the scrubs that run do (`$lib/stores/scrubs`, ADR-0367).
  `scripts/polling-check.mjs`, which `supervisor/tests/test_webui_polling.py` runs, fails for a
  `setInterval` whose callback reaches the API, also through an exported function of another
  module under `src/`, or changes a store or a variable that an API-reaching `$:` statement reads; `// polling-ignore: <reason>` above it exempts a
  countdown that asks once when its time has run out (ADR-0365).
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
  `common.ts` has the words that many areas use. Dates take `$intlLocale`. A value that the
  server answers as a word (an action, a state, a type) is shown through a map from the value to
  a key, `{ allow: 'account.firewall.allow' }`, never as it comes. System > Network keeps what
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
  errors fail, warnings do not), `npm run i18n` (the texts, above), `npm run design` (the design
  system, above) and `npm run build` (into `webui/build`).
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
- Every feature's user flow has a spec (ADR-0186).

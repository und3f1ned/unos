# webui/: the SvelteKit web UI and the E2E specs

SvelteKit 2 with Svelte 4 (`<script lang="ts">`, props through `export let`, `$:` statements),
the static adapter, strict TypeScript and TailwindCSS (ADR-0012). Caddy serves the built files
and proxies `/api/*` and `/ws/*` to the supervisor (`webui/Caddyfile`).

## Design system (ADR-0012)

- Pages are built from `$lib/components/ui`: `Button`, `Card`, `Badge`, `Input`, `Select`,
  `Modal`, `Alert`, `ProgressBar`, `EmptyState`, `StatusDot`. `Input` and `Select` take a
  `label`, and dialogs, lists and hints work with the keyboard and screen readers (ADR-0197).
- Colours are the tokens of `tailwind.config.js`: `surface`, `surface-elevated`,
  `surface-overlay`, `surface-muted`; `content`, `content-secondary`, `content-muted`; `border`;
  `status-success`, `status-warning`, `status-error`, `status-info`. Icons take the classes of
  `src/app.css`: `icon-cpu`, `icon-memory`, `icon-network`, `icon-storage`, `icon-container`,
  `icon-temperature`.
- Layout uses the classes of `src/app.css`: `page-container`, `page-header`, `page-title`,
  `section-title`, `card`, `card-header`, `card-title`, `card-nested`, `table`, `table-header`,
  `table-row`.
- Older pages still have raw `<button>` elements and `slate-*` colours: markup you write or
  change uses the components and the tokens.

## Data

- Live data comes from the stores of `$lib/stores/stats` (`stats`, `cpuStats`, `memoryStats`,
  `networkStats`, `diskIOStats`, `processes`, `statsHistory`, `temperatures`, `wsConnected`), which
  `/ws/v1/events` feeds every 2 seconds (ADR-0006). Rates come from the server; format them with
  `formatBytes` and `formatBytesPerSecond`.
- The `setInterval` polling in `LogsTab.svelte` and the storage page predates that rule: new live
  data comes over the WebSocket, as a container's log does (`streamContainerLogs` of
  `$lib/api/containers.ts`, ADR-0248).
- A WebSocket authenticates with its first message, `{"type": "auth", "token": ...}`; tokens stay
  out of URLs (ADR-0031). Close code 4401 ends the session, 4403 means forbidden.
- HTTP goes through `$lib/api/client.ts`, one module per API area in `$lib/api/`: `api.get`,
  `post`, `put`, `patch`, `delete` for JSON, `apiFetch` for other answers, `downloadAs` for files
  (a plain link cannot carry the token). It sends the Bearer token (ADR-0009), ends the session
  once on a 401 and throws `ApiError` with the server's message.
- Regular users see Dashboard and Account only (ADR-0030): every other page is for administrators.

## Checks and build

- `make frontend` runs `npm run check` (svelte-kit sync, then svelte-check `--threshold error`:
  errors fail, warnings do not) and `npm run build` (into `webui/build`).
- `webui/node_modules` comes from `make setup` (`npm ci` from `package-lock.json`). A new
  dependency is the owner's call: it needs the network and a lockfile change.
- The Node.js version of `webui/Dockerfile`, of `engines` in `package.json` and of CI agree;
  `supervisor/tests/test_deploy_scripts.py` checks them.
- `webui/Caddyfile` is the copy the image uses. `deploy/Caddyfile` stays identical to it, and
  `scripts/standin/front.mjs` sends the same security headers (`supervisor/tests/test_standin.py`).

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
- Signed-in specs use the `authenticatedPage` fixture of `tests/fixtures.ts`. `E2E_POOLS_BASE` is
  the stand-in's pools folder. Restore specs run only on a local target or with
  `E2E_ALLOW_RESTORE=1` (`tests/backup-restore.spec.ts`).
- Every feature's user flow has a spec (ADR-0186).

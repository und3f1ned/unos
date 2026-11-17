---
status: accepted
date: 2026-11-17
area: auth
decided-by: agent
---

# ADR-0392: An API token can be limited to reading and to areas of the API, checked where every credential is checked

## Context

An API token acted with all the rights of its user (ADR-0239), so a script that only reads, such as
a monitor, held a credential that could delete pools. The entry `auth.api-token-scopes` asks for
tokens limited to reading or to named areas of the API; it settles the last item of ADR-0033 and
ADR-0015. The owner delegated the design, asking above all that it be safe (ADR-0013): a limited
token must never reach more than it says, nor make itself or anything else more powerful.

## Decision

- **Scope.** A token has an access, read only or read and change, and its areas, every area or a
  list. A token with change access to every area is **full**: it acts as every token did before.
  Any other is **limited**.
- **Areas, by router prefix.** An area is the first segment of a path after `/api/v1/`, one per
  prefix of the routers that `supervisor/src/main.py` includes, named as the prefix: `apps`,
  `audit`, `auth` (the own account, users and API tokens), `backup`, `containers`, `files`,
  `groups`, `health` (with `/health/full` and `/health/prometheus`), `logs`, `monitor`, `network`,
  `notifications`, `offsite`, `power`, `scheduler`, `security`, `shares`, `snapshots`, `storage`,
  `system`, `updates` (`AREAS` in `supervisor/src/auth/scopes.py`). The API is organised and
  documented by these prefixes, so a script's author sees a URL's area in the URL itself; one
  area per prefix keeps areas whose change access runs programs as root (below) apart from the
  harmless ones. A route of a prefix that is no area fails the walk of the tests.
- **Stored** in the column `api_tokens.permissions` that the table already has for this (ADR-0253
  let it take only `*` until this entry), so nothing is altered (ADR-0319): `*` is full; otherwise
  the word `read` for read only, then the areas, comma-separated in alphabetical order; `read`
  alone is read only in every area. The tokens that exist hold `*`, the API's only value since
  ADR-0253 and its default before, and stay full; a value that a client sent before ADR-0253 takes
  the meaning of its words where they are a scope, and refuses its token otherwise.
- **Where it is checked.** In `authenticate_token` of `supervisor/src/auth/utils.py`, which every
  credential passes, HTTP and WebSocket, with a required `operation`: `verify_token` and
  `verify_token_allowing_setup` give the request's method and its path as routed, the WebSocket
  handshake and its check every minute the socket's path. Every route that is not public depends on
  one of them (`supervisor/tests/test_authz.py`), so a new route cannot escape the check, and it
  runs before any handler and before an upload reads its form (ADR-0062). After the 401s of
  `verify_api_token` and the 403 of a setup-only account (ADR-0386), a limited token gets 403, in
  this order: "This API token's scope cannot be read: delete it and create a new one"; "A limited
  API token cannot change accounts, users, API tokens or two-factor authentication, nor restore a
  backup: sign in to do it"; "This API token is limited to other areas of the API"; "This API token
  can only read"; "A read-only API token cannot read this: the answer holds secrets". Session
  tokens have no scope.
- **What reads.** GET and HEAD read; every other method changes. The exceptions, and the borderline
  operations kept as their method says, are the table `OPERATION_KINDS` of
  `supervisor/src/auth/scopes.py`, which the tests compare with a copy of their own:
  - secrets, which a read-only token does not read: `GET /backup/download/{filename}` (the database:
    password hashes, two-factor secrets, token hashes) and `GET /notifications/configs` (SMTP
    passwords, bot tokens and webhook URLs as stored). No GET downloads a key;
  - POSTs that only read: `POST /power/ups/test` (reads the UPS with the stored settings) and
    `POST /files/upload-leftovers/check` (looks for leftovers, keeps what it finds in memory);
  - changes although nothing in UNOS changes: `POST /notifications/test/{config_id}` and
    `POST /notifications/send` (send messages with the stored credentials) and
    `POST /health/disk/{device}/test` (starts a SMART self-test);
  - reads although they show much: `GET /files/download` and `GET /containers/{container_id}/logs`
    (what apps keep and print, their passwords included), `GET /audit/logs/export`,
    `GET /logs/file/{filename}`, and `GET /scheduler/tasks`, `GET /power/ups/config`,
    `GET /offsite/settings` and `GET /security/certificates`, which answer no passphrase, password
    or private key;
  - never for a limited token, whatever its access and areas: every change in the area `auth`
    (API tokens, users, the own password, two-factor authentication, recovery codes, the own SMB
    access, sessions), `POST /backup/restore/{filename}`, which replaces the accounts and their
    tokens, and turning the requirement of two-factor authentication on or off. So a limited token
    cannot escalate: only a session makes or changes tokens (ADR-0253), and none of the routes that
    change who may do what takes a limited token.
- **WebSockets** only send, so a read-only token opens them; their areas: `/ws/v1/logs/{container_id}`
  `containers`, `/ws/v1/journal` `logs`, and `/ws/v1/events`, which carries the data of several
  areas (stats, problems, scrubs, balances, a share's repair, app installs), every area. Refused,
  the socket closes with 4403; the check every minute reads the scope again, as a restore can
  change it.
- **Untrusted rows.** A restored database's value is read as stored at every use (ADR-0144): an
  unknown word, an empty or repeated item, `*` beside another item, no text at all, or text that
  is not UTF-8 refuses the token everywhere with the 403 above, nothing is dropped or guessed. The
  column is deferred and read as its SQLite type and its bytes (`scopes.stored_columns`), so that
  text the driver cannot decode still lets its owner list, disable and delete the token. The token
  list answers it with `scope_known` false (`read_only` true, `areas` empty), and a restore whose
  database has such tokens says how many among its warnings.
- **API.** `POST /api/v1/auth/tokens` takes `read_only` (a JSON boolean, default false) and `areas`
  (1 to 21 area names, each once; absent or null: every area); `permissions` left the API, and a
  field the request does not know answers 422, so that a client still sending `permissions` gets
  no full token. The answers of the create and the list carry `read_only`, `areas` (null: every
  area, sorted otherwise) and, in the list, `scope_known`. Without `read_only` and `areas` a token
  is full: a request that states no limit means what it meant before.
- **Web UI.** The Account page (System > Security) gets the card API tokens
  (`webui/src/lib/components/account/ApiTokensCard.svelte`, texts in
  `webui/src/lib/i18n/catalog/tokens.ts`): the tokens with their access, areas, state, expiry and
  last use, Disable Token, Enable Token and Delete Token, and New Token, a dialog that starts at
  Read only and Every area, the least that a person can choose, and shows the token once. It warns
  when the areas chosen with change access include `apps`, `backup`, `containers`, `offsite`,
  `scheduler` or `updates` (`WARNED_AREAS`), and says that through the API a limited token never
  changes accounts, users, tokens or two-factor authentication.
- **Audit.** The creation's entry names the scope: "Created API token: <name> (full access)", "(read
  only, every area)", "(read only: backup, storage)" or "(read and change: backup, storage)". A
  refused request writes none (ADR-0320).
- Tests: `supervisor/tests/test_token_scopes.py` (the walk of every route for each scope with a
  regular user's and an administrator's tokens, the table, the WebSockets, stored values that
  cannot be read, a restore, the audit entry), `webui/tests/api-tokens.spec.ts`,
  `webui/tests/mobile-layout.spec.ts`.

## Consequences

- A read-only token changes nothing that UNOS keeps; it still runs what reading runs, such as a
  SMART read that wakes a sleeping disk or a check of the UPS.
- Change access to an area is the administrator's power over it. In `apps`, `containers`,
  `scheduler` and `updates` that includes running programs as root on the host (a privileged app,
  a container's bind mount, a custom task, the source of updates), from where a token can do what
  a full one does. In `backup` and `offsite` it includes the database, with every account's
  password hash and two-factor secret (a backup that it makes and downloads, an off-device copy to
  a store of its choice), from which an offline guess of a weak password gains that account. In
  `shares` it can open a guest share, and `offsite` also sends copies of the pools elsewhere. Areas
  bound what a script is meant to do and what a leaked read-only or harmless-area token can do,
  not what these areas' change access can reach: the dialog and the user guide say so.
- A script that reads its own account (`GET /auth/me`) needs the area `auth`.
- A limited token is one more parse of a short string per request; a full token, none.
- A token's scope cannot change: a new token replaces it.

## Considered options

- A new table of scopes beside `api_tokens`: a token without its row would be full, a fail open,
  its row would need deleting with the token's, and SQLite reuses a deleted token's id.
- Areas that group prefixes as the web UI's pages do: fewer choices, but `scheduler` or `containers`
  would join harmless prefixes, and the API's URLs would no longer name their area.
- The check in a middleware: it would read the token a second time per request and see paths that
  no route serves.
- Refusing every change in the areas whose change access runs programs as root: a script that
  restarts a container could not have a limited token at all; the dialog and the user guide warn
  instead. For the same reason a token with change access to `backup` downloads backups: a backup
  script takes them off the device.
- A read-only default in the API as well: it would turn a request that states no limit into a
  different token than before; the dialog starts at read only, where a person chooses.

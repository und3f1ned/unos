---
status: accepted
date: 2026-11-17
area: auth
decided-by: agent
supersedes: [ADR-0009]
---

# ADR-0395: The web UI's session is an HttpOnly cookie accepted only from its own origin, and programs keep Bearer tokens

## Context

The web UI kept its session token in `localStorage` (ADR-0009), where any script that runs in the
page reads it and can send it elsewhere, to be used from anywhere until the session ends. The entry
`auth.token-storage` settles the second item of ADR-0033: keep the token out of `localStorage`, and
say whether a reload keeps the session. The owner delegated the design, asking above all that it be
safe (ADR-0013). Only an HttpOnly cookie is out of every script's reach, but the browser sends a
cookie by itself, also to the other ports of the NAS's name, where apps run, some of them over HTTPS.

## Decision

- **The cookie.** A sign-in of the web UI puts its session token, the JWT of ADR-0329, into the
  cookie `__Host-unos-session` (`SESSION_COOKIE` of `supervisor/src/auth/utils.py`): `HttpOnly`,
  `Secure`, `SameSite=Strict`, `Path=/`, no `Domain`, no `Max-Age`. The `__Host-` prefix keeps
  pages without TLS and other hosts of the domain from setting it. Without `Max-Age` the browser
  drops it when its session ends (a browser that restores sessions keeps it); the server's session
  ends it at the latest after 24 hours (ADR-0329).
- **The session key.** The cookie alone is no session. The sign-in answers `session_key`, an HMAC of
  the session's id under the JWT secret (`session_key`, so nothing is stored for it, ADR-0319), and
  the page sends it with the cookie in every request, in the header `X-UNOS-Session-Key`, and in a
  WebSocket's first message. The page keeps it in its tab's `sessionStorage` (`unos-session-key`),
  so that a reload keeps the session. Cookies are not bound to a port: the browser also sends the
  cookie to an HTTPS service on another port of the NAS's name, such as the catalog's Nextcloud on
  8443, whose server never sees the key; a script of the page reads the key but never the cookie.
  A cookie without the key of its session answers 401 "The session key does not match the session
  cookie" on every route, so a cookie that such a service sets in the place of UNOS's is refused
  too; such a service can still delete or replace the cookie, which signs the browser out.
- **The token of the cookie counts only there.** The sign-in marks its token (`cookie: true` in the
  JWT): as a Bearer token it answers 401 "This session is the web UI's: it is valid only in its
  cookie, with its session key", and in a WebSocket's first message 4401. The other way, a session
  of a program in the cookie with its key, is allowed: it is no more than its holder has.
- **Sign-in.** `POST /api/v1/auth/login` takes `cookie` (a JSON boolean, default false). With it,
  the answer sets the cookie and carries no token (`access_token` "", `token_type` "cookie"), only
  `session_key`; the step that asks for the second factor sets nothing. Only the web UI's own page
  may ask for it: the origin check below, with an Origin required, runs before the password is
  checked, and a refusal answers 403 without a login attempt or an audit entry.
- **Programs keep Bearer.** Without `cookie` the sign-in answers the token, for an
  `Authorization: Bearer` header, as `scripts/dev/api.py` and the E2E specs' API calls use it; API
  tokens are unchanged (ADR-0239, ADR-0392). The header wins over the cookie when a request has
  both. An API token in the cookie answers 401.
- **Where it is checked.** Every route that is not public takes its credential from `credential`
  in `supervisor/src/auth/utils.py`, under `verify_token`, `verify_token_allowing_setup`,
  `require_session` and the routes that read the session: 401 "Not authenticated" without one.
- **Against forged requests.** The cookie counts only from the web UI's own page (`from_own_origin`,
  checked before the key): the browser's `Sec-Fetch-Site`, when sent, must be `same-origin`, and its
  `Origin`, when sent, must be `https://` and the `Host` that the request was sent to, port
  included; every method but GET and HEAD needs the `Origin`, which browsers send with each of them.
  Otherwise the answer is 403 "The session cookie is accepted only from the web UI's own page".
  `SameSite=Strict` keeps other sites' pages from sending the cookie, but a page on another port of
  the NAS is the same site: its Origin, and its lack of the key, tell it apart. The key, which no
  other page has, does what a CSRF token would.
- **WebSockets.** No CORS protects a WebSocket. The web UI's first message is
  `{"type": "auth", "key": "<session key>"}` without a token: the server takes the cookie of the
  handshake, only with an `Origin` that passes the check and the key of its session; otherwise the
  socket closes with 4401. A program's token in the first message is used as before and needs no
  Origin. This replaces ADR-0031's first message for the web UI and takes the cookie that ADR-0031
  set aside; the rest of ADR-0031 stays: no credential in URLs, 10 seconds for the first message,
  the check every 60 seconds, 4401 and 4403.
- **Sign-out.** `POST /api/v1/auth/logout` with the cookie and its key ends the session (ADR-0329)
  and deletes the cookie (`Max-Age=0` with the same attributes); with a Bearer token it leaves the
  cookie alone. The page drops the key at once and goes to the sign-in page. A 401 answer counts as
  signed out: the cookie is then no session that the key goes with (ended, gone or replaced), so
  nothing that the browser holds is that session any more. The page cannot delete an HttpOnly
  cookie itself: when the request fails or cannot reach the server, the sign-in page says that the
  NAS did not confirm the sign-out and offers Sign Out Again, with the key kept in the page's
  memory for it until a sign-in (`signOutFailed` of `webui/src/lib/stores/auth.ts`). This replaces ADR-0329's "the web UI's
  Logout calls it with the token after it has forgotten it, so a failed call still signs the browser
  out".
- **Tabs.** A cookie changes without an event: the tabs of the origin talk over the
  `BroadcastChannel` `unos-session` (`webui/src/lib/api/client.ts`). A sign-in sends its key to the
  other tabs, which go on in that session (another account's too); Logout tells them once the server
  has answered that no session of the cookie and key is left; a new tab asks the open ones for the
  key and, without an answer within half a second, goes to the sign-in page. A request answers for
  the session it was sent in: a 401 to a request sent before the last sign-in or sign-out that the
  tab knows of changes nothing.
- **Earlier versions' token.** The page removes the `token` key that earlier versions left in
  `localStorage`, at every load.
- **What stays.** The setup-only session of ADR-0386 is a cookie session like any other, and so is
  the session that a restore keeps for the administrator who restored (`caller_session` reads the
  cookie). CORS stays as ADR-0009 set it: no other origin unless `UNOS_CORS_ALLOWED_ORIGINS` lists
  it, every origin with `UNOS_DEBUG_MODE=true` for development, methods and headers listed; a
  client of such an origin uses a Bearer token, and the cookie never counts from it. Files still
  download through `downloadAs`. Caddy (`webui/Caddyfile`) and the stand-in's front
  (`scripts/standin/front.mjs`) pass the `Cookie`, `Set-Cookie`, `Origin` and `Host` headers on
  unchanged.
- Tests: `supervisor/tests/test_session_cookie.py` (the cookie's attributes, the origin and the key
  on every route, the token of the cookie elsewhere, the WebSocket, sign-out, web storage),
  `supervisor/tests/test_backup_restore.py`, `webui/tests/session-cookie.spec.ts`; no spec reads a
  token from the page's storage (`webui/tests/session.ts`).

## Consequences

- No script of the page reads the session token, and no server that the browser sends the cookie to
  elsewhere can use it. A script injected into the page can still act with the session while the
  page is open, as any request of the page does, and so make an API token: the Content Security
  Policy (ADR-0025) and showing data as text (ADR-0027) stay the defense against injection.
- A reload keeps the session, and so does a new tab while a tab of the web UI is open in the
  browser; a new tab with none open asks to sign in again, with the password and, with two-factor
  authentication, a code.
- A visit without a session or a key costs at most one 401, which the access log keeps (ADR-0338).
- A reverse proxy in front of UNOS must pass the `Host` header on, as Caddy does, or every change
  of the web UI answers 403. The web UI works only over HTTPS: the Vite development server over
  plain HTTP no longer signs in; the stand-in serves HTTPS (`scripts/standin/e2e.sh`).
- The E2E specs run in Chromium. Firefox and Safari send the Origin of `fetch()` and of WebSockets
  as the Fetch standard says; nothing here tests them.
- A sign-out that does not reach the server leaves the session until it expires; the sign-in page
  says so. A sign-out whose session had already ended leaves the dead cookie until the browser
  drops it or a sign-in replaces it. A tab that learns of another tab's sign-in only after its own
  request was refused for the new cookie goes to the sign-in page.
- The `token` that earlier versions kept in `localStorage` is removed, not ended: its session stays
  valid until it expires, 24 hours at most.

## Considered options

- Memory only, a reload asking to sign in again: every reload and new tab would take the password
  and a code, and write a sign-in to the system disk (ADR-0214).
- The token in `sessionStorage`: scripts read it as they read `localStorage`.
- The cookie alone, with only the origin check against forged requests: the browser would hand a
  full session to every HTTPS service on another port of the NAS's name.
- A cookie with `Max-Age` of the session's lifetime: it would outlive the browser, as
  `localStorage` did, on a computer that others use too.
- Binding the session to the client's address: addresses change under people (IPv6 privacy
  addresses, Wi-Fi and mobile data), and the stored session row has no column for it (ADR-0319).

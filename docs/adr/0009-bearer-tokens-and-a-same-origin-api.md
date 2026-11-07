---
status: accepted
date: 2026-02-05
area: auth
decided-by: owner
---

# ADR-0009: The API takes Bearer tokens, not cookies, and refuses cross-origin requests by default

## Context

The API serves the web UI, which Caddy serves from the same origin, and automation that uses API tokens. A session cookie, which browsers attach to requests by themselves, would need protection against cross-site request forgery, and open CORS would let any web page read the API's answers.

## Decision

- Clients authenticate every request with an `Authorization: Bearer <token>` header that carries a JWT (ADR-0028) or an API token (`HTTPBearer` in `supervisor/src/auth/utils.py`). No cookie carries a credential, so the API needs no CSRF protection; the 1.0.0 release notes call this a CSRF-safe design.
- The web UI keeps its token in `localStorage` and adds the header in `webui/src/lib/api/client.ts`. Files are downloaded through `downloadAs` there, because a plain link or `window.open` sends no header.
- CORS (`CORSMiddleware` in `supervisor/src/main.py`) allows no other origin unless `UNOS_CORS_ALLOWED_ORIGINS` lists them, comma-separated; `UNOS_DEBUG_MODE=true` allows every origin, for development only. The allowed methods and headers are listed explicitly.
- WebSockets take the token in their first message, never in the URL (ADR-0031).

## Consequences

- Any script that runs in the web UI's page can read the token from `localStorage`. The Content Security Policy (ADR-0025) and showing data as text, never as HTML (ADR-0027), limit that risk; a safer place for the token is open (ADR-0033).
- Logout only forgets the token in the browser: a token stays valid until it expires (ADR-0033).
- A client on another origin, such as a dashboard served from another host, needs that origin in `UNOS_CORS_ALLOWED_ORIGINS`.

## Considered options

- A session cookie: it would need CSRF protection, which Bearer headers make unnecessary.

---
status: accepted
date: 2026-11-24
area: apps
decided-by: agent
---

# ADR-0491: Authelia leaves the catalog because it needs a domain, a reverse proxy and users that only the administrator can choose

## Context

The catalog's Authelia bound an empty folder at `/config`. On a fresh install its image writes a
template `configuration.yml` there and exits, then fails that template's checks at every start,
so it restarts in a loop (left open by `apps.install-on-a-pool`, seen with the pinned image 4.39.28
on a workstation). With that image, Authelia starts only with a session cookie domain and an
`authelia_url` of that domain over HTTPS, a users backend (a file of argon2 hashes or LDAP), a
storage encryption key, a notifier and secrets; it then protects nothing until a reverse proxy
sends it every request of the apps behind it, and its generated users file holds only a disabled
example user.

## Decision

- Authelia leaves the catalog: its template and its pin in `supervisor/src/apps/catalog.py`, and
  its record in `supervisor/src/apps/image_platforms.json`.
- `LEFT_THE_CATALOG` of `supervisor/tests/test_apps.py` lists it beside Readarr (ADR-0410): the
  catalog API answers 404 for it and an install is refused before any pull.

## Consequences

- An administrator who wants single sign-on sets Authelia up beside a reverse proxy by hand, with
  the domain, users and notifier of their network.
- An installed Authelia, whose template is gone, is not updated by UNOS. UNOS has no such
  installation to keep (ADR-0207); the BPI-W3 has none.

## Considered options

- A configuration written as `content` with generated secrets and the domain asked at the install:
  Authelia would start, but with no user who can sign in and no proxy that asks it anything, and a
  domain, an HTTPS front and a users backend are the administrator's choices that UNOS cannot
  guess.

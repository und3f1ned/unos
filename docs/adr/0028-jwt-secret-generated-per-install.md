---
status: accepted
date: 2026-11-05
area: auth
decided-by: agent
---

# ADR-0028: Each install generates its own JWT secret, and published placeholders are refused

## Context

`config.py` and the compose file fell back to the published secret `change-me-in-production`, and the supervisor accepted it. Only `install.sh` generated a random secret; a manual install, a plain `docker compose up` or a deploy by the old CLAUDE.md used the placeholder. On the live device the secret was the placeholder, and the audit showed that an administrator token signed with it elsewhere was accepted: anyone on the LAN could become administrator without a password.

## Decision

- Unless `UNOS_JWT_SECRET` is set (the compose file passes the host's `JWT_SECRET`), the supervisor uses `<data directory>/jwt_secret` (`/data/jwt_secret` in the `unos-data` volume), which it creates on first start with a random 256-bit value, mode 0600, atomically (`resolve_jwt_secret` in `supervisor/src/config.py`).
- It refuses to start with a published or common placeholder (`INSECURE_JWT_SECRETS`: the former compose, CLAUDE.md and test values, and words such as `secret` or `changeme`), also when the placeholder is in the secret file, and it refuses an empty secret file.
- `UNOS_JWT_ALGORITHM` accepts only HS256, HS384 or HS512, and every token must carry `exp` and `sub` (`supervisor/src/auth/utils.py`).
- `deploy/install.sh` no longer writes a secret, and `deploy/docker-compose.yml` has no default for it.
- Tests: `supervisor/tests/test_secret_bootstrap.py`.

## Consequences

- Every install signs with its own secret. At the switch everyone was signed out once; since then the secret survives restarts, rebuilds and reboots, because it lives in the data volume.
- Whoever can read the data volume or a backup of it can forge sessions, so `update.sh`'s backups, which contain the file, are root-only (ADR-0049).
- On the dev device `JWT_SECRET` is left unset on purpose.

## Considered options

- A secret only from `install.sh`'s `/etc/unos/secrets.env`, the 1.0.0 way: every other way of starting the stack used the placeholder.
- A secret only from the environment, with a refusal to start without one: not chosen; the audit recommended generating it on first start.

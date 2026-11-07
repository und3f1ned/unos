---
status: accepted
date: 2026-11-07
area: device
decided-by: agent
---

# ADR-0222: The device API relay caches the admin token in the device's RAM

## Context

`scripts/dev/api.py` calls the UNOS API of the dev device through a short relay that runs on the device against `https://127.0.0.1/api/v1` (ADR-0198). Every login writes to the device's SD card (ADR-0214): it adds a row to the login history and rewrites the whole audit log (ADR-0066). Logins are also limited to 5 per minute per client (ADR-0010). Measured on 2026-11-07: the first call of a session, which signs in, wrote 0.188 MiB to the SD card, and the later calls with the cached token about 0.020 MiB each.

## Decision

- The relay keeps the administrator's token in `/dev/shm/unos-dev-api.token` on the device: in RAM, owned by root, mode 0600 (`TOKEN_CACHE` in `scripts/dev/api.py`). A new token goes to a new file with an unguessable name in the same directory, renamed over the cache, so nothing is written through a link and no reader sees half a token.
- `/dev/shm` is shared by every user of the device, so the relay ignores a cache that is not a regular file of its own user with mode 0600 (a symlink, a FIFO, a file of another user, a file with another mode): it signs in and replaces it.
- It signs in only when no usable token is cached or the API refuses the cached one with 401, and then once: a 401 after that new login is the answer. The login, `UNOS_DEV_ADMIN_USER` and `UNOS_DEV_ADMIN_PASSWORD` from `dev.env`, travels on SSH's standard input, never on a command line (ADR-0212), and the relay sends it only to a loopback address of the device, never through a proxy.
- `scripts/dev/api.py --forget-token` deletes the token; a reboot of the device clears it.
- The relay runs in the read-only mount namespace (ADR-0221), where `/dev/shm` stays writable.
- Tested in `supervisor/tests/test_dev_scripts.py`, where the relay runs on the workstation against a stub HTTP server with the cache in a temporary directory: `test_the_relay_keeps_the_token_private_and_signs_in_only_when_needed` (mode 0600, reuse, one new login after a 401, `--forget-token`), `test_the_relay_never_reads_a_cache_that_is_not_its_own_private_file`, `test_the_relay_writes_the_token_through_no_link` and `test_the_relay_calls_only_the_api_of_the_device_itself`.

## Consequences

- A session of API calls signs in once: it writes one login's entries instead of one per call and stays far below the login limit.
- The token is a JWT, a bearer credential that stays valid until it expires, 24 hours by default (ADR-0033). It lies in the device's RAM, readable only by root, and root on the device can read the JWT secret and mint tokens anyway (ADR-0028).
- About 0.020 MiB per API call remains, mostly access-log lines in the containers' json-file logs on the SD card (ADR-0178; the feature-list entry `deploy.container-logs-off-the-system-disk`).
- The relay cannot sign in as an administrator with two-factor login; `api.py` says so and stops.

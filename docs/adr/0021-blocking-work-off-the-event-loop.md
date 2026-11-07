---
status: accepted
date: 2026-11-05
area: architecture
decided-by: agent
---

# ADR-0021: Blocking work runs off the event loop, with timeouts

## Context

The supervisor is an async FastAPI app on one event loop. In November 2026 its async code made 143 blocking calls, and an image pull or a scrub stopped the whole API, logins and the live stats included, until it finished.

## Decision

- Blocking work does not run on the event loop: host commands for storage and shares, scrubs, smartctl, bcrypt hashing and checking, container log streams, e-mail and the Docker calls of app installs and starts run in worker threads (`asyncio.to_thread`, or a dedicated executor such as the SMTP executor of `supervisor/src/notifications/router.py`).
- Each such call has a timeout, for example e-mail sending 15 s, scrub commands 60 s, smartctl 30 or 60 s, and 10 to 30 s for each host command of the SMB code.
- Scrubs return as soon as btrfs has started them (ADR-0071).

## Consequences

- A slow disk or a stuck host command occupies one worker thread instead of stopping the API.
- A command that hangs in the kernel, for example on a hung filesystem, is killed at its timeout but still waited for, so it keeps its thread until the kernel lets it go. Starting it with `Popen` and not waiting after the kill would free the thread; that is open.
- Not every route follows the rule yet: most routes of `supervisor/src/containers/router.py` (listing, creating, stopping and removing containers, their logs, listing, pulling and removing images) still call Docker on the event loop.

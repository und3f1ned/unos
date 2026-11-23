---
status: accepted
date: 2026-11-23
area: system
decided-by: agent
---

# ADR-0476: Notifications name the NAS by the host's name, read for each message, with the last name read as a fallback

## Context

Every notification named its sender "UNOS NAS" (Telegram, a webhook's `source`, the footers of
Discord and Slack), "[UNOS]" (an e-mail's subject) or "your UNOS NAS system" (the test), so the
messages of two NASes could not be told apart. The owner: "notifications must name the host, not
just 'UNOS NAS', since there may be more than one." The name must hold on a failing system disk
too, where ADR-0475 keeps the alerts' channels in RAM.

## Decision

- **The name.** What `hostname` prints on the host, read through `host.run_on_host` as
  `GET /system/info` reads it (ADR-0331), never the container's name. `notifications.host_name`
  reads it when a sender builds a message, so that a new name shows in the next one; the messages
  built within `HOST_NAME_REUSE` (1 s) of a read's start share it, so that a test's text and its
  footer, and the channels of one alert, give one name.
- **What the messages show** (`supervisor/src/notifications/router.py`). The sender `UNOS · <name>`:
  the last line of an e-mail and of Telegram, the footer of Discord and Slack, a webhook's `source`.
  An e-mail's subject is `[<name>] <title>`. A webhook's JSON keeps its keys and gains `host`, the
  name; a body template gains `{host}`. The test notification is "This is a test notification
  from UNOS on <name>." The `username` of Discord and Slack channels defaults to null, which sends
  `UNOS · <name>`; a name set through the API is sent as it is.
- **A read that fails.** One read runs at a time, in a daemon thread of its own, with
  `HOST_NAME_TIMEOUT` (5 s) for nsenter and hostname. A message waits for it until
  `HOST_NAME_WAIT` (6 s) after the read started; a read that failed or has not ended by then
  gives way to the last name read, kept in RAM, or, before any, to `UNOS` alone (the subject
  `[UNOS]`, `{host}` `UNOS`, `host` null). After a failed read no new one starts for
  `HOST_NAME_RETRY` (60 s): a hostname stuck on the host while nsenter is killed at its timeout
  does not cost every channel of an alert its 5 s. A read stuck in the container holds one
  thread, delays the messages once by at most 6 s, and holds no executor's worker: the alerts'
  e-mail loop (`asyncio.run` in `health/router.py`) waits for those at its end. The failure is
  logged once until a read works again. Nothing is written (ADR-0214).
- `supervisor/tests/test_notifications.py` (`TestTheHostsName`) checks each channel, a renamed
  host, one read for a test and for an alert, the four ways a read fails, the wait after a failed
  read, a read that hangs, and an e-mail alert beside a hung read.

## Consequences

- A notification starts one `nsenter … hostname` on the host, or none within a second of the
  last: a few milliseconds, no disk write.
- Channels stored with the old default `username` "UNOS" keep it as their sender's name (no
  conversion, ADR-0207); their footers name the host.
- A name that the host has not given since the supervisor started is never shown: after a restart
  on a failing disk, the messages say `UNOS` until a read works.

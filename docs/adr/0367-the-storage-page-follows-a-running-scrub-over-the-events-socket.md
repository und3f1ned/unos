---
status: accepted
date: 2026-11-12
area: storage
decided-by: agent
supersedes: [ADR-0313]
---

# ADR-0367: A scrub runs from its start, its result names how it ended, and the Storage page follows it over the events socket

## Context

ADR-0313 made a scrub run from its start, before btrfs records it, and named how it ended; the
Storage page asked `GET /api/v1/storage/pools/{name}/scrub` every 2 seconds while an answer said
running, against ADR-0006 (webui.no-http-polling). Working on through the features under the
owner's delegation ("work on through the features, ask no questions; I allow everything", in
translation), the agent moved the following to `/ws/v1/events`. The rules of ADR-0313 that stay are
restated here.

## Decision

- `get_scrub_status` (`supervisor/src/storage/pools.py`) reports the status as `btrfs scrub status`
  prints it: running, finished, aborted (cancelled) or interrupted (ended neither by finishing nor
  by a cancel). A scrub whose output has the "Time left" or "ETA" line runs, also below "no stats
  available", unless `btrfs replace status -1` reports a replace that runs ("x.y% done"): the
  kernel runs the copy of a disk replace as a scrub. Idle is "no stats available" without a scrub
  that runs. `errors` is the sum of the counts on the "Error summary" line. When btrfs fails to
  report the status, the endpoint answers 500 "Scrub status failed: <btrfs's error>", and when it
  does not answer within `SCRUB_COMMAND_TIMEOUT`, 500 as well (ADR-0290).
- Right after btrfs accepted a start, `start_scrub` answers running when the status says idle or
  cannot be read; right after a cancel that btrfs did, `cancel_scrub` answers aborted when the
  status says idle or cannot be read.
- `supervisor/src/storage/scrubs.py` keeps, in RAM, each pool whose scrub was seen running, with
  its last status or why it could not be read. The scrub API notes each answer (a status read, a
  start, a cancel) and, for a status read, why it failed; the Scheduler's scrub task notes its pool
  as running once btrfs started it. A pool joins only when its scrub runs. While an administrator's
  events socket is open, each time it sends the stats it asks for a read of the scrubs that run
  (`follow`): at most every 2 seconds and one read at a time, in a task of its own that the socket
  does not wait for; a read that a request answered meanwhile is dropped, its answer being older. A
  scrub that no longer runs, or whose status could not be read, is not read again. The socket sends
  administrators `{"type": "scrubs", "data": [...]}`, each pool's status as the API answers it or
  `{"pool": ..., "error": ...}`, after `auth_ok` once a scrub was seen running and whenever they
  change; regular users never get it, and nothing is read while no administrator is connected.
- The pool card (`webui/src/routes/storage/+page.svelte`) shows a running scrub with its progress
  (the bar named "Scrub progress", bytes, percent, rate and duration, once btrfs reports bytes
  scrubbed); a finished one as "Last scrub: No errors" or "Last scrub: N error(s)"; an aborted one
  as "Last scrub: cancelled before it finished" and an interrupted one as "Last scrub: interrupted
  before it finished", each followed by ", N error(s) found" when btrfs counted errors until then;
  each result with the time the scrub started. Nothing is shown for idle. The page asks for each
  mounted pool's status when it loads its lists, and shows each status of the scrubs messages
  (`scrubs` of `webui/src/lib/stores/scrubs.ts`) as it shows an answer. A status that cannot be read
  is shown as "Could not read the scrub status" with the reason, instead of the last one; the last
  one still decides the button, so that a scrub that was running can be cancelled.
- Tests: `supervisor/tests/test_storage.py` (`TestScrubStatus`, `TestScrubsForTheEventsSocket`),
  `supervisor/tests/test_ws_auth.py` (`TestTheScrubsOnTheEventsSocket`),
  `supervisor/tests/test_scheduler_validation.py` (`test_scrub_task`), `webui/tests/storage.spec.ts`
  (`Scrub (fake data)`, on route fakes and a fake events socket).

## Consequences

- A scrub started outside UNOS, on the host's command line, shows as running as soon as the
  container's btrfs prints its time left, and the page follows it once a status read found it
  running; its record stays the host's.
- A disk replace that runs while the container keeps the record of an earlier scrub still shows as
  a running scrub, and Cancel Scrub would cancel the replace. UNOS starts no replace.
- A cancelled or interrupted scrub never passes for a check of the whole pool: Scrub starts a new
  one. A restart of the supervisor, also with the host, forgets the last result (ADR-0171) and the
  scrubs it followed: a scrub that runs then joins at the page's next load.
- A status that could not be read is asked for again at the next load of the page.

---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
---

# ADR-0250: A scrub's result names a cancelled or interrupted scrub and counts every error btrfs reports

## Context

The Storage page showed a cancelled scrub ("aborted") as "Last scrub: No errors", like a scrub that checked the whole pool, and a scrub that btrfs reports as "interrupted" not at all. `get_scrub_status` counted only the first number of btrfs's error summary ("read=1 csum=12" gave 1), and answered "idle" when btrfs failed to report the status. After Cancel Scrub, or a status request that failed, the page stopped asking and kept showing the scrub as running (storage.scrub).

## Decision

- `get_scrub_status` (`supervisor/src/storage/pools.py`) reports the status as `btrfs scrub status` prints it: running, finished, aborted (cancelled) or interrupted (the filesystem was unmounted or the host restarted while it ran); idle when no scrub is recorded ("no stats available"). `errors` is the sum of the counts on the "Error summary" line (read, super, verify and csum errors), 0 for "no errors found". When btrfs fails to report the status, the endpoint answers 500 "Scrub status failed: <btrfs's error>", and when it does not answer within `SCRUB_COMMAND_TIMEOUT`, 500 as before (ADR-0071). Right after btrfs started or cancelled a scrub, a status that cannot be read is answered running or aborted, which btrfs's own answer already tells.
- The pool card (`webui/src/routes/storage/+page.svelte`) shows a running scrub with its progress (the bar named "Scrub progress", bytes, percent, rate and duration); a finished one as "Last scrub: No errors" or "Last scrub: N error(s)"; an aborted one as "Last scrub: cancelled before it finished" and an interrupted one as "Last scrub: interrupted before it finished", each followed by ", N error(s) found" when btrfs counted errors until then; each result with the time the scrub started. Nothing is shown for idle. Whenever an answer, Start Scrub's and Cancel Scrub's included, says running, the page asks again every 2 seconds until it does not. A status that cannot be read is shown as "Could not read the scrub status" with the reason, instead of the last one, and asked for again at the next load of the page; the last one still decides the button, so that a scrub that was running can be cancelled.
- Tests: `supervisor/tests/test_storage.py` (`TestScrubStatus`), `webui/tests/storage.spec.ts` (`Scrub (fake data)`, on route fakes).

## Consequences

- A cancelled or interrupted scrub never passes for a check of the whole pool: Scrub starts a new one (btrfs's resume is not offered).
- Still open (ADR-0071, storage.scrub-needs-mounted-pool): the 409 for a pool that is not mounted, and one helper for every scrub start.

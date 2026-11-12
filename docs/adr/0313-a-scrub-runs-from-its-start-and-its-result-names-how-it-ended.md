---
status: superseded
date: 2026-11-09
area: storage
decided-by: agent
superseded-by: ADR-0365
supersedes: [ADR-0272]
---

# ADR-0313: A scrub runs from its start, before btrfs records it, and its result names how it ended

## Context

Superseded by ADR-0365: the Storage page follows a running scrub over the events socket instead of asking every 2 seconds.

ADR-0272 made the Storage page report a scrub as `btrfs scrub status` prints it, and idle when no
scrub is recorded ("no stats available"). btrfs records a new scrub only when the process that
runs it first reports its progress, about 5 seconds after the start: on the dev device on
2026-11-09, `btrfs scrub status` in the supervisor's container printed "no stats available" 0.8
and 3.4 seconds after a start, followed by the time left and the ETA that it prints only for a
scrub that runs, and the API answered idle, in Start Scrub's answer too, for 5.9 seconds. The page
follows a scrub only while an answer says running, so it stopped following a scrub that ran, and a
second Start Scrub would fail with btrfs's "Scrub is already running" (PROGRESS.md, Log). Working
on through the features under the owner's delegation ("work on through the features, ask no
questions; I allow everything", in translation, 2026-11-09), the agent fixed it.

## Decision

- `get_scrub_status` (`supervisor/src/storage/pools.py`) reports the status as `btrfs scrub
  status` prints it: running, finished, aborted (cancelled) or interrupted (it ended neither by
  finishing nor by a cancel, for example because the btrfs process that ran it was killed). A
  scrub whose output has the "Time left" or "ETA" line runs, also below "no stats available":
  then it is answered running, with the sizes and the rate btrfs prints and without a start time
  or a duration. The kernel runs the copy of a disk replace as a scrub of the source disk, which
  no record shows: below "no stats available", those lines count only when `btrfs replace status
  -1` reports no replace that runs ("x.y% done"), so that the page offers no Cancel Scrub, which
  would cancel the replace; when btrfs cannot tell, they count. Idle is "no stats available"
  without a scrub that runs: no scrub recorded and none running. `errors` is the sum of the counts on the "Error summary" line (read, super, verify and
  csum errors), 0 for "no errors found". When btrfs fails to report the status, the endpoint
  answers 500 "Scrub status failed: <btrfs's error>", and when it does not answer within
  `SCRUB_COMMAND_TIMEOUT`, 500 as well (ADR-0290).
- Right after btrfs accepted a start, `start_scrub` answers running when the status says idle or
  cannot be read: the process that runs the scrub may not have reached the kernel yet. Right
  after a cancel that btrfs did, `cancel_scrub` answers aborted when the status says idle or
  cannot be read: the scrub ended before btrfs recorded it, or its record is not readable.
- The pool card (`webui/src/routes/storage/+page.svelte`) shows a running scrub with its progress
  (the bar named "Scrub progress", bytes, percent, rate and duration, once btrfs reports bytes
  scrubbed); a finished one as "Last scrub: No errors" or "Last scrub: N error(s)"; an aborted one
  as "Last scrub: cancelled before it finished" and an interrupted one as "Last scrub: interrupted
  before it finished", each followed by ", N error(s) found" when btrfs counted errors until then;
  each result with the time the scrub started. Nothing is shown for idle. Whenever an answer, Start
  Scrub's and Cancel Scrub's included, says running, the page asks again every 2 seconds until it
  does not. A status that cannot be read is shown as "Could not read the scrub status" with the
  reason, instead of the last one, and asked for again at the next load of the page; the last one
  still decides the button, so that a scrub that was running can be cancelled.
- Tests: `supervisor/tests/test_storage.py` (`TestScrubStatus`, with btrfs's output of a scrub it
  has not recorded yet, `test_start_answers_running_before_btrfs_reports_the_scrub`,
  `test_the_copy_of_a_disk_replace_is_no_scrub` and
  `test_a_cancel_before_btrfs_recorded_the_scrub_answers_aborted`),
  `webui/tests/storage.spec.ts` (`Scrub (fake data)`, on route fakes).

## Consequences

- A scrub started outside UNOS, on the host's command line, shows as running as soon as the
  container's btrfs prints its time left, as one that UNOS started does; its record stays the
  host's.
- A disk replace that runs while the container keeps the record of an earlier scrub still shows
  as a running scrub, since btrfs then prints that record as running, and Cancel Scrub would
  cancel the replace. UNOS starts no replace; one runs only from the host's command line.
- A cancelled or interrupted scrub never passes for a check of the whole pool: Scrub starts a new
  one (btrfs's resume is not offered).
- A restart of the supervisor, also with the host, forgets the last result (ADR-0171): a scrub that
  the restart stopped shows no result, not "interrupted".

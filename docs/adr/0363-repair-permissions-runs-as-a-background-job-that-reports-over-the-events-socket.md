---
status: accepted
date: 2026-11-12
area: architecture
decided-by: agent
supersedes: [ADR-0006, ADR-0155]
---

# ADR-0363: Repair permissions runs as a background job that reports over the events socket, where live data goes

## Context

ADR-0155 ran Repair permissions within its request, for at most 240 s and 5 million entries, and
ADR-0006 named it as the long operation that answers when it is done: a large share took several
runs, each one waited for. The feature `shares.repair-in-background` asks for one run per share,
which the Shares page follows live without polling. Working on through the features under the
owner's delegation ("work on through the features, ask no questions; I allow everything", in
translation), the agent chose the job's bound, its stop and its messages. This ADR restates the
rules of ADR-0006 and ADR-0155 that stay.

## Decision

Live data (ADR-0006):

- Live data travels over the supervisor's WebSockets in `supervisor/src/websocket/events.py`:
  `/ws/v1/events` sends the system stats every 2 seconds (`STATS_INTERVAL_SECONDS`), with the rates
  computed on the server, and `/ws/v1/logs/{container_id}` streams a container's log.
- The web UI reads the stats from the store in `webui/src/lib/stores/stats.ts` and its derived
  stores, which keep one socket, authenticate it (ADR-0031) and reconnect with backoff. Pages do not
  poll HTTP endpoints with `setInterval` and do not compute rates themselves.

Repair permissions (ADR-0155), now a background job:

- Only an administrator's Repair permissions of one share gives its files and folders the group
  unos-smb and the modes 2770 for folders and 0660 for files (0770 when the owner may execute),
  which removes the access that group and other bits gave other accounts; nothing does it
  automatically. Owners and contents never change. How it walks the tree is ADR-0156; in a walk
  that can take hours, it reads the mount table again with each progress report, so that a mount
  made inside the share meanwhile (an app's FUSE mount) is skipped by its path, unexamined, from
  then on.
- `POST /api/v1/shares/smb/{name}/repair-permissions` needs a served share on a pool that is mounted
  writable, a gid 70000 that no other group has (ADR-0151) and a safe parent chain (ADR-0153);
  otherwise it answers 409 and walks nothing. Then it answers 202 at once with the job: the share's
  name, the state `running`, the start's time, `finished_at` null and the counts at 0. The walk runs
  in a daemon thread of its own (`repair_router.py`), not in the event loop's pool, which a walk of
  hours would hold.
- One repair at a time: a start while one runs, of any share, answers 409 "A permissions repair is
  already running".
- Its bound: the walk stops after 24 hours (`repair.TIME_LIMIT_SECONDS`) with the state
  `incomplete`, and has no entry limit. That is far more than a share of tens of millions of entries
  takes, and still an end for a walk that never finishes, such as one in a tree that an app fills as
  fast as it is walked, which would hold the one repair that may run.
- Its stop: `DELETE` on the same path asks the running repair of that share to stop (202 with the
  job; 409 "No permissions repair of this share is running" otherwise). The walk stops before its
  next entry and ends `incomplete` with "Stopped by an administrator; run it again to continue
  (already repaired entries are skipped quickly)"; the audit entry of its end names who stopped it.
  Without it, a run of hours on the wrong share, or one that slows the pool when it is needed, could
  end only with a restart of the supervisor; what a stopped run changed stays changed, and a new run
  continues quickly. The stop, like the 24-hour bound, is checked between entries: it cannot end a
  walk blocked in a system call on the folder (a stalled disk), which stops only once the call
  returns.
- A deletion of the share, or a change of its name (other than in letter case) or of its folder,
  stops its repair too once committed (`repair_router.stop_for_share`), and its audit entry names it
  ("stopped by the deletion of the share", "a change of the share's name", "a change of the
  share's folder"): the walk would go on in a folder that is no longer the share's, under a name
  that no longer finds it. `DELETE` finds the repair of a share whose row is gone by the name it
  started with.
- Its progress and end: the walk reports its counts about once a second
  (`repair.PROGRESS_INTERVAL_SECONDS`). `repair_router.current_job()` is the running repair or the
  last one that ended, and `/ws/v1/events` sends it to administrators as
  `{"type": "repair", "data": ...}`, right after auth_ok, with `"data": null` when none has run since
  the start, and whenever it changes, at most every 2 seconds; regular users never get it. The web
  UI keeps what each message carries, null included, in the store `repairJob` of
  `webui/src/lib/stores/repair.ts`, so that after a restart of the supervisor, whose socket it signs
  in to again, it no longer shows a repair that ended with it: the Repair dialog shows the counts
  and a Stop repair button and can be closed, the share's card says Repairing permissions, and the
  Shares page reads its shares again when the repair ends. Nothing polls.
- Its end: one audit entry is written per run, and only then does the end become, at the same
  moment, the job, the share's last result (which `GET` on the same path answers) and a free slot
  for the next repair. The last results are kept in memory by the share's id: a rename keeps the
  result, a deletion drops it. A stop of the supervisor stops a running repair as an administrator's
  stop does, "stopped by a stop of the supervisor", and waits up to 5 seconds
  (`repair_router.SHUTDOWN_WAIT_SECONDS`) for its audit entry; a walk blocked in a system call ends
  without one. A restart forgets the job and the results.
- It does not change ACL entries: on a file with an ACL the group bits it sets become the ACL's
  mask, so named users and groups keep their access and can get more, and unos-smb gets only what
  the owning-group entry gives. The Repair dialog and `docs/USER_GUIDE.md` say so.
- Tests: `supervisor/tests/test_share_repair_job.py` (start, one at a time, stop, the end, a share
  deleted, renamed or moved, a stop of the supervisor, mounts made during the walk, roles),
  `test_share_repair.py` (the walk's stop and progress, Linux), `test_ws_auth.py`
  (`TestTheRepairOnTheEventsSocket`), `test_ws_stats.py`, `webui/tests/shares.spec.ts` and
  `webui/tests/websocket.spec.ts`; on the device, `scripts/dev/scenario.sh shares`, which waits for
  the end through `GET`.

## Consequences

- One run repairs a large share, and the page shows how far it got; a stopped or bounded run is
  continued by a new one.
- Other accounts, and apps that run as their own user, lose access to a repaired share (ADR-0160).
- The scrub status on the Storage page and the Auto of System > Logs, which still polled HTTP
  endpoints with timers when this was decided, now follow the events socket (ADR-0367) and a journal
  socket of their own (ADR-0366).

## Considered options

- A request with a higher limit: browsers give up on a request after 300 s, and a large share still
  needed several runs.
- A job whose progress the page polls with `GET`: ruled out by the live-data rule above.

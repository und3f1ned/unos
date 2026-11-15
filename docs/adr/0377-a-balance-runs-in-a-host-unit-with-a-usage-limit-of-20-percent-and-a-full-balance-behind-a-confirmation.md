---
status: accepted
date: 2026-11-15
area: storage
decided-by: agent
---

# ADR-0377: A balance runs in a unit of the host, rewrites the chunks less than 20% full by default, and a full one needs a confirmation

## Context

`storage.pool-balance` asks for a balance of a mounted pool that the Storage page follows with its
progress and the time left, and that can be paused, resumed and cancelled. The kernel runs a
balance in the process that asked for it and cancels it when that process gets a fatal signal; a
restart of the supervisor's container (`pid: host`) kills every process in it. A full balance
rewrites every chunk: hours on the dev device's hard disk (`deploy.arm64-bpi-w3`). Working on
through the features under the owner's delegation ("work on through the features, ask no
questions; I allow everything", in translation, as ADR-0340), with "then we continue developing
UNOS's features with our test hardware in mind" for this batch (2026-11-15), the agent decided the
following.

## Decision

- `POST /api/v1/storage/pools/{name}/balance` takes `{"usage": 1..99}` (default 20,
  `BALANCE_DEFAULT_USAGE` of `supervisor/src/storage/schemas.py`) for
  `btrfs balance start -dusage=N -musage=N` (system chunks follow the metadata's filter), or
  `{"full": true}` for `--full-balance`, which skips btrfs's 10-second warning; both at once are
  refused (422). 20% frees the space of mostly empty chunks, which is what a NAS needs against an
  allocation that fills the disk, in minutes; a full balance is rarely needed (after a disk was
  added), so the Balance dialog offers it second, with a warning and a checkbox
  ("Rewrite every chunk of <pool>") that enables Start Balance.
- `supervisor/src/storage/balances.py` runs `btrfs balance start` and `resume` on the host with
  `systemd-run --unit=unos-balance-<pool> --collect --quiet --service-type=exec
  --property=StandardOutput=truncate:/run/unos-balance-<pool>.log --property=StandardError=inherit`
  (`host.run_on_host`): systemd-run returns once btrfs runs, the balance survives a restart of the
  supervisor, and its output in `/run` (RAM) tells how it ended: "Done, had to relocate N out of M
  chunks" (finished), "balance canceled by user" (cancelled), "ERROR: ..." (failed, with that
  line). A log that says paused while no balance is paused, an empty one and none give no result.
  Its status (`btrfs balance status`: running, "pause requested" as pausing, "cancel requested" as
  cancelling, paused, idle), pause and cancel run in the container (`host.run_in_container`,
  ADR-0290); a pause or a cancel waits `STOP_WAIT` (10) seconds for btrfs, which returns once the
  kernel stopped after the chunk it moves, and then answers the status; one with nothing to stop,
  which btrfs-progs refuses with exit status 2 ("Not running" for a pause, "Not in progress" for a
  cancel), answers the status as well, and no audit entry. A start whose output already
  holds an error answers 500 "Balance start failed: <error>"; one that the kernel does not report
  yet answers running, as a scrub (ADR-0367).
- Refused with 409 and nothing run: a pool that the host's mount table does not have mounted as
  btrfs at its mountpoint (every balance request, also the status) or that is read-only (start,
  resume); a start or resume while a scrub of the pool runs; a start while a balance runs ("runs
  already") or is paused ("resume or cancel it"); a resume of a balance that is not paused.
  `pools.scrub_start`, the one start of a scrub (ADR-0311), refuses a scrub, also a scheduled one,
  while btrfs reports a balance of the pool running, pausing or cancelling; a balance status that
  cannot be read refuses no scrub, and one that does not answer within the start's timeout ends the
  start as a hung `btrfs scrub start` would (ADR-0290). `pools.maintenance_lock` holds each start between its checks and
  its start. A paused balance refuses no scrub: its resume waits for the scrub.
- The page follows a balance that runs over `/ws/v1/events` (`{"type": "balances"}`), as scrubs:
  `balances.note`, `current`, `follow`. The estimate (`seconds_left`) is the chunks still expected
  at the rate of those balanced since UNOS first saw the balance run; it starts again when the
  kernel's counts do (a resume). The audit log gets `balance_started` (with the usage limit or
  "every chunk"), `balance_paused`, `balance_resumed` and `balance_cancelled`, only for a change.
- Tests: `supervisor/tests/test_balance.py`, `test_storage.py` and `test_scheduler_validation.py`
  (a scrub refused while a balance runs), `test_ws_auth.py` (`TestTheBalancesOnTheEventsSocket`),
  `webui/tests/storage.spec.ts` (`Balance (fake data)`), `webui/tests/mobile-layout.spec.ts`, and
  `scripts/dev/scenario.sh balance` (`supervisor/tests/test_dev_scenarios.py`), which makes chunks
  about 12% full in a folder of the pool with `fallocate` and deletes, balances them with the
  default limit, checks the unit with `systemctl is-active`, pauses, resumes and cancels.

## Consequences

- A paused balance stays recorded on the disk, and the kernel resumes it when the pool is mounted
  again (also at the next boot): the card says so, and Cancel Balance ends it for good.
- A balance started by hand on the host shows on the card as running or paused and can be paused
  and cancelled; how it ended is known only for those that ran in the unit. After a reboot the log
  is gone, and the card shows no last result.
- The scrub's start runs one more btrfs command first; on a hung filesystem, that command is the one
  that the start's 500 names.
- The host needs systemd (`systemd-run`, in `HOST_TOOLS`), as the network's revert timer does.

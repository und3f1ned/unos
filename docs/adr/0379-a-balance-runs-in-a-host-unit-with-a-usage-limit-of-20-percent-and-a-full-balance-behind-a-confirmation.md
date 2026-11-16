---
status: accepted
date: 2026-11-15
area: storage
decided-by: agent
---

# ADR-0379: A balance runs in a unit of the host, rewrites the chunks less than 20% full by default, and a full one needs a confirmation

## Context

`storage.pool-balance` asks for a balance of a mounted pool that the Storage page follows with its
progress and the time left, and that can be paused, resumed and cancelled. The kernel runs a
balance in the process that asked for it and cancels it, deleting it from the disk, when that
process gets a fatal signal; a restart of the supervisor's container (`pid: host`) kills every
process in it, and a shutdown of the host sends SIGTERM to the processes of every unit it stops.
The kernel pauses a balance whose pause and cancel are both requested, and both requests return 0
(`btrfs_balance()`, Linux 6.1.150 and later, the dev device's 6.1.172 too). A full balance rewrites
every chunk: hours on the dev device's hard disk (`deploy.arm64-bpi-w3`). Every resume, the
kernel's at a mount and `btrfs balance resume`'s, gives each chunk type without a usage or
usage-range filter that is not being converted the filter usage=90 (`update_balance_args()` of
Linux 6.1's `fs/btrfs/volumes.c`, called by `btrfs_balance()` for `BTRFS_BALANCE_RESUME`: "chunks
that we have already balanced should be reasonably full"), so a resumed full balance skips the
chunks at least 90% full; a balance with a usage limit keeps its own. On the dev device
(2026-11-16, 6.1.172) a full balance of the pool data paused by the unit's `ExecStop` at a reboot
was resumed at the next mount as "balance: resume -dusage=90 -musage=90 -susage=90" and ended a
minute later after 4 block groups. Working on through the features under the owner's delegation
("work on through the features, ask no questions; I allow everything", in translation, as
ADR-0340), with "then we continue developing UNOS's features with our test hardware in mind" for
this batch (2026-11-15), the agent decided the following.

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
  --property=RequiresMountsFor=<mountpoint>
  --property=ExecStartPre=truncate --size=0 /run/unos-balance-<pool>.log
  --property=StandardOutput=append:/run/unos-balance-<pool>.log --property=StandardError=inherit
  --property=ExecStop=-btrfs balance pause <mountpoint> --property=TimeoutStopSec=10min`
  (`host.run_on_host`): systemd-run returns once btrfs runs, the balance survives a restart of the
  supervisor, and its output in `/run` (RAM) tells how it ended: "Done, had to relocate N out of M
  chunks" (finished), "balance paused by user" (no result: since then resumed or cancelled; it
  wins over "balance canceled by user" printed with it), "balance canceled by user" (cancelled),
  "ERROR: ..." (failed, with that line). An empty log and none give no result. A shutdown of the
  host stops the unit before it unmounts the pool (`RequiresMountsFor`): `ExecStop` pauses the
  balance, within `TimeoutStopSec` for the chunk being moved, before systemd's SIGTERM could
  cancel it, and the kernel resumes it when the pool is mounted again, as after a power cut (a full
  balance with usage=90, above).
  systemd runs `ExecStop` also after btrfs ended by itself; its pause then finds nothing ("-"
  ignores its failure), and the line it appends, "ERROR: balance pause on '<mountpoint>' failed:
  Not running", is not an error of the balance. Every command of the unit opens the output file,
  so it is appended to, and emptied by `ExecStartPre` before btrfs starts.
  Its status (`btrfs balance status`: running, "pause requested" as pausing, "cancel requested" as
  cancelling, paused, idle), pause and cancel run in the container (`host.run_in_container`,
  ADR-0290); a pause or a cancel waits `STOP_WAIT` (10) seconds for btrfs, which returns once the
  kernel stopped after the chunk it moves, and then answers the status; one with nothing to stop,
  which btrfs-progs refuses with exit status 2 ("Not running" for a pause, "Not in progress" for a
  cancel), answers the status as well, and no audit entry. A pause or a cancel reads the status
  first, under a lock of its own: one asked again while the first waits for its chunk answers the
  status without btrfs or an audit entry, and a cancel after which the status says paused (a pause
  from elsewhere won) answers paused without an audit entry. A start whose output already
  holds an error answers 500 "Balance start failed: <error>"; one that the kernel does not report
  yet answers running, as a scrub (ADR-0367).
- Refused with 409 and nothing run: a pool that the host's mount table does not have mounted as
  btrfs at its mountpoint (every balance request, also the status) or that is read-only (start,
  resume); a start or resume while a scrub of the pool runs; a start while a balance runs ("runs
  already") or is paused ("resume or cancel it"); a resume of a balance that is not paused; a
  cancel while a pause waits for the chunk being moved ("cancel the balance once it is paused"),
  and a pause while a cancel waits, since the kernel would pause the balance and the cancel would
  be audited all the same; a cancel of a paused balance of a read-only pool, which the kernel
  keeps for the next read-write mount (EROFS).
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
  again (also at the next boot): the card says so, and Cancel Balance ends it for good. A restart
  or shutdown of the host pauses a running balance in the same way; it waits up to 10 minutes for
  the chunk being moved, and should systemd end the pause then, its request stays with the kernel.
- A full balance that is paused (Pause Balance, a restart or shutdown of the host) or cut by a
  power loss, and then goes on (Resume Balance, the next mount), rewrites only the chunks less
  than 90% full, the kernel's rule (Context): UNOS keeps it and says so, in the card's note of a
  paused balance, in the full balance's warning and in `docs/USER_GUIDE.md`, with the way to
  rewrite every chunk: cancel it and start a new full balance.
- The card disables Cancel Balance while a pause waits for its chunk and for a paused balance of
  a read-only pool, and Resume Balance beside a scrub, as the server refuses them.
- A balance started by hand on the host shows on the card as running or paused and can be paused
  and cancelled; how it ended is known only for those that ran in the unit. After a reboot the log
  is gone, and the card shows no last result.
- The scrub's start runs one more btrfs command first; on a hung filesystem, that command is the one
  that the start's 500 names.
- The host needs systemd (`systemd-run`, in `HOST_TOOLS`), as the network's revert timer does.

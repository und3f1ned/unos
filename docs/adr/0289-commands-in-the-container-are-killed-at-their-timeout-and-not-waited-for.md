---
status: superseded
date: 2026-11-08
area: storage
decided-by: agent
superseded-by: ADR-0290
---

# ADR-0289: Commands of btrfs and smartctl in the container are killed at their timeout without a wait, and a command still stuck is not started again

## Context

Superseded by ADR-0290: a scheduled snapshot task whose cleanup gets no answer from btrfs fails its run, instead of succeeding with the reason in its output.

Scrubs, the error stats reset, SMART tests, the reads of SMART data and the snapshot commands run btrfs or smartctl in the supervisor's container, on the pools and disks that the privileged container sees. Their timeouts (ADR-0021, ADR-0071, ADR-0172) came from `subprocess.run`, which kills the process and then waits for it: a process stuck in the kernel, on a disk or a filesystem that stopped answering, dies of SIGKILL only when the kernel lets it go, so the worker thread that ran it stayed busy until then, often for good. The `btrfs subvolume show` of the scheduler's snapshot cleanup had no timeout at all. The hourly SMART monitor and the Storage page, which asks for the scrub status every 2 seconds without waiting for the answer before, could use up the threads that the whole API works with (storage.commands-time-out-cleanly).

## Decision

- `run_in_container(cmd, *, timeout, stdout, stderr)` in `supervisor/src/host.py` runs the commands below. It starts the command with `Popen` in a session of its own (`start_new_session`), with no input and its output as text (bytes that are not valid become U+FFFD). At the timeout it kills the command's process group with SIGKILL, so that the processes the command started end with it, as the command that nsenter starts would. It logs a warning and raises `subprocess.TimeoutExpired` at once; a daemon thread (`unos-reap:<pid>`) waits for the end and then closes the pipes. It signals the group only while the command is not waited for yet, so the group id still belongs to it, and no other process.
- One run of a command (the same argument list) at a time: a second run waits for the first, at most its own timeout. While a run that was killed at its timeout has not ended, the command is not started: the run raises TimeoutExpired at once, and "Not started" is logged once for each stuck run. So a pool or disk that hangs keeps at most one stuck process of each command, also when requests for it overlap, and the requests for it are answered at once. When the daemon thread cannot be started, the command is not marked and the error is raised.
- It runs `btrfs scrub start`, `status` and `cancel` (`supervisor/src/storage/pools.py`), the scrub start and the SMART test of scheduled tasks (`supervisor/src/scheduler/router.py`), `btrfs device stats --reset` and `smartctl -t` (`supervisor/src/health/router.py`), the reads of SMART data (`smartctl -j -a` of Disk Health's `parse_smart_data` and of the Storage page's `get_smart_data` in `supervisor/src/storage/disks.py`, and the SMART monitor's `smartctl -n standby -j -a`), and the `btrfs subvolume show` of the scheduler's snapshot cleanup.
- The timeouts and the answers stay as they were, with three exceptions. A SMART test that does not answer, `POST /api/v1/health/disk/{device}/test`, answers 500 "smartctl did not answer within 30 seconds: <device> may not be responding" instead of "Command timed out"; it and Disk Health's read take `SMARTCTL_TIMEOUT` (30 seconds) instead of a literal. The snapshot cleanup gives `btrfs subvolume show` 60 seconds (`SUBVOLUME_SHOW_TIMEOUT`): at the first that does not answer it stops and deletes nothing, and the run's output says "Old snapshots were not cleaned up: btrfs did not answer within 60 seconds. The pool may not be responding."
- The fake hosts of the tests (`install_fake_host`, `install_fake_samba_host`, the network fake) refuse `run_in_container`, as they refuse `subprocess.run`.
- Tests: `supervisor/tests/test_command_timeouts.py`. With real processes: the status and the output, a session of its own, a timeout that ends the command and the sleep it started, and leaves other processes alone. With fake processes whose wait never returns: the timeout raised at once, the group killed, the wait left to a daemon thread, a stuck command not started again and logged once, a second run that waits for the first and starts nothing when it hangs, and a command that a failed thread start leaves unmarked. Through the API, with one worker thread of each kind: every endpoint answers the timeout, then serves the next request at once, and answers again at once while the command is stuck; the scheduled scrub and SMART test fail with their message and free the scheduler's thread, the snapshot cleanup stops and keeps the snapshots, and the SMART monitor records the other disks.

## Consequences

- A command stuck in the kernel no longer holds a worker thread: the API's and the scheduler's threads are free when the timeout is answered. The stuck process stays until the kernel lets it go, with a daemon thread that waits for it; a reboot ends both.
- Overlapping requests for the same command each hold a worker thread until the first run's timeout, as they did before; then they are answered at once. The Storage page, which asks for the scrub status every 2 seconds without waiting for the answer before, can so hold the default executor's threads for one timeout; waiting for each answer there is open.
- A request for a pool or disk whose command is still stuck answers the timeout at once, also once the disk answers again, until the stuck process ended; the log says "Not started" once for each stuck run.
- A `btrfs scrub start` that does not answer within its timeout takes the scrub process that it may have started with it, as the answer says that the start did not answer; a start that answered leaves its scrub running, in the session of the start.
- The commands of `run_on_host` keep their timeout: it ends nsenter, which frees the thread, while the command runs on on the host (ADR-0258); ending their process groups, and not starting them again while they are stuck, belongs to architecture.host-command-layer.
- The commands of the Snapshots page (`run_cmd` of `supervisor/src/snapshots/router.py`, 600 seconds, also the scheduled snapshot itself) still run through `subprocess.run`. They name the folders they act on by descriptor numbers (ADR-0242), which this mark per argument list cannot tell apart, and their list runs one `btrfs subvolume show` per snapshot, which would have to stop at the first that does not answer; that is open.

## Considered options

- Killing only the command, not its group: a command run through nsenter, which forks for `--pid`, would run on; the group holds only what the command started, in its own session.
- No limit on stuck runs: every scrub status request of the Storage page would leave one more process stuck in the kernel.
- Failing at once while a run of the command is in progress, instead of waiting for it: two tabs that ask for a healthy pool's scrub status at the same moment would get an error, and the page stops following the scrub at an error.
- One mark per pool or disk instead of per command: every caller would have to name its target; per command the bound is already the few commands of a pool or a disk.

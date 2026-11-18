---
status: accepted
date: 2026-11-18
area: system
decided-by: agent
---

# ADR-0412: The load check fails for a UNOS container it could not measure, and the two-tab measurement is the owner's

## Context

ADR-0374 and ADR-0375 made the stats sample shared, took the healthcheck off Python, left Docker's
data root out of the walk for upload leftovers, and added the CPU and temperature lines to
`scripts/dev/check.sh`. Going through the entry `system.idle-load-budget` step by step, steps 1 to
3 had tests that assert them, except that no test showed the sample of `/ws/v1/events` taken in a
worker thread for several sockets, and step 4's check had holes: a UNOS container that ran but
had no reading from `docker stats` (the command failed, or showed `--`) was skipped, so the
check said OK without having measured it; a thermal zone whose temperature could not be read
broke the arithmetic of the script; and no test showed the average of the five readings, the
boundaries of the 15% budget and of the 5 C margin, the budget for unos-webui, or that the reading
writes nothing.

## Decision

- A running `unos-supervisor` or `unos-webui` without a CPU reading is a problem of the check
  ("has no CPU reading from docker stats, so its budget ... is unchecked"). A container that does
  not run is reported by the check of the containers already.
- A thermal zone whose temperature or trip point cannot be read or is not a number is shown as
  "temperature unreadable" or its trip is ignored; the check goes on and the other zones are
  still checked.
- The reading is `/proc`, `/sys` and `docker stats --no-stream` only (ADR-0214): no file is
  written. `test_check_sh_reads_load_and_heat_without_writing_anything` of
  `supervisor/tests/test_dev_scripts.py` is a guard on the text of that section (no tool that
  makes a file, every unquoted redirection to `/dev/null`); review keeps the rest.
- The budget is compared with the average as printed, to one decimal.
- Tests added: the average of five readings, the boundaries (15.0% passes, 15.1% fails; 5 C
  below the trip fails, 6 passes), both UNOS containers, the lowest passive trip among other
  trips, and, in `supervisor/tests/test_ws_auth.py`, two sockets that get one sample taken off the
  event loop.
- Step 5 of the entry, `unos-supervisor` below 15% of a core on average with a dashboard open in two
  real tabs on the BPI-F3, is the owner's: it needs a real sign-in and two browsers on the board,
  which agents do not have (ADR-0024). The entry's notes give the steps; `passes` stays false
  until the owner reports the figure.

## Consequences

- A `docker stats` that fails on the device now turns the check red instead of passing the budget
  unmeasured.
- The budget and the margin stay as ADR-0374 set them.

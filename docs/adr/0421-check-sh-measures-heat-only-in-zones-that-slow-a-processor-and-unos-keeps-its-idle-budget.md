---
status: accepted
date: 2026-11-19
area: system
decided-by: agent
supersedes: [ADR-0374]
---

# ADR-0421: The device check measures heat only in zones that slow a processor, and UNOS keeps its idle budget

## Context

ADR-0374 made `scripts/dev/check.sh` a problem of every thermal zone within 5 C of its first
passive trip. On the owner's BPI-F3 (SpacemiT K1, kernel 6.18.45-current-spacemit) that warned of
top_thermal and gpu_thermal at 40 to 64 C, whose first passive trip is 40 C: top_thermal binds
only its fan (pwm-fan) to its trips, and gpu_thermal and cluster1_thermal bind nothing, so under
their governor, step_wise, the kernel slows no processor there. cluster0_thermal binds the CPU's
clock (cpufreq-cpu0) from 75 C. On the BPI-W3 soc-thermal binds the CPU's and the GPU's clocks
at 85 C behind a first passive trip of 75 C. The owner asked to fix the false warning ("Yes, fix
it").

## Decision

- A thermal zone's passive trips count only when the zone binds a cooling device other than a
  fan to one of its trips (a `cdevN` link of the zone whose `type` does not contain `fan`, such as
  cpufreq and devfreq; a type that cannot be read counts too), or when its governor is
  `user_space`, whose daemon acts on the trips itself. Such a zone keeps every passive trip, its
  lowest one being the margin's, as before. A zone that binds only fans, or nothing, is shown as
  `<zone>: <t> C, no trip that slows a processor` and is no problem at its passive trips.
- Every zone, counted or not, is a problem within 5 C of its critical trip, where the kernel
  powers the board off.
- The rest of ADR-0374 stays: one stats sample serves every socket of `/ws/v1/events` until it
  is `STATS_INTERVAL_SECONDS` old, taken in a worker thread, with the list of the host's
  processes at most every `PROCESS_LIST_INTERVAL_SECONDS` (10 s); the check for leftovers of
  uploads leaves Docker's data root out; check.sh shows each running container's CPU averaged
  over five readings of `docker stats`, the load average and each zone with its first passive
  trip, and a container of UNOS above 15% of a core on average or a counted zone within 5 C of
  its first passive trip is a problem: the check exits 1.
- Tests: the check.sh tests of `supervisor/tests/test_dev_scripts.py` (their zones bind the
  CPU's clock as real boards do, and the BPI-F3's zones are a case of their own),
  `test_ws_stats.py` and `test_upload_leftovers.py`.

## Consequences

- On the BPI-F3 the check warns of cluster0_thermal from 70 C and of the other zones from 110 C;
  on the BPI-W3 soc-thermal stays as it was.
- A zone that binds only fans or nothing, such as the BPI-F3's cluster1_thermal (passive 75 and
  85 C, critical 115 C), is watched against its critical trip alone: the check warns 5 C before the
  kernel's power-off, not 5 C before a slowdown that does not come. A zone of an RK3588 with a
  critical trip alone now has that margin too.
- The budget of 15% of a core and the margin of 5 C stay the numbers of this decision.
- Step 4 of `system.idle-load-budget` says so: a thermal zone that slows a processor within 5 C
  of its first passive trip.

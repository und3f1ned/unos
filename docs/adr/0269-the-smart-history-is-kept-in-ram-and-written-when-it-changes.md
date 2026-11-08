---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
---

# ADR-0269: The SMART monitor keeps its history in RAM and writes it only when a pass changes it, and at shutdown

## Context

Every hour the SMART monitor adds a snapshot of each awake disk to its history and rewrote the whole of `/data/smart_history.json` after every pass, up to 720 snapshots of each disk, about 0.3 MB per disk, on the system disk, also when no counter had moved (storage.smart-trend-alerts). ADR-0214 allows writes on real changes of state only, and nothing written periodically.

## Decision

- The monitor (`_smart_monitor_pass` in `supervisor/src/health/router.py`) reads `SMART_HISTORY_FILE` at its first pass after a start and from then on keeps the history in RAM (`_SmartHistoryState`), at most `SMART_HISTORY_LIMIT` (720) snapshots of each disk. The SMART endpoints (`/health/smart/history/{serial}`, `/smart/trends`, `/smart/predictions`, `/smart-monitor/status`) read the history in RAM, and the file only before that first pass.
- A pass writes the file, atomically as before (ADR-0235), only when one of its snapshots changes the history: the first snapshot of a disk, a counter of `CRITICAL_ATTRIBUTES` whose value differs from the disk's latest earlier snapshot that holds it, or another path or lsblk serial than the disk's previous snapshot (`_changes_the_history`). The time, the temperature and the power-on hours change at every pass and are no change; a snapshot without the counters tells nothing of them (ADR-0270). The disks without a serial, which share the history "unknown", are compared with the snapshots taken at their own path.
- A write that fails is logged, and the next pass writes again; the pass's alerts are sent all the same.
- At shutdown, `stop_health_monitors` (the lifespan, ADR-0043) writes the history when RAM holds snapshots that the file does not (`save_unsaved_smart_history`). A pass that ends after that write, one that was still waiting for smartctl, keeps nothing: its alerts could no longer be sent, and the next start compares with the history written at shutdown, so that a rise of that pass is alerted then.
- Tests: `supervisor/tests/test_health_monitor.py` (`TestSmartHistoryWrites`), `supervisor/tests/test_smart_predictions.py`.

## Consequences

- A pass that changes nothing writes nothing, and neither does a start that changes nothing (ADR-0214). A rise is on the disk at once, so the counters an alert is measured against survive a power loss.
- A power loss or a crash loses the snapshots since the last write: copies of unchanged counters, with their temperatures and power-on hours. The predictions (ADR-0235) then count fewer snapshots: a disk may need up to a day of passes to be predicted again, and a rise stays longer within the last 24 snapshots, so that a replace-soon mark lasts longer, not shorter. A clean stop or a reboot loses at most the pass that runs at that moment, whose rise is alerted at the next start. An alert that is still being sent when the supervisor stops is cut off, and its rise, already written, is not alerted again: the monitor writes before it sends, as it did before this decision.
- ADR-0235's rules for a file that cannot be read apply when the file is read: at the monitor's first pass after a start, and by the endpoints before that pass. A file damaged while the supervisor runs is not read again: the next write replaces it with the history in RAM.
- The history takes RAM: well under 1 MB per disk at 720 snapshots.

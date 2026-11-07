---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
---

# ADR-0248: GET /health/smart/trends reports every tracked disk, and trends read each counter from the snapshots that hold it

## Context

`GET /api/v1/health/smart/trends` listed only the counters that rose, so a disk with steady counters could not be told from one that was not tracked (storage.smart-trend-alerts). The trend and the monitor's alerts read a counter missing from a snapshot (smartctl could not read the attribute table) as 0, so the next snapshot looked like a rise and sent an alert for counters that had not moved. The disks that smartctl reports without a serial share one history, "unknown", whose counters mixed several disks into rises that no disk had.

## Decision

- `GET /api/v1/health/smart/trends` answers `{"disks": [...], "critical_count", "warning_count"}`: one entry per disk in the SMART history, in its order, removed disks included, with `serial` (smartctl's, the history's key), `device` (the path of its latest snapshot), `data_points` (its snapshots) and `alerts`, the counters that rose (`SmartTrendAlert`), empty for a steady disk. The fields `disks_tracked` and the flat `alerts` list are gone (ADR-0207); `GET /smart-monitor/status` still reports `disks_tracked`, the number of histories, one for all the disks without a serial.
- A counter's trend compares its latest value with the first of its last 24 values (a day of hourly snapshots), or with its oldest when it has fewer, counting only the snapshots that hold it (`_counter_values` and `analyze_smart_trends` in `supervisor/src/health/router.py`), as the predictions read counters (ADR-0235); a counter held by fewer than two snapshots has no trend.
- The monitor alerts when its newest snapshot holds a counter above the disk's latest earlier snapshot that holds it (`_new_trend_alerts`, `_latest_counters`), once per rise. The alert reports the counter's rise over its last day, as the trend does; when the trend shows none, because the counter fell below its value of a day ago and rose again (pending sectors that were rewritten), the rise since that earlier value.
- In the history "unknown", the snapshots taken at one path are one disk (`_snapshots_of_one_disk`): its trend, its alerts and its entry in `/smart/trends` are computed per path. A serial that smartctl reports empty or blank counts as none and goes to "unknown"; any other is kept as smartctl prints it, as Disk Health shows it. The history still gets no prediction (ADR-0235).
- Tests: `supervisor/tests/test_health_monitor.py` (`TestSmartTrendAlerts`, `TestSmartTrendsEndpoint`, `TestSmartHistoryWrites`).

## Consequences

- A failed read of the attribute table neither ends a trend nor sends an alert after it, and a new rise after a drop is alerted.
- Two disks without a serial that swap their paths (a reboot that names them the other way round) are still mixed in "unknown" until each has a day of snapshots at its new path.
- A history that an older monitor stored under an empty serial is not continued: such a disk starts a new history under "unknown" (ADR-0207).
- No page reads `/smart/trends`; the alerts by e-mail and Telegram and the replace-soon marks are what users see.

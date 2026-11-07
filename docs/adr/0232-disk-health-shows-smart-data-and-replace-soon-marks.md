---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
---

# ADR-0232: Disk Health opens each disk's SMART data, and disks whose SMART trend scores 50 are marked replace soon

## Context

Disk Health listed each disk's model, temperature and health, but none of the SMART attributes that `GET /api/v1/health/disks` returns (storage.smart). The SMART monitor keeps the critical attributes of every awake disk per serial in `/data/smart_history.json`, and `GET /api/v1/health/smart/predictions` scored that history, but no page read the scores and no ADR fixed how they are computed (storage.smart-replace-soon). The score's rate was taken over the whole history, so a month of quiet history hid a fast rise, and the history keeps the serials of removed and replaced disks, whose predictions never changed.

## Decision

- Disk Health (`webui/src/routes/system/tabs/HealthTab.svelte`) opens each disk in a dialog with the data that `GET /health/disks` returned, so opening a disk runs no smartctl: overall health, temperature, power-on hours, power cycles, serial, firmware, capacity, the last self-test and the ATA attributes with their status (Failing at or below the attribute's threshold, Warning within 10 of it). NVMe disks have no ATA attributes: `SmartInfo.nvme_health` carries the numbers of their SMART/Health Information log in smartctl's order, and the dialog lists them. A disk without SMART data says so.
- The score (`predict_disk_failure` in `supervisor/src/health/router.py`) keeps the endpoint's rules, except that the rate is taken over the last day. A disk needs 24 snapshots, a day of hourly passes. Over its history, at most 720 snapshots (30 days), each of Reallocated_Sector_Ct (5), Current_Pending_Sector (197) and Offline_Uncorrectable (198) that rose adds 30, and 20 more when it rose by more than 0.1 per snapshot over the last 24 snapshots; each of Reported_Uncorrect (187), Command_Timeout (188) and UDMA_CRC_Error_Count (199) that rose above 100 adds 10. 50 or more (`REPLACE_SOON_SCORE`) is replace_soon, 30 monitor_closely, 10 watch, less healthy. Each addition to the score adds a risk factor, the reason shown. A disk whose history holds none of these attributes (NVMe disks: the monitor records ATA attributes only) gets no prediction, and neither do the disks that smartctl reports without a serial, which the monitor records together under "unknown".
- Predictions cover the disks connected now, the disks of Disk Health (ADR-0072). The monitor stores lsblk's serial with each snapshot (`lsblk_serial`, which can differ from smartctl's behind a USB bridge). A history whose latest snapshot holds one belongs to the disk that lsblk lists with that serial now, at its path now, and to no disk when none does. A history without one, or with one that several disks report (identical USB bridges), belongs to the disk now at the path of its latest snapshot, unless another history claims that disk by its lsblk serial. When two histories claim one disk, the later latest snapshot wins. The endpoint lists the disks with lsblk on the host in a worker thread, reads the history and runs no smartctl; it answers 500 when the host's disks cannot be listed, like the other SMART endpoints.
- A disk is marked replace soon while its prediction says replace_soon, and nothing about the mark is stored. Disk Health marks the disk whose serial (smartctl's, in the list and in the history) is the prediction's, with the risk factors and the recommendation. The dashboard shows administrators an alert that lists each such disk with its path, serial and risk factors, and links to Disk Health. Both pages read the predictions at every load and refresh, so a mark goes with the first load after its status changed. When the predictions cannot be read, both pages say so instead of showing no mark.
- Tests: `supervisor/tests/test_smart_predictions.py` (the score, its threshold, the window and the disks covered), `supervisor/tests/test_health_smart.py` (the attributes and the NVMe log of the dialog), `webui/tests/health.spec.ts`, `webui/tests/dashboard.spec.ts` (on the route fakes of `webui/tests/health-fakes.ts`, which give only answers the server can give) and `webui/tests/roles.spec.ts` (no prediction request for regular users).

## Consequences

- The score is crude. It follows rises, not levels: a disk that arrives with many reallocated sectors gets no mark. A mark lasts while a sector count rose by 3 or more within the last day, or while two of them rose within the kept history; a single rise leaves the mark a day after it, as monitor closely. A better model needs an ADR of its own.
- NVMe disks are never marked: their failing state shows only as FAILED health (smartctl reads the controller's critical warnings) and in the numbers of their health log, without a status.
- A disk that sleeps is not recorded (`smartctl -n standby`) and keeps the prediction of its last snapshots. Disk Health itself still wakes it, because `GET /health/disks` runs smartctl without `-n standby` (storage.disk-spindown).
- Snapshots taken before `lsblk_serial` was stored, and disks behind a bridge that reports no serial or the same serial as another, are placed by path: until the monitor records the disk that took over such a path, the dashboard lists the old serial there, while Disk Health, which matches by serial, marks nothing. The latest snapshots compare as local-time text, so after the clock went back (a board without a real-time clock before NTP, the end of daylight saving time) an older history can win such a tie for a while.
- Each load of Disk Health lists the host's disks twice, once for each request: lsblk reads sysfs and writes nothing.
- The web UI is tested on route fakes, as the stand-in has no disks; the backend tests pin the texts that the fakes copy.
- No mark can be dismissed. The monitor's SMART trend alerts by e-mail and Telegram stay as they are (storage.smart-trend-alerts).

"""Failure predictions from the SMART history, and the disks that get one (ADR-0235).

GET /health/smart/predictions scores the SMART history that the hourly
monitor stores per serial (storage.smart-trend-alerts). A disk needs at
least 24 snapshots; each of the sector counts 5, 197 and 198 that rose adds
30, and 20 more when it rose by more than 0.1 per snapshot over the last 24
snapshots; each of 187, 188 and 199 that rose above 100 adds 10. 50 or more
is replace_soon: the mark of Disk Health and the dashboard
(storage.smart-replace-soon), with the risk factors as its reason. A
counter is read from the snapshots that hold it. The history keeps the
serials of removed and replaced disks, so only the disks connected now get
a prediction: by the serial lsblk reported with the latest snapshot when
that serial names one connected disk and one history, or else by its path.
The endpoint reads the history only: smartctl never runs, and no disk is
woken. The history file is replaced atomically, and one that cannot be read
is an error, not an empty history.
"""

import importlib
import json
import subprocess
from datetime import datetime, timedelta

import pytest

from .test_health_smart import LSBLK_FAILS, NO_DISKS, _on_event_loop
from .test_pool_persistence import admin_client, install_fake_host

health_router = importlib.import_module("src.health.router")

START = datetime(2026, 11, 1)
REALLOCATED, PENDING, UNCORRECTABLE, CRC_ERRORS = 5, 197, 198, 199
# 3 new reallocated sectors in the last 24 snapshots: 0.125 a snapshot.
FAILING = {REALLOCATED: [0] * 21 + [3] * 3}
STEADY = {REALLOCATED: [0] * 24}
REPLACE_SOON = {
    "serial": "WD-1", "device": "/dev/sda", "risk_score": 50, "status": "replace_soon",
    "risk_factors": ["Reallocated_Sector_Ct: 3 (was 0)", "Reallocated_Sector_Ct increasing rapidly"],
    "recommendation": "Disk showing signs of failure. Replace within 30 days.",
    "data_points": 24,
}
SAME = object()  # lsblk reports smartctl's serial, as for SATA and NVMe disks


def snapshots(attributes, *, serial="WD-1", device="/dev/sda", lsblk_serial=SAME, start=START,
              count=None):
    """Hourly history entries of one disk, as the SMART monitor stores them (JSON keys).

    A value None leaves the attribute out of that snapshot, as when smartctl
    could not read the attribute table (the monitor then stores none).
    """
    if count is None:
        count = len(next(iter(attributes.values())))
    return [
        {
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "device": device,
            "serial": serial,
            "lsblk_serial": serial if lsblk_serial is SAME else lsblk_serial,
            "attributes": {
                str(attr): values[i] for attr, values in attributes.items() if values[i] is not None
            },
            "temperature": 35,
            "power_on_hours": 1000 + i,
        }
        for i in range(count)
    ]


@pytest.fixture
def disks(monkeypatch, tmp_path):
    """The host's whole disks sda and nvme0n1 (lsblk on the host), and a SMART history file.

    lsblk reports the serials WD-1 and S64ANS0T. FakeHost also has the boot
    SD card mmcblk1 and zram0, which are no disks of Disk Health.
    ``disks.write(history)`` stores a history; ``disks.lsblk_on_loop``
    records whether each lsblk ran on the event loop. install_fake_host
    refuses every other subprocess, smartctl included.
    """
    fake = install_fake_host(monkeypatch)
    fake.add_disk("sda", serial="WD-1")
    fake.add_disk("nvme0n1", rota=False, serial="S64ANS0T")
    history_file = tmp_path / "smart_history.json"
    monkeypatch.setattr(health_router, "SMART_HISTORY_FILE", str(history_file))
    fake.history_file = history_file
    fake.write = lambda history: history_file.write_text(json.dumps(history))
    fake.lsblk_on_loop = []
    host_run = fake.run

    def run_on_host(cmd, **kwargs):
        if cmd[0] == "lsblk":
            fake.lsblk_on_loop.append(_on_event_loop())
        return host_run(cmd, **kwargs)

    monkeypatch.setattr("src.host.run_on_host", run_on_host)
    return fake


@pytest.fixture
def api(disks):
    with admin_client() as client:
        yield client


def predictions(api) -> list[dict]:
    response = api.get("/api/v1/health/smart/predictions")
    assert response.status_code == 200, response.text
    return response.json()["predictions"]


def placed(api) -> list[tuple[str, str]]:
    """(serial, device) of each prediction."""
    return [(p["serial"], p["device"]) for p in predictions(api)]


def smartctl_reports(reallocated: int):
    """host.run_in_container for the SMART monitor: sda reports ``reallocated`` sectors, nvme0n1 sleeps."""

    def run(cmd, **kwargs):
        assert cmd[:-1] == ["smartctl", "-n", "standby", "-j", "-a"], cmd
        if cmd[-1] != "/dev/sda":  # asleep: exit status bit 1, no data
            return subprocess.CompletedProcess(cmd, 2, "", "")
        output = {
            "serial_number": "WD-1",
            "smart_status": {"passed": True},
            "ata_smart_attributes": {"table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": reallocated}},
            ]},
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(output), "")

    return run


# ---------------------------------------------------------------------------
# The score and its threshold
# ---------------------------------------------------------------------------


def test_a_fast_rise_of_reallocated_sectors_is_replace_soon_with_its_reasons(api, disks):
    disks.write({"WD-1": snapshots(FAILING)})

    assert predictions(api) == [REPLACE_SOON]


@pytest.mark.parametrize(
    "attributes,score,status",
    [
        # The threshold: 30 for the rise and 20 for its rate (more than 0.1 a snapshot).
        (FAILING, 50, "replace_soon"),
        # 2 in 24 snapshots is 0.083 a snapshot: a rise, not a fast one.
        ({REALLOCATED: [0] * 22 + [2] * 2}, 30, "monitor_closely"),
        # The rate counts the last 24 snapshots, whatever the length of the history.
        ({REALLOCATED: [0] * 27 + [3] * 3}, 50, "replace_soon"),
        ({REALLOCATED: [0] * 700 + [60] * 20}, 50, "replace_soon"),
        # A rise more than a day old is no longer fast.
        ({REALLOCATED: [0] * 10 + [3] * 40}, 30, "monitor_closely"),
        # Two sector counts that rose, however slowly.
        ({REALLOCATED: [0] * 99 + [1], PENDING: [0] * 99 + [1]}, 60, "replace_soon"),
        ({PENDING: [0] * 99 + [2], UNCORRECTABLE: [0] * 99 + [2]}, 60, "replace_soon"),
        # One slow rise and CRC errors above 100: 40, below the mark.
        ({REALLOCATED: [0] * 99 + [1], CRC_ERRORS: [90] * 99 + [150]}, 40, "monitor_closely"),
        ({CRC_ERRORS: [90] * 99 + [150]}, 10, "watch"),
        # CRC errors that rose but stay at 100 or less count nothing.
        ({CRC_ERRORS: [0] * 99 + [100]}, 0, "healthy"),
        # High counts that do not rise: the score follows the trend, not the level.
        ({REALLOCATED: [40] * 24}, 0, "healthy"),
        # A snapshot without the counters (smartctl could not read the attribute
        # table) tells nothing of them: it is no drop to 0 ...
        ({REALLOCATED: [0] * 21 + [3] * 3 + [None]}, 50, "replace_soon"),
        # ... and no rise from 0, here for as long as it is the oldest kept ...
        ({REALLOCATED: [None] + [8] * 99, PENDING: [None] + [1] * 99}, 0, "healthy"),
        # ... nor a fast one: the rate counts the last 24 snapshots that hold the counter.
        ({REALLOCATED: [0] * 70 + [3] * 6 + [None] + [3] * 23}, 30, "monitor_closely"),
    ],
)
def test_the_score_and_its_threshold(api, disks, attributes, score, status):
    disks.write({"WD-1": snapshots(attributes)})

    [prediction] = predictions(api)

    assert (prediction["risk_score"], prediction["status"]) == (score, status)


def test_a_disk_needs_a_day_of_snapshots(api, disks):
    disks.write({"WD-1": snapshots({REALLOCATED: [0] * 20 + [40] * 3})})

    assert predictions(api) == []


def test_a_disk_without_the_counters_gets_no_prediction(api, disks):
    # NVMe disks: the monitor records the ATA attributes only, so their
    # history holds none, and nothing can be said of their trend.
    disks.write({"S64ANS0T": snapshots({}, serial="S64ANS0T", device="/dev/nvme0n1", count=48)})

    assert predictions(api) == []


def test_the_highest_score_comes_first(api, disks):
    disks.write({
        "S64ANS0T": snapshots(STEADY, serial="S64ANS0T", device="/dev/nvme0n1"),
        "WD-1": snapshots(FAILING),
    })

    assert [(p["serial"], p["status"], p["risk_factors"], p["recommendation"])
            for p in predictions(api)] == [
        ("WD-1", "replace_soon", REPLACE_SOON["risk_factors"], REPLACE_SOON["recommendation"]),
        ("S64ANS0T", "healthy", [], "No concerning trends detected."),
    ]


def test_the_mark_goes_a_day_after_the_last_rise(api, disks, monkeypatch):
    disks.write({"WD-1": snapshots(FAILING)})
    assert predictions(api)[0]["status"] == "replace_soon"

    # Hourly passes of the monitor, and the disk reallocates no further sector.
    monkeypatch.setattr("src.host.run_in_container", smartctl_reports(3))
    for _ in range(20):
        assert health_router._smart_monitor_pass() == []
    assert predictions(api)[0]["status"] == "replace_soon"

    # The 21st pass: the rise (at the 22nd snapshot) left the last 24 snapshots.
    assert health_router._smart_monitor_pass() == []

    [prediction] = predictions(api)
    assert (prediction["data_points"], prediction["risk_score"], prediction["status"]) == (
        45, 30, "monitor_closely")
    assert prediction["risk_factors"] == ["Reallocated_Sector_Ct: 3 (was 0)"]
    assert prediction["recommendation"] == "Disk health degrading. Monitor daily and prepare replacement."


def test_a_rise_counts_until_it_leaves_the_kept_history(api, disks, monkeypatch):
    # save_smart_history keeps the last 720 snapshots, 30 days of hourly passes.
    disks.write({"WD-1": snapshots({REALLOCATED: [0] + [3] * 719})})
    assert predictions(api)[0]["status"] == "monitor_closely"

    monkeypatch.setattr("src.host.run_in_container", smartctl_reports(3))
    assert health_router._smart_monitor_pass() == []

    [prediction] = predictions(api)
    assert (prediction["data_points"], prediction["risk_score"], prediction["status"]) == (
        720, 0, "healthy")


def test_the_monitor_records_the_serial_lsblk_reports(disks, monkeypatch):
    monkeypatch.setattr("src.host.run_in_container", smartctl_reports(0))

    health_router._smart_monitor_pass()

    [entry] = health_router.load_smart_history()["WD-1"]
    assert (entry["device"], entry["serial"], entry["lsblk_serial"]) == ("/dev/sda", "WD-1", "WD-1")


# ---------------------------------------------------------------------------
# The disks that get a prediction: those connected now
# ---------------------------------------------------------------------------


def test_removed_and_replaced_disks_get_no_prediction(api, disks):
    disks.write({
        # Replaced in place: lsblk reports WD-1 at /dev/sda now.
        "OLD-1": snapshots(FAILING, serial="OLD-1", start=START - timedelta(days=3)),
        "WD-1": snapshots(FAILING),
        # Removed: lsblk lists no such disk any more.
        "GONE-1": snapshots(FAILING, serial="GONE-1", device="/dev/sdb"),
        "S64ANS0T": snapshots(STEADY, serial="S64ANS0T", device="/dev/nvme0n1"),
    })

    assert placed(api) == [("WD-1", "/dev/sda"), ("S64ANS0T", "/dev/nvme0n1")]


def test_a_replaced_disk_loses_its_mark_before_its_successor_is_recorded(api, disks):
    # The disk now at /dev/sda (lsblk: WD-1) sleeps, so the monitor has not
    # recorded it yet; the last snapshot at /dev/sda is the old disk's.
    disks.write({"OLD-1": snapshots(FAILING, serial="OLD-1", start=START + timedelta(days=1))})

    assert predictions(api) == []


def test_a_disk_on_another_path_after_a_reboot_shows_where_it_is_now(api, disks):
    # The kernel named the disks the other way round, and the monitor has not
    # recorded them since: each serial's latest snapshot is at the other's path.
    disks.write({
        "WD-1": snapshots(FAILING, device="/dev/nvme0n1"),
        "S64ANS0T": snapshots(STEADY, serial="S64ANS0T", device="/dev/sda"),
    })

    assert placed(api) == [("WD-1", "/dev/sda"), ("S64ANS0T", "/dev/nvme0n1")]


def test_a_disk_that_sleeps_keeps_its_prediction(api, disks):
    # smartctl -n standby records nothing while a disk sleeps: its last
    # snapshot can be days old.
    disks.write({
        "WD-1": snapshots(FAILING, start=START - timedelta(days=10)),
        "S64ANS0T": snapshots(STEADY, serial="S64ANS0T", device="/dev/nvme0n1"),
    })

    assert predictions(api)[0] == REPLACE_SOON


def test_a_disk_behind_a_usb_bridge_is_found_by_the_serial_lsblk_reports(api, disks):
    # lsblk reports the bridge's serial, smartctl the disk's own.
    disks.add_disk("sdc", serial="BRIDGE-1")
    disks.write({"WD-USB-1": snapshots(FAILING, serial="WD-USB-1", device="/dev/sdb",
                                       lsblk_serial="BRIDGE-1")})

    assert placed(api) == [("WD-USB-1", "/dev/sdc")]


def test_a_disk_without_an_lsblk_serial_is_found_at_its_path(api, disks):
    # A USB bridge that reports no serial: the snapshot's path is all there is.
    disks.add_disk("sdc", serial=None)
    disks.write({"WD-USB-2": snapshots(FAILING, serial="WD-USB-2", device="/dev/sdc",
                                       lsblk_serial=None)})

    assert placed(api) == [("WD-USB-2", "/dev/sdc")]


def test_a_disk_found_by_its_lsblk_serial_wins_over_a_path(api, disks):
    # The bridge without a serial was recorded at /dev/sda later, but lsblk
    # says that /dev/sda is WD-1 now.
    disks.write({
        "WD-USB-2": snapshots(FAILING, serial="WD-USB-2", lsblk_serial=None,
                              start=START + timedelta(days=1)),
        "WD-1": snapshots(STEADY),
    })

    assert placed(api) == [("WD-1", "/dev/sda")]


def test_disks_behind_identical_bridges_are_found_at_their_paths(api, disks):
    # Two enclosures whose bridges report the same serial: it names neither disk.
    disks.add_disk("sdc", serial="0123456789ABCDEF")
    disks.add_disk("sdd", serial="0123456789ABCDEF")
    disks.write({
        "WD-A": snapshots(FAILING, serial="WD-A", device="/dev/sdc", lsblk_serial="0123456789ABCDEF"),
        "WD-B": snapshots(STEADY, serial="WD-B", device="/dev/sdd", lsblk_serial="0123456789ABCDEF"),
    })

    assert placed(api) == [("WD-A", "/dev/sdc"), ("WD-B", "/dev/sdd")]


BRIDGE = "0123456789ABCDEF"


@pytest.mark.parametrize("connected", ["sdc", "sdd"])
@pytest.mark.parametrize("recorded_later", ["WD-A", "WD-B"])
def test_disks_behind_identical_bridges_after_one_enclosure_is_unplugged(
    api, disks, connected, recorded_later
):
    # lsblk now reports the bridges' serial for one disk only, and both histories
    # hold it: it names neither disk, whichever was recorded later in the pass.
    disks.add_disk(connected, serial=BRIDGE)
    later = timedelta(seconds=5)
    disks.write({
        "WD-A": snapshots(FAILING, serial="WD-A", device="/dev/sdc", lsblk_serial=BRIDGE,
                          start=START + (later if recorded_later == "WD-A" else timedelta())),
        "WD-B": snapshots(STEADY, serial="WD-B", device="/dev/sdd", lsblk_serial=BRIDGE,
                          start=START + (later if recorded_later == "WD-B" else timedelta())),
    })

    assert placed(api) == {"sdc": [("WD-A", "/dev/sdc")], "sdd": [("WD-B", "/dev/sdd")]}[connected]


def test_a_disk_behind_an_ambiguous_bridge_serial_needs_that_serial_at_its_path(api, disks):
    # After a reboot the enclosure still connected is at /dev/sdd, and a disk
    # the monitor has not recorded yet took /dev/sdc, where WD-A was recorded.
    disks.add_disk("sdc", serial="WD-9")
    disks.add_disk("sdd", serial=BRIDGE)
    disks.write({
        "WD-A": snapshots(FAILING, serial="WD-A", device="/dev/sdc", lsblk_serial=BRIDGE),
        "WD-B": snapshots(FAILING, serial="WD-B", device="/dev/sdd", lsblk_serial=BRIDGE),
    })

    assert placed(api) == [("WD-B", "/dev/sdd")]


def test_a_disk_replaced_inside_its_usb_enclosure(api, disks):
    # The enclosure's bridge reports its own serial for whichever disk it holds.
    disks.add_disk("sdc", serial="BRIDGE-1")
    old = snapshots(FAILING, serial="WD-OLD", device="/dev/sdc", lsblk_serial="BRIDGE-1")
    disks.write({"WD-OLD": old})
    # Until the monitor has recorded the new disk nothing tells the two apart:
    # the old disk's mark stays (ADR-0235).
    assert placed(api) == [("WD-OLD", "/dev/sdc")]

    # The new disk's first snapshot: both histories hold the bridge's serial at
    # that path, and the later one wins, with too few snapshots for a prediction.
    new = snapshots(STEADY, serial="WD-NEW", device="/dev/sdc", lsblk_serial="BRIDGE-1",
                    start=START + timedelta(days=2))
    disks.write({"WD-OLD": old, "WD-NEW": new[:1]})
    assert placed(api) == []

    disks.write({"WD-OLD": old, "WD-NEW": new})
    assert placed(api) == [("WD-NEW", "/dev/sdc")]


@pytest.mark.parametrize("newer_first", [False, True])
def test_of_two_disks_recorded_at_one_path_without_a_serial_the_later_wins(
    api, disks, newer_first
):
    # A disk swapped in an enclosure whose bridge reports no serial.
    disks.add_disk("sdc", serial=None)
    histories = [
        ("WD-OLD", snapshots(FAILING, serial="WD-OLD", device="/dev/sdc", lsblk_serial=None)),
        ("WD-NEW", snapshots(STEADY, serial="WD-NEW", device="/dev/sdc", lsblk_serial=None,
                             start=START + timedelta(days=2))),
    ]
    disks.write(dict(reversed(histories) if newer_first else histories))

    assert placed(api) == [("WD-NEW", "/dev/sdc")]


def test_disks_without_a_serial_get_no_prediction(api, disks):
    # The monitor records every disk that smartctl reports without a serial
    # under "unknown", so the counters of different disks mix there.
    disks.write({"unknown": snapshots(FAILING, serial="unknown", lsblk_serial=None)})

    assert predictions(api) == []


@pytest.mark.parametrize("lsblk_serial", ["SD-1", None])
def test_the_boot_medium_and_virtual_devices_get_no_prediction(api, disks, lsblk_serial):
    disks.write({
        "SD-1": snapshots(FAILING, serial="SD-1", device="/dev/mmcblk1", lsblk_serial=lsblk_serial),
        "ZRAM": snapshots(FAILING, serial="ZRAM", device="/dev/zram0", lsblk_serial=None),
    })

    assert predictions(api) == []


def test_the_disks_are_listed_on_the_host_in_a_worker_thread(api, disks):
    disks.write({"WD-1": snapshots(FAILING)})

    predictions(api)

    assert disks.lsblk_on_loop == [False]


def test_no_predictions_when_the_host_disks_cannot_be_listed(api, disks):
    disks.write({"WD-1": snapshots(FAILING)})
    disks.fail[("lsblk",)] = LSBLK_FAILS

    response = api.get("/api/v1/health/smart/predictions")

    # As GET /health/disks answers: the page says why instead of showing no mark.
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == NO_DISKS


def test_without_a_history_there_is_no_prediction(api, disks):
    assert not disks.history_file.exists()

    assert predictions(api) == []
    assert not disks.history_file.exists()


# ---------------------------------------------------------------------------
# The history file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        # Cut short, as a write cut by a power loss leaves it.
        json.dumps({"WD-1": snapshots(FAILING)})[:300],
        "",
        "[]",
        '{"WD-1": 5}',
        '{"WD-1": [5]}',
    ],
)
def test_a_history_that_cannot_be_read_is_an_error_not_an_empty_one(api, disks, content):
    disks.history_file.write_text(content)

    response = api.get("/api/v1/health/smart/predictions")

    # Not "no predictions": both pages say so instead of showing no mark.
    assert response.status_code == 500, response.text
    assert response.json()["detail"].startswith(
        f"Could not read the SMART history {disks.history_file}: "), response.text


@pytest.mark.parametrize("path", ["/smart/trends", "/smart/history/WD-1", "/smart-monitor/status"])
def test_the_other_history_endpoints_say_so_too(api, disks, path):
    disks.history_file.write_text("{")

    response = api.get(f"/api/v1/health{path}")

    assert response.status_code == 500, response.text
    assert response.json()["detail"].startswith(
        f"Could not read the SMART history {disks.history_file}: "), response.text


def test_a_read_during_the_hourly_write_gets_the_whole_old_history(api, disks, monkeypatch):
    disks.write({"WD-1": snapshots(FAILING)})
    old = disks.history_file.read_text()
    monkeypatch.setattr("src.host.run_in_container", smartctl_reports(3))

    with open(disks.history_file) as reader:
        assert health_router._smart_monitor_pass() == []
        # The new history replaced the file: the old one was never cut short.
        assert reader.read() == old

    assert predictions(api)[0]["data_points"] == 25
    assert sorted(path.name for path in disks.history_file.parent.iterdir()) == ["smart_history.json"]


def test_the_monitor_starts_a_new_history_when_the_file_cannot_be_read(disks, monkeypatch, caplog):
    disks.history_file.write_text("{")
    monkeypatch.setattr("src.host.run_in_container", smartctl_reports(0))

    health_router._smart_monitor_pass()

    assert {serial: len(entries) for serial, entries in health_router.load_smart_history().items()} == {
        "WD-1": 1}
    assert f"Could not read the SMART history {disks.history_file}: " in caplog.text
    assert "starting a new history" in caplog.text

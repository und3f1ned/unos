from types import SimpleNamespace

from datetime import datetime

import pytest
from unittest.mock import patch, MagicMock


class TestSystemInfo:
    """Tests for system information endpoints."""

    def test_get_system_info_returns_valid_data(self):
        """Test that system info returns valid data structure."""
        from src.system.router import get_system_info
        import asyncio
        
        # Mock verify_token
        with patch("src.system.router.verify_token"):
            result = asyncio.run(get_system_info())
        
        assert hasattr(result, "hostname")
        assert hasattr(result, "kernel")
        assert hasattr(result, "os")
        assert hasattr(result, "architecture")
        assert isinstance(result.hostname, str)
        assert isinstance(result.kernel, str)

    def test_the_answer_names_the_boot_time_boot_time_and_has_no_uptime(self):
        """system.boot-time-field: the host's boot time is boot_time, in UTC with its offset (ADR-0401);
        a field named uptime would mislead an API client into reading a duration (ADR-0207)."""
        import psutil
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        from src.auth.utils import verify_token
        from src.main import app

        app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True)
        try:
            data = TestClient(app).get("/api/v1/system/info").json()
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert "uptime" not in data
        assert data["boot_time"].endswith("Z")
        assert datetime.fromisoformat(data["boot_time"]).timestamp() == pytest.approx(psutil.boot_time(), abs=1)


class TestTemperatures:
    """Tests for temperature sensor readings."""

    def test_get_temperatures_returns_valid_structure(self):
        """Test that temperatures returns valid structure."""
        from src.system.router import get_temperatures
        import asyncio
        
        mock_sensors = {
            "coretemp": [
                MagicMock(label="Core 0", current=45.0, high=80.0, critical=100.0),
                MagicMock(label="Core 1", current=47.0, high=80.0, critical=100.0),
            ],
            "acpitz": [
                MagicMock(label="", current=35.0, high=None, critical=None),
            ]
        }
        
        with patch("src.system.router.verify_token"):
            with patch("psutil.sensors_temperatures", create=True, return_value=mock_sensors):
                result = asyncio.run(get_temperatures())
        
        assert len(result) == 2
        
        # Find coretemp group
        coretemp = next(g for g in result if g.name == "coretemp")
        assert len(coretemp.sensors) == 2
        assert coretemp.sensors[0].label == "Core 0"
        assert coretemp.sensors[0].current == 45.0
        assert coretemp.sensors[0].high == 80.0
        assert coretemp.sensors[0].critical == 100.0

    def test_get_temperatures_handles_missing_labels(self):
        """Test that missing labels get default names."""
        from src.system.router import get_temperatures
        import asyncio
        
        mock_sensors = {
            "acpitz": [
                MagicMock(label="", current=35.0, high=None, critical=None),
            ]
        }
        
        with patch("src.system.router.verify_token"):
            with patch("psutil.sensors_temperatures", create=True, return_value=mock_sensors):
                result = asyncio.run(get_temperatures())
        
        assert result[0].sensors[0].label == "Sensor 0"

    def test_get_temperatures_handles_zero_thresholds(self):
        """Test that zero thresholds are converted to None."""
        from src.system.router import get_temperatures
        import asyncio
        
        mock_sensors = {
            "coretemp": [
                MagicMock(label="Core 0", current=45.0, high=0.0, critical=0.0),
            ]
        }
        
        with patch("src.system.router.verify_token"):
            with patch("psutil.sensors_temperatures", create=True, return_value=mock_sensors):
                result = asyncio.run(get_temperatures())
        
        assert result[0].sensors[0].high is None
        assert result[0].sensors[0].critical is None

    def test_get_temperatures_empty_on_error(self):
        """Test that empty list is returned on psutil error."""
        from src.system.router import get_temperatures
        import asyncio
        
        with patch("src.system.router.verify_token"):
            with patch("psutil.sensors_temperatures", create=True, side_effect=Exception("No sensors")):
                result = asyncio.run(get_temperatures())
        
        assert result == []


def _reading(label: str, current: float) -> SimpleNamespace:
    """A reading as psutil.sensors_temperatures lists it."""
    return SimpleNamespace(label=label, current=current, high=None, critical=None)


# The NVMe disk that many hosts have, and that psutil may list before the CPU.
_NVME = [_reading("Composite", 41.9), _reading("Sensor 1", 41.9), _reading("Sensor 2", 44.9)]


class TestDashboardTemperature:
    """system.temperatures (ADR-0346): the dashboard's Temperature card shows the temperature that
    the stats message of /ws/v1/events gives, chosen in one place: the CPU's or
    SoC's sensor by the name of its group, and on a host without one the first reading of the
    first group that psutil lists. Before, the web UI showed the first group, which on many hosts
    is an ACPI zone or a disk."""

    @staticmethod
    def chosen(groups):
        """The temperature that the stats message gives for these sensor groups."""
        from src.websocket import events

        with patch("psutil.sensors_temperatures", create=True, return_value=groups):
            return events.get_system_stats()["data"]["cpu"]["temperature"]

    @pytest.mark.parametrize(
        ("groups", "expected"),
        [
            pytest.param(
                {"acpitz": [_reading("", 27.8), _reading("", 29.8)], "nvme": _NVME,
                 "coretemp": [_reading("Core 0", 50.0), _reading("Package id 0", 53.0), _reading("Core 1", 49.0)]},
                53.0, id="intel-package",
            ),
            pytest.param(
                {"nvme": _NVME, "k10temp": [_reading("Tccd1", 61.5), _reading("Tctl", 57.4)]},
                57.4, id="amd-tctl",
            ),
            # On the Ryzen X CPUs of the first two generations and the Threadripper 1000 and 2000,
            # Tctl is the die's temperature plus an offset, and k10temp lists Tdie, the die's own,
            # after it.
            pytest.param(
                {"k10temp": [_reading("Tctl", 65.0), _reading("Tdie", 45.0)]},
                45.0, id="amd-tdie-before-tctl",
            ),
            pytest.param(
                {"drivetemp": [_reading("", 33.0)], "cpu_thermal": [_reading("", 48.3)]},
                48.3, id="raspberry-pi",
            ),
            # The RK3588 under Rockchip's kernel: the kernel names the hwmon device of each thermal
            # zone after it, with "_" for "-" (soc-thermal is soc_thermal), and soc-thermal is the
            # sensor of the whole chip, beside those of the core clusters, the GPU and the NPU.
            pytest.param(
                {"nvme": _NVME, "bigcore0_thermal": [_reading("", 46.2)], "bigcore1_thermal": [_reading("", 46.2)],
                 "littlecore_thermal": [_reading("", 45.3)], "center_thermal": [_reading("", 44.4)],
                 "gpu_thermal": [_reading("", 43.5)], "npu_thermal": [_reading("", 44.4)],
                 "soc_thermal": [_reading("", 45.8)]},
                45.8, id="rk3588-soc-thermal",
            ),
            # Without hwmon, psutil names the groups after the thermal zones' types themselves.
            pytest.param(
                {"bigcore0-thermal": [_reading("", 46.2)], "soc-thermal": [_reading("", 45.8)]},
                45.8, id="rk3588-thermal-zones",
            ),
            # The mainline kernel's name for the same sensor of the RK3588.
            pytest.param(
                {"nvme": _NVME, "bigcore0_thermal": [_reading("", 46.2)], "package_thermal": [_reading("", 44.4)]},
                44.4, id="rk3588-package-thermal",
            ),
            pytest.param(
                {"cpu-thermal": [_reading("", 51.1)], "gpu-thermal": [_reading("", 49.0)]},
                51.1, id="cpu-thermal-zone",
            ),
            # The SpacemiT K1 of a Banana Pi BPI-F3 under Armbian's current-spacemit kernel, in the
            # order psutil lists them there: the NVMe drive's hwmon comes first, then the zones of
            # the top of the chip, the GPU and the CPU's two clusters (ADR-0370).
            pytest.param(
                {"nvme": _NVME, "top_thermal": [_reading("", 65.0)], "gpu_thermal": [_reading("", 61.0)],
                 "cluster0_thermal": [_reading("", 71.0)], "cluster1_thermal": [_reading("", 70.0)]},
                71.0, id="spacemit-k1-cluster0",
            ),
            # No sensor known as the CPU's: the first reading of the first group, as before.
            pytest.param(
                {"acpitz": [_reading("", 27.8), _reading("", 29.8)], "nvme": _NVME},
                27.8, id="unknown-first-group",
            ),
            # A known group without its chip-wide label (k10temp of a CPU older than Zen): its
            # first reading.
            pytest.param(
                {"nvme": _NVME, "k10temp": [_reading("", 52.0)]},
                52.0, id="known-group-without-label",
            ),
        ],
    )
    def test_the_cpu_or_soc_sensor_is_chosen(self, groups, expected):
        assert self.chosen(groups) == expected

    def test_a_host_without_sensors_gives_none(self):
        assert self.chosen({}) is None
        assert self.chosen({"acpitz": []}) is None

    def test_a_failing_sensor_read_gives_none(self):
        """macOS has no psutil.sensors_temperatures, and hwmon can fail on Linux."""
        from src.websocket import events

        with patch("psutil.sensors_temperatures", create=True, side_effect=OSError("No sensors")):
            message = events.get_system_stats()
        assert message["data"]["cpu"]["temperature"] is None

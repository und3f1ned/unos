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
        """system.boot-time-field: the host's boot time, in seconds since the epoch, is boot_time;
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
        assert data["boot_time"] == pytest.approx(psutil.boot_time(), abs=1)


class TestSystemStats:
    """Tests for system statistics."""

    def test_get_system_stats_returns_valid_data(self):
        """Test that system stats returns valid data structure."""
        from src.system.router import get_system_stats
        import asyncio
        
        with patch("src.system.router.verify_token"):
            result = asyncio.run(get_system_stats())
        
        assert hasattr(result, "cpu_percent")
        assert hasattr(result, "cpu_count")
        assert hasattr(result, "memory_total")
        assert hasattr(result, "memory_used")
        assert hasattr(result, "memory_percent")
        assert hasattr(result, "disk_total")
        assert hasattr(result, "disk_used")
        assert hasattr(result, "disk_percent")
        assert hasattr(result, "network_sent")
        assert hasattr(result, "network_recv")
        assert hasattr(result, "temperatures")

    def test_cpu_percent_in_valid_range(self):
        """Test that CPU percent is between 0 and 100."""
        from src.system.router import get_system_stats
        import asyncio
        
        with patch("src.system.router.verify_token"):
            result = asyncio.run(get_system_stats())
        
        assert 0 <= result.cpu_percent <= 100

    def test_memory_percent_in_valid_range(self):
        """Test that memory percent is between 0 and 100."""
        from src.system.router import get_system_stats
        import asyncio
        
        with patch("src.system.router.verify_token"):
            result = asyncio.run(get_system_stats())
        
        assert 0 <= result.memory_percent <= 100

    def test_disk_percent_in_valid_range(self):
        """Test that disk percent is between 0 and 100."""
        from src.system.router import get_system_stats
        import asyncio
        
        with patch("src.system.router.verify_token"):
            result = asyncio.run(get_system_stats())
        
        assert 0 <= result.disk_percent <= 100


class TestPCIeDevices:
    """Tests for PCIe device listing."""

    @pytest.fixture
    def mock_lspci_output(self):
        """Mock lspci -vmm output."""
        return """Slot:	00:00.0
Class:	Host bridge
Vendor:	Intel Corporation
Device:	Xeon E3-1200 v5/E3-1500 v5/6th Gen Core Processor Host Bridge/DRAM Registers
Rev:	07

Slot:	00:02.0
Class:	VGA compatible controller
Vendor:	Intel Corporation
Device:	HD Graphics 530
SVendor:	Dell
SDevice:	HD Graphics 530
Rev:	06
Driver:	i915
"""

    def test_parse_pcie_devices(self, mock_lspci_output):
        """Test parsing lspci output."""
        from src.system.router import get_pcie_devices
        import asyncio
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_lspci_output
        
        with patch("src.system.router.verify_token"):
            with patch("src.system.router.subprocess.run", return_value=mock_result):
                result = asyncio.run(get_pcie_devices())
        
        assert len(result) == 2
        
        # Check first device
        assert result[0].slot == "00:00.0"
        assert result[0].class_name == "Host bridge"
        assert result[0].vendor == "Intel Corporation"
        
        # Check second device
        assert result[1].slot == "00:02.0"
        assert result[1].class_name == "VGA compatible controller"
        assert result[1].driver == "i915"

    def test_pcie_devices_empty_on_lspci_failure(self):
        """Test that empty list is returned when lspci fails."""
        from src.system.router import get_pcie_devices
        import asyncio
        
        mock_result = MagicMock()
        mock_result.returncode = 1
        
        with patch("src.system.router.verify_token"):
            with patch("src.system.router.subprocess.run", return_value=mock_result):
                result = asyncio.run(get_pcie_devices())
        
        assert result == []


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


class TestNetworkInterfaces:
    """Tests for network interface listing."""

    def test_get_network_interfaces_excludes_loopback(self):
        """Test that loopback interface is excluded."""
        from src.system.router import get_network_interfaces
        import asyncio
        
        with patch("src.system.router.verify_token"):
            result = asyncio.run(get_network_interfaces())
        
        interface_names = [i.name for i in result]
        assert "lo" not in interface_names


class TestTimezone:
    """Tests for timezone management."""

    def test_get_timezone_success(self):
        """Test getting timezone."""
        from src.system.router import get_timezone
        import asyncio
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "America/New_York\n"
        
        with patch("src.system.router.verify_token"):
            with patch("src.system.router.subprocess.run", return_value=mock_result):
                result = asyncio.run(get_timezone())
        
        assert result == {"timezone": "America/New_York"}

    def test_get_timezone_unknown_on_failure(self):
        """Test timezone returns Unknown on failure."""
        from src.system.router import get_timezone
        import asyncio
        
        mock_result = MagicMock()
        mock_result.returncode = 1
        
        with patch("src.system.router.verify_token"):
            with patch("src.system.router.subprocess.run", return_value=mock_result):
                result = asyncio.run(get_timezone())
        
        assert result == {"timezone": "Unknown"}

import subprocess
import json
import os
import asyncio
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..auth.utils import verify_token
from ..security.validators import validate_interface_name, sanitize_error_message

# Rate limiter for power endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/power", tags=["power"])

CONFIG_FILE = "/data/power_config.json"
UPS_CONFIG_FILE = "/data/ups_config.json"

# UPS monitoring state
_ups_monitor_running = False
_ups_monitor_task: Optional[asyncio.Task] = None
UPS_CHECK_INTERVAL = 30  # Check every 30 seconds


class PowerConfig(BaseModel):
    wake_on_lan: bool = True
    auto_shutdown: bool = False
    shutdown_hour: int = 23
    shutdown_minute: int = 0
    auto_wakeup: bool = False
    wakeup_hour: int = 7
    wakeup_minute: int = 0
    idle_shutdown: bool = False
    idle_timeout_minutes: int = 30


class UPSConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = 3493
    ups_name: str = "ups"
    username: Optional[str] = None
    password: Optional[str] = None
    auto_shutdown: bool = True
    shutdown_battery_percent: int = 20
    shutdown_runtime_minutes: int = 5


class UPSStatus(BaseModel):
    available: bool
    name: Optional[str] = None
    status: Optional[str] = None  # OL (online), OB (on battery), LB (low battery)
    charge_percent: Optional[int] = None
    runtime_minutes: Optional[int] = None
    load_percent: Optional[int] = None
    input_voltage: Optional[float] = None
    output_voltage: Optional[float] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    battery_type: Optional[str] = None
    last_checked: str = ""


class PowerStatus(BaseModel):
    uptime: str
    uptime_seconds: int
    last_boot: str
    load_average: list[float]


def load_config() -> PowerConfig:
    """Load power config from file."""
    path = Path(CONFIG_FILE)
    if not path.exists():
        return PowerConfig()
    try:
        data = json.loads(path.read_text())
        return PowerConfig(**data)
    except Exception:
        return PowerConfig()


def save_config(config: PowerConfig) -> None:
    """Save power config to file."""
    path = Path(CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), indent=2))


def load_ups_config() -> UPSConfig:
    """Load UPS config from file."""
    path = Path(UPS_CONFIG_FILE)
    if not path.exists():
        return UPSConfig()
    try:
        data = json.loads(path.read_text())
        return UPSConfig(**data)
    except Exception:
        return UPSConfig()


def save_ups_config(config: UPSConfig) -> None:
    """Save UPS config to file."""
    path = Path(UPS_CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), indent=2))


def get_ups_status(config: UPSConfig) -> UPSStatus:
    """Get UPS status using upsc command (NUT client)."""
    try:
        # Try to get UPS data via upsc
        ups_identifier = f"{config.ups_name}@{config.host}:{config.port}"
        result = subprocess.run(
            ["upsc", ups_identifier],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return UPSStatus(available=False, last_checked=datetime.now().isoformat())

        # Parse upsc output
        data = {}
        for line in result.stdout.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip()

        # Map NUT variables to our model
        status = data.get("ups.status", "")
        charge = data.get("battery.charge")
        runtime = data.get("battery.runtime")
        load = data.get("ups.load")
        input_v = data.get("input.voltage")
        output_v = data.get("output.voltage")

        return UPSStatus(
            available=True,
            name=config.ups_name,
            status=status,
            charge_percent=int(charge) if charge else None,
            runtime_minutes=int(float(runtime) / 60) if runtime else None,
            load_percent=int(load) if load else None,
            input_voltage=float(input_v) if input_v else None,
            output_voltage=float(output_v) if output_v else None,
            manufacturer=data.get("ups.mfr"),
            model=data.get("ups.model"),
            battery_type=data.get("battery.type"),
            last_checked=datetime.now().isoformat()
        )
    except subprocess.TimeoutExpired:
        return UPSStatus(available=False, last_checked=datetime.now().isoformat())
    except FileNotFoundError:
        # upsc not installed
        return UPSStatus(available=False, last_checked=datetime.now().isoformat())
    except Exception:
        return UPSStatus(available=False, last_checked=datetime.now().isoformat())


async def ups_monitor_loop():
    """Background loop for UPS monitoring and auto-shutdown."""
    global _ups_monitor_running

    while _ups_monitor_running:
        try:
            config = load_ups_config()
            if not config.enabled:
                await asyncio.sleep(UPS_CHECK_INTERVAL)
                continue

            status = get_ups_status(config)

            if status.available and config.auto_shutdown:
                # Check if we should shutdown
                should_shutdown = False
                reason = ""

                # Check battery percentage
                if status.charge_percent is not None and status.charge_percent <= config.shutdown_battery_percent:
                    if "OB" in (status.status or ""):  # On battery
                        should_shutdown = True
                        reason = f"Battery at {status.charge_percent}% (threshold: {config.shutdown_battery_percent}%)"

                # Check runtime
                if status.runtime_minutes is not None and status.runtime_minutes <= config.shutdown_runtime_minutes:
                    if "OB" in (status.status or ""):  # On battery
                        should_shutdown = True
                        reason = f"Runtime at {status.runtime_minutes} min (threshold: {config.shutdown_runtime_minutes} min)"

                # Check for low battery flag
                if status.status and "LB" in status.status:
                    should_shutdown = True
                    reason = "UPS reports low battery"

                if should_shutdown and not is_in_container():
                    # Log and initiate shutdown
                    print(f"UPS auto-shutdown triggered: {reason}")
                    subprocess.Popen(["shutdown", "-h", "+1", f"UPS auto-shutdown: {reason}"])

        except Exception as e:
            print(f"UPS monitor error: {e}")

        await asyncio.sleep(UPS_CHECK_INTERVAL)


def is_in_container() -> bool:
    """Check if running inside a Docker container."""
    return os.path.exists("/.dockerenv") or os.environ.get("container") == "docker"


@router.get("/status", response_model=PowerStatus)
async def get_power_status(_: dict = Depends(verify_token)):
    """Get current power status."""
    try:
        # Get uptime
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])

        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)

        if days > 0:
            uptime = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            uptime = f"{hours}h {minutes}m"
        else:
            uptime = f"{minutes}m"

        # Get load average
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            load_average = [float(parts[0]), float(parts[1]), float(parts[2])]

        # Get last boot time
        try:
            result = subprocess.run(
                ["who", "-b"],
                capture_output=True, text=True
            )
            last_boot = result.stdout.strip().replace("system boot", "").strip() if result.returncode == 0 else "Unknown"
        except Exception:
            last_boot = "Unknown"

        return PowerStatus(
            uptime=uptime,
            uptime_seconds=int(uptime_seconds),
            last_boot=last_boot,
            load_average=load_average
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to get power status: {e}")


@router.get("/config", response_model=PowerConfig)
async def get_power_config(_: dict = Depends(verify_token)):
    """Get power configuration."""
    return load_config()


@router.put("/config", response_model=PowerConfig)
async def update_power_config(config: PowerConfig, _: dict = Depends(verify_token)):
    """Update power configuration."""
    save_config(config)
    return config


@router.post("/reboot")
@limiter.limit("2/minute")  # Rate limit reboot requests
async def reboot_system(request: Request, _: dict = Depends(verify_token)):
    """Reboot the system."""
    if is_in_container():
        return {"status": "unavailable", "message": "Reboot not available in container mode. Use host system to reboot."}
    try:
        subprocess.Popen(["shutdown", "-r", "+1", "System reboot requested via UNOS"])
        return {"status": "scheduled", "message": "System will reboot in 1 minute"}
    except FileNotFoundError:
        # Try systemctl
        try:
            subprocess.Popen(["systemctl", "reboot"])
            return {"status": "scheduled", "message": "System reboot initiated"}
        except Exception as e:
            raise HTTPException(500, f"Failed to schedule reboot: {e}")
    except Exception as e:
        raise HTTPException(500, f"Failed to schedule reboot: {e}")


@router.post("/shutdown")
@limiter.limit("2/minute")  # Rate limit shutdown requests
async def shutdown_system(request: Request, _: dict = Depends(verify_token)):
    """Shutdown the system."""
    if is_in_container():
        return {"status": "unavailable", "message": "Shutdown not available in container mode. Use host system to shutdown."}
    try:
        subprocess.Popen(["shutdown", "-h", "+1", "System shutdown requested via UNOS"])
        return {"status": "scheduled", "message": "System will shutdown in 1 minute"}
    except FileNotFoundError:
        # Try systemctl
        try:
            subprocess.Popen(["systemctl", "poweroff"])
            return {"status": "scheduled", "message": "System shutdown initiated"}
        except Exception as e:
            raise HTTPException(500, f"Failed to schedule shutdown: {e}")
    except Exception as e:
        raise HTTPException(500, f"Failed to schedule shutdown: {e}")


@router.post("/cancel")
async def cancel_shutdown(_: dict = Depends(verify_token)):
    """Cancel scheduled shutdown/reboot."""
    if is_in_container():
        return {"status": "none", "message": "No shutdown scheduled (container mode)"}
    try:
        result = subprocess.run(["shutdown", "-c"], capture_output=True, text=True)
        if result.returncode == 0:
            return {"status": "cancelled", "message": "Scheduled shutdown cancelled"}
        else:
            return {"status": "none", "message": "No shutdown scheduled"}
    except FileNotFoundError:
        return {"status": "none", "message": "No shutdown scheduled"}
    except Exception as e:
        raise HTTPException(500, f"Failed to cancel shutdown: {e}")


@router.post("/suspend")
async def suspend_system(_: dict = Depends(verify_token)):
    """Suspend the system to RAM."""
    if is_in_container():
        return {"status": "unavailable", "message": "Suspend not available in container mode. Use host system to suspend."}
    try:
        # Check if suspend is supported
        with open("/sys/power/state") as f:
            states = f.read().strip()
        if "mem" not in states:
            raise HTTPException(400, "Suspend not supported on this system")

        subprocess.Popen(["systemctl", "suspend"])
        return {"status": "suspending", "message": "System is suspending"}
    except FileNotFoundError:
        raise HTTPException(400, "Suspend not supported on this system")
    except Exception as e:
        raise HTTPException(500, f"Failed to suspend: {e}")


@router.get("/wol")
async def get_wol_status(_: dict = Depends(verify_token)):
    """Get Wake-on-LAN status for network interfaces."""
    try:
        result = subprocess.run(
            ["ip", "link", "show"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, "Failed to get network interfaces")

        interfaces = []
        current_iface = None

        for line in result.stdout.split("\n"):
            if ": " in line and not line.startswith(" "):
                parts = line.split(": ")
                if len(parts) >= 2:
                    name = parts[1].split("@")[0]
                    if name not in ["lo", "docker0"] and not name.startswith("veth") and not name.startswith("br-"):
                        current_iface = {"name": name, "wol": "unknown", "enabled": False}
                        interfaces.append(current_iface)

        # Get WOL status for each interface
        for iface in interfaces:
            try:
                wol_result = subprocess.run(
                    ["ethtool", iface["name"]],
                    capture_output=True, text=True
                )
                for line in wol_result.stdout.split("\n"):
                    if "Wake-on:" in line:
                        wol = line.split(":")[1].strip()
                        iface["wol"] = wol
                        iface["enabled"] = "g" in wol.lower()
            except Exception:
                pass

        return {"interfaces": interfaces}
    except FileNotFoundError:
        return {"interfaces": []}
    except Exception as e:
        raise HTTPException(500, f"Failed to get WOL status: {e}")


@router.post("/wol/{interface}/enable")
async def enable_wol(interface: str, _: dict = Depends(verify_token)):
    """Enable Wake-on-LAN for an interface."""
    # Validate interface name to prevent command injection
    validated_interface = validate_interface_name(interface)

    try:
        result = subprocess.run(
            ["ethtool", "-s", validated_interface, "wol", "g"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, "Failed to enable WOL")
        return {"status": "enabled", "interface": validated_interface}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, sanitize_error_message(e))


@router.post("/wol/{interface}/disable")
async def disable_wol(interface: str, _: dict = Depends(verify_token)):
    """Disable Wake-on-LAN for an interface."""
    # Validate interface name to prevent command injection
    validated_interface = validate_interface_name(interface)

    try:
        result = subprocess.run(
            ["ethtool", "-s", validated_interface, "wol", "d"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, "Failed to disable WOL")
        return {"status": "disabled", "interface": validated_interface}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, sanitize_error_message(e))


# ==================== UPS Monitoring ====================

@router.get("/ups/status", response_model=UPSStatus)
async def get_ups_status_endpoint(_: dict = Depends(verify_token)):
    """Get current UPS status."""
    config = load_ups_config()
    return get_ups_status(config)


@router.get("/ups/config", response_model=UPSConfig)
async def get_ups_config(_: dict = Depends(verify_token)):
    """Get UPS configuration."""
    return load_ups_config()


@router.put("/ups/config", response_model=UPSConfig)
async def update_ups_config(config: UPSConfig, _: dict = Depends(verify_token)):
    """Update UPS configuration."""
    save_ups_config(config)
    return config


@router.post("/ups/test")
async def test_ups_connection(_: dict = Depends(verify_token)):
    """Test UPS connection."""
    config = load_ups_config()
    status = get_ups_status(config)

    if status.available:
        return {
            "status": "connected",
            "ups_name": status.name,
            "model": status.model,
            "manufacturer": status.manufacturer,
            "battery_charge": status.charge_percent
        }
    else:
        raise HTTPException(503, "Unable to connect to UPS. Check NUT configuration.")


@router.get("/ups/list")
async def list_ups_devices(_: dict = Depends(verify_token)):
    """List available UPS devices from NUT."""
    config = load_ups_config()
    try:
        result = subprocess.run(
            ["upsc", "-l", f"{config.host}:{config.port}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"devices": [], "error": "Unable to list UPS devices"}

        devices = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        return {"devices": devices}
    except subprocess.TimeoutExpired:
        return {"devices": [], "error": "Connection timeout"}
    except FileNotFoundError:
        return {"devices": [], "error": "NUT client (upsc) not installed"}
    except Exception as e:
        return {"devices": [], "error": str(e)}


@router.get("/ups/monitor/status")
async def get_ups_monitor_status(_: dict = Depends(verify_token)):
    """Get UPS monitor status."""
    config = load_ups_config()
    return {
        "running": _ups_monitor_running,
        "enabled": config.enabled,
        "auto_shutdown": config.auto_shutdown,
        "shutdown_battery_percent": config.shutdown_battery_percent,
        "shutdown_runtime_minutes": config.shutdown_runtime_minutes
    }


async def start_ups_monitor():
    """Start the UPS monitor on app startup."""
    global _ups_monitor_running, _ups_monitor_task
    _ups_monitor_running = True
    _ups_monitor_task = asyncio.create_task(ups_monitor_loop())


async def stop_ups_monitor():
    """Stop the UPS monitor on app shutdown."""
    global _ups_monitor_running, _ups_monitor_task
    _ups_monitor_running = False
    if _ups_monitor_task:
        _ups_monitor_task.cancel()

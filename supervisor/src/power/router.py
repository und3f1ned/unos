import subprocess
import json
import os
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token

router = APIRouter(prefix="/power", tags=["power"])

CONFIG_FILE = "/data/power_config.json"


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
async def reboot_system(_: dict = Depends(verify_token)):
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
async def shutdown_system(_: dict = Depends(verify_token)):
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
    try:
        result = subprocess.run(
            ["ethtool", "-s", interface, "wol", "g"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Failed to enable WOL: {result.stderr}")
        return {"status": "enabled", "interface": interface}
    except Exception as e:
        raise HTTPException(500, f"Failed to enable WOL: {e}")


@router.post("/wol/{interface}/disable")
async def disable_wol(interface: str, _: dict = Depends(verify_token)):
    """Disable Wake-on-LAN for an interface."""
    try:
        result = subprocess.run(
            ["ethtool", "-s", interface, "wol", "d"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Failed to disable WOL: {result.stderr}")
        return {"status": "disabled", "interface": interface}
    except Exception as e:
        raise HTTPException(500, f"Failed to disable WOL: {e}")

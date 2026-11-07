import subprocess
import json
import logging
import os
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Literal, Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import host
from ..auth.utils import verify_token
from ..security.validators import INTERFACE_NAME_PATTERN, validate_interface_name

logger = logging.getLogger(__name__)

# Rate limiter for power endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/power", tags=["power"])

CONFIG_FILE = "/data/power_config.json"
UPS_CONFIG_FILE = "/data/ups_config.json"
# The Wake-on-LAN state an administrator chose per interface name, applied
# again at every start of the supervisor (reapply_wol_settings, ADR-0232).
WOL_FILE = "/data/wol_config.json"
# One Wake-on-LAN change at a time: each reads the file, changes it and writes it.
_wol_lock = threading.Lock()

# Reboot, shutdown, cancel, suspend and Wake-on-LAN act on the host through
# src/host.py (ADR-0232). With UNOS_HOST_EXEC=local the supervisor runs its
# host commands on its own machine: the local stand-in on a workstation or the
# unprivileged test stack. There every power endpoint answers 503 with this
# text and runs nothing.
POWER_OFF_DETAIL = (
    "Power actions are off: this supervisor runs its host commands on its own machine "
    "(UNOS_HOST_EXEC=local), not on a NAS host"
)
# Seconds a power command on the host may take.
HOST_TIMEOUT = 30
# The command that schedules each action one minute from now, with the wall
# message that the host's logged-in users see, and the answer.
_SCHEDULE = {
    "reboot": (["shutdown", "-r", "+1", "UNOS: reboot requested in the web interface"],
               "The host reboots in 1 minute"),
    "shutdown": (["shutdown", "-h", "+1", "UNOS: shutdown requested in the web interface"],
                 "The host shuts down in 1 minute"),
}
# logind's scheduled action and its time (microseconds since the epoch); ("", 0) when none.
_SCHEDULED_SHUTDOWN = [
    "busctl", "--json=short", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager", "ScheduledShutdown",
]
# logind's actions as the web UI names them. Dry runs (shutdown -k) only send
# the wall message: nothing is pending.
_PENDING_ACTIONS = {
    "reboot": "reboot", "kexec": "reboot", "soft-reboot": "reboot",
    "poweroff": "shutdown", "halt": "shutdown",
}
NOTHING_PENDING = "No reboot or shutdown is pending"

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


class PendingAction(BaseModel):
    """The reboot or shutdown that logind has scheduled on the host; both None when there is none."""
    action: Optional[Literal["reboot", "shutdown"]] = None
    at: Optional[datetime] = None


class PowerAnswer(BaseModel):
    status: Literal["scheduled", "cancelled", "none", "suspending"]
    message: str
    # What is pending after a reboot, shutdown or cancel; None after a suspend.
    pending: Optional[PendingAction] = None


class WolInterface(BaseModel):
    """An Ethernet interface of the host and its Wake-on-LAN state, as ethtool reports it."""
    name: str
    mac_address: Optional[str] = None
    # Whether it can wake the host by magic packet ("g" in ethtool's "Supports Wake-on").
    supported: bool
    # ethtool's "Wake-on" letters ("d": nothing wakes the host), "" when ethtool shows none.
    wol: str
    # Whether a magic packet wakes the host.
    enabled: bool


class WolStatus(BaseModel):
    interfaces: list[WolInterface]


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
    """Check if running inside a Docker container.

    Only the UPS monitor's shutdown checks it, which therefore never runs
    in the supervisor's container (system.ups-monitoring). The power actions
    reach the host through src/host.py instead (ADR-0232).
    """
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


# ==================== Reboot, shutdown and suspend (ADR-0232) ====================


def _refuse_local() -> None:
    """Refuse a power action where host commands would run on the supervisor's own machine."""
    if host.runs_locally():
        raise HTTPException(503, POWER_OFF_DETAIL)


def _run(cmd: list[str]) -> host.HostResult:
    """Run ``cmd`` on the host. A command that cannot start or hangs fails like one that exits non-zero."""
    try:
        return host.run_on_host(cmd, timeout=HOST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return host.HostResult(124, "", f"{cmd[0]} did not answer within {HOST_TIMEOUT} seconds")
    except OSError as exc:
        return host.HostResult(126, "", str(exc))


def _why(result: host.HostResult) -> str:
    """What the host said about a failed command."""
    text = (result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}")
    return text if len(text) <= 300 else text[:300] + "..."


def _read_pending() -> PendingAction:
    """The reboot or shutdown that logind has scheduled on the host. Raises HTTPException(500)."""
    result = _run(_SCHEDULED_SHUTDOWN)
    if not result.ok:
        raise HTTPException(500, f"Could not read the pending reboot or shutdown: {_why(result)}")
    try:
        action, usec = json.loads(result.stdout)["data"]
        pending = _PENDING_ACTIONS.get(action)
        at = datetime.fromtimestamp(int(usec) / 1_000_000, tz=timezone.utc) if pending and usec else None
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        raise HTTPException(
            500, f"Could not read the pending reboot or shutdown: busctl answered {result.stdout.strip()[:100]!r}"
        )
    if at is None:
        return PendingAction()
    return PendingAction(action=pending, at=at)


def _schedule(action: str) -> PowerAnswer:
    """Reboot or shut down the host one minute from now."""
    cmd, message = _SCHEDULE[action]
    result = _run(cmd)
    if not result.ok:
        raise HTTPException(500, f"Could not schedule the {action}: {_why(result)}")
    try:
        pending = _read_pending()
    except HTTPException as exc:
        # Scheduled all the same: the answer must not say otherwise.
        logger.warning("The %s is scheduled, but %s", action, exc.detail)
        pending = PendingAction(action=action)
    return PowerAnswer(status="scheduled", message=message, pending=pending)


def _cancel() -> PowerAnswer:
    """Cancel the pending reboot or shutdown, if there is one."""
    pending = _read_pending()
    if pending.action is None:
        return PowerAnswer(status="none", message=NOTHING_PENDING, pending=pending)
    result = _run(["shutdown", "-c"])
    if not result.ok:
        raise HTTPException(500, f"Could not cancel the pending {pending.action}: {_why(result)}")
    after = _read_pending()
    if after.action is not None:
        raise HTTPException(500, f"The pending {after.action} is still scheduled")
    return PowerAnswer(status="cancelled", message=f"The pending {pending.action} was cancelled", pending=after)


def _suspend() -> PowerAnswer:
    """Suspend the host to RAM, if its kernel offers it."""
    try:
        states = host.read_host_file("/sys/power/state", timeout=HOST_TIMEOUT)
    except (host.HostCommandError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"Could not read the host's sleep states: {exc}")
    if "mem" not in states.split():
        raise HTTPException(409, "The host cannot suspend: its kernel offers no suspend to RAM")
    result = _run(["systemctl", "suspend"])
    if not result.ok:
        raise HTTPException(500, f"Could not suspend the host: {_why(result)}")
    return PowerAnswer(status="suspending", message="The host is suspending")


@router.get("/pending", response_model=PendingAction)
async def get_pending_action(_: dict = Depends(verify_token)):
    """The reboot or shutdown pending on the host, and when it acts."""
    _refuse_local()
    return await asyncio.to_thread(_read_pending)


@router.post("/reboot", response_model=PowerAnswer)
@limiter.limit("2/minute")  # Rate limit reboot requests
async def reboot_system(request: Request, _: dict = Depends(verify_token)):
    """Reboot the host one minute from now."""
    _refuse_local()
    return await asyncio.to_thread(_schedule, "reboot")


@router.post("/shutdown", response_model=PowerAnswer)
@limiter.limit("2/minute")  # Rate limit shutdown requests
async def shutdown_system(request: Request, _: dict = Depends(verify_token)):
    """Shut the host down one minute from now."""
    _refuse_local()
    return await asyncio.to_thread(_schedule, "shutdown")


@router.post("/cancel", response_model=PowerAnswer)
async def cancel_shutdown(_: dict = Depends(verify_token)):
    """Cancel the pending reboot or shutdown of the host."""
    _refuse_local()
    return await asyncio.to_thread(_cancel)


@router.post("/suspend", response_model=PowerAnswer)
@limiter.limit("2/minute")  # Rate limit suspend requests
async def suspend_system(request: Request, _: dict = Depends(verify_token)):
    """Suspend the host to RAM now."""
    _refuse_local()
    return await asyncio.to_thread(_suspend)


# ==================== Wake-on-LAN (ADR-0232) ====================


def _read_wol(name: str, mac_address: Optional[str]) -> WolInterface:
    """The Wake-on-LAN state of one interface, from ethtool on the host. Raises HTTPException(500)."""
    result = _run(["ethtool", name])
    # 124: no answer in time, 126 and 127: ethtool could not start (nsenter's exit status when
    # the host lacks it). ethtool itself prints what it can, also when it exits non-zero.
    if result.returncode in (124, 126, 127):
        raise HTTPException(500, f"Could not read the Wake-on-LAN settings of {name}: {_why(result)}")
    supports = wake_on = ""
    for line in result.stdout.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "Supports Wake-on":
            supports = value.strip()
        elif key == "Wake-on":
            wake_on = value.strip()
    return WolInterface(
        name=name, mac_address=mac_address, supported="g" in supports, wol=wake_on, enabled="g" in wake_on
    )


def _list_wol() -> list[WolInterface]:
    """The host's Ethernet interfaces with their Wake-on-LAN state. Raises HTTPException(500).

    Physical devices only: Ethernet links without a link kind of their own,
    so no loopback, veth, bridge, bond, VLAN or tunnel, and only names that
    validate_interface_name accepts (they reach ethtool as arguments).
    """
    result = _run(["ip", "-d", "-j", "link", "show"])
    if not result.ok:
        raise HTTPException(500, f"Could not list the network interfaces: {_why(result)}")
    try:
        links = json.loads(result.stdout)
        if not isinstance(links, list):
            raise ValueError("not a list")
    except ValueError:
        raise HTTPException(500, "Could not list the network interfaces: ip answered no list in JSON")
    interfaces = []
    for link in links:
        if not isinstance(link, dict) or link.get("link_type") != "ether":
            continue
        linkinfo = link.get("linkinfo")
        if isinstance(linkinfo, dict) and linkinfo.get("info_kind"):
            continue
        name = link.get("ifname")
        if not isinstance(name, str) or not INTERFACE_NAME_PATTERN.fullmatch(name):
            continue
        address = link.get("address")
        interfaces.append(_read_wol(name, address if isinstance(address, str) else None))
    return interfaces


def _load_wol_choices() -> dict[str, bool]:
    """The Wake-on-LAN state chosen per interface name; entries that are not a name and a bool are left out."""
    path = Path(WOL_FILE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring the Wake-on-LAN settings in %s: %s", WOL_FILE, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring the Wake-on-LAN settings in %s: not an object", WOL_FILE)
        return {}
    return {
        name: on for name, on in data.items()
        if isinstance(name, str) and INTERFACE_NAME_PATTERN.fullmatch(name) and isinstance(on, bool)
    }


def _remember_wol(name: str, on: bool) -> None:
    """Keep the choice for the next start; the file is written only when it changes (ADR-0214)."""
    choices = _load_wol_choices()
    if choices.get(name) is on:
        return
    choices[name] = on
    path = Path(WOL_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(choices, indent=2, sort_keys=True) + "\n")


def _set_wol(name: str, on: bool) -> WolInterface:
    """Turn magic-packet wake on (``wol g``) or every wake event off (``wol d``) for one interface."""
    with _wol_lock:
        return _set_wol_locked(name, on)


def _set_wol_locked(name: str, on: bool) -> WolInterface:
    current = next((iface for iface in _list_wol() if iface.name == name), None)
    if current is None:
        raise HTTPException(404, f"No network interface named {name}")
    if not current.supported:
        raise HTTPException(409, f"{name} does not support Wake-on-LAN by magic packet")
    if current.enabled != on:
        state = "on" if on else "off"
        result = _run(["ethtool", "-s", name, "wol", "g" if on else "d"])
        if not result.ok:
            raise HTTPException(500, f"Could not turn Wake-on-LAN {state} for {name}: {_why(result)}")
        current = _read_wol(name, current.mac_address)
        if current.enabled != on:
            raise HTTPException(500, f"Wake-on-LAN is still {'off' if on else 'on'} on {name}")
    _remember_wol(name, on)
    return current


def _reapply_wol(choices: dict[str, bool]) -> None:
    with _wol_lock:
        _reapply_wol_locked(choices)


def _reapply_wol_locked(choices: dict[str, bool]) -> None:
    interfaces = {iface.name: iface for iface in _list_wol()}
    for name, on in sorted(choices.items()):
        state = "on" if on else "off"
        current = interfaces.get(name)
        if current is None:
            logger.warning("Wake-on-LAN was turned %s for %s, which the host no longer has", state, name)
            continue
        if not current.supported or current.enabled == on:
            continue
        result = _run(["ethtool", "-s", name, "wol", "g" if on else "d"])
        if result.ok:
            logger.info("Wake-on-LAN is %s again for %s", state, name)
        else:
            logger.warning("Could not turn Wake-on-LAN %s again for %s: %s", state, name, _why(result))


async def reapply_wol_settings() -> None:
    """Give each interface the Wake-on-LAN state an administrator chose for it.

    Many drivers forget it at a reboot. Runs once beside the startup, in
    main.py's lifespan (ADR-0043), off the event loop; logs, never raises.
    """
    choices = _load_wol_choices()
    if not choices or host.runs_locally():
        return
    try:
        await asyncio.to_thread(_reapply_wol, choices)
    except HTTPException as exc:
        logger.warning("Could not apply the Wake-on-LAN settings: %s", exc.detail)
    except Exception as exc:
        logger.warning("Could not apply the Wake-on-LAN settings: %s", exc)


@router.get("/wol", response_model=WolStatus)
async def get_wol_status(_: dict = Depends(verify_token)):
    """The host's Ethernet interfaces and their Wake-on-LAN state."""
    _refuse_local()
    return WolStatus(interfaces=await asyncio.to_thread(_list_wol))


@router.post("/wol/{interface}/enable", response_model=WolInterface)
async def enable_wol(interface: str, _: dict = Depends(verify_token)):
    """Let a magic packet to this interface wake the host."""
    _refuse_local()
    return await asyncio.to_thread(_set_wol, validate_interface_name(interface), True)


@router.post("/wol/{interface}/disable", response_model=WolInterface)
async def disable_wol(interface: str, _: dict = Depends(verify_token)):
    """Let nothing that reaches this interface wake the host."""
    _refuse_local()
    return await asyncio.to_thread(_set_wol, validate_interface_name(interface), False)


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

import subprocess
import json
import logging
import math
import os
import stat
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timedelta, UTC
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Annotated, Literal, Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import host, paths, state_files
from . import schedule
from ..audit.changes import audit_change, changed_fields
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.utils import verify_token
from ..openapi import BODY, RATE_LIMITED, answer, errors
from ..security.validators import INTERFACE_NAME_PATTERN, validate_interface_name
from ..times import UtcDatetime, utc_now

logger = logging.getLogger(__name__)

# Rate limiter for power endpoints
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/power", tags=["power"])

CONFIG_FILE = str(paths.POWER_CONFIG)
UPS_CONFIG_FILE = str(paths.UPS_CONFIG)
# The Wake-on-LAN state an administrator chose per interface name, applied
# again at every start of the supervisor (reapply_wol_settings, ADR-0233).
WOL_FILE = str(paths.WOL_CONFIG)
# One Wake-on-LAN change at a time: each reads the file, changes it and writes it.
_wol_lock = threading.Lock()

# Reboot, shutdown, cancel, suspend and Wake-on-LAN act on the host through
# src/host.py (ADR-0233). With UNOS_HOST_EXEC=local the supervisor runs its
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
_ups_monitor_task: asyncio.Task | None = None
UPS_CHECK_INTERVAL = 30  # Check every 30 seconds
# The monitor's last check of the UPS; None before its first one and while the UPS settings are
# off (ADR-0356). Kept in RAM only.
_ups_last_check: Optional["UPSStatus"] = None

# NUT's upsc runs on the host (ADR-0356), under the host's timeout (ADR-0258): at most
# UPSC_TIMEOUT seconds, SIGKILL UPSC_KILL_AFTER seconds later; nsenter is waited for UPSC_WAIT.
UPSC_TIMEOUT = 10
UPSC_KILL_AFTER = 5
UPSC_WAIT = UPSC_TIMEOUT + UPSC_KILL_AFTER + 5
UPSC_MISSING = "The host has no NUT client: upsc was not found. Install the package nut-client on the host"
UPS_PASSWORD_WITHOUT_USERNAME = "The NUT password needs a username"
# A UPS of NUT and its server, as upsc takes them in <ups>@<host>:<port>; they reach upsc on the
# host as one argument (ADR-0293): a name of ups.conf, and a host name, IPv4 address or IPv6
# address in brackets.
UPS_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
UPS_HOST_PATTERN = r"^(?:[A-Za-z0-9][A-Za-z0-9.-]{0,252}|\[[0-9A-Fa-f:.]{2,45}\])$"


class PowerConfig(BaseModel):
    """The power settings: the scheduled shutdown, the wake and the idle shutdown, each off by
    default. The times are on the clock of the host's time zone."""
    # supervisor/src/power/schedule.py acts on them (ADR-0407).

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "wake_on_lan": True, "auto_shutdown": True, "shutdown_hour": 23, "shutdown_minute": 0,
        "auto_wakeup": True, "wakeup_hour": 7, "wakeup_minute": 0, "idle_shutdown": False,
        "idle_timeout_minutes": 30,
    }]})

    wake_on_lan: bool = Field(True, description=(
        "Kept, but nothing acts on it: Wake-on-LAN is turned on and off per interface (POST /power/wol/...)."))
    auto_shutdown: bool = Field(False, description=(
        "Whether the scheduled shutdown is on: daily at shutdown_hour:shutdown_minute, announced 10 minutes ahead "
        "and postponed while a running job runs."))
    shutdown_hour: int = Field(default=23, ge=0, le=23, description="The hour of the scheduled shutdown, 0 to 23.")
    shutdown_minute: int = Field(default=0, ge=0, le=59, description="The minute of the scheduled shutdown, 0 to 59.")
    auto_wakeup: bool = Field(False, description=(
        "Whether the wake is on: the RTC's wake alarm is kept at wakeup_hour:wakeup_minute, which must come at "
        "least 10 minutes after the shutdown time when the scheduled shutdown is on too."))
    wakeup_hour: int = Field(default=7, ge=0, le=23, description="The hour of the wake, 0 to 23.")
    wakeup_minute: int = Field(default=0, ge=0, le=59, description="The minute of the wake, 0 to 59.")
    idle_shutdown: bool = Field(False, description=(
        "Whether the idle shutdown is on: the host shuts down once it has been idle for idle_timeout_minutes."))
    idle_timeout_minutes: int = Field(default=30, ge=20, le=1440, description=(
        "The minutes the host must be idle before the idle shutdown, 20 to 1440."))

    @property
    def acts(self) -> bool:
        """Whether a setting that acts on the host is on."""
        return self.auto_shutdown or self.auto_wakeup or self.idle_shutdown


class UPSSettings(BaseModel):
    """The UPS settings without the NUT password, which no answer shows (ADR-0356)."""
    enabled: bool = Field(False, description="Whether the UPS monitor reads the UPS, every 30 seconds.")
    host: str = Field(default="localhost", pattern=UPS_HOST_PATTERN, description=(
        "The host of the NUT server (upsd): a host name, an IPv4 address, or an IPv6 address in brackets."))
    port: int = Field(default=3493, ge=1, le=65535, description="upsd's TCP port.")
    ups_name: str = Field(default="ups", pattern=UPS_NAME_PATTERN, description=(
        "The UPS's name on the NUT server, as its ups.conf names it."))
    username: str | None = Field(None, description="The NUT user; null for none.")
    # The monitor's shutdown runs only outside a container (is_in_container).
    auto_shutdown: bool = Field(True, description=(
        "Whether the UPS monitor shuts the host down while the UPS is on battery at or below "
        "shutdown_battery_percent or shutdown_runtime_minutes, or reports a low battery. It does so only where "
        "the supervisor runs outside a container, never in UNOS's own."))
    shutdown_battery_percent: int = Field(20, description="The battery charge, in percent, for auto_shutdown.")
    shutdown_runtime_minutes: int = Field(5, description="The battery runtime left, in minutes, for auto_shutdown.")

    @property
    def ups_identifier(self) -> str:
        """The UPS as upsc names it."""
        return f"{self.ups_name}@{self.host}:{self.port}"


class UPSConfig(UPSSettings):
    """The UPS settings' file, with the NUT password: kept out of repr() and so out of the logs."""
    password: str | None = Field(default=None, repr=False, description="The NUT password; null for none.")


class UPSConfigRequest(UPSSettings):
    """The new UPS settings. The NUT password is write-only: without one (absent, null or empty)
    the stored one stays, and a request without a username stores none."""
    # ADR-0356.

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "enabled": True, "host": "localhost", "port": 3493, "ups_name": "ups", "username": "upsmon",
        "auto_shutdown": True, "shutdown_battery_percent": 20, "shutdown_runtime_minutes": 5,
        "password": "example-password",
    }]})

    password: str | None = Field(default=None, repr=False, description=(
        "The NUT password, which needs a username; absent, null or empty keeps the stored one."))


class UPSConfigAnswer(UPSSettings):
    """The UPS settings, and whether a NUT password is stored."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "enabled": True, "host": "localhost", "port": 3493, "ups_name": "ups", "username": "upsmon",
        "auto_shutdown": True, "shutdown_battery_percent": 20, "shutdown_runtime_minutes": 5,
        "has_password": True,
    }]})

    has_password: bool = Field(False, description="Whether a NUT password is stored; no answer shows it.")


class UPSStatus(BaseModel):
    """The UPS's state, as upsc on the host reads it from the NUT server."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "available": True, "reason": None, "name": "ups", "status": "OL", "charge_percent": 100,
        "runtime_minutes": 42, "load_percent": 18, "input_voltage": 230.0, "output_voltage": 230.0,
        "manufacturer": "Example Corp", "model": "Example UPS 1500", "battery_type": "PbAc",
        "last_checked": "2026-11-19T10:30:00.412093Z",
    }]})

    available: bool = Field(description="Whether upsc read the UPS.")
    reason: str | None = Field(None, description=(
        "Why it could not: the host has no upsc, upsc gave no answer in time, or what it reported; null when it read it."))
    name: str | None = Field(None, description="The UPS's name of the settings; null when it could not be read.")
    status: str | None = Field(None, description=(
        "NUT's status flags, such as OL (online), OB (on battery) and LB (low battery); empty when upsc gave none, "
        "null when the UPS could not be read."))
    charge_percent: int | None = Field(None, description="The battery's charge in percent; null when upsc gave none.")
    runtime_minutes: int | None = Field(None, description=(
        "The battery runtime left, in whole minutes; null when upsc gave none."))
    load_percent: int | None = Field(None, description="The load in percent of the UPS's capacity; null when upsc gave none.")
    input_voltage: float | None = Field(None, description="The input voltage in volts; null when upsc gave none.")
    output_voltage: float | None = Field(None, description="The output voltage in volts; null when upsc gave none.")
    manufacturer: str | None = Field(None, description="The UPS's manufacturer; null when upsc gave none.")
    model: str | None = Field(None, description="The UPS's model; null when upsc gave none.")
    battery_type: str | None = Field(None, description="The battery's chemistry, such as PbAc; null when upsc gave none.")
    last_checked: UtcDatetime | None = Field(None, description="When upsc was run, in UTC.")


class UPSMonitorStatus(BaseModel):
    """The UPS monitor, and what its last check of the UPS found."""
    # GET /power/ups/monitor/status (ADR-0356).

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "running": True, "enabled": True, "available": False,
        "reason": "The host has no NUT client: upsc was not found. Install the package nut-client on the host",
        "last_checked": "2026-11-19T10:30:00.412093Z", "auto_shutdown": True, "shutdown_battery_percent": 20,
        "shutdown_runtime_minutes": 5,
    }]})

    running: bool = Field(description="Whether the UPS monitor runs.")
    enabled: bool = Field(description="Whether the UPS settings are enabled.")
    available: bool = Field(description=(
        "Whether the monitor's last check read the UPS; false before its first check and while the settings are off."))
    reason: str | None = Field(None, description=(
        "Why the last check could not read the UPS; null when it could, before the first check and while the "
        "settings are off."))
    last_checked: UtcDatetime | None = Field(None, description=(
        "When the last check ran, in UTC; null before the first check and while the settings are off."))
    auto_shutdown: bool = Field(description="auto_shutdown of the UPS settings.")
    shutdown_battery_percent: int = Field(description="shutdown_battery_percent of the UPS settings.")
    shutdown_runtime_minutes: int = Field(description="shutdown_runtime_minutes of the UPS settings.")


class PowerStatus(BaseModel):
    """How long the host has been up, and its load."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "uptime": "3d 4h 12m", "uptime_seconds": 274320, "last_boot": "2026-11-16T06:18:41Z",
        "load_average": [0.42, 0.35, 0.3],
    }]})

    uptime: str = Field(description="How long the host has been up, such as 3d 4h 12m, 4h 12m or 12m.")
    uptime_seconds: int = Field(description="The same, in whole seconds.")
    last_boot: UtcDatetime = Field(description="When the host booted, in UTC, to the second.")
    load_average: list[float] = Field(description="The host's load averages over 1, 5 and 15 minutes.")


class PendingAction(BaseModel):
    """The reboot or shutdown that logind has scheduled on the host; both null when there is none."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"action": "shutdown", "at": "2026-11-19T22:01:00Z"}]})

    action: Literal["reboot", "shutdown"] | None = Field(None, description=(
        "reboot or shutdown; null when nothing is pending."))
    at: UtcDatetime | None = Field(None, description=(
        "When logind acts, in UTC; null when nothing is pending, or after a reboot or shutdown when it could not "
        "be read."))


class PowerAnswer(BaseModel):
    """What a reboot, shutdown, cancel or suspend did."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "scheduled", "message": "The host reboots in 1 minute",
        "pending": {"action": "reboot", "at": "2026-11-19T10:31:00Z"},
    }]})

    status: Literal["scheduled", "cancelled", "none", "suspending"] = Field(description=(
        "scheduled (a reboot or shutdown), cancelled, none (nothing was pending to cancel) or suspending."))
    message: str = Field(description="What happened, in English.")
    pending: PendingAction | None = Field(None, description=(
        "What is pending after a reboot, shutdown or cancel; null after a suspend."))


class WolInterface(BaseModel):
    """An Ethernet interface of the host and its Wake-on-LAN state, as ethtool reports it."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "eth0", "mac_address": "00:00:5e:00:53:01", "supported": True, "wol": "g", "enabled": True,
    }]})

    name: str = Field(description="The interface's name, such as eth0 or enp3s0.")
    mac_address: str | None = Field(None, description="Its MAC address; null when ip reports none.")
    supported: bool = Field(description=(
        "Whether it can wake the host by magic packet: g in ethtool's Supports Wake-on."))
    wol: str = Field(description="ethtool's Wake-on letters, d when nothing wakes the host; empty when ethtool shows none.")
    enabled: bool = Field(description="Whether a magic packet wakes the host.")


class WolStatus(BaseModel):
    """The host's Ethernet interfaces and their Wake-on-LAN state."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"interfaces": [
        {"name": "eth0", "mac_address": "00:00:5e:00:53:01", "supported": True, "wol": "g", "enabled": True},
        {"name": "eth1", "mac_address": "00:00:5e:00:53:02", "supported": False, "wol": "d", "enabled": False},
    ]}]})

    interfaces: list[WolInterface] = Field(description=(
        "The physical Ethernet interfaces: no loopback, veth, bridge, bond, VLAN or tunnel."))


def load_config() -> PowerConfig:
    """The power settings; the defaults without a file, or for one kept as .bad (ADR-0324)."""
    return state_files.load_json(Path(CONFIG_FILE), PowerConfig.model_validate, PowerConfig, what="the power settings")


def save_config(config: PowerConfig) -> None:
    """Replace the power settings' file (ADR-0324)."""
    state_files.write_json(Path(CONFIG_FILE), config.model_dump(), sync=False)


def _read_ups_config(data: object) -> UPSConfig:
    """The decoded UPS settings' file. A refusal names the fields and why, never their values:
    load_json logs it, and pydantic's own message repeats the values, the password among them
    (ADR-0356)."""
    try:
        return UPSConfig.model_validate(data)
    except ValidationError as exc:
        fields = "; ".join(f"{'.'.join(str(part) for part in error['loc']) or 'the file'}: {error['type']}"
                           for error in exc.errors())
        raise ValueError(f"it holds no valid UPS settings ({fields})") from None


def load_ups_config() -> UPSConfig:
    """The UPS settings; the defaults without a file, or for one kept as .bad (ADR-0324)."""
    return state_files.load_json(Path(UPS_CONFIG_FILE), _read_ups_config, UPSConfig, what="the UPS settings")


def saved_ups_config() -> UPSConfig | None:
    """The UPS settings that were saved; None while there are none (no file, or one kept as .bad)."""
    return state_files.load_json(Path(UPS_CONFIG_FILE), _read_ups_config, lambda: None, what="the UPS settings")


def save_ups_config(config: UPSConfig) -> None:
    """Replace the UPS settings' file (ADR-0324); it holds the NUT password, so only its owner reads it."""
    state_files.write_json(Path(UPS_CONFIG_FILE), config.model_dump(), sync=False, mode=0o600)


def narrow_ups_config_mode() -> None:
    """Give the UPS settings' file, and the one kept as .bad, the mode 0600 of save_ups_config when
    they have another one, such as the 0644 that the version before ADR-0356 gave the file with the
    NUT password, or a file put in place by hand. A file of mode 0600 is left as it is (ADR-0214).
    Logs a failure, never raises: the supervisor starts."""
    path = Path(UPS_CONFIG_FILE)
    for file in (path, state_files.bad_path(path)):
        try:
            if stat.S_IMODE(os.stat(file).st_mode) != 0o600:
                os.chmod(file, 0o600)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not make %s readable by its owner only: %s", file, exc)


def _upsc(args: list[str]) -> tuple[str | None, str | None]:
    """Run NUT's upsc with ARGS on the host (ADR-0356): its output and None, or None and why it
    could not run or what it reported. Blocking: run it off the event loop."""
    cmd = ["timeout", f"--kill-after={UPSC_KILL_AFTER}", str(UPSC_TIMEOUT), "upsc", *args]
    try:
        result = host.run_on_host(cmd, timeout=UPSC_WAIT, errors="replace")
    except subprocess.TimeoutExpired:
        return None, f"upsc did not answer within {UPSC_WAIT} seconds"
    except OSError as exc:
        return None, f"Could not run upsc: {exc}"
    if result.returncode == 127:  # timeout(1) found no upsc
        return None, UPSC_MISSING
    if result.returncode in (124, 137):  # ended by timeout(1), with SIGKILL after --kill-after
        return None, f"upsc did not answer within {UPSC_TIMEOUT} seconds"
    if not result.ok:
        output = " ".join((result.stderr or result.stdout).split())[:300]
        return None, f"upsc could not read {args[-1]}: {output or f'exit status {result.returncode}'}"
    return result.stdout, None


def _ups_number(data: dict[str, str], key: str) -> float | None:
    """The variable KEY of upsc's answer as a number, with decimals or without ("100", "23.4"); None
    when upsc gave none or one that is no finite number. Each value stands on its own: one that
    cannot be read leaves the others, and the UPS stays available (ADR-0356)."""
    try:
        number = float(data[key])
    except (KeyError, ValueError):
        return None
    return number if math.isfinite(number) else None


def get_ups_status(config: UPSSettings) -> UPSStatus:
    """The UPS's state, as upsc on the host reads it from upsd; unavailable with the reason when it
    cannot be read. Blocking: run it off the event loop."""
    checked = utc_now()
    output, reason = _upsc([config.ups_identifier])
    if output is None:
        return UPSStatus(available=False, reason=reason, last_checked=checked)
    data = {}
    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip()
    charge = _ups_number(data, "battery.charge")
    runtime = _ups_number(data, "battery.runtime")
    load = _ups_number(data, "ups.load")
    return UPSStatus(
        available=True,
        name=config.ups_name,
        status=data.get("ups.status", ""),  # OL (online), OB (on battery), LB (low battery)
        charge_percent=int(charge) if charge is not None else None,
        runtime_minutes=int(runtime / 60) if runtime is not None else None,
        load_percent=int(load) if load is not None else None,
        input_voltage=_ups_number(data, "input.voltage"),
        output_voltage=_ups_number(data, "output.voltage"),
        manufacturer=data.get("ups.mfr"),
        model=data.get("ups.model"),
        battery_type=data.get("battery.type"),
        last_checked=checked,
    )


def _log_ups_change(previous: UPSStatus | None, status: UPSStatus, config: UPSSettings) -> None:
    """Log a check of the monitor only when its outcome changes: a host without upsc gets one
    line, not one every UPS_CHECK_INTERVAL seconds (ADR-0356)."""
    if status.reason == (previous.reason if previous is not None else None):
        return
    if status.reason is None:
        logger.info("The UPS monitor reads %s again", config.ups_identifier)
    else:
        logger.warning("The UPS monitor cannot read %s: %s", config.ups_identifier, status.reason)


async def ups_monitor_loop():
    """Every UPS_CHECK_INTERVAL seconds while the UPS settings are enabled: read the UPS and shut
    the host down on low battery.

    While the UPS cannot be read (no upsc on the host, upsd out of reach) the monitor only keeps
    the reason for GET /power/ups/monitor/status: it shuts nothing down, writes nothing and logs
    the reason when it changes (ADR-0356). It reads the UPS once the host has upsc again.
    """
    global _ups_last_check

    while _ups_monitor_running:
        try:
            config = load_ups_config()
            if not config.enabled:
                _ups_last_check = None
                await asyncio.sleep(UPS_CHECK_INTERVAL)
                continue

            status = await asyncio.to_thread(get_ups_status, config)
            _log_ups_change(_ups_last_check, status, config)
            _ups_last_check = status

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
                    logger.warning("UPS auto-shutdown triggered: %s", reason)
                    subprocess.Popen(["shutdown", "-h", "+1", f"UPS auto-shutdown: {reason}"])

        except Exception as e:
            logger.warning("UPS monitor error: %s", e)

        await asyncio.sleep(UPS_CHECK_INTERVAL)


def is_in_container() -> bool:
    """Check if running inside a Docker container.

    Only the UPS monitor's shutdown checks it, which therefore never runs
    in the supervisor's container (system.ups-monitoring). The power actions
    reach the host through src/host.py instead (ADR-0233).
    """
    return os.path.exists("/.dockerenv") or os.environ.get("container") == "docker"


# The 503 of every route that acts on the host or reads it with a host command (_refuse_local).
_POWER_OFF = {
    503: "Power actions are off: this supervisor runs its host commands on its own machine (UNOS_HOST_EXEC=local).",
}
_WOL_ERRORS = {
    400: "The name is not a valid interface name.",
    404: "The host has no physical Ethernet interface of that name.",
    409: "The interface does not support Wake-on-LAN by magic packet.",
    500: "ip cannot list the interfaces, ethtool could not run, gave no answer in time or failed to change the "
         "setting, or the interface's Wake-on-LAN state did not change.",
}
_INTERFACE = Annotated[str, PathParam(description="The name of an Ethernet interface of the host, such as eth0.")]


@router.get(
    "/status", response_model=PowerStatus, summary="Read the host's uptime and load",
    responses=errors({500: "The host's uptime or load average cannot be read."}),
)
async def get_power_status(_: dict = Depends(verify_token)):
    """How long the host has been up, when it booted, and its load averages."""
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

        last_boot = (utc_now() - timedelta(seconds=uptime_seconds)).replace(microsecond=0)

        return PowerStatus(
            uptime=uptime,
            uptime_seconds=int(uptime_seconds),
            last_boot=last_boot,
            load_average=load_average
        )
    except Exception as e:
        logger.exception("Could not get power status")
        raise HTTPException(500, f"Failed to get power status: {e}")


@router.get("/config", response_model=PowerConfig, summary="Read the power settings")
async def get_power_config(_: dict = Depends(verify_token)):
    """The power settings: the scheduled shutdown, the wake and the idle shutdown."""
    return load_config()


@router.put(
    "/config", response_model=PowerConfig, summary="Replace the power settings",
    responses=errors(BODY, {
        400: "The wake comes less than 10 minutes after the scheduled shutdown.",
        409: "The wake was turned on or moved, and the host's RTC cannot wake the host: it has none, it has no "
             "wake alarm, its clock is more than 5 minutes off, or it cannot be read.",
        500: "The RTC did not take or keep the wake alarm, or the alarm could not be cleared.",
        503: "The scheduled shutdown, the wake or the idle shutdown is on, and this supervisor runs its host "
             "commands on its own machine (UNOS_HOST_EXEC=local).",
    }),
)
async def update_power_config(request: Request, config: PowerConfig, user=Depends(verify_token)):
    """Replace the power settings, and answer them. The RTC's wake alarm is set or cleared before
    the settings are stored; the settings are written, and the audit log gets settings_changed,
    only when they change. A setting that acts on the host is refused where host commands run on
    the supervisor's own machine.
    \f
    ADR-0214, ADR-0402, ADR-0407.
    """
    schedule.check_times(config)
    if config.acts:
        _refuse_local()
    saved = load_config()
    armed = await schedule.apply_wake(saved, config)
    changed = changed_fields(saved.model_dump(), config.model_dump())
    if changed:
        try:
            save_config(config)
        except Exception:
            if armed:  # no alarm for a wake that was not stored
                await schedule.undo_wake()
            raise
        await audit_change(AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="power",
                           details=f"Power settings changed: {changed}")
    return config


@router.get(
    "/schedule", response_model=schedule.PowerSchedule, summary="Read the power schedule",
    responses=errors(_POWER_OFF),
)
async def get_power_schedule(_: dict = Depends(verify_token)):
    """The next scheduled shutdown and what holds it, the next wake and the RTC's wake alarm, and
    whether the host is idle and why not. Reads the host and changes nothing.
    \f
    ADR-0407.
    """
    _refuse_local()
    return await schedule.status()


# ==================== Reboot, shutdown and suspend (ADR-0233) ====================


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
        at = datetime.fromtimestamp(int(usec) / 1_000_000, tz=UTC) if pending and usec else None
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


def _cancel() -> tuple[PowerAnswer, str | None]:
    """Cancel the pending reboot or shutdown, if there is one; the answer, and the action that was
    cancelled (None when none was pending)."""
    pending = _read_pending()
    if pending.action is None:
        return PowerAnswer(status="none", message=NOTHING_PENDING, pending=pending), None
    result = _run(["shutdown", "-c"])
    if not result.ok:
        raise HTTPException(500, f"Could not cancel the pending {pending.action}: {_why(result)}")
    after = _read_pending()
    if after.action is not None:
        raise HTTPException(500, f"The pending {after.action} is still scheduled")
    answer = PowerAnswer(status="cancelled", message=f"The pending {pending.action} was cancelled", pending=after)
    return answer, pending.action


def _check_suspend() -> None:
    """Refuse a suspend that the host cannot do (HTTPException)."""
    try:
        states = host.read_host_file("/sys/power/state", timeout=HOST_TIMEOUT)
    except (host.HostCommandError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"Could not read the host's sleep states: {exc}")
    if "mem" not in states.split():
        raise HTTPException(409, "The host cannot suspend: its kernel offers no suspend to RAM")


def _suspend() -> PowerAnswer:
    """Suspend the host to RAM now (HTTPException when systemctl fails)."""
    result = _run(["systemctl", "suspend"])
    if not result.ok:
        raise HTTPException(500, f"Could not suspend the host: {_why(result)}")
    return PowerAnswer(status="suspending", message="The host is suspending")


@router.get(
    "/pending", response_model=PendingAction, summary="Read the pending reboot or shutdown",
    responses=errors({500: "logind's pending reboot or shutdown cannot be read.", **_POWER_OFF}),
)
async def get_pending_action(_: dict = Depends(verify_token)):
    """The reboot or shutdown pending on the host, and when it acts."""
    _refuse_local()
    return await asyncio.to_thread(_read_pending)


# The audit action of a reboot or shutdown, scheduled or cancelled (ADR-0351).
_AUDIT_ACTIONS = {"reboot": AuditAction.SYSTEM_REBOOT, "shutdown": AuditAction.SYSTEM_SHUTDOWN}


async def _schedule_and_audit(request: Request, user, action: str) -> PowerAnswer:
    """Schedule ``action`` and write its audit entry (ADR-0351). The entry is appended as soon as
    the host accepted the command, a minute before logind acts; the clean reboot or shutdown that
    follows writes the page cache out with the file systems, so the entry is on the disk when the
    host goes down."""
    answer = await asyncio.to_thread(_schedule, action)
    await audit_change(_AUDIT_ACTIONS[action], request, user.username, resource_type="host",
                       details=answer.message, severity=AuditSeverity.WARNING)
    return answer


@router.post(
    "/reboot", response_model=PowerAnswer, summary="Reboot the host in a minute",
    responses=errors(RATE_LIMITED, {500: "The host's shutdown command failed or gave no answer in time.", **_POWER_OFF}),
)
@limiter.limit("2/minute")  # Rate limit reboot requests
async def reboot_system(request: Request, user=Depends(verify_token)):
    """Reboot the host one minute from now with the host's shutdown command, which POST
    /power/cancel cancels within that minute. The audit log gets system_reboot. At most 2 a minute.
    \f
    ADR-0233, ADR-0351.
    """
    _refuse_local()
    return await _schedule_and_audit(request, user, "reboot")


@router.post(
    "/shutdown", response_model=PowerAnswer, summary="Shut the host down in a minute",
    responses=errors(RATE_LIMITED, {500: "The host's shutdown command failed or gave no answer in time.", **_POWER_OFF}),
)
@limiter.limit("2/minute")  # Rate limit shutdown requests
async def shutdown_system(request: Request, user=Depends(verify_token)):
    """Shut the host down one minute from now with the host's shutdown command, which POST
    /power/cancel cancels within that minute. The audit log gets system_shutdown. At most 2 a
    minute.
    \f
    ADR-0233, ADR-0351.
    """
    _refuse_local()
    return await _schedule_and_audit(request, user, "shutdown")


@router.post(
    "/cancel", response_model=PowerAnswer, summary="Cancel the pending reboot or shutdown",
    responses=errors({
        500: "The pending reboot or shutdown cannot be read, shutdown -c failed on the host, or the action is "
             "still pending after it.",
        **_POWER_OFF,
    }),
)
async def cancel_shutdown(request: Request, user=Depends(verify_token)):
    """Cancel the pending reboot or shutdown of the host, also one of the power settings; status
    none when nothing is pending. A cancel is written to the audit log with the action it cancelled.
    \f
    ADR-0351.
    """
    _refuse_local()
    answer, cancelled = await asyncio.to_thread(_cancel)
    if cancelled is not None:
        await audit_change(_AUDIT_ACTIONS[cancelled], request, user.username, resource_type="host",
                           details=answer.message)
    return answer


@router.post(
    "/suspend", response_model=PowerAnswer, summary="Suspend the host to RAM",
    responses=errors(RATE_LIMITED, {
        409: "The host cannot suspend: its kernel offers no suspend to RAM.",
        500: "The host's sleep states cannot be read, or systemctl suspend failed.",
        **_POWER_OFF,
    }),
)
@limiter.limit("2/minute")  # Rate limit suspend requests
async def suspend_system(request: Request, user=Depends(verify_token)):
    """Suspend the host to RAM now. The audit log gets system_suspend before systemctl runs, since the
    host may be asleep when it returns; when systemctl fails, a second entry says so, as failed. At
    most 2 a minute.
    \f
    ADR-0402.
    """
    _refuse_local()
    await asyncio.to_thread(_check_suspend)
    await audit_change(AuditAction.SYSTEM_SUSPEND, request, user.username, resource_type="host",
                       details="The host suspends now", severity=AuditSeverity.WARNING)
    try:
        return await asyncio.to_thread(_suspend)
    except HTTPException as exc:
        await audit_change(AuditAction.SYSTEM_SUSPEND, request, user.username, resource_type="host",
                           details=str(exc.detail), severity=AuditSeverity.WARNING, success=False)
        raise


# ==================== Wake-on-LAN (ADR-0233) ====================


def _read_wol(name: str, mac_address: str | None) -> WolInterface:
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
    try:
        return state_files.load_json(Path(WOL_FILE), _wol_choices, dict, what="the Wake-on-LAN settings")
    except OSError as exc:
        logger.warning("Ignoring the Wake-on-LAN settings in %s: %s", WOL_FILE, exc)
        return {}


def _wol_choices(data: object) -> dict[str, bool]:
    """The decoded Wake-on-LAN file's choices (_load_wol_choices); ValueError for no object (ADR-0324)."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return {
        name: on for name, on in data.items()
        if isinstance(name, str) and INTERFACE_NAME_PATTERN.fullmatch(name) and isinstance(on, bool)
    }


def _remember_wol(name: str, on: bool) -> bool:
    """Keep the choice for the next start; the file is written only when it changes (ADR-0214).
    Whether it changed."""
    choices = _load_wol_choices()
    if choices.get(name) is on:
        return False
    choices[name] = on
    state_files.replace_file(Path(WOL_FILE), (json.dumps(choices, indent=2, sort_keys=True) + "\n").encode(), sync=False)
    return True


def _set_wol(name: str, on: bool) -> tuple[WolInterface, bool]:
    """Turn magic-packet wake on (``wol g``) or every wake event off (``wol d``) for one interface;
    the interface, and whether its state or the choice kept for the next start changed."""
    with _wol_lock:
        return _set_wol_locked(name, on)


def _set_wol_locked(name: str, on: bool) -> tuple[WolInterface, bool]:
    current = next((iface for iface in _list_wol() if iface.name == name), None)
    if current is None:
        raise HTTPException(404, f"No network interface named {name}")
    if not current.supported:
        raise HTTPException(409, f"{name} does not support Wake-on-LAN by magic packet")
    changed = current.enabled != on
    if changed:
        state = "on" if on else "off"
        result = _run(["ethtool", "-s", name, "wol", "g" if on else "d"])
        if not result.ok:
            raise HTTPException(500, f"Could not turn Wake-on-LAN {state} for {name}: {_why(result)}")
        current = _read_wol(name, current.mac_address)
        if current.enabled != on:
            raise HTTPException(500, f"Wake-on-LAN is still {'off' if on else 'on'} on {name}")
    remembered = _remember_wol(name, on)
    return current, changed or remembered


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


@router.get(
    "/wol", response_model=WolStatus, summary="List the interfaces' Wake-on-LAN state",
    responses=errors({500: "ip cannot list the interfaces, or ethtool could not run or gave no answer in time.",
                      **_POWER_OFF}),
)
async def get_wol_status(_: dict = Depends(verify_token)):
    """The host's physical Ethernet interfaces and their Wake-on-LAN state."""
    _refuse_local()
    return WolStatus(interfaces=await asyncio.to_thread(_list_wol))


async def _set_wol_and_audit(request: Request, user, interface: str, on: bool) -> WolInterface:
    """Turn Wake-on-LAN on or off; a change is audited (ADR-0351)."""
    name = validate_interface_name(interface)
    current, changed = await asyncio.to_thread(_set_wol, name, on)
    if changed:
        await audit_change(AuditAction.NETWORK_CONFIG_CHANGED, request, user.username,
                           resource_type="network_interface", resource_id=name,
                           details=f"Wake-on-LAN turned {'on' if on else 'off'}")
    return current


@router.post(
    "/wol/{interface}/enable", response_model=WolInterface, summary="Turn Wake-on-LAN on for an interface",
    responses=errors(_WOL_ERRORS, _POWER_OFF),
)
async def enable_wol(request: Request, interface: _INTERFACE, user=Depends(verify_token)):
    """Let a magic packet to this interface wake the host. The choice is applied again at every
    start of the supervisor, and a change is written to the audit log.
    \f
    ADR-0233, ADR-0351.
    """
    _refuse_local()
    return await _set_wol_and_audit(request, user, interface, True)


@router.post(
    "/wol/{interface}/disable", response_model=WolInterface, summary="Turn Wake-on-LAN off for an interface",
    responses=errors(_WOL_ERRORS, _POWER_OFF),
)
async def disable_wol(request: Request, interface: _INTERFACE, user=Depends(verify_token)):
    """Let nothing that reaches this interface wake the host. The choice is applied again at every
    start of the supervisor, and a change is written to the audit log.
    \f
    ADR-0233, ADR-0351.
    """
    _refuse_local()
    return await _set_wol_and_audit(request, user, interface, False)


# ==================== UPS Monitoring ====================

@router.get("/ups/status", response_model=UPSStatus, summary="Read the UPS now")
async def get_ups_status_endpoint(_: dict = Depends(verify_token)):
    """The UPS's state, read now with upsc on the host: available false with the reason when it
    cannot be read, such as while the host has no upsc.
    \f
    UPSC_MISSING; ADR-0356.
    """
    config = load_ups_config()
    return await asyncio.to_thread(get_ups_status, config)


def _ups_config_answer(config: UPSConfig) -> UPSConfigAnswer:
    """The settings without the NUT password, and whether one is stored (ADR-0356)."""
    return UPSConfigAnswer(**config.model_dump(exclude={"password"}), has_password=bool(config.password))


@router.get("/ups/config", response_model=UPSConfigAnswer, summary="Read the UPS settings")
async def get_ups_config(_: dict = Depends(verify_token)):
    """The UPS settings, without the NUT password: has_password says whether one is stored."""
    return _ups_config_answer(load_ups_config())


@router.put(
    "/ups/config", response_model=UPSConfigAnswer, summary="Replace the UPS settings",
    responses=errors(BODY, {400: "A password comes without a username."}),
)
async def update_ups_config(http_request: Request, request: UPSConfigRequest, user=Depends(verify_token)):
    """Replace the UPS settings, and answer them without the password; they are written, and the
    audit log gets settings_changed, only when they change.

    The NUT password is write-only: a request without one (absent, null or empty) keeps the stored
    one, a request without a username stores none, and a password without a username is refused.
    \f
    ADR-0214, ADR-0356, ADR-0402.
    """
    username = request.username or None
    if request.password and username is None:
        raise HTTPException(400, UPS_PASSWORD_WITHOUT_USERNAME)
    saved = saved_ups_config()
    if username is None:
        password = None
    elif request.password:
        password = request.password
    else:
        password = saved.password if saved is not None else None
    config = UPSConfig(**request.model_dump(exclude={"username", "password"}), username=username,
                       password=password)
    # The settings in force are the defaults while none are saved: the file is written, and the audit
    # log gets settings_changed, for the fields that differ from them. The credentials come by name
    # only, never their values (ADR-0214, ADR-0402).
    changed = changed_fields((saved or UPSConfig()).model_dump(), config.model_dump(),
                             shown=set(UPSSettings.model_fields) - {"username"})
    if changed:
        save_ups_config(config)
        await audit_change(AuditAction.SETTINGS_CHANGED, http_request, user.username, resource_type="ups",
                           details=f"UPS settings changed: {changed}")
    return _ups_config_answer(config)


@router.post(
    "/ups/test", summary="Test the connection to the UPS",
    responses={
        **answer({"status": "connected", "ups_name": "ups", "model": "Example UPS 1500",
                  "manufacturer": "Example Corp", "battery_charge": 100},
                 "status connected; ups_name: the UPS's name of the settings; model and manufacturer: the UPS's; "
                 "battery_charge: the battery's charge in percent; model, manufacturer and battery_charge are "
                 "null when upsc gave none."),
        **errors({503: "upsc cannot read the UPS: the host has none, it could not run or gave no answer in time, "
                       "or it reported an error, which the detail names."}),
    },
)
async def test_ups_connection(_: dict = Depends(verify_token)):
    """Read the UPS of the settings now, with upsc on the host."""
    config = load_ups_config()
    status = await asyncio.to_thread(get_ups_status, config)

    if status.available:
        return {
            "status": "connected",
            "ups_name": status.name,
            "model": status.model,
            "manufacturer": status.manufacturer,
            "battery_charge": status.charge_percent
        }
    else:
        raise HTTPException(503, f"Unable to connect to the UPS: {status.reason}")


@router.get(
    "/ups/list", summary="List the UPSs of the NUT server",
    responses=answer(
        {"devices": ["ups"]},
        "devices: the names of the UPSs that the NUT server serves. When they cannot be read, devices is empty "
        "and error says why: the host has no upsc, it could not run or gave no answer in time, or what it reported.",
    ),
)
async def list_ups_devices(_: dict = Depends(verify_token)):
    """The UPSs that the NUT server (upsd) of the settings' host and port serves, read with upsc on
    the host; an empty list and the error when they cannot be read."""
    config = load_ups_config()
    output, reason = await asyncio.to_thread(_upsc, ["-l", f"{config.host}:{config.port}"])
    if output is None:
        return {"devices": [], "error": reason}
    return {"devices": [line.strip() for line in output.splitlines() if line.strip()]}


@router.get("/ups/monitor/status", response_model=UPSMonitorStatus, summary="Read the UPS monitor's status")
async def get_ups_monitor_status(_: dict = Depends(verify_token)):
    """Whether the UPS monitor runs, and whether its last check read the UPS, or why not.
    \f
    ADR-0356.
    """
    config = load_ups_config()
    check = _ups_last_check if config.enabled else None
    return UPSMonitorStatus(
        running=_ups_monitor_running,
        enabled=config.enabled,
        available=check is not None and check.available,
        reason=check.reason if check is not None else None,
        last_checked=check.last_checked if check is not None else None,
        auto_shutdown=config.auto_shutdown,
        shutdown_battery_percent=config.shutdown_battery_percent,
        shutdown_runtime_minutes=config.shutdown_runtime_minutes,
    )


async def start_ups_monitor():
    """Start the UPS monitor on app startup, after narrowing the mode of the UPS settings' file."""
    global _ups_monitor_running, _ups_monitor_task
    narrow_ups_config_mode()
    _ups_monitor_running = True
    _ups_monitor_task = asyncio.create_task(ups_monitor_loop())


async def stop_ups_monitor():
    """Stop the UPS monitor on app shutdown."""
    global _ups_monitor_running, _ups_monitor_task
    _ups_monitor_running = False
    if _ups_monitor_task:
        _ups_monitor_task.cancel()

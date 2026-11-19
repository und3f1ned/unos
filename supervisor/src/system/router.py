import platform
import subprocess
import psutil
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from .. import host
from ..times import UtcDatetime, from_epoch
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction
from ..auth.utils import require_admin, verify_token
from ..host import HostResult
from ..openapi import ADMIN, BODY, SIGNED_IN, answer, errors
from ..security.validators import validate_timezone

# Read-only endpoints are available to every signed-in user;
# anything that changes the host requires an administrator. Host commands go through
# src/host.py only (ADR-0416): tests/test_host_command_layer.py fails for one that does not.
router = APIRouter(prefix="/system", tags=["system"])


def get_distro_name() -> str:
    """Get the Linux distribution name from the host's /etc/os-release."""
    # Try host's os-release first (mounted at /host/os-release)
    for path in ["/host/os-release", "/etc/os-release"]:
        try:
            with open(path, "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        # Remove PRETTY_NAME=" and trailing "
                        name = line.strip().split("=", 1)[1].strip('"')
                        return name
        except Exception:
            continue
    # Fallback to platform.system()
    return platform.system()


async def get_host_hostname() -> str:
    """The host's name, as `hostname` prints it on the host: a name set since the container
    started too (ADR-0331). The container's when the host cannot answer."""
    try:
        result = await host.run_on_host_async(["hostname"], timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return platform.node()


class SystemInfo(BaseModel):
    """What the host is and when it booted."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "hostname": "nas", "kernel": "6.1.115-vendor-rk35xx", "os": "Debian GNU/Linux 13 (trixie)",
        "architecture": "aarch64", "boot_time": "2026-11-18T07:12:03Z",
    }]})

    hostname: str = Field(description="The host's name; the container's when the host cannot tell.")
    kernel: str = Field(description="The release of the host's Linux kernel.")
    os: str = Field(description="The host's distribution, as its /etc/os-release names it.")
    architecture: str = Field(description="The host's machine architecture, such as x86_64 or aarch64.")
    boot_time: UtcDatetime = Field(description="When the host booted, in UTC.")  # ADR-0401


@router.get(
    "/info", response_model=SystemInfo, summary="Read the host's name, system and boot time",
    responses=errors(SIGNED_IN),
)
async def get_system_info(_=Depends(verify_token)):
    """The host's name, kernel, distribution, architecture and boot time. Any signed-in user."""
    return SystemInfo(
        hostname=await get_host_hostname(),
        kernel=platform.release(),
        os=get_distro_name(),
        architecture=platform.machine(),
        boot_time=from_epoch(psutil.boot_time()),
    )


class TimezoneUpdate(BaseModel):
    """The host's new time zone."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"timezone": "Europe/Berlin"}]})

    timezone: str = Field(description="A time zone that the host knows, as GET /system/timezones lists them.")


async def _timedatectl(*args: str) -> HostResult | None:
    """timedatectl on the host (ADR-0020, ADR-0331): in the container it has no systemd to ask.
    It runs under the host's timeout, so that it never acts after the answer (ADR-0258). None
    when it gave no answer in time."""
    try:
        result = await host.run_on_host_async(["timeout", "10", "timedatectl", *args], timeout=15)
    except subprocess.TimeoutExpired:
        return None
    except OSError as exc:  # no program to start: the stand-in, which runs commands as they are
        return HostResult(127, "", str(exc))
    return None if result.returncode == 124 else result


async def _host_zone() -> str:
    """The host's time zone; "Unknown" when timedatectl cannot tell."""
    result = await _timedatectl("show", "--property=Timezone", "--value")
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "Unknown"


@router.get(
    "/timezone", summary="Read the host's time zone",
    responses={**answer({"timezone": "Europe/Berlin"}, "The host's time zone, or Unknown."), **errors(SIGNED_IN)},
)
async def get_timezone(_=Depends(verify_token)):
    """The host's time zone; "Unknown" when timedatectl cannot tell. Any signed-in user."""
    return {"timezone": await _host_zone()}


async def _zones() -> list[str]:
    result = await _timedatectl("list-timezones")
    if result is None or result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


@router.get(
    "/timezones", summary="List the time zones the host knows",
    responses={
        **answer({"timezones": ["Africa/Abidjan", "Europe/Berlin", "UTC"]},
                 "The time zones, in timedatectl's order; none when timedatectl cannot tell."),
        **errors(SIGNED_IN),
    },
)
async def list_timezones(_=Depends(verify_token)):
    """The time zones the host knows. Any signed-in user."""
    return {"timezones": await _zones()}


@router.put(
    "/timezone", summary="Set the host's time zone",
    responses={
        **answer({"status": "ok", "timezone": "Europe/Berlin"}, "The zone that the host has now."),
        **errors(ADMIN, BODY, {
            400: "The zone is empty, too long, not a zone's name or not one that the host knows.",
            500: "timedatectl failed or gave no answer in time.",
        }),
    },
)
async def set_timezone(request: Request, data: TimezoneUpdate, user=Depends(require_admin)):
    """Set the host's time zone; the Scheduler follows it. A zone other than the one the host had
    is written to the audit log. Administrators only.
    \f
    ADR-0264, ADR-0351.
    """
    zones = set(await _zones())
    validated_tz = validate_timezone(data.timezone, zones or None)
    old = await _host_zone()
    result = await _timedatectl("set-timezone", validated_tz)
    if result is None or result.returncode != 0:
        reason = "no answer in time" if result is None else (result.stderr.strip() or f"exit status {result.returncode}")
        raise HTTPException(500, detail=f"Failed to set the time zone: {reason}")
    if old != validated_tz:
        await audit_change(
            AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="host",
            details=f"Time zone set to {validated_tz}" if old == "Unknown"
            else f"Time zone changed from {old} to {validated_tz}",
        )
    return {"status": "ok", "timezone": validated_tz}


class TemperatureSensor(BaseModel):
    """One temperature sensor, in degrees Celsius."""

    label: str = Field(description="The sensor's label; Sensor <n> when the driver gives none.")
    current: float = Field(description="The temperature now.")
    high: float | None = Field(None, description="The driver's high threshold; null when it gives none.")
    critical: float | None = Field(None, description="The driver's critical threshold; null when it gives none.")


class TemperatureGroup(BaseModel):
    """The sensors of one driver."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "soc_thermal",
        "sensors": [{"label": "Sensor 0", "current": 41.5, "high": None, "critical": 115.0}],
    }]})

    name: str = Field(description="The driver's name, such as coretemp or soc_thermal.")
    sensors: list[TemperatureSensor] = Field(description="Its sensors.")


@router.get(
    "/temperatures", response_model=list[TemperatureGroup], summary="Read the host's temperature sensors",
    responses=errors(SIGNED_IN),
)
async def get_temperatures(_=Depends(verify_token)):
    """The host's temperature sensors by driver; none when the host has none or they cannot be
    read. Any signed-in user."""
    groups = []
    try:
        sensors = psutil.sensors_temperatures()
        for name, entries in sensors.items():
            sensor_list = []
            for entry in entries:
                sensor_list.append(TemperatureSensor(
                    label=entry.label or f"Sensor {len(sensor_list)}",
                    current=entry.current,
                    high=entry.high if entry.high and entry.high > 0 else None,
                    critical=entry.critical if entry.critical and entry.critical > 0 else None
                ))
            if sensor_list:
                groups.append(TemperatureGroup(
                    name=name,
                    sensors=sensor_list
                ))
    except Exception:
        pass

    return groups

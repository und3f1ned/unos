import platform
import subprocess
import json
import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..auth.utils import require_admin, verify_token
from ..security.validators import validate_hostname, validate_timezone, validate_interface_name

# Read-only endpoints are available to every signed-in user;
# anything that changes the host requires an administrator.
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


def get_host_hostname() -> str:
    """Get the host's hostname."""
    # Try host's hostname first (mounted at /host/hostname)
    try:
        with open("/host/hostname", "r") as f:
            return f.read().strip()
    except Exception:
        pass
    # Fallback to platform.node()
    return platform.node()


class SystemInfo(BaseModel):
    hostname: str
    kernel: str
    os: str
    architecture: str
    boot_time: float  # when the host booted, in seconds since the epoch


class SystemStats(BaseModel):
    cpu_percent: float
    cpu_count: int
    memory_total: int
    memory_used: int
    memory_percent: float
    disk_total: int
    disk_used: int
    disk_percent: float
    network_sent: int
    network_recv: int
    temperatures: dict


@router.get("/info", response_model=SystemInfo)
async def get_system_info(_=Depends(verify_token)):
    return SystemInfo(
        hostname=get_host_hostname(),
        kernel=platform.release(),
        os=get_distro_name(),
        architecture=platform.machine(),
        boot_time=psutil.boot_time(),
    )


@router.get("/stats", response_model=SystemStats)
async def get_system_stats(_=Depends(verify_token)):
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()

    temps = {}
    try:
        sensors = psutil.sensors_temperatures()
        for name, entries in sensors.items():
            if entries:
                temps[name] = entries[0].current
    except Exception:
        pass

    return SystemStats(
        cpu_percent=psutil.cpu_percent(interval=0.1),
        cpu_count=psutil.cpu_count(),
        memory_total=memory.total,
        memory_used=memory.used,
        memory_percent=memory.percent,
        disk_total=disk.total,
        disk_used=disk.used,
        disk_percent=disk.percent,
        network_sent=network.bytes_sent,
        network_recv=network.bytes_recv,
        temperatures=temps,
    )


class NetworkInterface(BaseModel):
    name: str
    ip_address: str | None
    mac_address: str | None
    speed: int | None
    is_up: bool


class HostnameUpdate(BaseModel):
    hostname: str


class TimezoneUpdate(BaseModel):
    timezone: str


def get_host_network_interfaces() -> list[dict]:
    """Get network interfaces from the host's network namespace."""
    try:
        # Use nsenter to access host's network namespace (PID 1 is host's init when pid=host)
        result = subprocess.run(
            ["nsenter", "--target", "1", "--net", "ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return []


def get_interface_speed(ifname: str) -> int | None:
    """Get interface speed from the host using ethtool."""
    try:
        # Validate interface name to prevent command injection
        validate_interface_name(ifname)
        result = subprocess.run(
            ["nsenter", "--target", "1", "--net", "ethtool", ifname],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Speed:' in line:
                    # Parse speed like "1000Mb/s" or "10000Mb/s"
                    speed_str = line.split(':')[1].strip()
                    if 'Mb/s' in speed_str:
                        return int(speed_str.replace('Mb/s', ''))
                    elif 'Gb/s' in speed_str:
                        return int(float(speed_str.replace('Gb/s', '')) * 1000)
    except Exception:
        pass
    return None


@router.get("/network", response_model=list[NetworkInterface])
async def get_network_interfaces(_=Depends(verify_token)):
    """Get network interface information from the host."""
    interfaces = []
    
    # Get interfaces from host's network namespace
    host_interfaces = get_host_network_interfaces()
    
    for iface in host_interfaces:
        name = iface.get("ifname", "")
        
        # Skip loopback, docker, and veth interfaces
        if name in ("lo", "docker0", "dummy0") or name.startswith("veth") or name.startswith("br-"):
            continue
        
        # Get IPv4 address
        ip_addr = None
        for addr in iface.get("addr_info", []):
            if addr.get("family") == "inet" and addr.get("scope") == "global":
                ip_addr = addr.get("local")
                break
        
        # Get MAC address
        mac_addr = iface.get("address")
        
        # Get operational state
        is_up = iface.get("operstate", "").upper() == "UP"
        
        # Get speed
        speed = get_interface_speed(name) if is_up else None
        
        interfaces.append(NetworkInterface(
            name=name,
            ip_address=ip_addr,
            mac_address=mac_addr,
            speed=speed,
            is_up=is_up
        ))
    
    return interfaces


@router.get("/timezone")
async def get_timezone(_=Depends(verify_token)):
    """Get current timezone."""
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return {"timezone": result.stdout.strip()}
    except Exception:
        pass
    return {"timezone": "Unknown"}


@router.get("/timezones")
async def list_timezones(_=Depends(verify_token)):
    """List available timezones."""
    try:
        result = subprocess.run(
            ["timedatectl", "list-timezones"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return {"timezones": result.stdout.strip().split('\n')}
    except Exception:
        pass
    return {"timezones": []}


@router.put("/timezone")
async def set_timezone(data: TimezoneUpdate, _=Depends(require_admin)):
    """Set system timezone."""
    # Get valid timezones for validation
    valid_tz = set()
    try:
        tz_result = subprocess.run(
            ["timedatectl", "list-timezones"],
            capture_output=True, text=True, timeout=10
        )
        if tz_result.returncode == 0:
            valid_tz = set(tz_result.stdout.strip().split('\n'))
    except Exception:
        pass

    # Validate timezone (with known valid list if available)
    validated_tz = validate_timezone(data.timezone, valid_tz if valid_tz else None)

    result = subprocess.run(
        ["timedatectl", "set-timezone", validated_tz],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(500, detail="Failed to set timezone")
    return {"status": "ok", "timezone": validated_tz}


@router.put("/hostname")
async def set_hostname(data: HostnameUpdate, _=Depends(require_admin)):
    """Set system hostname."""
    # Validate hostname
    validated_hostname = validate_hostname(data.hostname)

    result = subprocess.run(
        ["hostnamectl", "set-hostname", validated_hostname],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(500, detail="Failed to set hostname")
    return {"status": "ok", "hostname": validated_hostname}


class PCIeDevice(BaseModel):
    slot: str
    class_name: str
    vendor: str
    device: str
    revision: str | None = None
    driver: str | None = None


@router.get("/pcie", response_model=list[PCIeDevice])
async def get_pcie_devices(_=Depends(verify_token)):
    """Get PCIe device information."""
    devices = []
    try:
        # Get basic device list
        result = subprocess.run(
            ["lspci", "-vmm"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return devices

        # Parse lspci output
        current_device = {}
        for line in result.stdout.split('\n'):
            line = line.strip()
            if not line:
                if current_device:
                    devices.append(PCIeDevice(
                        slot=current_device.get('Slot', ''),
                        class_name=current_device.get('Class', ''),
                        vendor=current_device.get('Vendor', ''),
                        device=current_device.get('Device', ''),
                        revision=current_device.get('Rev'),
                        driver=current_device.get('Driver')
                    ))
                    current_device = {}
                continue

            if ':' in line:
                key, _, value = line.partition(':')
                current_device[key.strip()] = value.strip()

        # Add last device if exists
        if current_device:
            devices.append(PCIeDevice(
                slot=current_device.get('Slot', ''),
                class_name=current_device.get('Class', ''),
                vendor=current_device.get('Vendor', ''),
                device=current_device.get('Device', ''),
                revision=current_device.get('Rev'),
                driver=current_device.get('Driver')
            ))

    except Exception:
        pass

    return devices


class TemperatureSensor(BaseModel):
    label: str
    current: float
    high: float | None = None
    critical: float | None = None


class TemperatureGroup(BaseModel):
    name: str
    sensors: list[TemperatureSensor]


@router.get("/temperatures", response_model=list[TemperatureGroup])
async def get_temperatures(_=Depends(verify_token)):
    """Get detailed temperature sensor information."""
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

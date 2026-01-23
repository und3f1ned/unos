import psutil
import time
from datetime import datetime
from collections import deque
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token

router = APIRouter(prefix="/monitor", tags=["monitor"])

# Store history for charts (last 60 data points = ~5 minutes at 5s intervals)
HISTORY_SIZE = 60
cpu_history = deque(maxlen=HISTORY_SIZE)
memory_history = deque(maxlen=HISTORY_SIZE)
network_history = deque(maxlen=HISTORY_SIZE)
disk_io_history = deque(maxlen=HISTORY_SIZE)

last_net_io = None
last_disk_io = None
last_time = None


class CpuStats(BaseModel):
    percent: float
    per_core: list[float]
    load_1: float
    load_5: float
    load_15: float
    cores: int
    threads: int


class MemoryStats(BaseModel):
    total: int
    used: int
    free: int
    available: int
    percent: float
    swap_total: int
    swap_used: int
    swap_percent: float


class NetworkStats(BaseModel):
    bytes_sent: int
    bytes_recv: int
    bytes_sent_rate: float  # bytes per second
    bytes_recv_rate: float
    packets_sent: int
    packets_recv: int
    interfaces: list[dict]


class DiskIOStats(BaseModel):
    read_bytes: int
    write_bytes: int
    read_rate: float  # bytes per second
    write_rate: float
    read_count: int
    write_count: int
    disks: list[dict]


class ProcessInfo(BaseModel):
    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    status: str
    username: str


class HistoryPoint(BaseModel):
    timestamp: str
    value: float


@router.get("/cpu")
async def get_cpu_stats(_: dict = Depends(verify_token)) -> CpuStats:
    """Get current CPU statistics."""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    per_core = psutil.cpu_percent(interval=0.1, percpu=True)
    load = psutil.getloadavg()
    cpu_count = psutil.cpu_count(logical=False) or 1
    cpu_threads = psutil.cpu_count(logical=True) or 1

    # Store in history
    cpu_history.append({
        "timestamp": datetime.now().isoformat(),
        "value": cpu_percent
    })

    return CpuStats(
        percent=cpu_percent,
        per_core=per_core,
        load_1=load[0],
        load_5=load[1],
        load_15=load[2],
        cores=cpu_count,
        threads=cpu_threads
    )


@router.get("/memory")
async def get_memory_stats(_: dict = Depends(verify_token)) -> MemoryStats:
    """Get current memory statistics."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Store in history
    memory_history.append({
        "timestamp": datetime.now().isoformat(),
        "value": mem.percent
    })

    return MemoryStats(
        total=mem.total,
        used=mem.used,
        free=mem.free,
        available=mem.available,
        percent=mem.percent,
        swap_total=swap.total,
        swap_used=swap.used,
        swap_percent=swap.percent
    )


@router.get("/network")
async def get_network_stats(_: dict = Depends(verify_token)) -> NetworkStats:
    """Get current network statistics."""
    global last_net_io, last_time

    net_io = psutil.net_io_counters()
    current_time = time.time()

    # Calculate rates
    bytes_sent_rate = 0.0
    bytes_recv_rate = 0.0
    if last_net_io and last_time:
        time_diff = current_time - last_time
        if time_diff > 0:
            bytes_sent_rate = (net_io.bytes_sent - last_net_io.bytes_sent) / time_diff
            bytes_recv_rate = (net_io.bytes_recv - last_net_io.bytes_recv) / time_diff

    last_net_io = net_io
    last_time = current_time

    # Get per-interface stats
    interfaces = []
    net_if_stats = psutil.net_if_stats()
    net_if_addrs = psutil.net_if_addrs()
    net_if_counters = psutil.net_io_counters(pernic=True)

    for name, stats in net_if_stats.items():
        if name == "lo" or not stats.isup:
            continue
        
        addrs = net_if_addrs.get(name, [])
        ipv4 = next((a.address for a in addrs if a.family.name == "AF_INET"), None)
        
        counters = net_if_counters.get(name)
        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "speed": stats.speed,
            "is_up": stats.isup,
            "bytes_sent": counters.bytes_sent if counters else 0,
            "bytes_recv": counters.bytes_recv if counters else 0
        })

    # Store in history (total rate)
    network_history.append({
        "timestamp": datetime.now().isoformat(),
        "sent": bytes_sent_rate,
        "recv": bytes_recv_rate
    })

    return NetworkStats(
        bytes_sent=net_io.bytes_sent,
        bytes_recv=net_io.bytes_recv,
        bytes_sent_rate=bytes_sent_rate,
        bytes_recv_rate=bytes_recv_rate,
        packets_sent=net_io.packets_sent,
        packets_recv=net_io.packets_recv,
        interfaces=interfaces
    )


@router.get("/disk-io")
async def get_disk_io_stats(_: dict = Depends(verify_token)) -> DiskIOStats:
    """Get current disk I/O statistics."""
    global last_disk_io, last_time

    disk_io = psutil.disk_io_counters()
    current_time = time.time()

    # Calculate rates
    read_rate = 0.0
    write_rate = 0.0
    if last_disk_io and last_time:
        time_diff = current_time - last_time
        if time_diff > 0:
            read_rate = (disk_io.read_bytes - last_disk_io.read_bytes) / time_diff
            write_rate = (disk_io.write_bytes - last_disk_io.write_bytes) / time_diff

    last_disk_io = disk_io
    last_time = current_time

    # Get per-disk stats
    disks = []
    disk_io_per = psutil.disk_io_counters(perdisk=True)
    for name, counters in disk_io_per.items():
        if name.startswith("loop") or name.startswith("ram"):
            continue
        disks.append({
            "name": name,
            "read_bytes": counters.read_bytes,
            "write_bytes": counters.write_bytes,
            "read_count": counters.read_count,
            "write_count": counters.write_count
        })

    # Store in history
    disk_io_history.append({
        "timestamp": datetime.now().isoformat(),
        "read": read_rate,
        "write": write_rate
    })

    return DiskIOStats(
        read_bytes=disk_io.read_bytes,
        write_bytes=disk_io.write_bytes,
        read_rate=read_rate,
        write_rate=write_rate,
        read_count=disk_io.read_count,
        write_count=disk_io.write_count,
        disks=disks
    )


@router.get("/processes")
async def get_top_processes(
    sort_by: str = "cpu",
    limit: int = 10,
    _: dict = Depends(verify_token)
) -> list[ProcessInfo]:
    """Get top processes by CPU or memory usage."""
    processes = []
    
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status", "username"]):
        try:
            info = proc.info
            processes.append(ProcessInfo(
                pid=info["pid"],
                name=info["name"] or "Unknown",
                cpu_percent=info["cpu_percent"] or 0,
                memory_percent=info["memory_percent"] or 0,
                memory_mb=(info["memory_info"].rss / 1024 / 1024) if info["memory_info"] else 0,
                status=info["status"] or "unknown",
                username=info["username"] or "unknown"
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Sort by specified field
    if sort_by == "memory":
        processes.sort(key=lambda x: x.memory_percent, reverse=True)
    else:
        processes.sort(key=lambda x: x.cpu_percent, reverse=True)

    return processes[:limit]


@router.get("/history/cpu")
async def get_cpu_history(_: dict = Depends(verify_token)) -> list[HistoryPoint]:
    """Get CPU usage history."""
    return [HistoryPoint(**p) for p in cpu_history]


@router.get("/history/memory")
async def get_memory_history(_: dict = Depends(verify_token)) -> list[HistoryPoint]:
    """Get memory usage history."""
    return [HistoryPoint(**p) for p in memory_history]


@router.get("/history/network")
async def get_network_history(_: dict = Depends(verify_token)) -> list[dict]:
    """Get network usage history."""
    return list(network_history)


@router.get("/history/disk-io")
async def get_disk_io_history(_: dict = Depends(verify_token)) -> list[dict]:
    """Get disk I/O history."""
    return list(disk_io_history)


@router.get("/all")
async def get_all_stats(_: dict = Depends(verify_token)):
    """Get all monitoring stats in one request."""
    cpu = await get_cpu_stats(_)
    memory = await get_memory_stats(_)
    network = await get_network_stats(_)
    disk_io = await get_disk_io_stats(_)

    return {
        "cpu": cpu,
        "memory": memory,
        "network": network,
        "disk_io": disk_io,
        "timestamp": datetime.now().isoformat()
    }

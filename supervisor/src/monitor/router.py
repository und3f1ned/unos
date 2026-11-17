import psutil
from datetime import datetime
from collections import deque
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth.utils import verify_token

router = APIRouter(prefix="/monitor", tags=["monitor"])

# Store history for charts (last 60 data points = ~5 minutes at 5s intervals)
HISTORY_SIZE = 60
cpu_history = deque(maxlen=HISTORY_SIZE)
memory_history = deque(maxlen=HISTORY_SIZE)


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


@router.get("/all")
async def get_all_stats(_: dict = Depends(verify_token)):
    """Get all monitoring stats in one request."""
    cpu = await get_cpu_stats(_)
    memory = await get_memory_stats(_)

    return {
        "cpu": cpu,
        "memory": memory,
        "timestamp": datetime.now().isoformat()
    }

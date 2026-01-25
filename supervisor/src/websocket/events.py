import asyncio
import time
import psutil
from datetime import datetime
from collections import deque
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from ..config import get_settings

settings = get_settings()
router = APIRouter()

# Track previous values for rate calculation
_prev_net_io = None
_prev_disk_io = None
_prev_time = None
_prev_net_per_iface = {}

# History storage (60 points = ~2 minutes at 2s interval)
HISTORY_SIZE = 60
cpu_history: deque = deque(maxlen=HISTORY_SIZE)
memory_history: deque = deque(maxlen=HISTORY_SIZE)
network_history: deque = deque(maxlen=HISTORY_SIZE)
disk_io_history: deque = deque(maxlen=HISTORY_SIZE)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()


def get_system_stats() -> dict:
    """Get comprehensive system statistics for WebSocket broadcast."""
    global _prev_net_io, _prev_disk_io, _prev_time, _prev_net_per_iface

    current_time = time.time()
    time_delta = current_time - _prev_time if _prev_time else 2.0

    # CPU stats
    cpu_percent = psutil.cpu_percent(interval=None)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    load = psutil.getloadavg()
    cpu_count = psutil.cpu_count(logical=False) or 1
    cpu_threads = psutil.cpu_count(logical=True) or 1

    # Memory stats
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Disk usage
    disk = psutil.disk_usage("/")

    # Network stats with rate calculation
    net_io = psutil.net_io_counters()
    bytes_sent_rate = 0.0
    bytes_recv_rate = 0.0
    if _prev_net_io and time_delta > 0:
        bytes_sent_rate = (net_io.bytes_sent - _prev_net_io.bytes_sent) / time_delta
        bytes_recv_rate = (net_io.bytes_recv - _prev_net_io.bytes_recv) / time_delta

    # Per-interface network stats with rates
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

        # Calculate per-interface rates
        iface_sent_rate = 0.0
        iface_recv_rate = 0.0
        if name in _prev_net_per_iface and counters and time_delta > 0:
            prev = _prev_net_per_iface[name]
            iface_sent_rate = (counters.bytes_sent - prev["bytes_sent"]) / time_delta
            iface_recv_rate = (counters.bytes_recv - prev["bytes_recv"]) / time_delta

        if counters:
            _prev_net_per_iface[name] = {
                "bytes_sent": counters.bytes_sent,
                "bytes_recv": counters.bytes_recv
            }

        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "speed": stats.speed,
            "is_up": stats.isup,
            "bytes_sent": counters.bytes_sent if counters else 0,
            "bytes_recv": counters.bytes_recv if counters else 0,
            "bytes_sent_rate": iface_sent_rate,
            "bytes_recv_rate": iface_recv_rate,
        })

    # Disk I/O stats with rate calculation
    disk_io = psutil.disk_io_counters()
    read_rate = 0.0
    write_rate = 0.0
    if _prev_disk_io and time_delta > 0:
        read_rate = (disk_io.read_bytes - _prev_disk_io.read_bytes) / time_delta
        write_rate = (disk_io.write_bytes - _prev_disk_io.write_bytes) / time_delta

    # Per-disk stats
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

    # Top processes
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status", "username"]):
        try:
            info = proc.info
            processes.append({
                "pid": info["pid"],
                "name": info["name"] or "Unknown",
                "cpu_percent": info["cpu_percent"] or 0,
                "memory_percent": info["memory_percent"] or 0,
                "memory_mb": (info["memory_info"].rss / 1024 / 1024) if info["memory_info"] else 0,
                "status": info["status"] or "unknown",
                "username": info["username"] or "unknown"
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
    top_processes = processes[:10]

    # Temperature sensors
    temps = {}
    try:
        sensors = psutil.sensors_temperatures()
        for name, entries in sensors.items():
            if entries:
                temps[name] = entries[0].current
    except Exception:
        pass

    # Update history
    timestamp = datetime.now().isoformat()
    cpu_history.append({"timestamp": timestamp, "value": cpu_percent})
    memory_history.append({"timestamp": timestamp, "value": memory.percent})
    network_history.append({"timestamp": timestamp, "sent": bytes_sent_rate, "recv": bytes_recv_rate})
    disk_io_history.append({"timestamp": timestamp, "read": read_rate, "write": write_rate})

    # Store previous values for next calculation
    _prev_net_io = net_io
    _prev_disk_io = disk_io
    _prev_time = current_time

    return {
        "type": "stats",
        "data": {
            # Basic stats (backward compatible)
            "cpu_percent": cpu_percent,
            "cpu_count": cpu_threads,
            "memory_total": memory.total,
            "memory_used": memory.used,
            "memory_percent": memory.percent,
            "disk_total": disk.total,
            "disk_used": disk.used,
            "disk_percent": disk.percent,
            "network_sent": net_io.bytes_sent,
            "network_recv": net_io.bytes_recv,
            "temperatures": temps,

            # Extended CPU stats
            "cpu": {
                "percent": cpu_percent,
                "per_core": per_core,
                "load_1": load[0],
                "load_5": load[1],
                "load_15": load[2],
                "cores": cpu_count,
                "threads": cpu_threads,
            },

            # Extended memory stats
            "memory": {
                "total": memory.total,
                "used": memory.used,
                "free": memory.free,
                "available": memory.available,
                "percent": memory.percent,
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_percent": swap.percent,
            },

            # Extended network stats
            "network": {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv,
                "bytes_sent_rate": bytes_sent_rate,
                "bytes_recv_rate": bytes_recv_rate,
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
                "interfaces": interfaces,
            },

            # Disk I/O stats
            "disk_io": {
                "read_bytes": disk_io.read_bytes,
                "write_bytes": disk_io.write_bytes,
                "read_rate": read_rate,
                "write_rate": write_rate,
                "read_count": disk_io.read_count,
                "write_count": disk_io.write_count,
                "disks": disks,
            },

            # Top processes
            "processes": top_processes,

            # History data
            "history": {
                "cpu": list(cpu_history),
                "memory": list(memory_history),
                "network": list(network_history),
                "disk_io": list(disk_io_history),
            },

            # Timestamp
            "timestamp": timestamp,
        },
    }


def verify_ws_token(token: str) -> bool:
    """Verify JWT token for WebSocket connection."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload.get("sub") is not None
    except JWTError:
        return False


@router.websocket("/ws/v1/events")
async def websocket_endpoint(websocket: WebSocket):
    # Get token from query param
    token = websocket.query_params.get("token")
    if not token or not verify_ws_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect(websocket)

    try:
        # Start sending stats
        while True:
            stats = get_system_stats()
            await websocket.send_json(stats)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


@router.websocket("/ws/v1/logs/{container_id}")
async def container_logs_endpoint(websocket: WebSocket, container_id: str):
    """Stream container logs in real-time."""
    import docker

    token = websocket.query_params.get("token")
    if not token or not verify_ws_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect(websocket)

    try:
        client = docker.DockerClient(base_url=f"unix://{settings.docker_socket}")
        container = client.containers.get(container_id)

        for log in container.logs(stream=True, follow=True, tail=50):
            await websocket.send_json({
                "type": "log",
                "container_id": container_id,
                "data": log.decode("utf-8"),
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        manager.disconnect(websocket)

import asyncio
import json
import psutil
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from ..config import get_settings

settings = get_settings()
router = APIRouter()


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
    """Get current system statistics."""
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

    return {
        "type": "stats",
        "data": {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count(),
            "memory_total": memory.total,
            "memory_used": memory.used,
            "memory_percent": memory.percent,
            "disk_total": disk.total,
            "disk_used": disk.used,
            "disk_percent": disk.percent,
            "network_sent": network.bytes_sent,
            "network_recv": network.bytes_recv,
            "temperatures": temps,
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

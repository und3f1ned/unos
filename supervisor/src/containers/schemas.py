from pydantic import BaseModel
from enum import Enum


class ContainerState(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    PAUSED = "paused"
    RESTARTING = "restarting"
    CREATED = "created"
    DEAD = "dead"


class PortMapping(BaseModel):
    host_port: int | None = None
    container_port: int
    protocol: str = "tcp"


class VolumeMount(BaseModel):
    host_path: str
    container_path: str
    mode: str = "rw"


class ContainerInfo(BaseModel):
    id: str
    name: str
    image: str
    status: str
    state: ContainerState
    created: str
    ports: list[PortMapping] = []
    labels: dict[str, str] = {}


class ContainerCreate(BaseModel):
    name: str
    image: str
    ports: list[PortMapping] = []
    volumes: list[VolumeMount] = []
    environment: dict[str, str] = {}
    restart_policy: str = "unless-stopped"
    labels: dict[str, str] = {}


class ContainerLogs(BaseModel):
    container_id: str
    logs: str


class ImageInfo(BaseModel):
    id: str
    tags: list[str]
    size: int
    created: str

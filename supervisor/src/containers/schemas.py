from pydantic import BaseModel, ConfigDict, Field
from enum import Enum


class ContainerState(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    PAUSED = "paused"
    RESTARTING = "restarting"
    CREATED = "created"
    DEAD = "dead"


class PortMapping(BaseModel):
    """A port of a container and the host's port that publishes it."""

    host_port: int | None = Field(
        None, description="The host's port that publishes it; null when no host port publishes it."
    )
    container_port: int = Field(description="The container's port.")
    protocol: str = Field("tcp", description="Its protocol, as Docker names it: tcp (the default), udp or sctp.")


class VolumeMount(BaseModel):
    """A host folder or file bound into a new container."""

    host_path: str = Field(description="The host's folder or file, as an absolute path.")
    container_path: str = Field(description="Where the container sees it, as an absolute path.")
    mode: str = Field("rw", description="Docker's options of the bind: rw (the default) to read and write, ro to read only.")


_CONTAINER_EXAMPLE = {
    "id": "4f2a9c81d03e",
    "name": "jellyfin",
    "image": "jellyfin/jellyfin:12.4.20261026-211555",
    "status": "running",
    "state": "running",
    "created": "2026-11-18T09:14:03.512345678Z",
    "ports": [{"host_port": 8096, "container_port": 8096, "protocol": "tcp"}],
    "labels": {"unos.app": "jellyfin", "unos.app.pool": "data", "unos.app.version": "12.4.20261026-211555"},
}


class ContainerInfo(BaseModel):
    """A Docker container on the host, as Docker reports it."""

    model_config = ConfigDict(json_schema_extra={"examples": [_CONTAINER_EXAMPLE]})

    id: str = Field(description="Docker's short id of the container: the first 12 hex digits of its id.")
    name: str = Field(description="The container's name.")
    image: str = Field(
        description="The first tag of the container's image, or the image's short id (sha256: and 12 hex digits) "
                    "when it has no tag."
    )
    status: str = Field(
        description="Docker's status of the container: created, running, paused, restarting, removing, exited or dead."
    )
    state: ContainerState = Field(
        description="The status as one of running, exited, paused, restarting, created and dead: created for removing."
    )
    created: str = Field(description="When Docker created the container, as Docker writes it (UTC, with Z).")
    ports: list[PortMapping] = Field(
        [],
        description="Its ports with the host's ports that publish them; the list of all containers also names "
                    "the ports that no host port publishes, the answer about one container does not.",
    )
    labels: dict[str, str] = Field(
        {}, description="Its Docker labels; the containers of an app carry unos.app, the app's id, among them."
    )


class ContainerCreate(BaseModel):
    """A container to create and start."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "whoami",
        "image": "traefik/whoami:v1.10.3",
        "ports": [{"host_port": 8081, "container_port": 80, "protocol": "tcp"}],
        "volumes": [{"host_path": "/mnt/pools/data/whoami", "container_path": "/srv", "mode": "ro"}],
        "environment": {"TZ": "UTC"},
        "restart_policy": "unless-stopped",
        "labels": {},
    }]})

    name: str = Field(description="The container's name; one of UNOS's own (starting with unos-) is refused.")
    image: str = Field(
        description="The image it runs, as name:tag or name@sha256:...; Docker pulls it first when it lacks it."
    )
    ports: list[PortMapping] = Field(
        [], description="Its ports; each one with a host port is published on that port of the host."
    )
    volumes: list[VolumeMount] = Field([], description="The host's folders and files bound into it.")
    environment: dict[str, str] = Field({}, description="Variables of its environment, by name.")
    restart_policy: str = Field(
        "unless-stopped", description="Docker's restart policy: no, always, on-failure or unless-stopped (the default)."
    )
    labels: dict[str, str] = Field({}, description="Docker labels to give it.")


class ContainerLogs(BaseModel):
    """The last lines of a container's log."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "container_id": "jellyfin",
        "logs": "2026-11-18T09:14:05.102938475Z [09:14:05] [INF] [1] Main: Jellyfin version: 12.4.0\n"
                "2026-11-18T09:14:09.874512036Z [09:14:09] [INF] [1] Main: Startup complete 0:00:04.77\n",
    }]})

    container_id: str = Field(description="The container's id or name, as the request named it.")
    logs: str = Field(
        description="Its last lines of standard output and standard error, each after Docker's time stamp (UTC)."
    )


class ImageInfo(BaseModel):
    """A Docker image on the host."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "sha256:3b8e1f0c9a2d",
        "tags": ["jellyfin/jellyfin:12.4.20261026-211555"],
        "size": 1342177280,
        "created": "2026-10-26T21:15:55.123456789Z",
    }]})

    id: str = Field(description="Docker's short id of the image: sha256: and the first 12 hex digits of its id.")
    tags: list[str] = Field(description="Its tags (name:tag); none for an image without a tag.")
    size: int = Field(description="Its size as Docker reports it, in bytes.")
    created: str = Field(description="When the image was built, as Docker writes it (UTC, with Z).")

import contextlib
import docker
import threading
import requests
import urllib3
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container
from docker.types import LogConfig
from ..config import get_settings
from .schemas import ContainerInfo, ContainerCreate, ContainerState, PortMapping, ImageInfo

# The log options of every container that UNOS creates, as of its own two (deploy/docker-compose.yml):
# Docker rotates the json-file log at 10 MB and keeps 3 files, so a container that logs much cannot
# fill the system disk (ADR-0330).
CAPPED_LOG = LogConfig(type=LogConfig.types.JSON, config={"max-size": "10m", "max-file": "3"})

settings = get_settings()

# System containers that should be hidden from users
SYSTEM_CONTAINERS = [
    "unos-supervisor",
    "unos-webui",
]

# System images that should be protected
SYSTEM_IMAGES = [
    "deploy-supervisor",
    "deploy-webui",
]

# Label of containers installed from the app catalog (see apps.router).
UNOS_APP_LABEL = "unos.app"

# The states of a container that Docker counts as running (State.Running): it answers a start of
# such a container, and a stop of any other, without an error and without changing anything (HTTP
# 304, which docker-py does not pass on). The audit log records only starts and stops that changed
# the container (ADR-0320, ADR-0350); a paused container is not started, and is stopped.
RUNNING_STATES = ("running", "restarting", "paused")

# Seconds each request to the Docker API may take before docker-py gives up; a
# stop also waits for the container's stop timeout. Pulls are sent without a
# timeout by docker-py: their length depends on the image (ADR-0254).
DOCKER_TIMEOUT_SECONDS = 60
# What an image's removal may take: Docker deletes its layers before it answers, which took more
# than a minute for an image of 480 MiB on the dev device's SD card (2026-11-19).
IMAGE_REMOVE_TIMEOUT_SECONDS = 600


def is_system_container(name: str) -> bool:
    """Check if container is a system container."""
    return name in SYSTEM_CONTAINERS or name.startswith("unos-")


def is_system_image(tags: list[str]) -> bool:
    """Check if image is a system image."""
    for tag in tags:
        image_name = tag.split(":")[0]
        if image_name in SYSTEM_IMAGES or image_name.startswith("deploy-"):
            return True
    return False


class DockerUnavailable(DockerException):
    """Docker did not answer when a client was made: its socket is missing or refuses the connection."""


# The errors of a request that Docker did not answer: no client could be made, no connection
# came about (requests' ConnectTimeout is a ConnectionError too), or the connection broke during
# the answer, which docker-py passes on from requests or, for a stream such as a pull, from
# urllib3. The container and app routes answer them with 503 and the reason (ADR-0323). An
# answer that did not come within DOCKER_TIMEOUT_SECONDS (ReadTimeout) is not one of them: a
# stop waits for the container, which can take longer (ADR-0254).
NO_ANSWER = (
    DockerUnavailable,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    urllib3.exceptions.ProtocolError,
)


def no_answer_detail(error: BaseException) -> str:
    """The detail of the 503 for an error of NO_ANSWER."""
    return f"Docker is not available: {error}"


def get_client() -> docker.DockerClient:
    """Get a Docker client. Its calls block: make them in a worker thread (ADR-0021).

    docker.from_env asks Docker for its API version: DockerUnavailable when that request got no
    answer. An error that Docker answered it with, or an answer without the version, is passed on
    as docker-py raises it.
    """
    try:
        return docker.from_env(timeout=DOCKER_TIMEOUT_SECONDS)
    except DockerException as e:
        # docker-py raises a DockerException from within its handler of what went wrong.
        if isinstance(e.__context__, (APIError, KeyError)):
            raise
        raise DockerUnavailable(str(e)) from e


def list_containers(all: bool = True) -> list[ContainerInfo]:
    """List all containers (excluding system containers)."""
    client = get_client()
    containers = client.containers.list(all=all)

    result = []
    for c in containers:
        # Skip system containers
        if is_system_container(c.name):
            continue

        ports = []
        for port_str, bindings in (c.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
            container_port = int(port_str.split("/")[0])
            protocol = port_str.split("/")[1] if "/" in port_str else "tcp"
            if bindings:
                for b in bindings:
                    ports.append(
                        PortMapping(
                            host_port=int(b["HostPort"]) if b.get("HostPort") else None,
                            container_port=container_port,
                            protocol=protocol,
                        )
                    )
            else:
                ports.append(PortMapping(container_port=container_port, protocol=protocol))

        state = c.status.lower()
        try:
            container_state = ContainerState(state)
        except ValueError:
            container_state = ContainerState.CREATED

        result.append(
            ContainerInfo(
                id=c.short_id,
                name=c.name,
                image=c.image.tags[0] if c.image.tags else c.image.short_id,
                status=c.status,
                state=container_state,
                created=c.attrs.get("Created", ""),
                ports=ports,
                labels=c.labels,
            )
        )

    return result


def get_container(container_id: str) -> ContainerInfo:
    """Get container by ID or name."""
    client = get_client()
    try:
        c = client.containers.get(container_id)
    except NotFound:
        raise ValueError(f"Container {container_id} not found")

    ports = []
    for port_str, bindings in (c.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
        container_port = int(port_str.split("/")[0])
        protocol = port_str.split("/")[1] if "/" in port_str else "tcp"
        if bindings:
            for b in bindings:
                ports.append(
                    PortMapping(
                        host_port=int(b["HostPort"]) if b.get("HostPort") else None,
                        container_port=container_port,
                        protocol=protocol,
                    )
                )

    state = c.status.lower()
    try:
        container_state = ContainerState(state)
    except ValueError:
        container_state = ContainerState.CREATED

    return ContainerInfo(
        id=c.short_id,
        name=c.name,
        image=c.image.tags[0] if c.image.tags else c.image.short_id,
        status=c.status,
        state=container_state,
        created=c.attrs.get("Created", ""),
        ports=ports,
        labels=c.labels,
    )


def create_container(config: ContainerCreate) -> ContainerInfo:
    """Create and start a new container."""
    client = get_client()

    # Prevent creating containers with system names
    if is_system_container(config.name):
        raise ValueError("Container name is reserved for system use")

    # Build port bindings
    port_bindings = {}
    exposed_ports = {}
    for p in config.ports:
        port_key = f"{p.container_port}/{p.protocol}"
        exposed_ports[port_key] = {}
        if p.host_port:
            port_bindings[port_key] = p.host_port

    # Build volume bindings
    volumes = {}
    binds = []
    for v in config.volumes:
        volumes[v.container_path] = {}
        binds.append(f"{v.host_path}:{v.container_path}:{v.mode}")

    # Create container
    container = client.containers.run(
        config.image,
        name=config.name,
        detach=True,
        ports=port_bindings if port_bindings else None,
        volumes=binds if binds else None,
        environment=config.environment if config.environment else None,
        restart_policy={"Name": config.restart_policy},
        labels=config.labels,
        log_config=CAPPED_LOG,
    )

    return get_container(container.short_id)


def container_exists(name: str) -> bool:
    """Whether Docker has a container of this name: a create that failed at its start leaves one."""
    try:
        get_client().containers.get(name)
    except DockerException:
        return False
    return True


def _check_app_start(container: Container) -> None:
    """Run the Apps page's checks before a UNOS app starts.

    HTTPException 409 if the app must not start, 500 if one of its folders
    cannot be checked or created.
    """
    if UNOS_APP_LABEL in container.labels:
        # Imported here: apps.router imports this module.
        from ..apps.router import prepare_app_start

        prepare_app_start(container)


def _app_change(container: Container) -> contextlib.AbstractContextManager:
    """The lock of the app that ``container`` belongs to, held while the Containers page starts,
    restarts or removes it: 409 while an install, an update, a rollback or another change of that
    app is under way, which would meet a container it did not expect (ADR-0408). Nothing for a
    container that is no app's."""
    if UNOS_APP_LABEL not in container.labels:
        return contextlib.nullcontext()
    # Imported here: apps.stacks imports this module.
    from ..apps import stacks

    return stacks.app_lock(stacks.app_key(container), container.labels.get(UNOS_APP_LABEL) or container.name)


# Held, per container, from reading its state to the start or stop that follows, so that of two
# requests at the same moment only one finds the container in the state it changes (the audit entry
# of a start or a stop is written for that one only, ADR-0402). Docker answers the other without an
# error. Keyed by the container's ID, which Docker gives: the other containers go on meanwhile.
_STATE_CHANGE: dict[str, threading.Lock] = {}
_STATE_CHANGE_GUARD = threading.Lock()


def _state_lock(container: Container) -> threading.Lock:
    with _STATE_CHANGE_GUARD:
        return _STATE_CHANGE.setdefault(container.id, threading.Lock())


def start_container(container_id: str) -> tuple[ContainerInfo, bool]:
    """Start a stopped container; a UNOS app only if its data folders are usable.

    With whether it started it: not when it ran already (RUNNING_STATES).
    """
    client = get_client()
    try:
        known = client.containers.get(container_id)
        with _app_change(known):
            _check_app_start(known)  # reads the pools: outside the lock
            with _state_lock(known):
                container = client.containers.get(container_id)  # its state now
                started = container.status not in RUNNING_STATES
                container.start()
                container.reload()
        return get_container(container_id), started
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def stop_container(container_id: str) -> tuple[ContainerInfo, bool]:
    """Stop a running container; with whether it stopped it: not when it did not run (RUNNING_STATES)."""
    client = get_client()
    try:
        # Prevent stopping system containers
        known = client.containers.get(container_id)
        if is_system_container(known.name):
            raise ValueError("Cannot stop system container")

        with _state_lock(known):
            container = client.containers.get(container_id)  # its state now
            stopped = container.status in RUNNING_STATES
            container.stop()
            container.reload()
        return get_container(container_id), stopped
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def restart_container(container_id: str) -> ContainerInfo:
    """Restart a container; a UNOS app only if its data folders are usable. Not a system container."""
    client = get_client()
    try:
        container = client.containers.get(container_id)

        # As stop and remove: UNOS's own containers are not restarted from the Containers page.
        if is_system_container(container.name):
            raise ValueError("Cannot restart system container")

        with _app_change(container):
            _check_app_start(container)
            container.restart()
            container.reload()
        return get_container(container_id)
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def remove_container(container_id: str, force: bool = False) -> str:
    """Remove a container; returns its name."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        
        # Prevent removing system containers
        if is_system_container(container.name):
            raise ValueError("Cannot remove system container")

        with _app_change(container):
            container.remove(force=force)
        return container.name
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def get_container_logs(container_id: str, tail: int = 100) -> str:
    """Get container logs."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        logs = container.logs(tail=tail, timestamps=True).decode("utf-8")
        return logs
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def list_images() -> list[ImageInfo]:
    """List all images (excluding system images)."""
    client = get_client()
    images = client.images.list()

    result = []
    for img in images:
        # Skip system images
        if is_system_image(img.tags):
            continue

        result.append(
            ImageInfo(
                id=img.short_id,
                tags=img.tags,
                size=img.attrs.get("Size", 0),
                created=img.attrs.get("Created", ""),
            )
        )

    return result


def pull_image(image: str) -> ImageInfo:
    """Pull an image from registry; see :func:`pull_image_changed`."""
    return pull_image_changed(image)[0]


def pull_image_changed(image: str) -> tuple[ImageInfo, bool]:
    """Pull an image from registry, with whether the pull changed anything: the image is new, or the
    reference now names another image than before (its audit entry is written for a real change only,
    ADR-0402)."""
    client = get_client()
    before = {known.id for known in client.images.list(name=image)}
    if not before:  # the list may not know a reference by digest
        try:
            before = {client.images.get(image).id}
        except NotFound:
            pass
    img = client.images.pull(image)
    return _image_info(img), img.id not in before


def _image_info(img) -> ImageInfo:
    return ImageInfo(
        id=img.short_id,
        tags=img.tags,
        size=img.attrs.get("Size", 0),
        created=img.attrs.get("Created", ""),
    )


class ImageInUse(Exception):
    """Containers use the image; the containers API answers 409 with the message (ADR-0255)."""


def _in_use_message(image: str, containers: list[Container]) -> str:
    names = sorted(c.name for c in containers)
    if len(names) == 1:
        return f"The image {image} is used by the container {names[0]}. Remove the container first."
    return f"The image {image} is used by the containers {', '.join(names)}. Remove the containers first."


def remove_image(image_id: str, force: bool = False) -> str:
    """Remove an image with all its tags; returns its name for the audit log: its first tag, else its
    short ID (ADR-0402).

    Without force, an image that a container uses, running or stopped, is refused
    (ImageInUse) before anything is removed. Docker deletes an image with more than
    one tag by its ID only when forced ("image is referenced in multiple
    repositories"), so each tag is removed on its own, without force: the last one
    takes the image with it, and what is left of it is then removed by its ID
    (ADR-0255). Docker's own refusals still reach the caller as docker-py errors. Its client waits
    IMAGE_REMOVE_TIMEOUT_SECONDS: Docker answers once the layers are deleted.
    """
    client = get_client()
    client.api.timeout = IMAGE_REMOVE_TIMEOUT_SECONDS  # docker-py's every request reads it
    try:
        img = client.images.get(image_id)
        if is_system_image(img.tags):
            raise ValueError("Cannot remove system image")
        label = img.tags[0] if img.tags else img.short_id
        if force:
            client.images.remove(img.id, force=True)
            return label
        users = client.containers.list(all=True, filters={"ancestor": img.id})
        if users:
            raise ImageInUse(_in_use_message(img.tags[0] if img.tags else img.short_id, users))
        for tag in img.tags:
            client.images.remove(tag, force=False)
    except NotFound:
        raise ValueError(f"Image {image_id} not found")
    try:
        client.images.remove(img.id, force=False)
    except NotFound:
        pass  # removing its last tag removed the image
    return label

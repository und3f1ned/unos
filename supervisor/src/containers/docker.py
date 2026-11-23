import contextlib
import docker
import re
import threading
from datetime import UTC, datetime
from typing import Any
import requests
import urllib3
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container
from docker.models.resource import Model
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


# types-docker types the attributes of Docker's objects Optional, as docker-py reads them from the
# answer's dictionary; Docker gives every container a name, and every container and image an ID.
def _name(container: Container) -> str:
    name = container.name
    assert name is not None
    return name


def _id(model: Model) -> str:
    model_id = model.id
    assert model_id is not None
    return model_id


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


def _summary_name(summary: dict[str, Any]) -> str:
    """The container's name from its summary in Docker's list: of its Names, the one that is no
    legacy link's (/<other container>/<alias>), without its slash."""
    names = summary.get("Names") or []
    return next((name[1:] for name in names if name.count("/") == 1), (names or ["/"])[0].lstrip("/"))


# An image's ID or a prefix of it as `docker images` shows it (12 hex digits) or longer: what a
# container created by an ID has as its image.
_IMAGE_ID_PREFIX = re.compile(r"[0-9a-f]{12,64}")


def _image_tags(client: docker.DockerClient) -> dict[str, str]:
    """Each image's first tag that is no digest form (name@sha256:..., which the containerd image
    store lists among the tags of an image pulled by its digest), by the image's ID: Docker's list
    of images, one request. An image without such a tag is not in it."""
    tags = {}
    for image in client.api.images():
        tag = next((t for t in image.get("RepoTags") or [] if "@" not in t and t != "<none>:<none>"), None)
        if tag is not None and image.get("Id"):
            tags[image["Id"]] = tag
    return tags


def _image_label_of(summary: dict[str, Any], tags: dict[str, str]) -> str:
    """The container's image from its summary: its image's first tag that is no digest form
    (_image_tags); else the reference the container was created from, which Docker gives while it
    still names the container's image (a digest the app catalog created it from); else the image's
    short ID (sha256: and 12 hex digits), as Docker then gives the image's ID or the container was
    created by an ID."""
    image = summary.get("Image") or ""
    image_id = summary.get("ImageID") or ""
    if image_id in tags:
        return tags[image_id]
    digits = image.removeprefix("sha256:")
    if image_id and (image == image_id or (
        _IMAGE_ID_PREFIX.fullmatch(digits) and image_id.removeprefix("sha256:").startswith(digits)
    )):
        return image_id[:19] if image_id.startswith("sha256:") else image_id[:12]
    return image


def _summary_ports(summary: dict[str, Any]) -> list[PortMapping]:
    """The container's ports from its summary: those that the host publishes, one for each address,
    and those that it does not, sorted by the text port/protocol as Docker sorts its inspect's port
    map (the list gives them in no order), each port's addresses in Docker's order."""
    ports = []
    for port in summary.get("Ports") or []:
        container_port = int(port["PrivatePort"])
        protocol = port.get("Type") or "tcp"
        ports.append((
            f"{container_port}/{protocol}",
            PortMapping(host_port=int(port["PublicPort"]) if port.get("PublicPort") else None,
                        container_port=container_port, protocol=protocol),
        ))
    ports.sort(key=lambda entry: entry[0])
    return [mapping for _, mapping in ports]


def _created(seconds: Any) -> str:
    """When Docker created the container, from the list's Unix seconds: RFC 3339 in UTC, to the second."""
    try:
        return datetime.fromtimestamp(int(seconds), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _info(summary: dict[str, Any], tags: dict[str, str]) -> ContainerInfo:
    """A container's answer from its summary in Docker's list and the images' tags (_image_tags): no
    request of its own."""
    status = str(summary.get("State") or "")
    try:
        state = ContainerState(status.lower())
    except ValueError:
        state = ContainerState.CREATED
    return ContainerInfo(
        id=str(summary["Id"])[:12],
        name=_summary_name(summary),
        image=_image_label_of(summary, tags),
        status=status,
        state=state,
        created=_created(summary.get("Created")),
        ports=_summary_ports(summary),
        labels=summary.get("Labels") or {},
    )


def list_containers(all: bool = True) -> list[ContainerInfo]:
    """The containers, but UNOS's own (is_system_container): after get_client's version query,
    Docker's list of containers and its list of images, which hold all that the answer needs; no
    inspect of a container or of an image."""
    client = get_client()
    summaries = client.api.containers(all=all)
    tags = _image_tags(client)
    return [info for info in (_info(summary, tags) for summary in summaries) if not is_system_container(info.name)]


def get_container(container_id: str) -> ContainerInfo:
    """A container by its ID, a prefix of its ID or its name, answered as list_containers answers it:
    Docker's inspect finds it, and its summary in Docker's list, filtered by its full ID, with the
    images' tags, is the answer (Docker takes the filter id as an ID or a prefix of one, so the full
    ID names it alone). ValueError when Docker has no such container."""
    client = get_client()
    try:
        full_id = client.api.inspect_container(container_id)["Id"]
    except NotFound:
        raise ValueError(f"Container {container_id} not found")
    summaries = client.api.containers(all=True, filters={"id": full_id})
    if not summaries:  # removed in between
        raise ValueError(f"Container {container_id} not found")
    return _info(summaries[0], _image_tags(client))


def create_container(config: ContainerCreate) -> ContainerInfo:
    """Create and start a new container."""
    client = get_client()

    # Prevent creating containers with system names
    if is_system_container(config.name):
        raise ValueError("Container name is reserved for system use")

    # Build port bindings
    port_bindings = {}
    exposed_ports: dict[str, dict] = {}
    for p in config.ports:
        port_key = f"{p.container_port}/{p.protocol}"
        exposed_ports[port_key] = {}
        if p.host_port:
            port_bindings[port_key] = p.host_port

    # Build volume bindings
    volumes: dict[str, dict] = {}
    binds = []
    for v in config.volumes:
        volumes[v.container_path] = {}
        binds.append(f"{v.host_path}:{v.container_path}:{v.mode}")

    # Create container. types-docker's restart policies lack Docker's "unless-stopped" and "no".
    container = client.containers.run(  # type: ignore[call-overload]
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

    return stacks.app_lock(stacks.app_key(container), container.labels.get(UNOS_APP_LABEL) or _name(container))


# Held, per container, from reading its state to the start or stop that follows, so that of two
# requests at the same moment only one finds the container in the state it changes (the audit entry
# of a start or a stop is written for that one only, ADR-0402). Docker answers the other without an
# error. Keyed by the container's ID, which Docker gives: the other containers go on meanwhile.
_STATE_CHANGE: dict[str, threading.Lock] = {}
_STATE_CHANGE_GUARD = threading.Lock()


def _state_lock(container: Container) -> threading.Lock:
    with _STATE_CHANGE_GUARD:
        return _STATE_CHANGE.setdefault(_id(container), threading.Lock())


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
        if is_system_container(_name(known)):
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
        if is_system_container(_name(container)):
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
        if is_system_container(_name(container)):
            raise ValueError("Cannot remove system container")

        with _app_change(container):
            container.remove(force=force)
        return _name(container)
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
    names = sorted(_name(c) for c in containers)
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
    (ADR-0255). With the containerd image store the tags include the image's digest references
    (name@sha256:..., ADR-0468), and Docker removes with the last tag of a repository the
    repository's digest references, and with a digest reference every reference of its
    repository: the whole image when all are of one. A reference, or the ID, that an earlier
    removal took is passed over. The image is not found (ValueError) when it is not there to read,
    or when none of the removals found anything to remove. Docker's own refusals still reach the
    caller as docker-py errors. Its client waits IMAGE_REMOVE_TIMEOUT_SECONDS: Docker answers once
    the layers are deleted.
    """
    client = get_client()
    client.api.timeout = IMAGE_REMOVE_TIMEOUT_SECONDS  # docker-py's every request reads it
    try:
        img = client.images.get(image_id)
        if is_system_image(img.tags):
            raise ValueError("Cannot remove system image")
        label = img.tags[0] if img.tags else img.short_id
        if force:
            client.images.remove(_id(img), force=True)
            return label
        users = client.containers.list(all=True, filters={"ancestor": _id(img)})
        if users:
            raise ImageInUse(_in_use_message(img.tags[0] if img.tags else img.short_id, users))
    except NotFound:
        raise ValueError(f"Image {image_id} not found")
    removed = False
    for reference in [*img.tags, _id(img)]:  # the ID last: what the tags left of the image
        try:
            client.images.remove(reference, force=False)
            removed = True
        except NotFound:
            pass  # an earlier removal took it with it
    if not removed:  # another removed the image after it was read
        raise ValueError(f"Image {image_id} not found")
    return label

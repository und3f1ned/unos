import docker
from docker.errors import NotFound
from docker.models.containers import Container
from ..config import get_settings
from .schemas import ContainerInfo, ContainerCreate, ContainerState, PortMapping, ImageInfo

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


def get_client() -> docker.DockerClient:
    """Get Docker client."""
    return docker.from_env()


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
    )

    return get_container(container.short_id)


def _check_app_start(container: Container) -> None:
    """Run the Apps page's checks before a UNOS app starts.

    HTTPException 409 if the app must not start, 500 if one of its folders
    cannot be checked or created.
    """
    if UNOS_APP_LABEL in container.labels:
        # Imported here: apps.router imports this module.
        from ..apps.router import prepare_app_start

        prepare_app_start(container)


def start_container(container_id: str) -> ContainerInfo:
    """Start a stopped container; a UNOS app only if its data folders are usable."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        _check_app_start(container)
        container.start()
        container.reload()
        return get_container(container_id)
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def stop_container(container_id: str) -> ContainerInfo:
    """Stop a running container."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        
        # Prevent stopping system containers
        if is_system_container(container.name):
            raise ValueError("Cannot stop system container")
        
        container.stop()
        container.reload()
        return get_container(container_id)
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def restart_container(container_id: str) -> ContainerInfo:
    """Restart a container; a UNOS app only if its data folders are usable."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        _check_app_start(container)
        container.restart()
        container.reload()
        return get_container(container_id)
    except NotFound:
        raise ValueError(f"Container {container_id} not found")


def remove_container(container_id: str, force: bool = False) -> None:
    """Remove a container."""
    client = get_client()
    try:
        container = client.containers.get(container_id)
        
        # Prevent removing system containers
        if is_system_container(container.name):
            raise ValueError("Cannot remove system container")
        
        container.remove(force=force)
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
    """Pull an image from registry."""
    client = get_client()
    img = client.images.pull(image)
    return ImageInfo(
        id=img.short_id,
        tags=img.tags,
        size=img.attrs.get("Size", 0),
        created=img.attrs.get("Created", ""),
    )


def remove_image(image_id: str, force: bool = False) -> None:
    """Remove an image."""
    client = get_client()
    try:
        # Get image to check if it is a system image
        img = client.images.get(image_id)
        if is_system_image(img.tags):
            raise ValueError("Cannot remove system image")
        
        client.images.remove(image_id, force=force)
    except NotFound:
        raise ValueError(f"Image {image_id} not found")

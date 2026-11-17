"""A strict fake of docker-py's Docker client for the apps of several containers (ADR-0381).

``StackDocker`` keeps containers, networks and images as Docker does, and answers only the calls
that the Apps API makes: a method it does not have raises AttributeError, and an argument it
does not know raises TypeError, as docker-py does. Its host configs are docker-py's own
(``HostConfig`` of API version 1.47), and so are its networking and endpoint configs; the
high-level ``containers.create`` turns its arguments into those of the low-level create with
docker-py's own conversion, which refuses what docker-py refuses.

As with docker-py, a ``Container`` is the client's copy of what Docker said of a container (its
``attrs``): ``start``, ``stop`` and ``remove`` only ask Docker, which changes its own containers
(``containers_``), and the copy shows a change only after a ``reload``; ``containers.get`` and
``containers.list`` give fresh copies. So an answer built from a container that the code did not
reload after it changed it shows the state from before, as with the real client.

Every call is logged in order (``calls``: the call, its subject and its arguments). ``fail(call,
subject, error)`` makes that call raise ``error`` for that subject (a container's or network's
name, an image reference; None: any) until ``clear``. ``publish(ref, ...)`` puts an image in the
registry that a pull reads, with the sizes of its layers and, for a pull that fails halfway, the
error line that Docker's pull stream then sends; ``api.pull`` streams the progress lines of
Docker's ``POST /images/create`` (docker-py's ``stream=True, decode=True``), and the image is in
Docker's store once the stream was read to its end; ``pull_hook(ref, line)``, when set, is called
before each line is handed out; ``script(name, *states)`` gives the states that a container reaches,
one at each reload after its start, the last one staying (an image with a health check is
otherwise "starting" at its first reload and healthy from its second). ``info`` names
``root_dir`` as Docker's root folder.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import _create_container_args
from docker.types import HostConfig
from docker.types.networks import EndpointConfig, NetworkingConfig
from docker.utils import parse_repository_tag

API_VERSION = "1.47"
CREATED = "2026-11-16T09:00:00.000000000Z"


class Call(NamedTuple):
    name: str  # "containers.list", "api.create_container", "container.start", ...
    subject: str | None  # the container, network or image the call is about
    args: tuple
    kwargs: dict


def _conflict(message: str) -> APIError:
    return APIError(f"409 Client Error: Conflict ({message})", explanation=message)


@dataclass
class Image:
    """An image of the registry or of Docker's store: its declared VOLUMEs and health check."""

    ref: str  # "name:tag@sha256:..." or "name:tag"
    volumes: tuple[str, ...] = ()
    healthcheck: bool = False
    names: set[str] = field(default_factory=set)  # the references Docker knows it by
    # The download sizes of its layers, which a pull reports; the error line that a pull sends
    # after its first layer instead of finishing (None: it finishes).
    layers: tuple[int, ...] = (3000, 1000)
    pull_error: str | None = None

    @property
    def id(self) -> str:
        return "sha256:" + hashlib.sha256(self.ref.encode()).hexdigest()

    @property
    def attrs(self) -> dict:
        return {
            "Id": self.id,
            "RepoTags": sorted(n for n in self.names if "@" not in n),
            "RepoDigests": sorted(n for n in self.names if "@" in n),
            "Config": {
                "Volumes": {path: {} for path in self.volumes} or None,
                "Healthcheck": {"Test": ["CMD", "true"]} if self.healthcheck else None,
            },
        }


class _Live:
    """A container as Docker has it: its inspect data, which Docker's own calls change."""

    def __init__(self, docker: StackDocker, attrs: dict, image: Image) -> None:
        self._docker = docker
        self.attrs = attrs
        self.image = image
        # The states that its next reloads reach (StackDocker.script).
        self.pending: list[dict] = []

    @property
    def id(self) -> str:
        return self.attrs["Id"]

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def name(self) -> str:
        return self.attrs["Name"].lstrip("/")

    @property
    def labels(self) -> dict:
        return self.attrs["Config"].get("Labels") or {}

    @property
    def status(self) -> str:
        return self.attrs["State"]["Status"]

    def _network(self) -> str | None:
        mode = self.attrs["HostConfig"].get("NetworkMode")
        return None if mode in (None, "default", "bridge", "host", "none") else mode

    def start(self) -> None:
        if self.status == "running":
            return
        network = self._network()
        if network is not None and network not in self._docker.networks_:
            raise NotFound(f"network {network} not found")
        state = self.attrs["State"]
        state.update(Status="running", Running=True, Restarting=False, ExitCode=0, StartedAt=CREATED)
        if self.image.healthcheck:
            state["Health"] = {"Status": "starting", "FailingStreak": 0, "Log": []}
        scripted = self._docker.scripts.get(self.name)
        self.pending = [copy.deepcopy(s) for s in scripted] if scripted else (
            [{"Health": {"Status": status, "FailingStreak": 0, "Log": []}} for status in ("starting", "healthy")]
            if self.image.healthcheck else []
        )
        self.attrs["NetworkSettings"]["Ports"] = {
            spec: [{"HostIp": "0.0.0.0", "HostPort": b.get("HostPort", "")} for b in binds or []]
            for spec, binds in (self.attrs["HostConfig"].get("PortBindings") or {}).items()
        }
        if network is not None:
            self._docker.networks_[network].attach(self)

    def halt(self) -> None:
        if self.status in ("running", "restarting", "paused"):
            self.attrs["State"].update(Status="exited", Running=False, Restarting=False, ExitCode=0)
            self.attrs["State"].pop("Health", None)
        self.pending = []
        self._detach()

    def _detach(self) -> None:
        self.attrs["NetworkSettings"]["Ports"] = {}
        for network in self._docker.networks_.values():
            network.detach(self)

    def advance(self) -> None:
        """Move on to the next scripted state, if any."""
        if self.pending:
            change = self.pending.pop(0) if len(self.pending) > 1 else self.pending[0]
            self.attrs["State"].update(copy.deepcopy(change))
            if not self.attrs["State"].get("Running", True):
                self._detach()


class Container:
    """A container as docker-py's Container shows it: a copy of its inspect data in ``attrs``,
    read again only by ``reload``."""

    def __init__(self, docker: StackDocker, live: _Live) -> None:
        self._docker = docker
        self.attrs = copy.deepcopy(live.attrs)

    @property
    def id(self) -> str:
        return self.attrs["Id"]

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def name(self) -> str:
        return self.attrs["Name"].lstrip("/")

    @property
    def labels(self) -> dict:
        return self.attrs["Config"].get("Labels") or {}

    @property
    def status(self) -> str:
        return self.attrs["State"]["Status"]

    def start(self) -> None:
        self._docker._call("container.start", self.name)
        self._docker._find(self.id).start()

    def stop(self, timeout: int | None = None) -> None:
        self._docker._call("container.stop", self.name, timeout=timeout)
        self._docker._find(self.id).halt()

    def remove(self, v: bool = False, link: bool = False, force: bool = False) -> None:
        self._docker._call("container.remove", self.name, v=v, force=force)
        self._docker._remove(self._docker._find(self.id), force=force)

    def reload(self) -> None:
        self._docker._call("container.reload", self.name)
        live = self._docker._find(self.id)
        live.advance()
        self.attrs = copy.deepcopy(live.attrs)


class Network:
    """A network as docker-py's Network shows it."""

    def __init__(self, docker: StackDocker, name: str, driver: str, labels: dict) -> None:
        self._docker = docker
        self.id = f"net{next(docker.ids):061x}"
        self.name = name
        self.labels = dict(labels)
        self.driver = driver
        self.containers: dict[str, _Live] = {}

    @property
    def attrs(self) -> dict:
        return {
            "Name": self.name,
            "Id": self.id,
            "Driver": self.driver,
            "Labels": dict(self.labels),
            "Containers": {c.id: {"Name": c.name} for c in self.containers.values()},
        }

    def attach(self, container: _Live) -> None:
        self.containers[container.id] = container

    def detach(self, container: _Live) -> None:
        self.containers.pop(container.id, None)

    def remove(self) -> None:
        self._docker._call("network.remove", self.name)
        if self.name not in self._docker.networks_:
            raise NotFound(f"network {self.name} not found")
        if self.containers:
            raise APIError(
                "403 Client Error: Forbidden",
                explanation=f"error while removing network: network {self.name} has active endpoints",
            )
        del self._docker.networks_[self.name]


class _Listed:
    """A network as docker-py lists it without ``greedy``: no containers."""

    def __init__(self, network: Network, *, greedy: bool) -> None:
        self._network = network
        self.id = network.id
        self.name = network.name
        self.attrs = network.attrs if greedy else {**network.attrs, "Containers": {}}

    def remove(self) -> None:
        self._network.remove()


class _Containers:
    def __init__(self, docker: StackDocker) -> None:
        self._docker = docker

    def list(self, all: bool = False, filters: dict | None = None, ignore_removed: bool = False) -> list[Container]:
        self._docker._call("containers.list", None, all=all, filters=filters, ignore_removed=ignore_removed)
        label = (filters or {}).get("label")
        result = []
        for container in self._docker.containers_.values():
            if not all and container.status != "running":
                continue
            if label is not None:
                key, sep, value = label.partition("=")
                if key not in container.labels or (sep and container.labels[key] != value):
                    continue
            result.append(Container(self._docker, container))
        return result

    def get(self, container_id: str) -> Container:
        self._docker._call("containers.get", container_id)
        return Container(self._docker, self._docker._find(container_id))

    def create(self, image: str, command: Any = None, **kwargs: Any) -> Container:
        """docker-py's high-level create: its arguments become the low-level create's with
        docker-py's own conversion (TypeError for one that docker-py does not take)."""
        self._docker._call("containers.create", kwargs.get("name"), image, **kwargs)
        create = _create_container_args({**kwargs, "image": image, "command": command, "version": API_VERSION})
        create.pop("version", None)
        answer = self._docker.api._create(**create)
        return Container(self._docker, self._docker._find(answer["Id"]))


class _Images:
    def __init__(self, docker: StackDocker) -> None:
        self._docker = docker

    def get(self, name: str) -> Image:
        self._docker._call("images.get", name)
        image = self._docker._local_image(name)
        if image is None:
            raise ImageNotFound(f"No such image: {name}")
        return image


class _Networks:
    def __init__(self, docker: StackDocker) -> None:
        self._docker = docker

    def create(self, name: str, driver: str | None = None, labels: dict | None = None,
               check_duplicate: bool | None = None) -> Network:
        self._docker._call("networks.create", name, driver=driver, labels=labels, check_duplicate=check_duplicate)
        if name in self._docker.networks_:
            raise _conflict(f"network with name {name} already exists")
        network = Network(self._docker, name, driver or "bridge", labels or {})
        self._docker.networks_[name] = network
        return network

    def list(self, names: list[str] | None = None, filters: dict | None = None, greedy: bool = False) -> list[_Listed]:
        self._docker._call("networks.list", None, names=names, filters=filters, greedy=greedy)
        label = (filters or {}).get("label")
        result = []
        for network in self._docker.networks_.values():
            # Docker's name filter matches a part of the name.
            if names is not None and not any(part in network.name for part in names):
                continue
            if label is not None:
                key, sep, value = label.partition("=")
                if key not in network.labels or (sep and network.labels[key] != value):
                    continue
            result.append(_Listed(network, greedy=greedy))
        return result


class _Api:
    """docker-py's low-level APIClient, the calls of a container with a network or MaskedPaths."""

    def __init__(self, docker: StackDocker) -> None:
        self._docker = docker
        self._version = API_VERSION

    def pull(self, repository: str, tag: str | None = None, stream: bool = False, auth_config: dict | None = None,
             decode: bool = False, platform: str | None = None, all_tags: bool = False) -> Any:
        """docker-py's low-level pull: with ``stream`` and ``decode``, the progress lines of Docker's
        pull as dicts. A reference that the registry lacks fails before the stream, as Docker's 404."""
        repository, image_tag = parse_repository_tag(repository)
        tag = tag or image_tag or "latest"
        ref = f"{repository}{'@' if tag.startswith('sha256:') else ':'}{tag}"
        self._docker._call("api.pull", ref, repository, tag=tag, stream=stream, decode=decode)
        if not (stream and decode):
            raise TypeError("the fake streams decoded lines only")
        image = self._docker.registry.get(ref)
        if image is None:
            raise APIError("404 Client Error: Not Found", explanation=f"manifest for {ref} not found: manifest unknown")
        return self._docker._pull_lines(ref, repository, tag, image)

    def create_host_config(self, **kwargs: Any) -> HostConfig:
        self._docker._call("api.create_host_config", None, **kwargs)
        return HostConfig(version=API_VERSION, **kwargs)

    def create_networking_config(self, endpoints_config: dict | None = None) -> NetworkingConfig:
        self._docker._call("api.create_networking_config", None, endpoints_config=endpoints_config)
        return NetworkingConfig(endpoints_config)

    def create_endpoint_config(self, aliases: list[str] | None = None) -> EndpointConfig:
        self._docker._call("api.create_endpoint_config", None, aliases=aliases)
        return EndpointConfig(API_VERSION, aliases=aliases)

    def create_container(self, image: str, command: Any = None, ports: list | None = None,
                         environment: dict | None = None, name: str | None = None,
                         host_config: dict | None = None, labels: dict | None = None,
                         networking_config: dict | None = None, stop_timeout: int | None = None) -> dict:
        self._docker._call(
            "api.create_container", name, image, ports=ports, environment=environment, host_config=host_config,
            labels=labels, networking_config=networking_config, stop_timeout=stop_timeout,
        )
        return self._create(image=image, command=command, ports=ports, environment=environment, name=name,
                            host_config=host_config, labels=labels, networking_config=networking_config,
                            stop_timeout=stop_timeout)

    def _create(self, *, image: str, command: Any = None, ports: list | None = None,
                environment: dict | list | None = None, name: str | None = None, host_config: dict | None = None,
                labels: dict | None = None, networking_config: dict | None = None,
                stop_timeout: int | None = None) -> dict:
        docker = self._docker
        local = docker._local_image(image)
        if local is None:
            raise ImageNotFound(f"No such image: {image}")
        if name in docker.containers_:
            raise _conflict(f'The container name "/{name}" is already in use')
        host_config = dict(host_config or {})
        network = host_config.get("NetworkMode")
        if network not in (None, "default", "bridge", "host", "none") and network not in docker.networks_:
            raise NotFound(f"network {network} not found")
        if isinstance(environment, dict):
            env = [f"{key}={value}" for key, value in environment.items()]
        else:
            env = list(environment or [])
        endpoints = (networking_config or {}).get("EndpointsConfig") or {}
        container_id = f"{next(docker.ids):064x}"
        attrs = {
            "Id": container_id,
            "Name": f"/{name}",
            "Created": CREATED,
            "Config": {
                "Image": image,
                "Env": env,
                "Labels": dict(labels or {}),
                "StopTimeout": stop_timeout,
                "ExposedPorts": {f"{p[0]}/{p[1]}" if isinstance(p, tuple) else str(p): {} for p in ports or []},
            },
            "State": {"Status": "created", "Running": False, "Restarting": False, "ExitCode": 0, "Error": ""},
            "HostConfig": host_config,
            "Mounts": [
                {"Type": m["Type"], "Source": m["Source"], "Destination": m["Target"], "Mode": "",
                 "RW": not m.get("ReadOnly", False), "Propagation": "rprivate"}
                for m in host_config.get("Mounts") or []
            ],
            "NetworkSettings": {"Ports": {}, "Networks": {
                net: {"Aliases": (endpoint or {}).get("Aliases")} for net, endpoint in endpoints.items()
            }},
        }
        docker.containers_[name] = _Live(docker, attrs, local)
        return {"Id": container_id, "Warnings": []}

    def remove_container(self, container: str, v: bool = False, force: bool = False) -> None:
        self._docker._call("api.remove_container", container, v=v, force=force)
        self._docker._remove(self._docker._find(container), force=force)

    def tag(self, image: str, repository: str, tag: str | None = None, force: bool = False) -> bool:
        self._docker._call("api.tag", image, repository, tag=tag)
        local = self._docker._local_image(image)
        if local is None:
            raise ImageNotFound(f"No such image: {image}")
        local.names.add(f"{repository}:{tag or 'latest'}")
        return True


class StackDocker:
    """A Docker client with Docker's containers, networks and images; see the module."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        # The folder that Docker's info names as its root (DockerRootDir).
        self.root_dir = "/var/lib/docker"
        self.failures: dict[tuple[str, str | None], BaseException] = {}
        # Docker's containers by name, as Docker has them now (a Container is a client's copy).
        self.containers_: dict[str, _Live] = {}
        self.networks_: dict[str, Network] = {}
        self.images_: dict[str, Image] = {}
        self.registry: dict[str, Image] = {}
        self.scripts: dict[str, list[dict]] = {}
        # Called with the reference and each line of a pull before the line is handed out.
        self.pull_hook: Any = None
        self.ids = itertools.count(1)
        self.containers = _Containers(self)
        self.images = _Images(self)
        self.networks = _Networks(self)
        self.api = _Api(self)

    def info(self) -> dict:
        """What Docker says of itself: here only the folder of its data."""
        self._call("info", None)
        return {"DockerRootDir": self.root_dir}

    # -- set up -----------------------------------------------------------

    def publish(self, ref: str, *, volumes: tuple[str, ...] = (), healthcheck: bool = False,
                layers: tuple[int, ...] = (3000, 1000), pull_error: str | None = None) -> Image:
        """An image a pull can get: by tag and, for a pinned ``ref`` (name:tag@digest), by digest."""
        image = Image(ref, volumes=volumes, healthcheck=healthcheck, layers=layers, pull_error=pull_error)
        name_tag, _, digest = ref.partition("@")
        self.registry[name_tag] = image
        if digest:
            name, _, _ = name_tag.rpartition(":")
            self.registry[f"{name}@{digest}"] = image
        return image

    def pulled(self, ref: str) -> Image:
        """An image already in Docker's store, as a pull of ``ref`` leaves it."""
        image = self.registry.get(ref) or self.publish(ref)
        name_tag, _, digest = ref.partition("@")
        image.names.add(name_tag)
        if digest:
            image.names.add(f"{name_tag.rpartition(':')[0]}@{digest}")
        self.images_[image.ref] = image
        return image

    def script(self, name: str, *states: dict) -> None:
        self.scripts[name] = list(states)

    def fail(self, call: str, subject: str | None, error: BaseException) -> None:
        self.failures[(call, subject)] = error

    def clear(self) -> None:
        self.failures.clear()

    def network(self, name: str, *, labels: dict | None = None) -> Network:
        """A network that Docker has already."""
        network = Network(self, name, "bridge", labels or {})
        self.networks_[name] = network
        return network

    # -- what the tests read -------------------------------------------------

    def called(self, *names: str) -> list[tuple[str, str | None]]:
        """The calls of the given names (all without names), as (call, subject), in order."""
        return [(c.name, c.subject) for c in self.calls if not names or c.name in names]

    def container(self, name: str) -> Container:
        """A fresh copy of the container ``name``, as containers.get gives it, without a call."""
        return Container(self, self.containers_[name])

    # -- Docker's behaviour --------------------------------------------------

    def _call(self, call: str, subject: str | None, /, *args: Any, **kwargs: Any) -> None:
        self.calls.append(Call(call, subject, args, kwargs))
        error = self.failures.get((call, subject)) or self.failures.get((call, None))
        if error is not None:
            raise error

    def _pull_lines(self, ref: str, repository: str, tag: str, image: Image) -> Any:
        """What Docker's pull stream sends, line by line: each layer is named, downloaded in two
        steps of its size, extracted and complete; then the digest and the status. The image is in
        Docker's store once the last line was read."""
        lines: list[dict] = [{"status": f"Pulling from {repository}", "id": tag}]
        ids = [hashlib.sha256(f"{image.ref}/{n}".encode()).hexdigest()[:12] for n in range(len(image.layers))]
        lines += [{"status": "Pulling fs layer", "progressDetail": {}, "id": layer} for layer in ids]
        for n, (layer, size) in enumerate(zip(ids, image.layers)):
            lines += [
                {"status": "Downloading", "progressDetail": {"current": size // 2, "total": size}, "id": layer},
                {"status": "Downloading", "progressDetail": {"current": size, "total": size}, "id": layer},
                {"status": "Download complete", "progressDetail": {}, "id": layer},
            ]
            if n == 0 and image.pull_error is not None:
                lines.append({"errorDetail": {"message": image.pull_error}, "error": image.pull_error})
                break
            lines += [
                {"status": "Extracting", "progressDetail": {"current": size, "total": size}, "id": layer},
                {"status": "Pull complete", "progressDetail": {}, "id": layer},
            ]
        else:
            lines += [{"status": f"Digest: {image.ref.partition('@')[2] or 'sha256:' + '0' * 64}"},
                      {"status": f"Status: Downloaded newer image for {ref}"}]
        for line in lines:
            if self.pull_hook is not None:
                self.pull_hook(ref, line)
            yield line
        if image.pull_error is None:
            image.names.add(ref)
            self.images_[image.ref] = image

    def _find(self, key: str) -> _Live:
        for container in self.containers_.values():
            if key in (container.name, container.id) or (len(key) >= 12 and container.id.startswith(key)):
                return container
        raise NotFound(f"No such container: {key}")

    def _local_image(self, name: str) -> Image | None:
        return next((image for image in self.images_.values() if name in image.names), None)

    def _remove(self, container: _Live, *, force: bool) -> None:
        if container.status == "running" and not force:
            raise _conflict(f"cannot remove container {container.name}: container is running")
        container.halt()
        del self.containers_[container.name]

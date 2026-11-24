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
error line that Docker's pull stream then sends; a pull is Docker's ``POST /images/create``, which
the install sends through the client's requests session (``api.post`` to
``{api.base_url}/v{api.api_version}/images/create``, logged as ``api.pull`` with the image's
reference) and reads as a stream of JSON lines (``PullResponse``): the image is in Docker's store
once the stream was read to its end. ``pull_hook(ref, line)``, when set, is called before each line
is handed out; ``stall(ref, after)`` makes the pull of ``ref`` stop sending after ``after`` lines,
as a stalled pull does: the read then ends as requests' read timeout ends it, or blocks until the
response is shut down (``after`` with ``block=True``); ``script(name, *states)`` gives the states that a container reaches,
one at each reload after its start, the last one staying (an image with a health check is
otherwise "starting" at its first reload and healthy from its second). ``info`` names
``root_dir`` as Docker's root folder.

For an app's update (ADR-0408) an image also has the ``created`` time and the ``diff_ids`` of its
configuration, as Docker's inspect shows them (``Created``, ``RootFS.Layers``) and an ``image_id``
(its configuration's digest, which a registry names too); ``images.list`` lists Docker's store as
docker-py does (each image inspected), a container's ``rename`` renames it as Docker does,
``script_image(ref, *states)`` scripts every container created from the image ``ref`` (name@digest)
and ``start_hook(container)``, when set, is called at each start of a container.

A container's ``exec_run`` answers a command run in it (a companion's ready command, ADR-0415) from
``exec_results`` of its name, after ``exec_hook(container)`` when set, and refuses it with 409 for a
container that does not run, as Docker does; a create keeps the command it was given as the
container's ``Config.Cmd``, and a health check of ``["NONE"]`` leaves the container of an image with
one without health.

A container's ``update`` changes its HostConfig as Docker's update does, with Docker's refusals of
a memory limit below 6 MB, a swap below the memory, a CPU quota below 1 ms and a CPU quota beside
NanoCpus (ADR-0390); ``api.containers`` is Docker's summary list, and ``events`` answers the events
of the past that ``oom(name, at)`` recorded, as Docker answers ``GET /events`` with ``until``.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import threading
from types import SimpleNamespace
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import ExecResult, _create_container_args
from docker.types import HostConfig
from docker.types.networks import EndpointConfig, NetworkingConfig
import requests
from urllib3.exceptions import ReadTimeoutError

API_VERSION = "1.47"
CREATED = "2026-11-16T09:00:00.000000000Z"


class Call(NamedTuple):
    name: str  # "containers.list", "api.create_container", "container.start", ...
    subject: str | None  # the container, network or image the call is about
    args: tuple
    kwargs: dict


def _conflict(message: str) -> APIError:
    return APIError(f"409 Client Error: Conflict ({message})", explanation=message)


def _refused(status: int, message: str) -> APIError:
    """Docker's refusal with its HTTP status, as docker-py raises it: 400 for a value that Docker
    does not take, 409 for a conflict with what the container has."""
    reason = {400: "Bad Request", 409: "Conflict"}[status]
    response = SimpleNamespace(status_code=status, reason=reason, url="http+docker://localhost/v1.47/containers/update")
    return APIError(f"{status} Client Error: {reason}", response=response, explanation=message)


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
    # Its configuration's digest (Docker's image id; by default one made from ``ref``), when it was
    # built, and the digests of its unpacked layers (ADR-0408).
    image_id: str | None = None
    created: str = CREATED
    diff_ids: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.image_id or "sha256:" + hashlib.sha256(self.ref.encode()).hexdigest()

    @property
    def attrs(self) -> dict:
        return {
            "Id": self.id,
            "Created": self.created,
            "Size": sum(self.layers),
            "RepoTags": sorted(n for n in self.names if "@" not in n),
            "RepoDigests": sorted(n for n in self.names if "@" in n),
            "RootFS": {"Type": "layers", "Layers": list(self.diff_ids)},
            "Config": {
                "Volumes": {path: {} for path in self.volumes} or None,
                "Healthcheck": {"Test": ["CMD", "true"]} if self.healthcheck else None,
            },
        }


class PullResponse:
    """requests' Response of Docker's ``POST /images/create``, streamed: its JSON lines, a status,
    ``close`` and ``raw.shutdown`` (urllib3's, which ends a read that blocks in another thread)."""

    def __init__(self, docker: StackDocker, ref: str, lines: list[dict], image: Image | None, *,
                 status_code: int = 200, stall_after: int | None = None, block: bool = False) -> None:
        self._docker = docker
        self.ref = ref
        self._lines = lines
        self._image = image
        self.status_code = status_code
        self.reason = "OK" if status_code < 400 else "Not Found"
        self._stall_after = stall_after
        self._block = block
        self.closed = False
        self.shut_down = threading.Event()
        self.raw = SimpleNamespace(shutdown=self.shut_down.set)

    def json(self) -> dict:
        return self._lines[0] if self._lines else {}

    def close(self) -> None:
        self.closed = True

    def iter_lines(self) -> Any:
        for n, line in enumerate(self._lines):
            if self._stall_after is not None and n == self._stall_after:
                if self._block:
                    self.shut_down.wait(10)
                    raise requests.exceptions.ConnectionError("Connection broken: the socket was shut down")
                raise requests.exceptions.ConnectionError(
                    ReadTimeoutError(None, f"/images/create?fromImage={self.ref}", "Read timed out.")
                )
            if self.shut_down.is_set():
                raise requests.exceptions.ConnectionError("Connection broken: the socket was shut down")
            if self._docker.pull_hook is not None:
                self._docker.pull_hook(self.ref, line)
            yield json.dumps(line).encode()
        if self._image is not None and self._image.pull_error is None:
            self._image.names.add(self.ref)
            self._docker.images_[self._image.ref] = self._image


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
        checked = self.image.healthcheck and (self.attrs["Config"].get("Healthcheck") or {}).get("Test") != ["NONE"]
        if checked:
            state["Health"] = {"Status": "starting", "FailingStreak": 0, "Log": []}
        scripted = self._docker.scripts.get(self.name) or self._docker.image_scripts.get(self.attrs["Config"]["Image"])
        if self._docker.start_hook is not None:
            self._docker.start_hook(self)
        self.pending = [copy.deepcopy(s) for s in scripted] if scripted else (
            [{"Health": {"Status": status, "FailingStreak": 0, "Log": []}} for status in ("starting", "healthy")]
            if checked else []
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

    def rename(self, name: str) -> None:
        """docker-py's Container.rename: Docker renames the container, which keeps everything else."""
        self._docker._call("container.rename", self.name, name)
        self._docker._rename(self._docker._find(self.id), name)

    def reload(self) -> None:
        self._docker._call("container.reload", self.name)
        live = self._docker._find(self.id)
        live.advance()
        self.attrs = copy.deepcopy(live.attrs)

    def exec_run(self, cmd: Any, stdout: bool = True, stderr: bool = True, stdin: bool = False, tty: bool = False,
                 privileged: bool = False, user: str = "", detach: bool = False, stream: bool = False,
                 socket: bool = False, environment: Any = None, workdir: str | None = None,
                 demux: bool = False) -> ExecResult:
        """docker-py's Container.exec_run of a command that ends: its exit code and output, from
        ``exec_results`` of the container (the next answer at each run, the last one staying; exit
        code 0 without output by default). Docker refuses it with 409 for a container that does not
        run, as for one that is restarting."""
        self._docker._call("container.exec_run", self.name, cmd, stdout=stdout, stderr=stderr)
        if (stdin, tty, privileged, user, detach, stream, socket, environment, workdir, demux) != (
            False, False, False, "", False, False, False, None, None, False,
        ):
            raise TypeError("the fake runs a command to its end and reads its output only")
        live = self._docker._find(self.id)
        if self._docker.exec_hook is not None:
            self._docker.exec_hook(live)
        if live.status != "running" or live.attrs["State"].get("Restarting"):
            raise _refused(409, f"Container {live.id} is not running")
        answers = self._docker.exec_results.get(self.name) or [(0, b"")]
        code, output = answers.pop(0) if len(answers) > 1 else answers[0]
        return ExecResult(code, output)

    def update(self, **kwargs: Any) -> dict:
        """docker-py's Container.update: the low-level update_container of the container."""
        self._docker._call("container.update", self.name, **kwargs)
        return self._docker.api._update(self.id, **kwargs)


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

    def list(self, name: str | None = None, all: bool = False, filters: dict | None = None) -> list[Image]:
        """docker-py's list: every image of Docker's store, as its inspect shows it."""
        self._docker._call("images.list", None, name=name, all=all, filters=filters)
        if name is not None or filters:
            raise TypeError("the fake lists every image only")
        return list(self._docker.images_.values())

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

    base_url = "http+docker://localhost"
    api_version = API_VERSION

    def __init__(self, docker: StackDocker) -> None:
        self._docker = docker
        self._version = API_VERSION

    def post(self, url: str, params: dict | None = None, headers: dict | None = None, stream: bool = False,
             timeout: Any = None) -> PullResponse:
        """requests' post of the client's session: only Docker's pull, ``POST /images/create``, streamed.
        A reference that the registry lacks answers 404 before any line, as Docker does."""
        if url != f"{self.base_url}/v{self.api_version}/images/create":
            raise TypeError(f"the fake posts only a pull, not {url}")
        if not stream or set(params or {}) != {"fromImage", "tag"}:
            raise TypeError(f"the fake streams a pull of fromImage and tag, not {params}")
        repository, tag = params["fromImage"], params["tag"]
        ref = f"{repository}{'@' if tag.startswith('sha256:') else ':'}{tag}"
        self._docker._call("api.pull", ref, repository, tag=tag, timeout=timeout)
        image = self._docker.registry.get(ref)
        if image is None:
            return PullResponse(self._docker, ref, [{"message": f"manifest for {ref} not found: manifest unknown"}],
                                None, status_code=404)
        stall = self._docker.stalls.get(ref)
        return PullResponse(self._docker, ref, self._docker._pull_lines(repository, tag, image), image,
                            stall_after=stall[0] if stall else None, block=stall[1] if stall else False)

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
                         networking_config: dict | None = None, stop_timeout: int | None = None,
                         healthcheck: dict | None = None) -> dict:
        self._docker._call(
            "api.create_container", name, image, ports=ports, environment=environment, host_config=host_config,
            labels=labels, networking_config=networking_config, stop_timeout=stop_timeout, command=command,
            healthcheck=healthcheck,
        )
        return self._create(image=image, command=command, ports=ports, environment=environment, name=name,
                            host_config=host_config, labels=labels, networking_config=networking_config,
                            stop_timeout=stop_timeout, healthcheck=healthcheck)

    def create_container_from_config(self, config: dict, name: str | None = None, platform: str | None = None) -> dict:
        """docker-py's create of a container from the body of Docker's create: its Config, HostConfig and
        NetworkingConfig, as a container's inspect gives the first two (the SD standby's critical apps,
        ADR-0498)."""
        self._docker._call("api.create_container_from_config", name, copy.deepcopy(config))
        known = {"Image", "Cmd", "Env", "Labels", "StopTimeout", "Healthcheck", "HostConfig", "NetworkingConfig",
                 "ExposedPorts", "Hostname", "Volumes", "Entrypoint", "WorkingDir", "User"}
        if set(config) - known:
            raise TypeError(f"the fake does not create from {sorted(set(config) - known)}")
        return self._create(image=config["Image"], command=config.get("Cmd"), environment=config.get("Env"), name=name,
                            host_config=copy.deepcopy(config.get("HostConfig") or {}), labels=config.get("Labels"),
                            networking_config=config.get("NetworkingConfig"), stop_timeout=config.get("StopTimeout"),
                            healthcheck=config.get("Healthcheck"))

    def _create(self, *, image: str, command: Any = None, ports: list | None = None,
                environment: dict | list | None = None, name: str | None = None, host_config: dict | None = None,
                labels: dict | None = None, networking_config: dict | None = None,
                stop_timeout: int | None = None, healthcheck: dict | None = None) -> dict:
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
            "Created": next(docker.create_times) if docker.create_times is not None else CREATED,
            "Image": local.id,
            "Config": {
                "Image": image,
                # The command that the create gave; None: the image's own.
                "Cmd": list(command) if command else None,
                # The health check that the create gave (Test ["NONE"]: none); None: the image's own.
                "Healthcheck": {"Test": list((healthcheck or {}).get("test") or (healthcheck or {}).get("Test"))}
                if healthcheck else None,
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

    def containers(self, quiet: bool = False, all: bool = False, trunc: bool = False, latest: bool = False,
                   since: str | None = None, before: str | None = None, limit: int = -1, size: bool = False,
                   filters: dict | None = None) -> list[dict]:
        """docker-py's low-level list: Docker's summary of each container (GET /containers/json), the
        running ones unless ``all``, filtered by a label as Docker filters them."""
        self._docker._call("api.containers", None, all=all, filters=filters)
        if quiet or trunc or latest or since or before or limit != -1 or size:
            raise TypeError("the fake lists containers by all and filters only")
        label = (filters or {}).get("label")
        result = []
        for container in self._docker.containers_.values():
            if not all and container.status != "running":
                continue
            if label is not None:
                key, sep, value = label.partition("=")
                if key not in container.labels or (sep and container.labels[key] != value):
                    continue
            result.append({
                "Id": container.id,
                "Names": [f"/{container.name}"],
                "Image": container.attrs["Config"]["Image"],
                "Labels": dict(container.labels),
                "State": container.status,
                "Status": "Up 5 minutes" if container.status == "running" else "Exited (0) 5 minutes ago",
            })
        return result

    def _update(self, container: str, *, blkio_weight: Any = None, cpu_period: int | None = None,
                cpu_quota: int | None = None, cpu_shares: Any = None, cpuset_cpus: Any = None,
                cpuset_mems: Any = None, mem_limit: int | None = None, mem_reservation: Any = None,
                memswap_limit: int | None = None, kernel_memory: Any = None, restart_policy: Any = None) -> dict:
        """Docker's POST /containers/{id}/update as docker-py sends it (a value that is 0 or None is
        not sent, and Docker takes 0 as unchanged) and as Docker checks it: the values it refuses
        and the conflicts with a limit already set, with Docker's messages."""
        if any(value is not None for value in (blkio_weight, cpu_shares, cpuset_cpus, cpuset_mems,
                                               mem_reservation, kernel_memory, restart_policy)):
            raise TypeError("the fake updates only the memory, the swap and the CPU quota and period")
        sent = {key: value for key, value in (("Memory", mem_limit), ("MemorySwap", memswap_limit),
                                              ("CpuPeriod", cpu_period), ("CpuQuota", cpu_quota)) if value}
        live = self._docker._find(container)
        host = live.attrs["HostConfig"]
        memory, swap = sent.get("Memory", 0), sent.get("MemorySwap", 0)
        period, quota = sent.get("CpuPeriod", 0), sent.get("CpuQuota", 0)
        if host.get("NanoCpus") and (period > 0 or quota > 0):
            raise _refused(409, "Conflicting options: CPU Quota cannot be updated as NanoCPUs has already been set")
        if memory != 0 and memory < 6 * 1024 * 1024:
            raise _refused(400, "Minimum memory limit allowed is 6MB")
        if memory > 0 and swap > 0 and swap < memory:
            raise _refused(400, "Minimum memoryswap limit should be larger than memory limit, see usage")
        if (memory > 0 and swap == -1 and (host.get("MemorySwap") or 0) > 0 and memory > host["MemorySwap"]
                and live.status == "running"):
            # Docker 29.9 with runc 1.5 on the dev device: -1 leaves the running container's swap
            # limit as it was, and runc refuses a memory limit above it (2026-11-17).
            raise _refused(500, "Cannot update container: failed to update resources: runc did not terminate "
                                "successfully: exit status 1: memory+swap limit should be >= memory limit")
        if memory > 0 and swap == 0 and (host.get("MemorySwap") or 0) > 0 and memory > host["MemorySwap"]:
            raise _refused(409, "Memory limit should be smaller than already set memoryswap limit, "
                                "update the memoryswap at the same time")
        if period != 0 and not 1000 <= period <= 1000000:
            raise _refused(400, "CPU cfs period can not be less than 1ms (i.e. 1000) or larger than 1s (i.e. 1000000)")
        if 0 < quota < 1000:
            raise _refused(400, "CPU cfs quota can not be less than 1ms (i.e. 1000)")
        # What the host's kernel cannot limit, Docker discards with a warning (its sysinfo).
        warnings = []
        if "memory" in self._docker.unsupported and ("Memory" in sent or "MemorySwap" in sent):
            sent.pop("Memory", None)
            sent.pop("MemorySwap", None)
            warnings.append("Your kernel does not support memory limit capabilities or the cgroup is not mounted. "
                            "Limitation discarded.")
        if "cpu" in self._docker.unsupported and ("CpuQuota" in sent or "CpuPeriod" in sent):
            sent.pop("CpuQuota", None)
            sent.pop("CpuPeriod", None)
            warnings.append("Your kernel does not support CPU CFS scheduler. CPU quota discarded.")
        host.update(sent)
        return {"Warnings": warnings}

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
        # The folder that Docker's info names as its root (DockerRootDir), and its DriverStatus,
        # which names the containerd image store when Docker keeps its images there.
        self.root_dir = "/var/lib/docker"
        self.driver_status: list[list[str]] = []
        self.failures: dict[tuple[str, str | None], BaseException] = {}
        # Docker's containers by name, as Docker has them now (a Container is a client's copy).
        self.containers_: dict[str, _Live] = {}
        self.networks_: dict[str, Network] = {}
        self.images_: dict[str, Image] = {}
        self.registry: dict[str, Image] = {}
        self.scripts: dict[str, list[dict]] = {}
        # The answers of the commands run in a container (Container.exec_run), by its name: (exit
        # code, output) at each run, the last one staying.
        self.exec_results: dict[str, list[tuple[int, bytes]]] = {}
        # Called with each container (as Docker has it) that a command is run in, before its answer.
        self.exec_hook: Any = None
        # The states of every container created from an image (name@digest), as ``scripts``.
        self.image_scripts: dict[str, list[dict]] = {}
        # Called with each container (as Docker has it) that starts.
        self.start_hook: Any = None
        # The times of the containers' creates, one after the other (None: all CREATED).
        self.create_times: Any = None
        # Docker's events, in their order (``oom``).
        self.events_: list[dict] = []
        # The limits that the host's kernel cannot apply ("memory", "cpu"): Docker's update
        # discards them with a warning.
        self.unsupported: set[str] = set()
        # Called with the reference and each line of a pull before the line is handed out.
        self.pull_hook: Any = None
        # The pulls that stop sending: by reference, after how many lines, and whether the read
        # then blocks until the response is shut down (else it times out).
        self.stalls: dict[str, tuple[int, bool]] = {}
        self.ids = itertools.count(1)
        self.containers = _Containers(self)
        self.images = _Images(self)
        self.networks = _Networks(self)
        self.api = _Api(self)

    def info(self) -> dict:
        """What Docker says of itself: here only the folder of its data and its store."""
        self._call("info", None)
        return {"DockerRootDir": self.root_dir, "DriverStatus": self.driver_status}

    def events(self, since: int | None = None, until: int | None = None, filters: dict | None = None,
               decode: bool | None = None) -> Any:
        """Docker's events of the past, as GET /events with ``until`` answers them and closes: those
        of ``events_`` from ``since`` to ``until`` (whole seconds, both included, as Docker compares
        them with each event's timeNano) that the filters of type, event and label match."""
        self._call("events", None, since=since, until=until, filters=filters, decode=decode)
        if not decode or until is None:
            raise TypeError("the fake answers decoded events of the past only (decode and until)")
        filters = filters or {}

        def matches(event: dict) -> bool:
            nano = event["timeNano"]
            if since is not None and nano < since * 1_000_000_000:
                return False
            if nano > until * 1_000_000_000:
                return False
            for key, attribute in (("type", "Type"), ("event", "Action")):
                wanted = filters.get(key)
                if wanted is not None and event[attribute] not in ([wanted] if isinstance(wanted, str) else wanted):
                    return False
            label = filters.get("label")
            for wanted in [] if label is None else [label] if isinstance(label, str) else label:
                key, sep, value = wanted.partition("=")
                attributes = event["Actor"]["Attributes"]
                if key not in attributes or (sep and attributes[key] != value):
                    return False
            return True

        return iter([copy.deepcopy(event) for event in self.events_ if matches(event)])

    def oom(self, name: str, at: float) -> None:
        """The kernel ended a process of the container ``name`` for lack of memory at the time ``at``
        (seconds since the epoch): Docker's event "oom", which containerd reports for each OOM kill
        in the container's cgroup, with the container's labels among its attributes."""
        live = self.containers_[name]
        self.events_.append({
            "status": "oom", "id": live.id, "from": live.attrs["Config"]["Image"], "Type": "container",
            "Action": "oom", "Actor": {"ID": live.id, "Attributes": {
                "image": live.attrs["Config"]["Image"], "name": name, **live.labels,
            }}, "scope": "local", "time": int(at), "timeNano": int(at * 1_000_000_000),
        })

    # -- set up -----------------------------------------------------------

    def publish(self, ref: str, *, volumes: tuple[str, ...] = (), healthcheck: bool = False,
                layers: tuple[int, ...] = (3000, 1000), pull_error: str | None = None, image_id: str | None = None,
                created: str = CREATED, diff_ids: tuple[str, ...] = ()) -> Image:
        """An image a pull can get: by tag and, for a pinned ``ref`` (name:tag@digest), by digest."""
        image = Image(ref, volumes=volumes, healthcheck=healthcheck, layers=layers, pull_error=pull_error,
                      image_id=image_id, created=created, diff_ids=diff_ids)
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

    def stall(self, ref: str, after: int, *, block: bool = False) -> None:
        """The pull of ``ref`` (as the install asks for it: name:tag or name@digest) sends ``after``
        lines, then nothing more."""
        self.stalls[ref] = (after, block)

    def script(self, name: str, *states: dict) -> None:
        self.scripts[name] = list(states)

    def script_image(self, ref: str, *states: dict) -> None:
        """The states of every container created from ``ref`` (name@digest), as ``script``."""
        self.image_scripts[ref] = list(states)

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

    def _pull_lines(self, repository: str, tag: str, image: Image) -> list[dict]:
        """What Docker's pull stream sends, line by line: each layer is named, downloaded in two
        steps of its size, extracted and complete; then the digest and the status."""
        ref = f"{repository}{'@' if tag.startswith('sha256:') else ':'}{tag}"
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
        return lines

    def _find(self, key: str) -> _Live:
        for container in self.containers_.values():
            if key in (container.name, container.id) or (len(key) >= 12 and container.id.startswith(key)):
                return container
        raise NotFound(f"No such container: {key}")

    def _local_image(self, name: str) -> Image | None:
        """An image of Docker's store by one of its references or by its id."""
        return next((image for image in self.images_.values() if name in image.names or name == image.id), None)

    def _rename(self, container: _Live, name: str) -> None:
        if name in self.containers_:
            raise _conflict(f'The container name "/{name}" is already in use')
        del self.containers_[container.name]
        container.attrs["Name"] = f"/{name}"
        self.containers_[name] = container

    def _remove(self, container: _Live, *, force: bool) -> None:
        if container.status == "running" and not force:
            raise _conflict(f"cannot remove container {container.name}: container is running")
        container.halt()
        del self.containers_[container.name]

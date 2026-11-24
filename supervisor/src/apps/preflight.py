"""What an install checks of Docker and the host before it pulls anything (ADR-0381).

The host ports of an app: each port of its template, under the host port of the request's
overrides, must be one that Docker can bind when the container starts. A port that another
container publishes, running or stopped (Docker binds it when that container starts), or that
a program on the host listens on, at any address, answers 409 that names the container or the
program and asks for another host port, before a pull that can take minutes. The host's
sockets are read with ``ss`` through src/host.py (ADR-0020); a read that fails is logged and
the install goes on, and Docker's own bind at the start is then the last check.

An app in the host's network (``network_mode`` "host") binds its container ports on the host
itself: Docker discards the port bindings of such a container, so its ports cannot be moved
(400 for an override that moves one), they are the ports checked, and a taken one asks to
remove the container or stop the program that holds it.

The file system of Docker's images (ADR-0493): containerd's root when Docker keeps its images in
the containerd image store of the host's containerd service, else Docker's data root as Docker's
info names it (src/system/docker_data.py), on a pool since ADR-0486. It must keep FREE_SPACE_FLOOR
free once the install's images are there. An image of the catalog (a PinnedImage, ADR-0410) counts
its estimated size unless Docker has it already. The free space is read on the host with df
through src/host.py, and the texts name the place: the pool that holds the folder, else the system
disk. When it cannot be read, the install answers 500 and pulls nothing. A host without the folder,
as when the supervisor does not run on Docker's host (the compose test stack, whose container also
lacks the systemctl that the containerd image store makes it ask), has no file system to read: that
is logged, the install goes on, and Docker's own pull is then the last check.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from docker.errors import NotFound
from fastapi import HTTPException

from .. import host
from ..system import docker_data
from .catalog import PINNED_IMAGES, PinnedImage
from .schemas import AppTemplate
from .stacks import DOCKER_ERRORS

logger = logging.getLogger(__name__)

HOST_TIMEOUT = 15
# The host's listening TCP and bound UDP sockets, without a header, with numeric addresses and
# the programs that hold them: "tcp LISTEN 0 4096 0.0.0.0:445 0.0.0.0:* users:(("smbd",...))".
SS_COMMAND = ["ss", "-H", "-l", "-n", "-t", "-u", "-p"]
_SOCKET_TYPES = ("tcp", "udp")
# The first program of ss's process column.
_PROGRAM = re.compile(r'users:\(\("([^"]*)"')
MIN_PORT = 1
MAX_PORT = 65535


def plan_ports(template: AppTemplate, overrides: dict[int, int]) -> dict[str, int]:
    """The host port of each port of ``template``, as "<container port>/<protocol>" to the host
    port, with ``overrides`` (by container port) in place of the template's: 400 for a host port
    outside 1 to 65535 and for a host port and protocol given twice. An app in the host's network
    binds its container port: 400 for an override that moves it."""
    bindings: dict[str, int] = {}
    given: set[tuple[int, str]] = set()
    for mapping in template.ports:
        spec = f"{mapping.container}/{mapping.protocol}"
        if _in_host_network(template):
            port = mapping.container
            if overrides.get(mapping.container, port) != port:
                raise HTTPException(400, f"{template.name} runs in the host's network: its port {spec} cannot be moved")
        else:
            port = overrides.get(mapping.container, mapping.host)
        if not MIN_PORT <= port <= MAX_PORT:
            raise HTTPException(400, f"Host port for {spec} must be between {MIN_PORT} and {MAX_PORT}")
        if (port, mapping.protocol) in given:
            raise HTTPException(400, f"Host port {port}/{mapping.protocol} is given twice")
        given.add((port, mapping.protocol))
        bindings[spec] = port
    return bindings


def check_ports(template: AppTemplate, bindings: dict[str, int], containers: Iterable[Any]) -> None:
    """409 when a host port of ``bindings`` (``template``'s, from ``plan_ports``) is taken:
    published by one of ``containers`` (Docker's, running or stopped: docker-py's Container
    objects with their inspect data), or held by a program on the host. The host is read only for
    ports that no container publishes; nothing is read for an app without ports. Blocks: call it
    in a worker thread."""
    if not bindings:
        return
    wanted = {spec: (port, spec.partition("/")[2]) for spec, port in bindings.items()}
    published = _published_ports(containers)
    # A running container's port is also a listener of Docker's proxy on the host: the
    # container is named.
    listening = _host_listeners() if any(key not in published for key in wanted.values()) else {}
    problems: list[str] = []
    for spec, (port, protocol) in wanted.items():
        if (port, protocol) in published:
            taken = f"Port {port}/{protocol} is already used by the container {published[port, protocol]}"
            remedy = "remove that container first"
        elif (port, protocol) in listening:
            taken = f"Port {port}/{protocol} is already in use on the host by {listening[port, protocol] or 'a program'}"
            remedy = "stop that program first"
        else:
            continue
        if _in_host_network(template):
            problems.append(f"{taken}: {template.name} runs in the host's network and cannot move its port; {remedy}")
        else:
            problems.append(f"{taken}: choose another host port for {spec}")
    if problems:
        raise HTTPException(409, "; ".join(problems))


def _in_host_network(template: AppTemplate) -> bool:
    """Whether the app runs in the host's network: Docker discards its container's port
    bindings, and the app binds its container ports on the host itself."""
    return template.network_mode == "host"


def _published_ports(containers: Iterable[Any]) -> dict[tuple[int, str], str]:
    """(host port, protocol) to "<name> (<state>)" of the container that publishes it."""
    published: dict[tuple[int, str], str] = {}
    for container in containers:
        bindings = (container.attrs.get("HostConfig") or {}).get("PortBindings") or {}
        for spec, binds in bindings.items():
            protocol = spec.partition("/")[2] or "tcp"
            for bind in binds or []:
                for port in _host_ports(bind.get("HostPort") or ""):
                    published.setdefault((port, protocol), f"{container.name} ({container.status})")
    return published


def _host_ports(value: str) -> range:
    """The host ports of a binding's HostPort: one ("8096"), a range ("8000-8010"), or none ("",
    a port that Docker chooses at the start)."""
    first, _, last = value.partition("-")
    try:
        return range(int(first), int(last or first) + 1)
    except ValueError:
        return range(0)


def _host_listeners() -> dict[tuple[int, str], str | None]:
    """(port, protocol) to the program that listens on it on the host, at any address (None when
    ss cannot name it); none when the host cannot be read, which is logged."""
    try:
        result = host.run_on_host(SS_COMMAND, timeout=HOST_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read the ports in use on the host: %s", exc)
        return {}
    if not result.ok:
        logger.warning(
            "Could not read the ports in use on the host (exit status %d): %s", result.returncode, result.stderr.strip()
        )
        return {}
    listening: dict[tuple[int, str], str | None] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0] not in _SOCKET_TYPES:
            continue
        # The local address and port: 0.0.0.0:445, [::]:22, *:5353, 127.0.0.53%lo:53.
        _, colon, port = fields[4].rpartition(":")
        if not colon or not port.isdigit():
            continue
        program = _PROGRAM.search(line)
        name = "".join(ch for ch in program.group(1) if ch.isprintable()) if program else ""
        listening.setdefault((int(port), fields[0]), name or None)
    return listening


# The file system of Docker's images keeps this much free after an install's pulls (ADR-0381).
FREE_SPACE_FLOOR = 2 * 1024**3
# df's POSIX listing of the file system of its argument, a folder of the host: a header, then one
# line with its free 1024-byte blocks. Where the host lacks the folder, the script exits 3, a status
# that neither nsenter nor df uses. The path reaches the shell as a positional argument, never as
# script text.
FREE_SPACE_SCRIPT = '[ -e "$1" ] || exit 3; exec df -P -k -- "$1"'
_NO_FOLDER = 3
# A line of df -P: the file system (whose name may hold spaces), its 1024-byte blocks, the used
# and the free ones, the capacity in percent, then the mount point.
_DF_LINE = re.compile(r"\s(\d+)\s+(\d+)\s+(\d+)\s+\d+%\s")


@dataclass(frozen=True)
class ImageStore:
    """Where Docker keeps its images (ADR-0493): the folder, the pool that holds it (None: the
    system disk) and the free space of its file system (None: the host has no such folder)."""

    folder: str
    pool: str | None
    free: int | None

    @property
    def place(self) -> str:
        """The place in a text: "the pool data", "the system disk"."""
        return f"the pool {self.pool}" if self.pool else "the system disk"


def image_store(client: Any) -> ImageStore:
    """Where Docker keeps its images and the free space there; a host without the folder is logged.
    500 when Docker's info, containerd's configuration or the free space cannot be read. Blocks."""
    try:
        info = client.info()
    except DOCKER_ERRORS as exc:
        raise _unread(str(exc)) from exc
    info = info if isinstance(info, dict) else {}
    try:
        folder = docker_data.images_root(info)
    except docker_data.DockerDataError as exc:
        # Docker's socket without Docker's host, as in the compose test stack: where its data root
        # is missing, so is what told where containerd keeps the images.
        root = info.get("DockerRootDir")
        if isinstance(root, str) and root.startswith("/") and _host_lacks(root):
            _not_checked(root)
            return ImageStore(root, None, None)
        raise _unread(str(exc)) from exc
    free = _free_bytes(folder)
    if free is None:
        _not_checked(folder)
    return ImageStore(folder, docker_data.pool_of(folder), free)


def _not_checked(folder: str) -> None:
    logger.warning(
        "Could not read the free space where Docker keeps its images: the host has no %s; it is not checked", folder
    )


def _host_lacks(folder: str) -> bool:
    """Whether the host lacks ``folder``; False when that cannot be told."""
    try:
        return host.run_on_host(["test", "-e", folder], timeout=HOST_TIMEOUT).returncode == 1
    except (OSError, subprocess.SubprocessError):
        return False


def check_space(client: Any, template: AppTemplate, images: Iterable[str]) -> None:
    """409 when the file system of Docker's images would keep less than FREE_SPACE_FLOOR free once
    those of ``images`` (the references the install pulls) that Docker lacks are pulled, each
    counted with its PinnedImage.disk_bytes; the text names the pool or the system disk. 500 when
    the free space cannot be read; a host without the folder is not checked. Blocks: call it in a
    worker thread, before any pull."""
    try:
        need = sum(pin.disk_bytes for pin in _missing_pins(client, images))
    except DOCKER_ERRORS as exc:
        raise _unread(str(exc)) from exc
    store = image_store(client)
    if store.free is None or store.free - need >= FREE_SPACE_FLOOR:
        return
    remedy = "Remove unused images on Containers > Images first."
    if need:
        detail = (
            f"{template.name}'s images need about {_gib(need)} on {store.place}, which has {_gib(store.free)} free; "
            f"at least {_gib(FREE_SPACE_FLOOR)} must stay free. {remedy}"
        )
    else:
        detail = (
            f"{store.place[0].upper()}{store.place[1:]} has {_gib(store.free)} free; at least "
            f"{_gib(FREE_SPACE_FLOOR)} must stay free when {template.name} is installed. {remedy}"
        )
    raise HTTPException(409, detail)


def _missing_pins(client: Any, images: Iterable[str]) -> list[PinnedImage]:
    """The PinnedImages of ``images``, each once, that Docker's image store lacks. A reference that
    PINNED_IMAGES does not record (a test's template) counts nothing."""
    missing: list[PinnedImage] = []
    for ref in dict.fromkeys(images):
        pin = PINNED_IMAGES.get(ref)
        if pin is None:
            continue
        try:
            client.images.get(f"{pin.name}@{pin.digest}")
        except NotFound:
            missing.append(pin)
    return missing


def _free_bytes(folder: str) -> int | None:
    """The free space of the file system of ``folder`` on the host; None when the host lacks it;
    500 when it cannot be read."""
    try:
        result = host.run_on_host(
            ["/bin/sh", "-c", FREE_SPACE_SCRIPT, "unos-free-space", folder], timeout=HOST_TIMEOUT, errors="replace"
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _unread(str(exc)) from exc
    if result.returncode == _NO_FOLDER:
        return None
    if not result.ok:
        raise _unread(result.stderr.strip() or f"df exited with status {result.returncode}")
    free = _available(result.stdout)
    if not free:
        raise _unread("df printed no free space")
    return min(free)


def _available(listing: str) -> list[int]:
    """The free bytes of each file system that ``df -P -k`` lists."""
    return [int(match[3]) * 1024 for match in map(_DF_LINE.search, listing.splitlines()) if match]


def _unread(reason: str) -> HTTPException:
    return HTTPException(
        500, f"Could not read the free space where Docker keeps its images: {reason}; nothing was pulled"
    )


def _gib(size: int) -> str:
    """``size`` bytes in GiB, to a tenth: "3.4 GiB", "2 GiB"."""
    return f"{size / 1024**3:.1f}".removesuffix(".0") + " GiB"

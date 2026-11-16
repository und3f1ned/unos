"""What an install checks of Docker and the host before it pulls anything (ADR-0381).

The host ports of an app: each port of its template, under the host port of the request's
overrides, must be one that Docker can bind when the container starts. A port that another
container publishes, running or stopped (Docker binds it when that container starts), or that
a program on the host listens on, at any address, answers 409 that names the container or the
program and asks for another host port, before a pull that can take minutes. The host's
sockets are read with ``ss`` through src/host.py (ADR-0020); a read that fails is logged and
the install goes on, and Docker's own bind at the start is then the last check.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException

from .. import host
from .schemas import AppTemplate

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
    outside 1 to 65535 and for a host port and protocol given twice."""
    bindings: dict[str, int] = {}
    given: set[tuple[int, str]] = set()
    for mapping in template.ports:
        spec = f"{mapping.container}/{mapping.protocol}"
        port = overrides.get(mapping.container, mapping.host)
        if not MIN_PORT <= port <= MAX_PORT:
            raise HTTPException(400, f"Host port for {spec} must be between {MIN_PORT} and {MAX_PORT}")
        if (port, mapping.protocol) in given:
            raise HTTPException(400, f"Host port {port}/{mapping.protocol} is given twice")
        given.add((port, mapping.protocol))
        bindings[spec] = port
    return bindings


def check_ports(bindings: dict[str, int], containers: Iterable[Any]) -> None:
    """409 when a host port of ``bindings`` ("<container port>/<protocol>" to the host port) is
    taken: published by one of ``containers`` (Docker's, running or stopped: docker-py's
    Container objects with their inspect data), or held by a program on the host. The host is
    read only for ports that no container publishes; nothing is read for an app without ports.
    Blocks: call it in a worker thread."""
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
            problems.append(
                f"Port {port}/{protocol} is already used by the container {published[port, protocol]}: "
                f"choose another host port for {spec}"
            )
        elif (port, protocol) in listening:
            problems.append(
                f"Port {port}/{protocol} is already in use on the host by {listening[port, protocol] or 'a program'}: "
                f"choose another host port for {spec}"
            )
    if problems:
        raise HTTPException(409, "; ".join(problems))


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

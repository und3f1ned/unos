"""Local names for apps: ``<label>-<host name>.local``, announced by avahi on the LAN and routed by the
web UI's Caddy to the app's port on the host (ADR-0420).

An administrator gives a published TCP port of an installed app a label; the app's web port, the first
TCP port of its catalog template, is offered first. The list, NAMES_FILE in the volume APP_NAMES_DIR,
is written only on a change. From it:

- SITES_FILE (in SITES_DIR of the same volume, which webui/Caddyfile imports) holds one Caddy site per
  name, HTTPS from Caddy's local CA, with ``reverse_proxy`` to GATEWAY (the host on unos-net) and the
  app's port; Caddy loads it with ``caddy reload`` (security/serving.py, ADR-0404), and a file it
  refuses is put back as it was;
- the host's unit UNIT (UNIT_FILE) announces each name with ``avahi-publish -a -R``, an alias of the
  host's IPv4 address of its default route, which the unit reads when it starts: at boot, and when a
  change or Announce again restarts it. Not /etc/avahi/hosts: avahi refuses a static host of the host's
  own address (its reverse record is the host's own), which ``-R`` leaves out;
- UNOS's firewall rules (remote_rules.py) let Caddy reach the apps' ports through ufw.

The changes hold remote access's lock (wireguard.lock), so that the two never take back each other's
firewall rules. Host commands run through src/host.py under the host's timeout(1) (security.firewall.run).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import HTTPException

from .. import host, state_files
from ..security import firewall
from . import remote_rules, wireguard

logger = logging.getLogger(__name__)

# The volume app-names (deploy/docker-compose.yml): read-write here, read-only in the web UI.
APP_NAMES_DIR = "/app-names"
NAMES_FILE = "names.json"
# webui/Caddyfile imports {$UNOS_APP_NAMES:on}/*.caddy of the volume: the web UI's command sets
# UNOS_APP_NAMES=off when Caddy does not validate the file, so that it never takes the web UI down.
SITES_DIR = "on"
SITES_FILE = "sites.caddy"
UNIT = "unos-app-names.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
# The host on unos-net (deploy/docker-compose.yml): the web UI's Caddy reaches the apps' ports there.
GATEWAY = "172.31.254.1"
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
HOST_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
MAX_NAMES = 32
# A DNS label holds at most 63 characters.
MAX_LABEL = 63

# The unit's command, one argument of /bin/sh -c: $0 is the unit's name, the names follow. It reads the
# address of the host's default route (as ip route get of a TEST-NET-1 address shows it), waits for one
# without a log line, and runs avahi-publish for each name. systemd expands "${NAME}" and "$$" in a
# command's arguments, "%" begins a specifier and backslashes escape: the script has none of them
# (tests/test_app_names.py).
ANNOUNCE_SCRIPT = (
    "addr() { set -- $(ip -4 route get 192.0.2.1 2> /dev/null); while [ $# -gt 1 ]; do "
    "if [ \"$1\" = src ]; then echo \"$2\"; return; fi; shift; done; }; "
    "a=$(addr); while [ -z \"$a\" ]; do sleep 30; a=$(addr); done; "
    "for n do avahi-publish -a -R \"$n\" \"$a\" & done; wait"
)
# The addresses that the unit's avahi-publish processes announce, one line each: the arguments of each
# child of the unit's main process. Exit status 3: the unit does not run.
ANNOUNCED_SCRIPT = (
    'pid=$(systemctl show -p MainPID --value "$1")\n'
    '[ -n "$pid" ] && [ "$pid" != 0 ] || exit 3\n'
    'for c in $(cat "/proc/$pid/task/$pid/children" 2> /dev/null); do\n'
    '  tr "\\000" " " < "/proc/$c/cmdline" 2> /dev/null; echo\n'
    'done\n'
)
NOT_RUNNING = 3

# Texts that webui/tests/remote-access.spec.ts repeats word for word (ADR-0189).
LOCAL_DETAIL = (
    "App names are off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): "
    "this is not a NAS host"
)
NO_VOLUME = "The volume app-names is not mounted in the supervisor: deploy UNOS's compose file again"
NO_ANNOUNCER = "avahi-utils is not installed on the host: install the package avahi-utils to announce app names"


@dataclass(frozen=True)
class Entry:
    label: str
    app: str
    port: int


def names_path() -> Path:
    return Path(APP_NAMES_DIR) / NAMES_FILE


def sites_path() -> Path:
    return Path(APP_NAMES_DIR) / SITES_DIR / SITES_FILE


def check_label(value: str) -> str:
    value = value.strip()
    if not LABEL.fullmatch(value):
        raise ValueError("the label must be 1 to 32 characters a-z, 0-9 and -, not starting or ending with -")
    return value


def _read(value: object) -> list[Entry]:
    if not isinstance(value, list):
        raise ValueError("not a list")
    entries = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("not an entry")
        port = item.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535 or not isinstance(item.get("app"), str):
            raise ValueError("not an entry")
        entries.append(Entry(check_label(str(item.get("label"))), item["app"], port))
    return entries


def load() -> list[Entry]:
    """The names an administrator gave; none without the volume or the file."""
    return state_files.load_json(names_path(), _read, list, what="the app names")


def wanted_ports() -> list[int]:
    """The ports that Caddy reaches for the names, for UNOS's firewall rules."""
    if not os.path.isdir(APP_NAMES_DIR):
        return []
    return sorted({entry.port for entry in load()})


def fqdn(label: str, host_name: str) -> str:
    return f"{label}-{host_name}.local"


def render_sites(entries: list[Entry], host_name: str) -> str:
    """The Caddy sites of the names: every value checked, nothing an administrator typed but labels."""
    lines = ["# UNOS app names (ADR-0420): written by the supervisor, System > Remote Access."]
    for entry in entries:
        lines += [
            "",
            f"{fqdn(check_label(entry.label), host_name)} {{",
            "\ttls internal",
            f"\treverse_proxy {GATEWAY}:{int(entry.port)}",
            "}",
        ]
    return "\n".join(lines) + "\n"


def render_unit(entries: list[Entry], host_name: str) -> str:
    """The unit that announces the names (every name checked: a-z, 0-9, - and dots)."""
    names = " ".join(fqdn(check_label(entry.label), host_name) for entry in entries)
    return (
        "# UNOS app names (ADR-0420): written by UNOS, System > Remote Access. Changes made here are lost.\n"
        "[Unit]\n"
        "Description=UNOS: announce the local names of apps\n"
        "Requires=avahi-daemon.service\n"
        "After=avahi-daemon.service network-online.target\n"
        "Wants=network-online.target\n"
        "PartOf=avahi-daemon.service\n"
        "\n"
        "[Service]\n"
        f"ExecStart=/bin/sh -c '{ANNOUNCE_SCRIPT}' unos-app-names {names}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def announced(text: str) -> str | None:
    """The address in the output of ANNOUNCED_SCRIPT: the last word of the first avahi-publish line."""
    for line in text.splitlines():
        words = line.split()
        if words and words[0].rpartition("/")[2] == "avahi-publish" and len(words) >= 2:
            try:
                return str(ipaddress.IPv4Address(words[-1]))
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# The host
# ---------------------------------------------------------------------------


def refuse_locally() -> None:
    if host.runs_locally():
        raise HTTPException(503, LOCAL_DETAIL)


def _require_volume() -> None:
    if not os.path.isdir(APP_NAMES_DIR):
        raise HTTPException(409, NO_VOLUME)


async def has_announcer() -> bool:
    """Whether the host has avahi-publish (OPTIONAL_HOST_TOOLS, package avahi-utils)."""
    missing = await asyncio.to_thread(host.missing_host_binaries, ["avahi-publish"])
    if missing is None:
        raise HTTPException(500, "Whether the host has avahi-utils could not be checked")
    return not missing


async def _require_announcer() -> None:
    if not await has_announcer():
        raise HTTPException(409, NO_ANNOUNCER)


async def host_name() -> str:
    result = await firewall.run(["hostname"])
    name = result.stdout.strip().lower() if result.ok else ""
    if not HOST_NAME.fullmatch(name):
        raise HTTPException(500, f"Could not read the host's name: {firewall.reason(result) if not result.ok else name!r}")
    return name


async def host_address() -> str | None:
    """The host's IPv4 address on the interface of its default route; None without one."""
    routes = await firewall.run(["ip", "-j", "-4", "route", "show", "default"])
    try:
        found = json.loads(routes.stdout or "[]") if routes.ok else []
    except ValueError:
        found = []
    devices = [route.get("dev") for route in found if isinstance(route, dict) and route.get("dev")]
    for device in devices:
        addresses = await firewall.run(["ip", "-j", "-4", "addr", "show", "dev", device])
        try:
            links = json.loads(addresses.stdout or "[]") if addresses.ok else []
        except ValueError:
            continue
        for link in links:
            for info in link.get("addr_info") or []:
                if info.get("scope") == "global" and info.get("local"):
                    try:
                        return str(ipaddress.IPv4Address(str(info["local"])))
                    except ValueError:
                        continue
    return None


async def announced_address() -> str | None:
    """The address that the unit announces the names with now; None while it does not run."""
    result = await firewall.run(["/bin/sh", "-c", ANNOUNCED_SCRIPT, "unos-app-names-announced", UNIT])
    return announced(result.stdout) if result.ok else None


async def _read_unit() -> str | None:
    result = await firewall.run(["cat", UNIT_FILE])
    if result.ok:
        return result.stdout
    if "No such file" in result.stderr:
        return None
    raise HTTPException(500, f"Could not read {UNIT_FILE}: {firewall.reason(result)}")


async def _systemctl(*args: str) -> None:
    result = await firewall.run(["systemctl", *args])
    if not result.ok:
        raise HTTPException(500, f"Failed to announce the app names (systemctl {' '.join(args)}): "
                                 f"{firewall.reason(result)}")


async def _install_unit(text: str | None, *, restart: bool) -> None:
    """Make UNIT announce ``text``'s names (None: none), restarted when it changed or ``restart``."""
    current = await _read_unit()
    if text is None:
        if current is None:
            return
        await _systemctl("disable", "--now", UNIT)
        removed = await firewall.run(["rm", "-f", UNIT_FILE])
        if not removed.ok:
            raise HTTPException(500, f"Could not remove {UNIT_FILE}: {firewall.reason(removed)}")
        await _systemctl("daemon-reload")
        return
    if current != text:
        try:
            await asyncio.to_thread(host.write_host_file_atomic, UNIT_FILE, text, mode="0644")
        except Exception as exc:
            logger.exception("Could not write %s", UNIT_FILE)
            raise HTTPException(500, f"Could not write {UNIT_FILE}: {type(exc).__name__}") from exc
        await _systemctl("daemon-reload")
    elif not restart:
        return
    await _systemctl("enable", UNIT)
    await _systemctl("restart", UNIT)


def installed_apps() -> list[tuple[str, int]]:
    """The published TCP ports of the installed apps' main containers, (app, host port): each app's web
    port first, the host port of the first TCP port of its catalog template, then the others. Blocking
    (Docker)."""
    from ..apps import stacks
    from ..apps.catalog import get_app_by_id
    from ..containers.docker import get_client

    try:
        containers = get_client().containers.list(all=True, filters={"label": stacks.UNOS_APP_LABEL},
                                                  ignore_removed=True)
    except stacks.DOCKER_ERRORS as exc:
        raise HTTPException(503, f"Docker could not list the apps: {exc}") from None
    members: dict[str, list] = {}
    for container in containers:
        members.setdefault(stacks.app_key(container), []).append(container)
    found: list[tuple[str, int]] = []
    for key, group in members.items():
        template = get_app_by_id(group[0].labels.get(stacks.UNOS_APP_LABEL, ""))
        main = stacks.group(key, group, template).main
        if main is None:
            continue
        published = (main.attrs.get("NetworkSettings") or {}).get("Ports") or {}
        web = next((f"{p.container}/tcp" for p in (template.ports if template else []) if p.protocol == "tcp"), None)

        def order(spec: str) -> tuple[bool, int]:
            number = spec.partition("/")[0]
            return spec != web, int(number) if number.isdigit() else 0  # noqa: B023 - sorted() below, in this iteration

        ports: list[int] = []
        for spec in sorted(published, key=order):
            if not spec.endswith("/tcp"):
                continue
            for binding in published[spec] or []:
                port = str(binding.get("HostPort") or "")
                if port.isdigit() and int(port) not in ports:
                    ports.append(int(port))
                    break
        found += [(main.name, port) for port in ports]
    return found


async def _apps() -> list[tuple[str, int]]:
    try:
        return await asyncio.to_thread(installed_apps)
    except HTTPException as exc:
        logger.warning("The app names list no installed apps: %s", exc.detail)
        return []


def _current_tls() -> str:
    """The UNOS_TLS that Caddy serves with now: the served certificate when it loaded (ADR-0404)."""
    from ..security import serving

    served = serving.read_served()
    if served is None:
        return "local"
    probed = serving.probe_fingerprint()
    if probed is not None and probed != serving.leaf_fingerprint(served):
        return "local"
    return "served"


def _install_sites(text: str | None, force: bool) -> None:
    """Put the sites file in place (None: none) and have Caddy load it; Caddy's refusal or a Caddy
    that cannot be reached puts the file back as it was. Blocking: call it in a worker thread."""
    from ..security import serving
    from ..security.router import _CERTIFICATES_LOCK

    path = sites_path()
    with _CERTIFICATES_LOCK:
        try:
            previous = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            previous = None
        if previous == text and not force:
            return
        _put(path, text)
        try:
            serving.reload_caddy(_current_tls())
        except serving.CaddyTimedOut:
            raise
        except BaseException:
            _put(path, previous)
            raise


def _put(path: Path, text: str | None) -> None:
    if text is None:
        path.unlink(missing_ok=True)
        if not path.parent.is_dir():
            return
    else:
        path.parent.mkdir(mode=0o755, exist_ok=True)
        state_files.replace_file(path, text.encode(), sync=True, mode=0o644)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def _caddy(text: str | None, force: bool) -> None:
    from ..security import serving

    try:
        await asyncio.to_thread(_install_sites, text, force)
    except serving.CaddyTimedOut as exc:
        raise HTTPException(504, f"The web UI's Caddy did not answer in time: {exc}") from None
    except serving.CaddyRefused as exc:
        logger.warning("Caddy did not take the app names: %s", exc)
        raise HTTPException(502, f"Caddy did not load the app names and serves what it served before: {exc}") from None
    except serving.CaddyUnreachable as exc:
        raise HTTPException(503, f"The web UI's Caddy cannot be reached: {exc}") from None


async def _apply(entries: list[Entry], *, force: bool = False) -> None:
    """Make Caddy, the announcing unit, the list and the firewall follow ``entries``.

    The firewall first lets Caddy reach every port of the old list and the new (an allow rule cuts
    nobody off), then the unit, then Caddy, whose refusal puts the unit back; the list is saved only
    once both took it, and the rules of ports no longer named go last (one left behind is logged)."""
    old = load()
    name = await host_name()
    if entries and await host_address() is None:
        raise HTTPException(409, "The host has no IPv4 address on its default route: avahi cannot announce the names")
    for entry in entries:
        if len(f"{entry.label}-{name}") > MAX_LABEL:
            raise HTTPException(409, f"The name {fqdn(entry.label, name)} is longer than a DNS label may be")
    await remote_rules.sync(app_ports=[e.port for e in (*old, *entries)])
    old_unit = await _read_unit()
    await _install_unit(render_unit(entries, name) if entries else None, restart=force)
    try:
        await _caddy(render_sites(entries, name) if entries else None, force)
    except HTTPException:
        try:
            await _install_unit(old_unit, restart=False)
        except HTTPException as exc:
            logger.warning("The app names' unit could not be put back: %s", exc.detail)
        raise
    if entries != old:
        await asyncio.to_thread(state_files.write_json, names_path(), [asdict(e) for e in entries], sync=True)
    try:
        await remote_rules.sync(app_ports=[e.port for e in entries])
    except HTTPException as exc:
        logger.warning("A firewall rule of a port no longer named stays: %s", exc.detail)


# ---------------------------------------------------------------------------
# The API's work
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Status:
    volume: bool
    announcer: bool
    host_name: str
    address: str | None
    announced: str | None
    entries: list[Entry]
    apps: list[tuple[str, int]]


async def status() -> Status:
    refuse_locally()
    volume = os.path.isdir(APP_NAMES_DIR)
    entries = load() if volume else []
    return Status(volume, await has_announcer(), await host_name(), await host_address(),
                  await announced_address() if entries else None, entries, await _apps())


async def add(label: str, app: str, port: int | None = None) -> Entry:
    refuse_locally()
    _require_volume()
    await _require_announcer()
    async with wireguard.lock():
        entries = load()
        if any(entry.label == label for entry in entries):
            raise HTTPException(409, f"An app has the name {label} already")
        if len(entries) >= MAX_NAMES:
            raise HTTPException(409, f"UNOS gives at most {MAX_NAMES} app names")
        ports = [p for name, p in await _apps() if name == app]
        if port is None and ports:
            port = ports[0]
        if port is None or port not in ports:
            raise HTTPException(409, f"No installed app {app} publishes the TCP port {port}" if port
                                else f"No installed app {app} publishes a port")
        await remote_rules.refuse_while_firewall_waits()
        entry = Entry(label, app, port)
        await _apply([*entries, entry])
        logger.info("App name %s for %s (port %d)", label, app, port)
        return entry


async def remove(label: str) -> Entry:
    refuse_locally()
    _require_volume()
    async with wireguard.lock():
        entries = load()
        entry = next((e for e in entries if e.label == label), None)
        if entry is None:
            raise HTTPException(404, f"No app has the name {label}")
        await remote_rules.refuse_while_firewall_waits()
        await _apply([e for e in entries if e.label != label])
        logger.info("App name %s removed", label)
        return entry


async def apply_again() -> None:
    """Announce the names with the host's address of now and have Caddy load them again."""
    refuse_locally()
    _require_volume()
    await _require_announcer()
    async with wireguard.lock():
        await remote_rules.refuse_while_firewall_waits()
        await _apply(load(), force=True)


async def reconcile_on_startup() -> None:
    """At the supervisor's start: Caddy's file and the announcing unit as the list renders them, for a
    host whose name changed. Writes and reloads only what changed; logs, never raises."""
    if host.runs_locally() or not os.path.isdir(APP_NAMES_DIR):
        return
    try:
        entries = load()
        if not entries and not os.path.exists(sites_path()):
            return
        async with wireguard.lock():
            await _apply(entries)
    except HTTPException as exc:
        logger.warning("The app names could not be applied at the start: %s", exc.detail)
    except Exception:
        logger.exception("The app names could not be applied at the start")

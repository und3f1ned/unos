"""Bonds, VLANs and bridges of the host, made and removed through systemd-networkd (ADR-0424).

Every command runs on the host through src/host.py (ADR-0020), under the host's timeout(1).
UNOS changes the host's network only where systemd-networkd keeps it and NetworkManager does not
run: each virtual interface is a .netdev and a .network file of networkd's own, and each port a
.network file that comes before the host's own (the prefixes ``04-unos-`` and ``05-unos-``); a VLAN's
parent gets a .network file of UNOS's that holds its own settings and the VLAN= of its VLANs. Live, the files are in LIVE_DIR, /run, where
networkd reads them too; KEPT_DIR on the system disk holds the kept ones, which the host's unit
BOOT_UNIT puts back into LIVE_DIR before networkd starts at boot. A change that is not kept
therefore never outlives a reboot.

Every change records what it adds and removes in STATE_DIR on the host first (SAVE_SCRIPT). A
change that touches an interface that carries the default route or the asking computer's route,
or a port, parent or master of one (``protected``), also writes the marker ``pending`` and arms the
transient systemd timer REVERT_UNIT, as the firewall's revert does (ADR-0416): unless the change is
kept within REVERT_SECONDS (keep()), the host undoes it (REVERT_SCRIPT), also without the
supervisor. Other changes are kept as soon as networkd has made them. A change that networkd does
not make within VERIFY_SECONDS is undone at once (UNDO_SCRIPT), and by the timer when that fails.
UNOS lists, changes and removes only the interfaces whose .netdev file it wrote.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import subprocess
import time
import weakref
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException

from .. import host
from ..security.validators import INTERFACE_NAME_PATTERN, sanitize_error_message
from . import links
from .links import is_hidden

# On the host. STATE_DIR and LIVE_DIR are in /run, a tmpfs: a change that waits to be kept costs no
# writes to the system disk, and a reboot drops it. KEPT_DIR is written only when a change is kept.
STATE_DIR = "/run/unos/netdevs"
LIVE_DIR = "/run/systemd/network"
KEPT_DIR = "/etc/unos/netdevs"
LEASES_DIR = "/run/systemd/netif/leases"
BOOT_UNIT = "unos-netdevs.service"
BOOT_UNIT_FILE = f"/etc/systemd/system/{BOOT_UNIT}"
REVERT_UNIT = "unos-netdevs-revert"
REVERT_SECONDS = 120
VERIFY_SECONDS = 20
VERIFY_INTERVAL = 1.0
PREFIX = "05-unos-"
# A port's file comes before every interface's own, so that a VLAN or a bond that is a bridge's port
# takes its port's file, and networkd applies the first file that matches.
PORT_PREFIX = "04-unos-"

HOST_TIMEOUT = 30
KILL_AFTER = 5
COMMAND_TIMEOUT = HOST_TIMEOUT + KILL_AFTER + 5
TIMED_OUT = 124
NO_ANSWER = -1
# The exit status of CANCEL_SCRIPT when no change waits to be kept.
NOTHING_PENDING = 3

MIN_MTU = 1280
MAX_MTU = 9216
MAX_PORTS = 8
MAX_ADDRESSES = 4
BOND_MODES = ("802.3ad", "balance-rr")
# Names the kernel or UNOS keep for themselves: sysfs's bonding_masters, /proc's all and default.
RESERVED_NAMES = {"lo", "all", "default", "bonding_masters", "unos-wg"}

# The texts that webui/tests/network-fakes.ts repeats word for word (ADR-0189).
LOCAL_DETAIL = (
    "UNOS runs its host commands locally (UNOS_HOST_EXEC=local): it does not change this machine's network"
)
CHANGE_WAITS = "A network change waits to be kept: keep it, or wait until it is undone"
REVERT_RUNNING = "The last network change is being undone: try again in a few seconds"
NOTHING_TO_KEEP = "No network change waits to be kept: it was kept already, or it was undone"
NOT_NETWORKD = (
    "UNOS makes bonds, VLANs and bridges only where systemd-networkd keeps the host's network, "
    "and this host's is kept by {manager}"
)

Kind = Literal["bond", "vlan", "bridge"]
Addressing = Literal["none", "dhcp", "static", "copied"]


# ---------------------------------------------------------------------------
# The host scripts. They run with /bin/sh (dash on Debian and Ubuntu): $0 names the script, $1 is
# STATE_DIR, $2 LIVE_DIR, $3 KEPT_DIR. A path is the name of a file in LIVE_DIR and KEPT_DIR, one
# that UNOS made (_relative_ok). REVERT_SCRIPT is the command of a transient systemd unit, and systemd
# expands "${NAME}" and "$$" in a command's arguments and "%" begins a specifier: the revert has
# none of them (tests/test_virtual_interfaces_scripts.py).
# ---------------------------------------------------------------------------

# $4...: "add:<path>" a file the change writes into LIVE_DIR, "replace:<path>" one it writes over
# and "remove:<path>" one it removes from there (a copy of those two goes into saved/),
# "created:<name>" an interface it makes, "change:<text>" what it is. The ops file keeps their
# order: the files of an interface before those that refer to it.
SAVE_SCRIPT = """\
set -e
umask 077
rm -rf "$1"
mkdir -p "$1/saved"
s=$1
l=$2
shift 3
: > "$s/ops"
: > "$s/created"
for a in "$@"; do
  case "$a" in
    add:*) echo "add ${a#add:}" >> "$s/ops" ;;
    remove:*|replace:*)
      op=${a%%:*}
      r=${a#*:}
      echo "$op $r" >> "$s/ops"
      cp -p "$l/$r" "$s/saved/$r" ;;
    created:*) echo "${a#created:}" >> "$s/created" ;;
    change:*) echo "${a#change:}" > "$s/change" ;;
  esac
done
"""

# $4: the time of the revert, in seconds since the epoch.
MARK_SCRIPT = 'echo "$4" > "$1/pending"\n'

# undo STATE LIVE: the change's ops in reverse order (sed: tac), so that a file is removed before
# the files it refers to and put back after them; the interfaces it made deleted (networkd removes
# none); networkd reloaded, which reconfigures each interface whose files changed. A step that fails
# does not stop the others, and networkd is reloaded in any case: as much as can be is put back.
_UNDO = """\
undo() {
  rc=0
  sed '1!G;h;$!d' "$1/ops" > "$1/ops.reversed" || return 1
  while read -r op r; do
    case "$op" in
      add) rm -f "$2/$r" || rc=1 ;;
      remove|replace) cp -p "$1/saved/$r" "$2/$r" || rc=1 ;;
    esac
  done < "$1/ops.reversed"
  for n in $(cat "$1/created"); do
    if ip link show dev "$n" > /dev/null 2>&1; then ip link delete dev "$n" || rc=1; fi
  done
  networkctl reload || rc=1
  return $rc
}
"""

UNDO_SCRIPT = _UNDO + 'undo "$1" "$2"\n'

# The timer's command: nothing to do for a change that was kept; a revert that fails leaves
# "failed" for the status to report. "undoing" is the marker of a change that failed and waits for
# the timer to undo it: no Keep takes it.
REVERT_SCRIPT = (
    '{ rm "$1/pending" 2> /dev/null || rm "$1/undoing" 2> /dev/null; } || exit 0\n' + _UNDO
    + 'undo "$1" "$2" || { echo failed > "$1/failed"; exit 1; }\n'
    + 'rm -rf "$1/saved" "$1/ops" "$1/ops.reversed" "$1/created" "$1/change"\n'
)

# Keep the change that waits: the revert and a Keep both start by taking the marker, and only one of
# them can (rename is atomic). "kept" says that the change is the administrator's until PERSIST has
# written it to KEPT_DIR. Exit status 3: none waits.
CANCEL_SCRIPT = """\
mv "$1/pending" "$1/kept" 2> /dev/null || exit 3
systemctl stop unos-netdevs-revert.timer 2> /dev/null
exit 0
"""

# A change that waits no longer, undone at once: its marker and timer, and its record.
DISCARD_SCRIPT = """\
rm -f "$1/pending" "$1/undoing"
systemctl stop unos-netdevs-revert.timer 2> /dev/null
rm -rf "$1"
exit 0
"""

# Write the change into KEPT_DIR in the order of its ops, each file atomically, so that the kept
# files never refer to an interface they lack, also after a power cut; then forget the change.
PERSIST_SCRIPT = """\
set -e
umask 022
if [ ! -e "$1/ops" ]; then rm -rf "$1"; exit 0; fi
mkdir -p "$3"
while read -r op r; do
  case "$op" in
    add|replace)
      cp -p "$2/$r" "$3/$r.unos-tmp"
      sync "$3/$r.unos-tmp" 2> /dev/null || sync
      mv -f "$3/$r.unos-tmp" "$3/$r" ;;
    remove) rm -f "$3/$r" ;;
  esac
done < "$1/ops"
sync "$3" 2> /dev/null || sync
rm -rf "$1"
"""

# $4: a file of LIVE_DIR; its text on standard input.
WRITE_SCRIPT = """\
set -e
umask 022
mkdir -p "$2"
cat > "$2/$4.unos-tmp"
mv -f "$2/$4.unos-tmp" "$2/$4"
"""

# $4: a file of LIVE_DIR to remove.
REMOVE_SCRIPT = 'rm -f "$2/$4"\n'

# The metadata line of each interface that UNOS made, live.
ITEMS_SCRIPT = """\
for f in "$2"/05-unos-*.netdev; do
  [ -e "$f" ] || continue
  sed -n 's/^# unos: //p' "$f"
done
"""

# Whether KEPT_DIR still holds an interface's .netdev.
KEPT_ANY_SCRIPT = """\
for f in "$3"/05-unos-*.netdev; do
  [ -e "$f" ] && exit 0
done
exit 1
"""

# Each interface of the host's sysfs: its name, physical or virtual, wireless or wired.
SYSFS_SCRIPT = """\
for d in /sys/class/net/*; do
  [ -e "$d" ] || continue
  p=virtual
  [ -e "$d/device" ] && p=physical
  w=wired
  [ -e "$d/wireless" ] && w=wireless
  echo "$(basename "$d") $p $w"
done
"""

SCRIPTS = {
    "unos-netdevs-save": SAVE_SCRIPT,
    "unos-netdevs-mark": MARK_SCRIPT,
    "unos-netdevs-undo": UNDO_SCRIPT,
    "unos-netdevs-revert": REVERT_SCRIPT,
    "unos-netdevs-cancel": CANCEL_SCRIPT,
    "unos-netdevs-discard": DISCARD_SCRIPT,
    "unos-netdevs-persist": PERSIST_SCRIPT,
    "unos-netdevs-write": WRITE_SCRIPT,
    "unos-netdevs-remove": REMOVE_SCRIPT,
    "unos-netdevs-items": ITEMS_SCRIPT,
    "unos-netdevs-kept-any": KEPT_ANY_SCRIPT,
    "unos-netdevs-sysfs": SYSFS_SCRIPT,
}

# The host's unit that puts the kept files where networkd reads them, before it starts: the .netdev
# files first, so that a copy that stops part way leaves no file that names an interface it lacks.
# systemd reads "$$" in a unit file as "$".
BOOT_UNIT_TEXT = f"""\
# Made by UNOS (ADR-0424): the bonds, VLANs and bridges kept in UNOS, for systemd-networkd.
[Unit]
Description=UNOS: put the kept bonds, VLANs and bridges where systemd-networkd reads them
DefaultDependencies=no
Before=systemd-networkd.service network-pre.target
RequiresMountsFor={KEPT_DIR} /run
ConditionPathIsDirectory={KEPT_DIR}

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'mkdir -p {LIVE_DIR} && for f in {KEPT_DIR}/*.netdev {KEPT_DIR}/*.network; do [ ! -e "$$f" ] || cp -p "$$f" {LIVE_DIR}/ || exit 1; done'

[Install]
WantedBy=systemd-networkd.service
"""

# One lock per event loop (cf. network.links).
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    """Serializes the changes of virtual interfaces."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def _script(name: str, *args: str) -> list[str]:
    return ["/bin/sh", "-c", SCRIPTS[name], name, STATE_DIR, LIVE_DIR, KEPT_DIR, *args]


def revert_timer() -> list[str]:
    """The command that arms the revert on the host."""
    return [
        "systemd-run",
        f"--unit={REVERT_UNIT}",
        "--description=UNOS: undo the network change unless it is kept",
        f"--on-active={REVERT_SECONDS}",
        "--timer-property=AccuracySec=1s",
        "--timer-property=RemainAfterElapse=no",
        "--setenv=LC_ALL=C",
        "--collect",
        "--quiet",
        "--",
        *_script("unos-netdevs-revert"),
    ]


async def run(cmd: list[str], *, input: str | None = None) -> host.HostResult:
    """Run ``cmd`` on the host under its timeout(1), in English; a command that could not run, or
    that the host did not answer in time (NO_ANSWER), is a failed result."""
    timed = ["timeout", f"--kill-after={KILL_AFTER}", str(HOST_TIMEOUT), "env", "LC_ALL=C", *cmd]
    try:
        result = await host.run_on_host_async(timed, timeout=COMMAND_TIMEOUT, input=input, errors="replace")
    except subprocess.TimeoutExpired:
        return host.HostResult(NO_ANSWER, "", f"the host did not answer within {COMMAND_TIMEOUT} seconds")
    except (OSError, subprocess.SubprocessError) as exc:
        return host.HostResult(NO_ANSWER, "", sanitize_error_message(exc) or type(exc).__name__)
    if result.returncode == TIMED_OUT:
        return host.HostResult(TIMED_OUT, "", f"it did not finish within {HOST_TIMEOUT} seconds on the host")
    return result


def reason(result: host.HostResult) -> str:
    """What a failed host command said, in one line, for an error message."""
    output = result.stderr if result.stderr.strip() else result.stdout
    text = "; ".join(dict.fromkeys(line.strip() for line in output.splitlines() if line.strip()))
    return sanitize_error_message(RuntimeError(text)) if text else f"exit status {result.returncode}"


def _refuse_locally() -> None:
    """503 where the supervisor runs its host commands on the machine it runs on (ADR-0233)."""
    if host.runs_locally():
        raise HTTPException(503, LOCAL_DETAIL)


def _json(text: str, what: str) -> list:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        raise HTTPException(500, f"Failed to parse {what}")
    if not isinstance(data, list):
        raise HTTPException(500, f"Failed to parse {what}")
    return [entry for entry in data if isinstance(entry, dict)]


async def _ok(cmd: list[str], what: str) -> str:
    result = await run(cmd)
    if not result.ok:
        raise HTTPException(500, f"Could not read {what}: {reason(result)}")
    return result.stdout


# ---------------------------------------------------------------------------
# What UNOS made: the metadata line of each .netdev
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """A virtual interface that UNOS made, as its .netdev file's metadata line records it."""

    kind: Kind
    name: str
    files: list[str]
    mode: str | None = None
    ports: list[str] = field(default_factory=list)
    parent: str | None = None
    vlan_id: int | None = None
    mtu: int | None = None
    addressing: Addressing = "none"
    addresses: list[str] = field(default_factory=list)
    copied_from: str | None = None

    def metadata(self) -> str:
        return json.dumps({
            "kind": self.kind, "name": self.name, "mode": self.mode, "ports": self.ports,
            "parent": self.parent, "vlan_id": self.vlan_id, "mtu": self.mtu,
            "addressing": self.addressing, "addresses": self.addresses,
            "copied_from": self.copied_from, "files": self.files,
        }, separators=(",", ":"))


def valid_name(name: str) -> bool:
    """A name UNOS gives an interface: the kernel's limit of 15 characters, letters, digits, '_', '.'
    and '-', not one of the names the kernel, Docker or UNOS keep."""
    return (
        isinstance(name, str) and INTERFACE_NAME_PATTERN.fullmatch(name) is not None
        and name not in RESERVED_NAMES and not is_hidden(name) and not name.startswith(("docker", "dummy"))
    )


def _relative_ok(path: str, name: str) -> bool:
    """A file that UNOS writes for the interface ``name``: its .netdev and .network, or a port's."""
    if path in (f"{PREFIX}{name}.netdev", f"{PREFIX}{name}.network"):
        return True
    port = path.removeprefix(f"{PORT_PREFIX}{name}+").removesuffix(".network")
    return path.startswith(f"{PORT_PREFIX}{name}+") and path.endswith(".network") and valid_port_name(port)


def parse_item(line: str) -> Item | None:
    """An interface from its metadata line; None when the line is not one that UNOS writes."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind, name, files = data.get("kind"), data.get("name"), data.get("files")
    if kind not in ("bond", "vlan", "bridge") or not valid_name(name):
        return None
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and _relative_ok(f, name) for f in files):
        return None
    ports = data.get("ports") or []
    if not isinstance(ports, list) or not all(valid_port_name(p) for p in ports):
        return None
    parent = data.get("parent")
    if parent is not None and not valid_port_name(parent):
        return None
    vlan_id = data.get("vlan_id")
    if kind == "vlan" and (not isinstance(vlan_id, int) or not 1 <= vlan_id <= 4094 or parent is None):
        return None
    mtu = data.get("mtu")
    if mtu is not None and (not isinstance(mtu, int) or not MIN_MTU <= mtu <= MAX_MTU):
        return None
    addressing = data.get("addressing", "none")
    if addressing not in ("none", "dhcp", "static", "copied"):
        return None
    addresses = data.get("addresses") or []
    if not isinstance(addresses, list) or not all(isinstance(a, str) for a in addresses):
        return None
    mode = data.get("mode")
    if kind == "bond" and mode not in BOND_MODES:
        return None
    copied_from = data.get("copied_from")
    if copied_from is not None and not valid_port_name(copied_from):
        return None
    return Item(kind=kind, name=name, files=files, mode=mode if kind == "bond" else None, ports=ports,
                parent=parent, vlan_id=vlan_id if kind == "vlan" else None, mtu=mtu, addressing=addressing,
                addresses=addresses, copied_from=copied_from)


def valid_port_name(name: object) -> bool:
    """A name of the host's interfaces, as the kernel takes it, that UNOS reads from its files."""
    return isinstance(name, str) and INTERFACE_NAME_PATTERN.fullmatch(name) is not None


async def _items() -> tuple[dict[str, Item], list[str]]:
    """The interfaces that UNOS made, live, and the metadata lines it could not read."""
    text = await _ok(_script("unos-netdevs-items"), "the bonds, VLANs and bridges made in UNOS")
    items: dict[str, Item] = {}
    unreadable: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        item = parse_item(line)
        if item is None or item.name in items:
            unreadable.append(line.strip()[:200])
        else:
            items[item.name] = item
    return items, unreadable


# ---------------------------------------------------------------------------
# The host's network as UNOS reads it
# ---------------------------------------------------------------------------


@dataclass
class Link:
    """An interface of the host: ``ip -d -j link``, its addresses, sysfs and networkd's view."""

    name: str
    index: int
    mac: str
    operstate: str
    kind: str            # physical, bond, bridge, vlan or the kernel's kind (dummy, wireguard, ...)
    wireless: bool
    master: str | None
    parent: str | None   # the link of a VLAN or of another stacked interface
    vlan_id: int | None
    addresses: list[str]
    setup: str           # networkd's setup state: configured, unmanaged, ...


@dataclass
class Snapshot:
    manager: str
    links: dict[str, Link]
    protected: set[str]
    items: dict[str, Item]
    unreadable: list[str]
    client_route: str | None


async def _active(unit: str) -> bool:
    return (await run(["systemctl", "is-active", "--quiet", unit])).ok


async def manager() -> str:
    """Who keeps the host's network: NetworkManager, systemd-networkd, ifupdown or none."""
    if await _active("NetworkManager.service"):
        return "NetworkManager"
    if await _active("systemd-networkd.service"):
        return "systemd-networkd"
    if await _active("networking.service"):
        return "ifupdown"
    return "none"


def parse_networkctl_list(text: str) -> dict[str, str]:
    """Each link's setup state in ``networkctl list --no-legend``: IDX LINK TYPE OPERATIONAL SETUP."""
    setup: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit():
            setup[parts[1]] = parts[4]
    return setup


_NETWORK_FILE_LINE = re.compile(r"^\s*Network File:\s*(\S+)\s*$", re.MULTILINE)


def parse_network_file(text: str) -> str | None:
    """The .network file that networkd applies to a link, from ``networkctl status <link>``."""
    match = _NETWORK_FILE_LINE.search(text)
    if not match or match.group(1) in ("n/a", "-"):
        return None
    return match.group(1)


def _kind(entry: dict, physical: bool) -> str:
    info = entry.get("linkinfo") or {}
    kind = info.get("info_kind")
    if kind:
        return str(kind)
    if physical and entry.get("link_type") == "ether":
        return "physical"
    return str(entry.get("link_type") or "unknown")


def _protected(links: dict[str, Link], carriers: set[str]) -> set[str]:
    """The carriers of the default route and of the asking computer's route, with what they rest on:
    a VLAN's parent and a bond's or a bridge's ports, again and again."""
    protected: set[str] = set()
    todo = [name for name in carriers if name in links]
    while todo:
        name = todo.pop()
        if name in protected:
            continue
        protected.add(name)
        link = links[name]
        if link.parent and link.parent in links:
            todo.append(link.parent)
        todo.extend(other.name for other in links.values() if other.master == name)
    return protected


async def snapshot(client: str | None) -> Snapshot:
    """The host's links, who keeps its network, what UNOS made and what a change must not cut."""
    found = await manager()
    entries = _json(await _ok(["ip", "-d", "-j", "link", "show"], "the host's interfaces"), "network data")
    addr_entries = _json(await _ok(["ip", "-j", "addr", "show"], "the host's addresses"), "network data")
    sysfs = (await _ok(_script("unos-netdevs-sysfs"), "the host's interfaces")).splitlines()
    kinds = {parts[0]: (parts[1] == "physical", parts[2] == "wireless")
             for parts in (line.split() for line in sysfs) if len(parts) == 3}
    setup: dict[str, str] = {}
    if found == "systemd-networkd":
        listed = await run(["networkctl", "list", "--no-legend", "--no-pager"])
        if listed.ok:
            setup = parse_networkctl_list(listed.stdout)
    addresses: dict[str, list[str]] = {}
    for entry in addr_entries:
        addresses[str(entry.get("ifname", ""))] = [
            f"{a['local']}/{a.get('prefixlen')}" for a in entry.get("addr_info", [])
            if a.get("family") in ("inet", "inet6") and a.get("local") and a.get("scope") != "link"
            and a.get("scope") != "host"
        ]
    links: dict[str, Link] = {}
    for entry in entries:
        name = str(entry.get("ifname", ""))
        if not name or name == "lo":
            continue
        physical, wireless = kinds.get(name, (False, False))
        info = entry.get("linkinfo") or {}
        vlan_id = (info.get("info_data") or {}).get("id") if info.get("info_kind") == "vlan" else None
        links[name] = Link(
            name=name, index=int(entry.get("ifindex") or 0), mac=str(entry.get("address") or ""),
            operstate=str(entry.get("operstate") or "UNKNOWN"), kind=_kind(entry, physical), wireless=wireless,
            master=entry.get("master"), parent=entry.get("link"),
            vlan_id=vlan_id if isinstance(vlan_id, int) else None,
            addresses=addresses.get(name, []), setup=setup.get(name, "unmanaged"),
        )
    carriers: set[str] = set()
    for family in (["-4"], ["-6"]):
        result = await run(["ip", *family, "-j", "route", "show", "default"])
        if result.ok:
            for route in _json(result.stdout, "routing data"):
                hops = [route, *(h for h in route.get("nexthops") or [] if isinstance(h, dict))]
                carriers |= {str(hop["dev"]) for hop in hops if hop.get("dev")}
    route_to = await _route_to(client)
    if route_to:
        carriers.add(route_to)
    carriers = {name for name in carriers if not is_hidden(name)}
    items, unreadable = await _items()
    return Snapshot(found, links, _protected(links, carriers), items, unreadable, route_to)


async def _route_to(client: str | None) -> str | None:
    """The interface of the host's route to the asking computer."""
    try:
        address = ipaddress.ip_address(client or "")
    except ValueError:
        return None
    if address.is_loopback or address.is_unspecified:
        return None
    result = await run(["ip", "-j", "route", "get", str(address)])
    if not result.ok:
        return None
    routes = _json(result.stdout, "routing data")
    return str(routes[0].get("dev")) if routes and routes[0].get("dev") else None


# The folders that systemd-networkd reads its .network files from.
NETWORK_DIRS = ("/etc/systemd/network/", "/run/systemd/network/", "/usr/local/lib/systemd/network/",
                "/usr/lib/systemd/network/", "/lib/systemd/network/")


async def _network_file(name: str) -> str | None:
    """The .network file that networkd applies to ``name``, None when it applies none; 409 for a path
    that is not a .network file of networkd's folders."""
    result = await run(["networkctl", "status", name, "--no-pager"])
    if not result.ok:
        raise HTTPException(500, f"Could not ask systemd-networkd about {name}: {reason(result)}")
    path = parse_network_file(result.stdout)
    if path is not None:
        folder, _, base = path.rpartition("/")
        if (f"{folder}/" not in NETWORK_DIRS or not base.endswith(".network") or base.startswith(".")
                or ".." in path):
            raise HTTPException(409, f"systemd-networkd applies {path} to {name}, which UNOS does not read")
    return path


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------

HEADER = "# Made by UNOS (ADR-0424), System > Network: UNOS removes this file with {kind} {name}.\n"

# The sections of a .network file that a bond or a bridge takes over from the port whose addresses
# it takes: how it gets its addresses and routes, not what belongs to the port's link.
COPIED_SECTIONS = {
    "Network", "Address", "Route", "DHCP", "DHCPv4", "DHCPv6", "IPv6AcceptRA", "RoutingPolicyRule",
    "NextHop", "Neighbor", "IPv6Prefix", "IPv6SendRA", "IPv6AddressLabel",
}
# The settings of [Network] that make or join other interfaces: not taken over.
STACKING_KEYS = {
    "Bond", "Bridge", "VRF", "VLAN", "MACVLAN", "MACVTAP", "IPVLAN", "IPVTAP", "VXLAN", "Tunnel", "MACsec",
    "Xfrm", "BatmanAdvanced", "IPoIB",
}


def copied_sections(text: str) -> tuple[str, bool]:
    """The sections of a port's .network file that its new bond or bridge takes over (its [Match],
    [Link] and the port's own sections left out, and in [Network] what makes or joins other
    interfaces), and whether they set the DHCP client's identity (IAID= or ClientIdentifier=)."""
    out: list[str] = []
    section = None
    identity = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section in COPIED_SECTIONS:
                out.append(f"\n[{section}]")
            continue
        if section not in COPIED_SECTIONS:
            continue
        key = line.split("=", 1)[0].strip()
        if section == "Network" and key in STACKING_KEYS:
            continue
        if section == "DHCPv4" and key in ("IAID", "ClientIdentifier"):
            identity = True
        out.append(line)
    return "\n".join(out).strip() + "\n", identity


def _section_lines(text: str, section: str) -> list[str]:
    """The settings of ``section`` in a networkd file."""
    out, current = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
        elif current == section and line and not line.startswith(("#", ";")):
            out.append(line)
    return out


def lease_iaid(lease: str) -> int | None:
    """The IAID of a networkd DHCPv4 lease's client identifier, when it is the DUID kind (type 255,
    RFC 4361): a bond or bridge that sends it with the same DUID gets the port's lease."""
    for line in lease.splitlines():
        if line.startswith("CLIENTID="):
            value = line.split("=", 1)[1].strip().lower()
            if re.fullmatch(r"ff[0-9a-f]{8}[0-9a-f]+", value):
                return int(value[2:10], 16)
    return None


def _addressing_lines(addressing: str, addresses: list[str]) -> str:
    """[Network] and [DHCPv4] of an interface that does not take a port's addresses: none, DHCPv4
    without the gateway, routes and DNS of the server (it never takes the default route), or static
    addresses without a gateway."""
    lines = ["[Network]", "LinkLocalAddressing=no", "IPv6AcceptRA=no", "ConfigureWithoutCarrier=yes"]
    if addressing == "dhcp":
        lines.insert(1, "DHCP=ipv4")
        lines += ["", "[DHCPv4]", "UseGateway=no", "UseRoutes=no", "UseDNS=no", "UseDomains=no"]
    elif addressing == "static":
        lines[1:1] = [f"Address={address}" for address in addresses]
    return "\n".join(lines) + "\n"


def render_netdev(item: Item, mac: str | None) -> str:
    lines = [HEADER.format(kind=item.kind, name=item.name).rstrip("\n"), f"# unos: {item.metadata()}",
             "[NetDev]", f"Name={item.name}", f"Kind={item.kind}"]
    if mac:
        lines.append(f"MACAddress={mac}")
    if item.kind == "bond":
        lines += ["", "[Bond]", f"Mode={item.mode}", "MIIMonitorSec=100ms"]
        if item.mode == "802.3ad":
            lines.append("TransmitHashPolicy=layer3+4")
    elif item.kind == "vlan":
        lines += ["", "[VLAN]", f"Id={item.vlan_id}"]
    else:
        lines += ["", "[Bridge]", "STP=no"]
    return "\n".join(lines) + "\n"


def render_network(item: Item, copied: str | None, iaid: int | None) -> str:
    lines = [HEADER.format(kind=item.kind, name=item.name).rstrip("\n"), "[Match]", f"Name={item.name}", ""]
    link = [f"MTUBytes={item.mtu}"] if item.mtu else []
    if copied is None:
        # Not the host's uplink: the boot does not wait for it (systemd-networkd-wait-online).
        link.append("RequiredForOnline=no")
    if link:
        lines += ["[Link]", *link, ""]
    text = "\n".join(lines) + "\n"
    if copied is None:
        return text + _addressing_lines(item.addressing, item.addresses)
    body = copied
    if iaid is not None:
        body += f"\n[DHCPv4]\nIAID={iaid}\n"
    return text + body


def render_port(item: Item, port: str) -> str:
    key = "Bond" if item.kind == "bond" else "Bridge"
    return (HEADER.format(kind=item.kind, name=item.name)
            + f"[Match]\nName={port}\n\n[Link]\nRequiredForOnline=no\n\n[Network]\n{key}={item.name}\n")


def parent_file(parent: str) -> str:
    """The .network file of UNOS's that a VLAN's parent takes while VLANs are on it: before the
    host's own, which may match other interfaces too (netplan's Name=e*), so that its VLAN= reaches
    the parent alone."""
    return f"{PORT_PREFIX}{parent}.network"


def parent_sections(text: str, ours: set[str]) -> str:
    """A parent's .network file without its [Match] and the VLAN= of UNOS's VLANs (``ours``): what it
    keeps, the host's own VLANs included."""
    out: list[str] = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section != "Match":
                out.append(f"\n[{section}]")
            continue
        key, _, value = line.partition("=")
        if section == "Match" or (section == "Network" and key.strip() == "VLAN"
                                  and set(value.split()) <= ours and value.split()):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def render_parent(parent: str, kept: str, vlans: list[str]) -> str:
    header = (f"# Made by UNOS (ADR-0424), System > Network: the settings of {parent} with its VLANs; UNOS "
              f"removes this file with its last VLAN.\n")
    body = kept if kept.strip() else ""
    return (header + f"[Match]\nName={parent}\n\n" + body + "\n[Network]\n"
            + "".join(f"VLAN={vlan}\n" for vlan in vlans))


def port_file(master: str, port: str) -> str:
    # "+" is in no name that UNOS gives or takes (valid_name), so a port's file is never another's.
    return f"{PORT_PREFIX}{master}+{port}.network"


# ---------------------------------------------------------------------------
# The plan of a change
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    action: Literal["create", "delete"]
    item: Item
    # (add, replace or remove, a file of LIVE_DIR, its text), in order: an interface's files before
    # those that refer to it when it is made, after them when it is removed.
    ops: list[tuple[str, str, str | None]]
    touched: set[str]               # the interfaces networkd reconfigures, releases or removes
    created: list[str]

    def record(self) -> str:
        """The change as STATE_DIR's ``change`` file holds it: "create bond bond0"."""
        return f"{self.action} {self.item.kind} {self.item.name}"

    def description(self) -> str:
        return describe(self.action, self.item.kind, self.item.name)


def describe(action: str, kind: str, name: str) -> str:
    """A change in English, for error messages and the audit log: "Create VLAN eth0.10"."""
    return f"{action.capitalize()} {'VLAN' if kind == 'vlan' else kind} {name}"


@dataclass
class Recorded:
    action: Literal["create", "delete"]
    kind: Kind
    name: str


def parse_record(text: str) -> Recorded | None:
    """STATE_DIR's ``change`` file; None when it is not one that UNOS writes."""
    parts = text.split()
    if (len(parts) == 3 and parts[0] in ("create", "delete") and parts[1] in ("bond", "vlan", "bridge")
            and valid_name(parts[2])):
        return Recorded(parts[0], parts[1], parts[2])  # type: ignore[arg-type]
    return None


def _require_networkd(snap: Snapshot) -> None:
    if snap.manager != "systemd-networkd":
        raise HTTPException(409, NOT_NETWORKD.format(manager=snap.manager if snap.manager != "none" else "no network manager that UNOS knows"))


def _check_new_name(name: str, snap: Snapshot) -> None:
    if not valid_name(name):
        raise HTTPException(
            400, f"{name!r} is not a name UNOS gives an interface: 1 to 15 letters, digits, '_', '.' and '-', "
                 "starting with a letter, and not one that the kernel, Docker or UNOS keep",
        )
    if name in snap.links or name in snap.items:
        raise HTTPException(409, f"The host has an interface named {name} already")


def _check_mtu(mtu: int | None) -> None:
    if mtu is not None and not MIN_MTU <= mtu <= MAX_MTU:
        raise HTTPException(400, f"The MTU must be {MIN_MTU} to {MAX_MTU} bytes")


def check_addresses(addressing: str, addresses: list[str]) -> list[str]:
    """Static addresses as networkd takes them: an address with its prefix, IPv4 or IPv6, of a host."""
    if addressing != "static":
        if addresses:
            raise HTTPException(400, "Addresses are given only with static addressing")
        return []
    if not 1 <= len(addresses) <= MAX_ADDRESSES:
        raise HTTPException(400, f"Static addressing takes 1 to {MAX_ADDRESSES} addresses")
    out: list[str] = []
    for text in addresses:
        try:
            interface = ipaddress.ip_interface(text)
        except ValueError:
            raise HTTPException(400, f"{text!r} is not an address with its prefix length, such as 192.0.2.10/24")
        if "/" not in text:
            raise HTTPException(400, f"{text!r} has no prefix length, such as 192.0.2.10/24")
        address = interface.ip
        if (address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local
                or (address.version == 4 and interface.network.prefixlen < 31
                    and address in (interface.network.network_address, interface.network.broadcast_address))):
            raise HTTPException(400, f"{text} is not an address that a host takes")
        value = str(interface)
        if value in out:
            raise HTTPException(400, f"{text} is given twice")
        out.append(value)
    return out


def _check_port(name: str, snap: Snapshot, *, bond: bool, taken: set[str]) -> Link:
    """A port of a new bond (a wired physical interface) or bridge (also a bond or a VLAN)."""
    if not valid_port_name(name) or is_hidden(name):
        raise HTTPException(400, f"{name!r} is not an interface that UNOS changes")
    if name in taken:
        raise HTTPException(400, f"{name} is named twice")
    link = snap.links.get(name)
    if link is None:
        raise HTTPException(404, f"Interface {name} not found")
    allowed = ("physical",) if bond else ("physical", "bond", "vlan")
    if link.kind not in allowed or link.wireless:
        what = "a wired Ethernet interface" if bond else "a wired Ethernet interface, a bond or a VLAN"
        raise HTTPException(409, f"{name} cannot be a port of a {'bond' if bond else 'bridge'}: only {what} can")
    if link.master:
        raise HTTPException(409, f"{name} is a port of {link.master} already: an interface is in one bond or bridge only")
    owners = [item.name for item in snap.items.values() if name in item.ports]
    if owners:
        raise HTTPException(409, f"{name} is a port of {owners[0]} already: an interface is in one bond or bridge only")
    uppers = sorted({other.name for other in snap.links.values() if other.parent == name}
                    | {item.name for item in snap.items.values() if item.parent == name})
    if uppers:
        raise HTTPException(409, f"{name} carries {', '.join(uppers)}: an interface with VLANs on it cannot be a port")
    if link.addresses and name not in snap.protected:
        raise HTTPException(
            409, f"{name} has addresses ({', '.join(link.addresses)}), which it would lose as a port: "
                 "remove them on the host first",
        )
    return link


async def _refuse_a_waiting_disable(name: str) -> None:
    """409 for an interface whose disable from System > Network waits to be kept: its revert would
    put its addresses back (ADR-0244)."""
    if (await run(["test", "-e", f"{links.STATE_DIR}/{name}/pending"])).ok:
        raise HTTPException(409, f"A disable of {name} waits to be kept: keep it, or wait until it is undone")


def _primary(ports: list[Link], snap: Snapshot) -> Link | None:
    """The port whose addresses its bond or bridge takes over: the one that is protected (a bond or
    bridge takes at most one)."""
    return next((port for port in ports if port.name in snap.protected), None)


# The drop-ins of a .network file in networkd's folders ($1: its name), one per line.
DROPINS_SCRIPT = """\
for d in /etc/systemd/network /run/systemd/network /usr/local/lib/systemd/network /usr/lib/systemd/network \\
    /lib/systemd/network; do
  for f in "$d/$1.d"/*.conf; do
    [ -e "$f" ] && echo "$f"
  done
done
exit 0
"""


async def _read_network_file(name: str, path: str) -> str:
    """The text of ``path``, the .network file of ``name``: 409 when it has drop-ins, which UNOS
    would not carry over, or is too large."""
    base = path.rsplit("/", 1)[-1]
    dropins = await run(["/bin/sh", "-c", DROPINS_SCRIPT, "unos-netdevs-dropins", base])
    if not dropins.ok:
        raise HTTPException(500, f"Could not read the drop-ins of the network file of {name}: {reason(dropins)}")
    if dropins.stdout.strip():
        raise HTTPException(409, f"The network file of {name} has drop-ins ({', '.join(dropins.stdout.split())}): "
                                 "UNOS does not take its settings over")
    text = await _ok(["cat", path], f"the network file of {name}")
    if len(text) > 65536:
        raise HTTPException(409, f"The network file of {name} is too large to take over")
    return text


async def _copied_config(primary: Link) -> tuple[str, int | None]:
    """The sections that a bond or bridge takes over from ``primary``'s .network file, and the IAID
    of its DHCPv4 lease when the file sets no DHCP identity of its own."""
    path = await _network_file(primary.name)
    if path is None:
        raise HTTPException(409, f"systemd-networkd does not manage {primary.name}, so UNOS cannot move its addresses")
    text = await _read_network_file(primary.name, path)
    stacked = [line.split("=", 1)[0].strip() for line in _section_lines(text, "Network")
               if line.split("=", 1)[0].strip() in STACKING_KEYS]
    if stacked:
        raise HTTPException(409, f"The network file of {primary.name} makes or joins other interfaces "
                                 f"({', '.join(sorted(set(stacked)))}): it cannot be a port")
    copied, identity = copied_sections(text)
    iaid = None
    if not identity and primary.index:
        lease = await run(["cat", f"{LEASES_DIR}/{primary.index}"])
        if lease.ok:
            iaid = lease_iaid(lease.stdout)
    return copied, iaid


async def plan_bond_or_bridge(kind: Kind, name: str, ports: list[str], *, mode: str | None, mtu: int | None,
                              addressing: str, addresses: list[str], snap: Snapshot) -> Plan:
    _check_new_name(name, snap)
    _check_mtu(mtu)
    bond = kind == "bond"
    if bond and mode not in BOND_MODES:
        raise HTTPException(400, f"A bond's mode is one of {', '.join(BOND_MODES)}")
    if bond and not 2 <= len(ports) <= MAX_PORTS:
        raise HTTPException(400, f"A bond takes 2 to {MAX_PORTS} interfaces")
    if not bond and len(ports) > MAX_PORTS:
        raise HTTPException(400, f"A bridge takes at most {MAX_PORTS} ports")
    taken: set[str] = set()
    found = []
    for port in ports:
        found.append(_check_port(port, snap, bond=bond, taken=taken))
        taken.add(port)
    carriers = [port.name for port in found if port.name in snap.protected]
    if len(carriers) > 1:
        raise HTTPException(
            409, f"{' and '.join(carriers)} each carry the connection to UNOS or the default route: a {kind} takes "
                 "over the settings of one of them only",
        )
    for port in ports:
        await _refuse_a_waiting_disable(port)
    primary = _primary(found, snap)
    copied = iaid = None
    if primary is not None:
        copied, iaid = await _copied_config(primary)
        addressing, addresses = "copied", []
    else:
        addresses = check_addresses(addressing, addresses)
    netdev, network = f"{PREFIX}{name}.netdev", f"{PREFIX}{name}.network"
    port_files = [port_file(name, port) for port in ports]
    item = Item(kind=kind, name=name, files=[netdev, network, *port_files], mode=mode if bond else None,
                ports=list(ports), mtu=mtu, addressing=addressing, addresses=addresses,
                copied_from=primary.name if primary else None)
    ops = [("add", netdev, render_netdev(item, primary.mac if primary else None)),
           ("add", network, render_network(item, copied, iaid))]
    ops += [("add", path, render_port(item, port)) for path, port in zip(port_files, ports)]
    return Plan("create", item, ops, set(ports), [name])


async def plan_vlan(parent: str, vlan_id: int, name: str | None, *, mtu: int | None, addressing: str,
                    addresses: list[str], snap: Snapshot) -> Plan:
    if not valid_port_name(parent) or is_hidden(parent):
        raise HTTPException(400, f"{parent!r} is not an interface that UNOS changes")
    if not 1 <= vlan_id <= 4094:
        raise HTTPException(400, "A VLAN id is 1 to 4094")
    link = snap.links.get(parent)
    if link is None:
        raise HTTPException(404, f"Interface {parent} not found")
    if link.kind not in ("physical", "bond") or link.wireless:
        raise HTTPException(409, f"A VLAN goes on a wired Ethernet interface or a bond, and {parent} is neither")
    if link.master:
        raise HTTPException(409, f"{parent} is a port of {link.master}: put the VLAN on {link.master}")
    await _refuse_a_waiting_disable(parent)
    name = name or f"{parent}.{vlan_id}"
    _check_new_name(name, snap)
    _check_mtu(mtu)
    same = [other.name for other in snap.links.values() if other.parent == parent and other.vlan_id == vlan_id]
    same += [item.name for item in snap.items.values() if item.parent == parent and item.vlan_id == vlan_id]
    if same:
        raise HTTPException(409, f"{parent} carries VLAN {vlan_id} already ({same[0]})")
    addresses = check_addresses(addressing, addresses)
    netdev, network = f"{PREFIX}{name}.netdev", f"{PREFIX}{name}.network"
    item = Item(kind="vlan", name=name, files=[netdev, network], parent=parent, vlan_id=vlan_id, mtu=mtu,
                addressing=addressing, addresses=addresses)
    others = [other.name for other in snap.items.values() if other.kind == "vlan" and other.parent == parent]
    ops = [("add", netdev, render_netdev(item, None)), ("add", network, render_network(item, None, None)),
           await _parent_op(parent, [*others, name], snap)]
    return Plan("create", item, ops, {parent}, [name])


async def _parent_op(parent: str, vlans: list[str], snap: Snapshot) -> tuple[str, str, str | None]:
    """The op that gives ``parent`` UNOS's file with ``vlans`` (none: the file goes, and the parent's
    own file applies again): the settings that networkd applies to it now, with its VLAN=."""
    own = f"{LIVE_DIR}/{parent_file(parent)}"
    exists = (await run(["test", "-e", own])).ok
    if not vlans:
        return ("remove", parent_file(parent), None)
    path = await _network_file(parent)
    if path is None:
        raise HTTPException(409, f"systemd-networkd does not manage {parent}, so UNOS cannot put a VLAN on it")
    if exists and path != own:
        raise HTTPException(409, f"systemd-networkd applies {path} to {parent}, not UNOS's file for its VLANs")
    text = await _read_network_file(parent, path)
    ours = {item.name for item in snap.items.values() if item.kind == "vlan"} | set(vlans)
    return ("replace" if exists else "add", parent_file(parent),
            render_parent(parent, parent_sections(text, ours), vlans))


async def plan_delete(name: str, snap: Snapshot) -> Plan:
    if not valid_port_name(name):
        raise HTTPException(400, f"{name!r} is not an interface's name")
    item = snap.items.get(name)
    if item is None:
        if name in snap.links:
            raise HTTPException(409, f"{name} was not made in UNOS: UNOS removes only the interfaces it made")
        raise HTTPException(404, f"No bond, VLAN or bridge named {name} was made in UNOS")
    uppers = sorted({other.name for other in snap.links.values() if other.parent == name}
                    | {other.name for other in snap.items.values() if other.parent == name})
    if uppers:
        raise HTTPException(409, f"{name} carries {', '.join(uppers)}: delete them first")
    link = snap.links.get(name)
    if link is not None and link.master:
        raise HTTPException(409, f"{name} is a port of {link.master}: delete {link.master} first")
    owners = [other.name for other in snap.items.values() if name in other.ports]
    if owners:
        raise HTTPException(409, f"{name} is a port of {owners[0]}: delete {owners[0]} first")
    touched = {name, *item.ports, *([item.parent] if item.parent else [])}
    ops: list[tuple[str, str, str | None]] = []
    if item.kind == "vlan" and item.parent:
        others = [o.name for o in snap.items.values() if o.kind == "vlan" and o.parent == item.parent and o.name != name]
        if others or (await run(["test", "-e", f"{LIVE_DIR}/{parent_file(item.parent)}"])).ok:
            ops.append(await _parent_op(item.parent, others, snap))
    ops += [("remove", path, None) for path in reversed(item.files)]
    return Plan("delete", item, ops, touched, [])


def touches_protected(plan: Plan, snap: Snapshot) -> bool:
    """Whether the change may cut the asking computer or the host's uplink off, so that it waits to be
    kept with its revert armed."""
    return bool(plan.touched & snap.protected)


# ---------------------------------------------------------------------------
# Carrying a change out
# ---------------------------------------------------------------------------


@dataclass
class Change:
    item: Item
    action: str
    revert_in: int | None


@dataclass
class Waiting:
    revert_in: int | None
    reverting: bool
    failed: bool
    change: Recorded | None


async def _revert_state() -> tuple[int | None, bool, bool, bool]:
    """The marker's time (None without ``pending``), whether ``undoing`` is there, whether the revert
    runs, and whether its timer is armed."""
    marker = await run(["cat", f"{STATE_DIR}/pending"])
    deadline = None
    if marker.ok:
        try:
            deadline = int(marker.stdout.strip())
        except ValueError:
            deadline = 0
    undoing = (await run(["test", "-e", f"{STATE_DIR}/undoing"])).ok
    return deadline, undoing, await _active(f"{REVERT_UNIT}.service"), await _active(f"{REVERT_UNIT}.timer")


async def waiting() -> Waiting:
    """The change that waits to be kept, as the host's revert state shows it. A change that failed
    and waits for the timer to undo it (``undoing``) is being undone: it cannot be kept."""
    deadline, undoing, running, armed = await _revert_state()
    failed = (await run(["test", "-e", f"{STATE_DIR}/failed"])).ok
    left = None
    change = None
    if deadline is not None and not running and armed:
        left = max(0, deadline - int(time.time()))
        described = await run(["cat", f"{STATE_DIR}/change"])
        change = parse_record(described.stdout) if described.ok else None
    return Waiting(left, running or (undoing and armed), failed, change)


async def _settle() -> None:
    """Before a change: 409 while one waits or is undone; what an earlier one left is finished (a
    Keep that did not write it to KEPT_DIR) or undone (a change that was interrupted, a revert that
    failed, or a marker whose timer is gone)."""
    deadline, undoing, running, armed = await _revert_state()
    if running or (undoing and armed):
        raise HTTPException(409, REVERT_RUNNING)
    if deadline is not None and armed:
        raise HTTPException(409, CHANGE_WAITS)
    if (await run(["test", "-e", f"{STATE_DIR}/kept"])).ok:
        await _persist()
        return
    if (await run(["test", "-e", f"{STATE_DIR}/ops"])).ok:
        undone = await run(_script("unos-netdevs-undo"))
        if not undone.ok:
            raise HTTPException(500, f"An earlier network change could not be undone: {reason(undone)}")
        await run(_script("unos-netdevs-discard"))


async def _persist() -> None:
    """Write the recorded change into KEPT_DIR; the boot unit while KEPT_DIR holds an interface."""
    unit = await _ensure_boot_unit()
    persisted = await run(_script("unos-netdevs-persist"))
    if not persisted.ok:
        raise HTTPException(
            500, f"The change is made, but only until the next reboot: it could not be written to {KEPT_DIR} "
                 f"({reason(persisted)}). UNOS writes it again before the next change",
        )
    if not (await run(_script("unos-netdevs-kept-any"))).ok:
        await _remove_boot_unit()
    elif not unit:
        raise HTTPException(500, f"The change is kept, but {BOOT_UNIT} could not be set up: it lasts until the next reboot")


async def _ensure_boot_unit() -> bool:
    """BOOT_UNIT, written and enabled when it is not; whether it is now."""
    current = await run(["cat", BOOT_UNIT_FILE])
    if current.ok and current.stdout == BOOT_UNIT_TEXT and (await run(["systemctl", "is-enabled", "--quiet", BOOT_UNIT])).ok:
        return True
    if not (current.ok and current.stdout == BOOT_UNIT_TEXT):
        written = await run(["/bin/sh", "-c", 'set -e; cat > "$1.unos-tmp"; sync "$1.unos-tmp" 2> /dev/null || sync; '
                             'mv -f "$1.unos-tmp" "$1"', "unos-netdevs-unit", BOOT_UNIT_FILE], input=BOOT_UNIT_TEXT)
        if not written.ok:
            return False
        await run(["systemctl", "daemon-reload"])
    return (await run(["systemctl", "enable", "--quiet", BOOT_UNIT])).ok


async def _remove_boot_unit() -> None:
    """UNOS keeps no interface any more: its unit and KEPT_DIR go."""
    await run(["systemctl", "disable", "--quiet", BOOT_UNIT])
    await run(["rm", "-f", BOOT_UNIT_FILE])
    await run(["systemctl", "daemon-reload"])
    await run(["rm", "-rf", KEPT_DIR])


async def _undo(failed: str, *, armed: bool, answered: bool = True) -> HTTPException:
    """Undo the recorded change after ``failed``; the 500 that says what happened. When the host did
    not answer, its command may still run there: an armed revert is left to undo it at its time."""
    if armed:
        # A change that failed is never kept: no Keep takes this marker, the timer does.
        await run(["mv", "-f", f"{STATE_DIR}/pending", f"{STATE_DIR}/undoing"])
    if armed and not answered:
        return HTTPException(500, f"{failed}. It is undone by itself within {REVERT_SECONDS} seconds")
    undone = await run(_script("unos-netdevs-undo"))
    if undone.ok:
        await run(_script("unos-netdevs-discard"))
        return HTTPException(500, f"{failed}. It was undone")
    if armed:
        return HTTPException(500, f"{failed}. It could not be undone now ({reason(undone)}): "
                                  f"it is undone by itself within {REVERT_SECONDS} seconds")
    return HTTPException(500, f"{failed}. It could not be undone: {reason(undone)}")


async def _made_otherwise(item: Item, entry: dict) -> str | None:
    """What of ``item`` networkd made otherwise than its files say: a VLAN's id, a bond's mode, or a
    link of it that networkd failed to configure."""
    data = (entry.get("linkinfo") or {}).get("info_data") or {}
    if item.kind == "vlan" and data.get("id") != item.vlan_id:
        return f"{item.name} has the VLAN id {data.get('id')}, not {item.vlan_id}"
    if item.kind == "bond" and data.get("mode") != item.mode:
        return f"{item.name} has the mode {data.get('mode')}, not {item.mode}"
    listed = await run(["networkctl", "list", "--no-legend", "--no-pager"])
    setup = parse_networkctl_list(listed.stdout) if listed.ok else {}
    failed = [name for name in (item.name, *item.ports) if setup.get(name) == "failed"]
    if failed:
        return f"systemd-networkd failed to configure {', '.join(failed)}"
    return None


async def _verify(plan: Plan) -> str | None:
    """Wait until networkd has made the change; what it has not made by VERIFY_SECONDS."""
    deadline = time.monotonic() + VERIFY_SECONDS
    while True:
        problem = None
        result = await run(["ip", "-d", "-j", "link", "show"])
        if not result.ok:
            problem = f"the interfaces could not be read ({reason(result)})"
        else:
            entries = {str(e.get("ifname")): e for e in _json(result.stdout, "network data")}
            name = plan.item.name
            if plan.action == "create":
                if name not in entries:
                    problem = f"systemd-networkd did not make {name}"
                else:
                    loose = [port for port in plan.item.ports if entries.get(port, {}).get("master") != name]
                    if loose:
                        problem = f"systemd-networkd did not make {', '.join(loose)} a port of {name}"
                    elif plan.item.parent and entries[name].get("link") != plan.item.parent:
                        problem = f"systemd-networkd did not make {name} on {plan.item.parent}"
                    else:
                        problem = await _made_otherwise(plan.item, entries[name])
            elif name in entries:
                problem = f"{name} is still there"
        if problem is None:
            return None
        if time.monotonic() >= deadline:
            return f"{problem} within {VERIFY_SECONDS} seconds"
        await asyncio.sleep(VERIFY_INTERVAL)


async def _carry_out(plan: Plan, snap: Snapshot) -> Change:
    armed = touches_protected(plan, snap)
    args = [f"{op}:{path}" for op, path, _ in plan.ops]
    args += [f"created:{name}" for name in plan.created] + [f"change:{plan.record()}"]
    saved = await run(_script("unos-netdevs-save", *args))
    if not saved.ok:
        await run(_script("unos-netdevs-discard"))
        raise HTTPException(500, f"Nothing was changed: the change could not be recorded ({reason(saved)})")
    deadline = int(time.time()) + REVERT_SECONDS
    if armed:
        marked = await run(_script("unos-netdevs-mark", str(deadline)))
        timer = await run(revert_timer()) if marked.ok else marked
        if not timer.ok:
            await run(_script("unos-netdevs-discard"))
            raise HTTPException(500, f"Nothing was changed: the automatic revert could not be set up ({reason(timer)})")
    failed = f"{plan.description()} failed"
    for op, path, text in plan.ops:
        if op == "remove":
            done = await run(_script("unos-netdevs-remove", path))
        else:
            done = await run(_script("unos-netdevs-write", path), input=text)
        if not done.ok:
            raise await _undo(f"{failed}: {path} could not be {'removed' if op == 'remove' else 'written'} "
                              f"({reason(done)})", armed=armed, answered=done.returncode != NO_ANSWER)
    if plan.action == "delete" and plan.item.name in snap.links:
        deleted = await run(["ip", "link", "delete", "dev", plan.item.name])
        if not deleted.ok:
            raise await _undo(f"{failed}: {reason(deleted)}", armed=armed, answered=deleted.returncode != NO_ANSWER)
    reloaded = await run(["networkctl", "reload"])
    if not reloaded.ok:
        raise await _undo(f"{failed}: systemd-networkd could not reload ({reason(reloaded)})", armed=armed,
                          answered=reloaded.returncode != NO_ANSWER)
    problem = await _verify(plan)
    if problem:
        raise await _undo(f"{failed}: {problem}", armed=armed)
    if armed:
        return Change(plan.item, plan.action, max(0, deadline - int(time.time())))
    # Made, and kept: should the write to KEPT_DIR fail, the next change writes it (_settle).
    await run(["touch", f"{STATE_DIR}/kept"])
    await _persist()
    return Change(plan.item, plan.action, None)


# ---------------------------------------------------------------------------
# The changes
# ---------------------------------------------------------------------------


async def status(client: str | None) -> tuple[Snapshot, Waiting]:
    _refuse_locally()
    async with _lock():
        snap = await snapshot(client)
        return snap, await waiting()


async def create_bond(name: str, mode: str, ports: list[str], mtu: int | None, addressing: str,
                      addresses: list[str], client: str | None) -> Change:
    _refuse_locally()
    async with _lock():
        await _settle()
        snap = await snapshot(client)
        _require_networkd(snap)
        plan = await plan_bond_or_bridge("bond", name, ports, mode=mode, mtu=mtu, addressing=addressing,
                                         addresses=addresses, snap=snap)
        return await _carry_out(plan, snap)


async def create_bridge(name: str, ports: list[str], mtu: int | None, addressing: str, addresses: list[str],
                        client: str | None) -> Change:
    _refuse_locally()
    async with _lock():
        await _settle()
        snap = await snapshot(client)
        _require_networkd(snap)
        plan = await plan_bond_or_bridge("bridge", name, ports, mode=None, mtu=mtu, addressing=addressing,
                                         addresses=addresses, snap=snap)
        return await _carry_out(plan, snap)


async def create_vlan(parent: str, vlan_id: int, name: str | None, mtu: int | None, addressing: str,
                      addresses: list[str], client: str | None) -> Change:
    _refuse_locally()
    async with _lock():
        await _settle()
        snap = await snapshot(client)
        _require_networkd(snap)
        plan = await plan_vlan(parent, vlan_id, name, mtu=mtu, addressing=addressing, addresses=addresses,
                               snap=snap)
        return await _carry_out(plan, snap)


async def delete(name: str, client: str | None) -> Change:
    _refuse_locally()
    async with _lock():
        await _settle()
        snap = await snapshot(client)
        _require_networkd(snap)
        return await _carry_out(await plan_delete(name, snap), snap)


async def keep() -> Recorded | None:
    """Keep the change that waits: its revert is called off and it is written to KEPT_DIR. What the
    change was, when its record says."""
    _refuse_locally()
    async with _lock():
        described = await run(["cat", f"{STATE_DIR}/change"])
        result = await run(_script("unos-netdevs-cancel"))
        if result.returncode == NOTHING_PENDING:
            raise HTTPException(409, NOTHING_TO_KEEP)
        if not result.ok:
            # The cancel's first step, the rename of the marker, is what keeps the change (ADR-0246).
            marker = await run(["test", "-e", f"{STATE_DIR}/pending"])
            if marker.ok:
                raise HTTPException(500, f"The network change could not be kept, so it is undone by itself: {reason(result)}")
            if marker.returncode != 1 or marker.stderr.strip():
                raise HTTPException(
                    500, f"Whether the network change was kept is not known: {reason(result)}. Refresh the page to see."
                )
        await _persist()
        return parse_record(described.stdout) if described.ok else None

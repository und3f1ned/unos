"""Remote access: a WireGuard tunnel on the host that reaches the NAS alone (ADR-0420).

The interface ``unos-wg`` is wireguard-tools' ``wg-quick@unos-wg`` unit of the host's systemd, enabled
while remote access is on, so that the host brings it up at boot by itself. Its configuration, CONF,
is written by UNOS alone and holds no secret: the server's private key is made on the host by
``wg genkey`` into KEY_FILE (KEY_SCRIPT) and loaded by the configuration's PostUp; the supervisor
reads only its public key. No command here prints a private key (``wg showconf``, ``wg show ... dump``
or ``private-key``, ``wg-quick strip``, a read of KEY_FILE): tests/test_remote_access.py fails for one.

A peer's key pair is made in memory (X25519) unless the device gives its own public key, and its
configuration is answered once and kept nowhere. The file keeps each peer's public key, its /32 and a
comment line with its name. What a peer reaches is the NAS alone: the server takes from each peer only
its /32, the peer's configuration sends only the server's tunnel address through the tunnel, and
PostUp drops whatever the host would forward from the tunnel but Docker's DNAT of a published port.

Every command runs on the host through src/host.py (ADR-0020), under the host's timeout(1) with
LC_ALL=C (security.firewall.run, ADR-0258).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import logging
import re
import weakref
from dataclasses import dataclass, field, replace
from datetime import datetime, UTC

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from fastapi import HTTPException

from .. import host
from ..security import firewall
from ..security.validators import sanitize_error_message

logger = logging.getLogger(__name__)

INTERFACE = "unos-wg"
WG_DIR = "/etc/wireguard"
CONF = f"{WG_DIR}/{INTERFACE}.conf"
KEY_FILE = f"{WG_DIR}/{INTERFACE}.key"
UNIT = f"wg-quick@{INTERFACE}"
DEFAULT_PORT = 51820
DEFAULT_NETWORK = "10.99.0.0/24"
PORT_MIN, PORT_MAX = 1024, 65535
PREFIX_MIN, PREFIX_MAX = 16, 28
# The private IPv4 networks of the local networks (ADR-0095): SMB's hosts allow and UNOS's firewall
# rules let a tunnel network inside them in. Not 100.64.0.0/10 (Tailscale's) or link-local.
TUNNEL_PARENTS = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
NAME_MAX = 32

# Forwarded packets from the tunnel are dropped unless Docker's DNAT of a published port made them.
FORWARD_RULE = "FORWARD -i %i -m conntrack ! --ctstate DNAT -j DROP"

# Texts that webui/tests/remote-access.spec.ts repeats word for word (ADR-0189).
LOCAL_DETAIL = (
    "Remote access is off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): "
    "this is not a NAS host"
)
TOOLS_MISSING = "wireguard-tools is not installed on the host: install the package wireguard-tools to use remote access"
NOT_SET_UP = "Turn remote access on first: peers need its endpoint, port and tunnel network"
CHANGE_WHILE_ON = "Turn remote access off to change its port or tunnel network"
PEERS_IN_NETWORK = "Revoke every peer before the tunnel network changes: their addresses lie in it"
PEERS_USE_PORT = "Revoke every peer before the port changes: their configurations name it"

# Host scripts, run with /bin/sh: $0 names the script. KEY_SCRIPT makes the server's key once, under
# umask 077, through a temporary file renamed into place and synced, and prints its public key only.
KEY_SCRIPT = """\
set -e
umask 077
mkdir -p "$1"
if [ ! -s "$2" ]; then
  wg genkey > "$2.unos-new"
  mv -f "$2.unos-new" "$2"
  sync "$2" "$1" 2> /dev/null || sync
fi
wg pubkey < "$2"
"""
# The public key of the server's key, or exit status 3 without one.
PUBKEY_SCRIPT = '[ -s "$1" ] || exit 3\nwg pubkey < "$1"\n'
NO_KEY = 3

_HEADER = "# UNOS remote access (ADR-0420): written by UNOS, System > Remote access. Changes made here are lost."
_ENDPOINT = "# UNOS endpoint: "
_PEER = "# UNOS peer: "

_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()


def lock() -> asyncio.Lock:
    """Serializes the changes of remote access (one per event loop, as the firewall's)."""
    loop = asyncio.get_running_loop()
    found = _locks.get(loop)
    if found is None:
        found = _locks[loop] = asyncio.Lock()
    return found


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


_LABEL = r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
_DNS_NAME = re.compile(rf"(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*\.?")
# Printable: no control character, and none that Python's splitlines takes for a line break.
_NAME = re.compile(r"[^\x00-\x1f\x7f-\x9f\u2028\u2029]{1,%d}" % NAME_MAX)


def check_endpoint(value: str) -> str:
    """A DNS name or an IP address that peers reach the NAS at; ValueError otherwise."""
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not _DNS_NAME.fullmatch(value) or value.replace(".", "").isdigit():
        raise ValueError("the endpoint must be a DNS name or an IP address, such as nas.example.org or 203.0.113.7")
    return value.lower()


def check_network(value: str) -> ipaddress.IPv4Network:
    """The tunnel network: IPv4, /16 to /28, inside the private networks of TUNNEL_PARENTS."""
    try:
        network = ipaddress.ip_network(value.strip(), strict=True)
    except ValueError:
        raise ValueError("the tunnel network must be an IPv4 network such as 10.99.0.0/24") from None
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("the tunnel network must be an IPv4 network such as 10.99.0.0/24")
    if not PREFIX_MIN <= network.prefixlen <= PREFIX_MAX:
        raise ValueError(f"the tunnel network's prefix must be /{PREFIX_MIN} to /{PREFIX_MAX}")
    if not any(network.subnet_of(parent) for parent in TUNNEL_PARENTS):
        raise ValueError("the tunnel network must lie in 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16")
    return network


def check_name(value: str) -> str:
    """A peer's name: 1 to NAME_MAX printable characters, kept in a comment line of the file."""
    value = value.strip()
    if not _NAME.fullmatch(value):
        raise ValueError(f"the name must be 1 to {NAME_MAX} printable characters")
    return value


def check_public_key(value: str) -> str:
    """A WireGuard public key: 32 bytes in base64."""
    value = value.strip()
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the public key must be a WireGuard key: 44 characters of base64") from None
    if len(raw) != 32 or base64.b64encode(raw).decode() != value:
        raise ValueError("the public key must be a WireGuard key: 44 characters of base64")
    return value


def peer_id(public_key: str) -> str:
    return "wg-" + hashlib.sha256(public_key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# The configuration file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Peer:
    name: str
    public_key: str
    address: ipaddress.IPv4Address

    @property
    def id(self) -> str:
        return peer_id(self.public_key)


@dataclass(frozen=True)
class Config:
    address: ipaddress.IPv4Interface  # the server's tunnel address, with the network's prefix
    port: int
    endpoint: str
    peers: tuple[Peer, ...] = field(default_factory=tuple)

    @property
    def network(self) -> ipaddress.IPv4Network:
        return self.address.network


def render(config: Config) -> str:
    """The file that wg-quick reads: no private key in it."""
    lines = [
        _HEADER,
        f"{_ENDPOINT}{config.endpoint}",
        "[Interface]",
        f"Address = {config.address}",
        f"ListenPort = {config.port}",
        # The drop first: no packet of a peer is forwarded before it is in place.
        f"PostUp = iptables -I {FORWARD_RULE}",
        f"PostUp = wg set %i private-key {KEY_FILE}",
        f"PostDown = iptables -D {FORWARD_RULE} || true",
    ]
    for peer in config.peers:
        lines += ["", f"{_PEER}{peer.name}", "[Peer]", f"PublicKey = {peer.public_key}",
                  f"AllowedIPs = {peer.address}/32"]
    return "\n".join(lines) + "\n"


def parse(text: str) -> Config | None:
    """The configuration UNOS wrote; None for a file it cannot read as one."""
    endpoint = None
    address = port = None
    peers: list[Peer] = []
    name = None
    section = None
    pending: dict[str, str] = {}

    def close_peer() -> None:
        if section == "Peer" and "PublicKey" in pending and "AllowedIPs" in pending:
            try:
                ip = ipaddress.ip_interface(pending["AllowedIPs"].split(",")[0].strip()).ip
            except ValueError:
                return
            if ip.version == 4:
                peers.append(Peer(name or "", pending["PublicKey"], ip))

    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith(_ENDPOINT):
            endpoint = line.removeprefix(_ENDPOINT).strip()
        elif line.startswith(_PEER):
            close_peer()
            section, pending, name = None, {}, line.removeprefix(_PEER).strip()
        elif line.startswith("["):
            if section == "Peer":
                close_peer()
                name = None
            section, pending = line.strip("[]"), {}
        elif "=" in line and not line.startswith("#"):
            key, value = (part.strip() for part in line.split("=", 1))
            pending[key] = value
            if section == "Interface" and key == "Address":
                try:
                    address = ipaddress.ip_interface(value.split(",")[0].strip())
                except ValueError:
                    return None
            elif section == "Interface" and key == "ListenPort" and value.isdigit():
                port = int(value)
    close_peer()
    if address is None or address.version != 4 or port is None or not endpoint:
        return None
    return Config(address=address, port=port, endpoint=endpoint, peers=tuple(peers))


def peer_config(config: Config, server_key: str, peer: Peer, private_key: str | None) -> str:
    """What the peer's device imports: answered once, never stored."""
    host_part = f"[{config.endpoint}]" if ":" in config.endpoint else config.endpoint
    lines = [f"# UNOS remote access: {peer.name}", "[Interface]"]
    if private_key is None:
        lines.append("# PrivateKey: the private key that this device made with its public key")
    else:
        lines.append(f"PrivateKey = {private_key}")
    lines += [
        f"Address = {peer.address}/32",
        "",
        "[Peer]",
        f"PublicKey = {server_key}",
        f"Endpoint = {host_part}:{config.port}",
        f"AllowedIPs = {config.address.ip}/32",
    ]
    return "\n".join(lines) + "\n"


def new_key_pair() -> tuple[str, str]:
    """A WireGuard key pair, base64: (private, public)."""
    key = X25519PrivateKey.generate()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


# ---------------------------------------------------------------------------
# The host
# ---------------------------------------------------------------------------


def _script(name: str, script: str, *args: str) -> list[str]:
    return ["/bin/sh", "-c", script, name, *args]


def refuse_locally() -> None:
    if host.runs_locally():
        raise HTTPException(503, LOCAL_DETAIL)


async def installed() -> bool:
    missing = await asyncio.to_thread(host.missing_host_binaries, ["wg", "wg-quick"])
    if missing is None:
        raise HTTPException(500, "Whether the host has wireguard-tools could not be checked")
    return not missing


async def require_tools() -> None:
    refuse_locally()
    if not await installed():
        raise HTTPException(409, TOOLS_MISSING)


async def read_config() -> Config | None:
    """The configuration on the host; None without one. 500 when it cannot be read."""
    result = await firewall.run(["cat", CONF])
    if not result.ok:
        if "No such file" in result.stderr:
            return None
        raise HTTPException(500, f"Could not read remote access's configuration: {firewall.reason(result)}")
    return parse(result.stdout)


async def unit_state() -> tuple[bool, bool]:
    """Whether wg-quick@unos-wg is enabled (comes up at boot) and active (up now)."""
    enabled = (await firewall.run(["systemctl", "is-enabled", "--quiet", UNIT])).ok
    active = (await firewall.run(["systemctl", "is-active", "--quiet", UNIT])).ok
    return enabled, active


async def server_public_key(*, make: bool = False) -> str | None:
    """The public key of the server's key, made on the host first when ``make``; None without one."""
    if make:
        result = await firewall.run(_script("unos-wg-key", KEY_SCRIPT, WG_DIR, KEY_FILE))
        if not result.ok:
            raise HTTPException(500, f"Failed to make the server's key: {firewall.reason(result)}")
    else:
        result = await firewall.run(_script("unos-wg-pubkey", PUBKEY_SCRIPT, KEY_FILE))
        if result.returncode == NO_KEY:
            return None
        if not result.ok:
            raise HTTPException(500, f"Could not read the server's public key: {firewall.reason(result)}")
    key = result.stdout.strip()
    try:
        return check_public_key(key)
    except ValueError:
        raise HTTPException(500, "The host's wg pubkey printed no public key") from None


async def write_config(config: Config) -> None:
    try:
        await asyncio.to_thread(host.write_host_file_atomic, CONF, render(config), mode="0600")
    except Exception as exc:
        logger.exception("Could not write remote access's configuration")
        raise HTTPException(500, f"Could not write remote access's configuration: {_why(exc)}") from exc


def _why(exc: Exception) -> str:
    return sanitize_error_message(exc) or type(exc).__name__


async def live_peers() -> dict[str, tuple[datetime | None, int | None, int | None]]:
    """Each peer of the running interface: its last handshake and its bytes received and sent."""
    found: dict[str, list] = {}
    handshakes = await firewall.run(["wg", "show", INTERFACE, "latest-handshakes"])
    if handshakes.ok:
        for line in handshakes.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                seconds = int(parts[1])
                found.setdefault(parts[0], [None, None, None])[0] = (
                    datetime.fromtimestamp(seconds, UTC) if seconds else None)
    transfer = await firewall.run(["wg", "show", INTERFACE, "transfer"])
    if transfer.ok:
        for line in transfer.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                entry = found.setdefault(parts[0], [None, None, None])
                entry[1], entry[2] = int(parts[1]), int(parts[2])
    return {key: (value[0], value[1], value[2]) for key, value in found.items()}


async def _json(cmd: list[str], what: str) -> list[dict]:
    result = await firewall.run(cmd)
    if not result.ok:
        raise HTTPException(500, f"Could not read the host's {what}: {firewall.reason(result)}")
    try:
        found = json.loads(result.stdout or "[]")
    except ValueError:
        raise HTTPException(500, f"Could not read the host's {what}: ip printed no JSON") from None
    return [item for item in found if isinstance(item, dict)]


async def host_networks() -> list[tuple[ipaddress.IPv4Network, str]]:
    """The IPv4 networks of the host's addresses and routes outside unos-wg, with their interface."""
    networks: list[tuple[ipaddress.IPv4Network, str]] = []
    for link in await _json(["ip", "-j", "-4", "addr"], "addresses"):
        if link.get("ifname") == INTERFACE:
            continue
        for info in link.get("addr_info") or []:
            try:
                networks.append((ipaddress.IPv4Interface(f"{info['local']}/{info['prefixlen']}").network,
                                 str(link.get("ifname"))))
            except (KeyError, ValueError):
                continue
    for route in await _json(["ip", "-j", "-4", "route", "show", "table", "all"], "routes"):
        if route.get("dev") == INTERFACE or route.get("dst") in (None, "default"):
            continue
        try:
            networks.append((ipaddress.IPv4Network(route["dst"], strict=False), str(route.get("dev"))))
        except ValueError:
            continue
    return networks


async def network_snapshot() -> set[tuple]:
    """The host's IPv4 addresses and routes outside unos-wg, to compare before and after a change."""
    found: set[tuple] = set()
    for link in await _json(["ip", "-j", "-4", "addr"], "addresses"):
        if link.get("ifname") == INTERFACE:
            continue
        for info in link.get("addr_info") or []:
            found.add(("address", link.get("ifname"), info.get("local"), info.get("prefixlen")))
    for route in await _json(["ip", "-j", "-4", "route", "show", "table", "all"], "routes"):
        if route.get("dev") == INTERFACE:
            continue
        found.add(("route", *(str(route.get(key)) for key in
                              ("type", "dst", "gateway", "dev", "table", "prefsrc", "metric"))))
    return found


def _describe(item: tuple) -> str:
    if item[0] == "address":
        return f"the address {item[2]}/{item[3]} of {item[1]}"
    return f"the route to {item[2]} on {item[4]}"


def _address_of(endpoint: str) -> ipaddress.IPv4Address | None:
    """The IPv4 address of ``address:port`` as ss prints it (also [::ffff:a.b.c.d]:port)."""
    text = endpoint.rpartition(":")[0].strip("[]")
    try:
        found = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return None
    if found.version == 6:
        return found.ipv4_mapped
    return found


async def peers_in(network: ipaddress.IPv4Network) -> list[str]:
    """The addresses of the host's established TCP connections (SSH, SMB, ...) that lie in ``network``:
    the tunnel's route would take their replies."""
    result = await firewall.run(["ss", "-Htn", "state", "established"])
    if not result.ok:
        raise HTTPException(500, f"Could not read the host's connections: {firewall.reason(result)}")
    found = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            address = _address_of(parts[3])
            if address is not None and address in network and str(address) not in found:
                found.append(str(address))
    return found


async def udp_listener(port: int) -> str | None:
    """The program that listens on UDP ``port`` of the host, or None."""
    result = await firewall.run(["ss", "-Hulnp"])
    if not result.ok:
        raise HTTPException(500, f"Could not read the host's UDP ports: {firewall.reason(result)}")
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].rpartition(":")[2] == str(port):
            found = re.search(r'users:\(\("([^"]+)"', line)
            return found.group(1) if found else "a program of the kernel"
    return None


# ---------------------------------------------------------------------------
# The state, as the API answers it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class State:
    installed: bool
    enabled: bool = False
    active: bool = False
    config: Config | None = None
    public_key: str | None = None
    live: dict = field(default_factory=dict)


async def status() -> State:
    refuse_locally()
    if not await installed():
        return State(installed=False)
    config = await read_config()
    enabled, active = await unit_state()
    key = await server_public_key() if config is not None else None
    live = await live_peers() if active else {}
    return State(True, enabled, active, config, key, live)


# ---------------------------------------------------------------------------
# The changes
# ---------------------------------------------------------------------------


async def enable(port: int, network: ipaddress.IPv4Network, endpoint: str, client: str | None = None
                 ) -> tuple[bool, Config]:
    """Turn remote access on: the key, the configuration, the firewall's rule, then the unit. ``client`` is
    the address of the asking computer: a tunnel network that holds it, or another computer the host
    talks to, would take their replies, so it is refused."""
    from . import remote_rules

    await require_tools()
    async with lock():
        old = await read_config()
        enabled, active = await unit_state()
        if old is not None and (enabled or active) and (old.port != port or old.network != network):
            raise HTTPException(409, CHANGE_WHILE_ON)
        if old is not None and old.peers and old.network != network:
            raise HTTPException(409, PEERS_IN_NETWORK)
        if old is not None and old.peers and old.port != port:
            raise HTTPException(409, PEERS_USE_PORT)
        if old is not None and enabled and active:
            # On with this port and network: only the endpoint may change, and the rule is made sure of.
            await remote_rules.sync(remote_port=port)
            if old.endpoint == endpoint:
                return False, old
            await write_config(replace(old, endpoint=endpoint))
            return True, replace(old, endpoint=endpoint)
        if not active:
            program = await udp_listener(port)
            if program is not None:
                raise HTTPException(409, f"UDP port {port} is in use on the host ({program}): choose another port")
        for found, interface in await host_networks():
            if found.overlaps(network):
                raise HTTPException(409, f"The tunnel network {network} overlaps the host's network {found} ({interface})")
        asking = _address_of(f"{client}:0") if client else None
        talking = [*([str(asking)] if asking is not None and asking in network else []), *await peers_in(network)]
        if talking:
            raise HTTPException(409, f"The tunnel network {network} holds {', '.join(dict.fromkeys(talking))}, "
                                     "which the NAS talks to on the LAN: choose another network")
        await remote_rules.refuse_while_firewall_waits()
        address = ipaddress.IPv4Interface(f"{next(network.hosts())}/{network.prefixlen}")
        config = Config(address=address, port=port, endpoint=endpoint, peers=old.peers if old else ())
        before = await network_snapshot()
        await server_public_key(make=True)
        await write_config(config)
        try:
            await remote_rules.sync(remote_port=port)
        except HTTPException:
            await _back_off()
            raise
        started = await firewall.run(["systemctl", "enable", "--now", UNIT])
        if not started.ok:
            await _back_off()
            raise HTTPException(500, f"Failed to turn remote access on: {firewall.reason(started)}")
        changed = sorted(_describe(item) for item in (before ^ await network_snapshot()))
        if changed:
            await _back_off()
            raise HTTPException(500, "Remote access was turned off again: bringing the tunnel up changed "
                                     + "; ".join(changed))
        logger.info("Remote access turned on: UDP port %d, tunnel network %s", port, network)
        return True, config


async def _back_off() -> None:
    """Undo a turning on that failed: the unit off and the firewall's rule gone; errors are logged."""
    from . import remote_rules

    stopped = await firewall.run(["systemctl", "disable", "--now", UNIT])
    if not stopped.ok:
        logger.error("Could not turn remote access off again: %s", firewall.reason(stopped))
    try:
        await remote_rules.sync(remote_port=None)
    except HTTPException as exc:
        logger.error("Could not remove remote access's firewall rule: %s", exc.detail)


async def disable() -> tuple[bool, str | None]:
    """Turn remote access off: the unit stopped and disabled, the firewall's rule removed; the key,
    the configuration and the peers stay. Whether it was on, and what could not be done after the
    tunnel went down (its firewall rule stays then, to be removed by the next change)."""
    from . import remote_rules

    await require_tools()
    async with lock():
        enabled, active = await unit_state()
        if not enabled and not active:
            return False, None
        await remote_rules.refuse_while_firewall_waits()
        stopped = await firewall.run(["systemctl", "disable", "--now", UNIT])
        if not stopped.ok:
            raise HTTPException(500, f"Failed to turn remote access off: {firewall.reason(stopped)}")
        logger.info("Remote access turned off")
        try:
            await remote_rules.sync(remote_port=None)
        except HTTPException as exc:
            logger.warning("Remote access is off, but its firewall rule could not be removed: %s", exc.detail)
            return True, f"Remote access is off, but its firewall rule could not be removed: {exc.detail}"
        return True, None


async def set_endpoint(endpoint: str) -> bool:
    await require_tools()
    async with lock():
        config = await read_config()
        if config is None:
            raise HTTPException(409, NOT_SET_UP)
        if config.endpoint == endpoint:
            return False
        await write_config(replace(config, endpoint=endpoint))
        return True


def _free_address(config: Config) -> ipaddress.IPv4Address:
    taken = {config.address.ip, *(peer.address for peer in config.peers)}
    for candidate in config.network.hosts():
        if candidate not in taken:
            return candidate
    raise HTTPException(409, f"The tunnel network {config.network} has no free address for another peer")


async def add_peer(name: str, public_key: str | None) -> tuple[Peer, str]:
    """Add a peer; its configuration, with the private key made for it unless it gave its public key."""
    await require_tools()
    async with lock():
        config = await read_config()
        if config is None:
            raise HTTPException(409, NOT_SET_UP)
        if any(peer.name == name for peer in config.peers):
            raise HTTPException(409, f"A peer is named {name} already")
        server_key = await server_public_key()
        if server_key is None:
            raise HTTPException(409, NOT_SET_UP)
        private_key = None
        if public_key is None:
            private_key, public_key = new_key_pair()
        if public_key == server_key or any(peer.public_key == public_key for peer in config.peers):
            raise HTTPException(409, "A peer has this public key already")
        peer = Peer(name, public_key, _free_address(config))
        await write_config(replace(config, peers=(*config.peers, peer)))
        _enabled, active = await unit_state()
        if active:
            added = await firewall.run(["wg", "set", INTERFACE, "peer", public_key,
                                        "allowed-ips", f"{peer.address}/32"])
            if not added.ok:
                await write_config(config)
                raise HTTPException(500, f"Failed to add the peer to the tunnel: {firewall.reason(added)}")
        logger.info("Remote access: peer %s added with the address %s", name, peer.address)
        return peer, peer_config(config, server_key, peer, private_key)


async def revoke_peer(ident: str) -> Peer:
    await require_tools()
    async with lock():
        config = await read_config()
        peer = next((p for p in (config.peers if config else ()) if p.id == ident), None)
        if config is None or peer is None:
            raise HTTPException(404, f"Peer not found: {ident}")
        await write_config(replace(config, peers=tuple(p for p in config.peers if p.id != ident)))
        _enabled, active = await unit_state()
        if active:
            removed = await firewall.run(["wg", "set", INTERFACE, "peer", peer.public_key, "remove"])
            if not removed.ok:
                raise HTTPException(
                    500, f"The peer was removed from the configuration, but the running tunnel still has it "
                         f"({firewall.reason(removed)}): turn remote access off and on again")
        logger.info("Remote access: peer %s revoked", peer.name)
        return peer

"""System > Network: the host's interfaces, DNS, gateway, routes and connections (ADR-0245).

Every command runs on the host through src/host.py (ADR-0020). Disable and
Enable are in links.py: a Disable comes back on by itself unless it is kept
(ADR-0244). A Disable, Keep or Enable that changed an interface, and a new host
name, are audited (ADR-0351).
"""

import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import links
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction
from ..auth.utils import verify_token
from ..security.validators import validate_interface_name

router = APIRouter(prefix="/network", tags=["network"])

# The host's /etc/hostname, mounted into the container (deploy/docker-compose.yml).


class NetworkInterface(BaseModel):
    name: str
    mac: str
    # The operational state the kernel reports: UP, DOWN (also without a link), UNKNOWN, ...
    state: str
    # Administratively up: what Disable and Enable change.
    enabled: bool
    # IPv4 and IPv6, with their prefix lengths.
    ip_addresses: list[str]
    # Mb/s, while the link is up and the driver knows it.
    speed: int | None
    mtu: int
    type: str
    # Seconds until a disable that waits to be kept comes back on by itself.
    revert_in: int | None


class InterfaceList(BaseModel):
    interfaces: list[NetworkInterface]
    # How long a disable waits to be kept.
    revert_seconds: int


class DNSConfig(BaseModel):
    nameservers: list[str]
    search_domains: list[str]


class HostnameConfig(BaseModel):
    hostname: str


class NetworkStats(BaseModel):
    interface: str
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int


def _is_dockers(dev: str) -> bool:
    return dev.startswith(("docker", "br-", "veth"))


def _json_list(text: str, what: str) -> list:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        raise HTTPException(500, f"Failed to parse {what}")
    if not isinstance(data, list):
        raise HTTPException(500, f"Failed to parse {what}")
    return data


async def _speed(name: str) -> int | None:
    """The link speed in Mb/s from the host's sysfs: None without a link, for Wi-Fi and when unknown."""
    if "/" in name or name in ("", ".", ".."):
        return None
    result = await links.run(["cat", f"/sys/class/net/{name}/speed"], timeout=5)
    try:
        speed = int(result.stdout.strip()) if result.ok else 0
    except ValueError:
        return None
    return speed if speed > 0 else None


async def _interface(entry: dict) -> NetworkInterface:
    """An interface of `ip -j addr show`, with its speed and a disable that waits to be kept."""
    name = entry.get("ifname", "")
    enabled = links.is_enabled(entry)
    state = entry.get("operstate", "unknown")
    return NetworkInterface(
        name=name,
        mac=entry.get("address", ""),
        state=state,
        enabled=enabled,
        ip_addresses=[
            f"{addr['local']}/{addr.get('prefixlen')}"
            for addr in entry.get("addr_info", [])
            if addr.get("family") in ("inet", "inet6") and addr.get("local")
        ],
        speed=await _speed(name) if state == "UP" else None,
        mtu=entry.get("mtu", 1500),
        type=entry.get("link_type", "unknown"),
        revert_in=None if enabled else await links.revert_in(name),
    )


@router.get("/interfaces", response_model=InterfaceList)
async def get_interfaces(_=Depends(verify_token)):
    """The host's interfaces, without the loopback and Docker's."""
    result = await links.run(["ip", "-j", "addr", "show"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get network interfaces: {links.reason(result)}")
    entries = [
        e for e in _json_list(result.stdout, "network data")
        if isinstance(e, dict) and not links.is_hidden(e.get("ifname", ""))
    ]
    interfaces = await asyncio.gather(*(_interface(entry) for entry in entries))
    return InterfaceList(interfaces=list(interfaces), revert_seconds=links.REVERT_SECONDS)


@router.get("/interfaces/{name}", response_model=NetworkInterface)
async def get_interface(name: str, _=Depends(verify_token)):
    """One interface of the host."""
    validate_interface_name(name)
    if links.is_hidden(name):
        raise HTTPException(404, f"Interface {name} not found")
    result = await links.run(["ip", "-j", "addr", "show", "dev", name])
    if not result.ok:
        if "does not exist" in result.stderr:
            raise HTTPException(404, f"Interface {name} not found")
        raise HTTPException(500, f"Failed to get interface {name}: {links.reason(result)}")
    entries = [e for e in _json_list(result.stdout, "network data") if isinstance(e, dict)]
    if not entries:
        raise HTTPException(404, f"Interface {name} not found")
    return await _interface(entries[0])


async def _audit_interface(request: Request, user, name: str, details: str) -> None:
    await audit_change(AuditAction.NETWORK_CONFIG_CHANGED, request, user.username,
                       resource_type="network_interface", resource_id=name, details=details)


@router.post("/interfaces/{name}/down")
async def bring_interface_down(request: Request, name: str, user=Depends(verify_token)):
    """Disable an interface. It comes back on by itself after revert_in seconds unless the
    change is kept with POST .../down/confirm (ADR-0244)."""
    revert_in = await links.disable(name)
    await _audit_interface(
        request, user, name, f"Disabled; it comes back on by itself in {revert_in} seconds unless the disable is kept"
    )
    return {"status": "success", "interface": name, "state": "down", "revert_in": revert_in}


@router.post("/interfaces/{name}/down/confirm")
async def keep_interface_down(request: Request, name: str, user=Depends(verify_token)):
    """Keep a disable that waits to be kept; 409 when none waits (it was turned back on)."""
    await links.keep(name)
    await _audit_interface(request, user, name, "Disable kept: it stays off")
    return {"status": "success", "interface": name, "state": "down"}


@router.post("/interfaces/{name}/up")
async def bring_interface_up(request: Request, name: str, user=Depends(verify_token)):
    """Enable an interface, with the addresses and routes its disable saved."""
    if await links.enable(name):
        await _audit_interface(request, user, name, "Enabled")
    return {"status": "success", "interface": name, "state": "up"}


@router.get("/stats")
async def get_network_stats(_=Depends(verify_token)):
    """Traffic counters of the host's interfaces, from its /proc/net/dev."""
    result = await links.run(["cat", "/proc/net/dev"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get network stats: {links.reason(result)}")
    stats = []
    for line in result.stdout.splitlines()[2:]:  # two header lines
        name, _sep, counters = line.partition(":")
        name, parts = name.strip(), counters.split()
        if len(parts) < 16 or links.is_hidden(name):
            continue
        try:
            stats.append(NetworkStats(
                interface=name,
                rx_bytes=int(parts[0]), rx_packets=int(parts[1]), rx_errors=int(parts[2]),
                tx_bytes=int(parts[8]), tx_packets=int(parts[9]), tx_errors=int(parts[10]),
            ))
        except ValueError:
            continue
    return {"stats": stats}


# A field of `resolvectl status`: "DNS Servers: 192.168.1.1". Its further values
# follow on the next lines, indented and without a name. IPv6 addresses are
# lower-case hexadecimal and have no space after a colon, so none looks like a field.
_RESOLVECTL_FIELD = re.compile(r"\s*([A-Za-z][A-Za-z.]*(?: [A-Za-z.]+)*):(?:\s+|$)(.*)")


def parse_resolvectl(text: str) -> tuple[list[str], list[str]]:
    """The DNS servers and search domains in the output of `resolvectl status`.

    Fallback servers (used only when no other is known), IPv6 link-local
    servers and routing-only domains ("~.", "~example.org") are left out.
    """
    servers: list[str] = []
    domains: list[str] = []
    field = None
    for line in text.splitlines():
        match = _RESOLVECTL_FIELD.match(line)
        if match:
            field, values = match.group(1), match.group(2).split()
        elif line[:1].isspace() and line.strip():
            values = line.split()
        else:
            # A section ("Global", "Link 2 (eth0)") or an empty line.
            field, values = None, []
        if field == "DNS Servers":
            for value in values:
                server = value.split("#", 1)[0]  # "1.1.1.1#cloudflare-dns.com" (DNS over TLS)
                if not server.lower().startswith("fe80:") and server not in servers:
                    servers.append(server)
        elif field in ("DNS Domain", "Search Domains"):
            domains.extend(d for d in values if not d.startswith("~") and d not in domains)
    return servers, domains


@router.get("/dns", response_model=DNSConfig)
async def get_dns_config(_=Depends(verify_token)):
    """The host's DNS servers and search domains: systemd-resolved's, else /etc/resolv.conf's."""
    servers: list[str] = []
    domains: list[str] = []
    resolvectl = await links.run(["resolvectl", "status"])
    if resolvectl.ok:
        servers, domains = parse_resolvectl(resolvectl.stdout)
    if not servers:
        resolv_conf = await links.run(["cat", "/etc/resolv.conf"])
        if not resolv_conf.ok and not resolvectl.ok:
            raise HTTPException(500, f"Failed to get DNS config: {links.reason(resolv_conf)}")
        for line in resolv_conf.stdout.splitlines() if resolv_conf.ok else []:
            parts = line.split()
            if parts[:1] == ["nameserver"] and len(parts) > 1 and parts[1] not in ("127.0.0.53", "127.0.0.11"):
                servers.append(parts[1])
            elif parts[:1] == ["search"]:
                domains.extend(d for d in parts[1:] if d not in domains)
    return DNSConfig(nameservers=servers, search_domains=domains)


@router.get("/hostname", response_model=HostnameConfig)
async def get_hostname(_=Depends(verify_token)):
    """The host's name, as `hostname` prints it on the host (ADR-0331)."""
    result = await links.run(["hostname"])
    return HostnameConfig(hostname=result.stdout.strip() if result.ok else "unknown")


@router.put("/hostname")
async def set_hostname(request: Request, config: HostnameConfig, user=Depends(verify_token)):
    """Set the host's name with hostnamectl, which also writes /etc/hostname: it lasts (ADR-0331).
    A name other than the one `hostname` printed before is audited (ADR-0351)."""
    if not re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", config.hostname):
        raise HTTPException(400, "Invalid hostname format")
    before = await links.run(["hostname"])
    old = before.stdout.strip() if before.ok else ""
    result = await links.run(["hostnamectl", "set-hostname", config.hostname])
    if not result.ok:
        raise HTTPException(500, f"Failed to set hostname: {links.reason(result)}")
    if old != config.hostname:
        await audit_change(
            AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="host",
            details=f"Host name changed from {old} to {config.hostname}" if old else f"Host name set to {config.hostname}",
        )
    return {"status": "success", "hostname": config.hostname}


@router.get("/routes")
async def get_routes(_=Depends(verify_token)):
    """The host's IPv4 and IPv6 routing tables (main), without Docker's interfaces."""
    ipv4 = await links.run(["ip", "-j", "route", "show"])
    if not ipv4.ok:
        raise HTTPException(500, f"Failed to get routes: {links.reason(ipv4)}")
    routes = _json_list(ipv4.stdout, "routing data")
    # A host without IPv6 has no IPv6 table.
    ipv6 = await links.run(["ip", "-6", "-j", "route", "show"])
    if ipv6.ok:
        routes += _json_list(ipv6.stdout, "routing data")
    return {"routes": [r for r in routes if isinstance(r, dict) and not _is_dockers(r.get("dev", ""))]}


@router.get("/gateway")
async def get_default_gateway(_=Depends(verify_token)):
    """The host's IPv4 default route."""
    result = await links.run(["ip", "-j", "route", "show", "default"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get gateway: {links.reason(result)}")
    routes = [r for r in _json_list(result.stdout, "routing data") if isinstance(r, dict)]
    route = next((r for r in routes if not _is_dockers(r.get("dev", ""))), routes[0] if routes else None)
    if route is None:
        return {"gateway": None, "interface": None}
    return {"gateway": route.get("gateway"), "interface": route.get("dev")}


@router.get("/connections")
async def get_connections(_=Depends(verify_token)):
    """The host's TCP and UDP sockets (`ss -tunap`), the first 50."""
    result = await links.run(["ss", "-tunap"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get connections: {links.reason(result)}")
    connections = []
    for line in result.stdout.strip().splitlines()[1:]:  # a header line
        parts = line.split()
        if len(parts) >= 5:
            connections.append({
                "protocol": parts[0],
                "state": parts[1],
                "local": parts[4],
                "remote": parts[5] if len(parts) > 5 else "",
            })
    return {"connections": connections[:50]}

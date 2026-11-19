"""System > Network: the host's interfaces, DNS, gateway, routes and connections (ADR-0245).

Every command runs on the host through src/host.py (ADR-0020). Disable and
Enable are in links.py: a Disable comes back on by itself unless it is kept
(ADR-0244). A Disable, Keep or Enable that changed an interface, and a new host
name, are audited (ADR-0351).
"""

import asyncio
import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from . import links
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction
from ..auth.utils import verify_token
from ..openapi import BODY, answer, errors
from ..security.validators import validate_interface_name

router = APIRouter(prefix="/network", tags=["network"])

# The host's /etc/hostname, mounted into the container (deploy/docker-compose.yml).


_ETH0 = {
    "name": "eth0", "mac": "00:00:5e:00:53:01", "state": "UP", "enabled": True,
    "ip_addresses": ["192.0.2.10/24", "2001:db8::10/64", "fe80::1/64"], "speed": 1000, "mtu": 1500,
    "type": "ether", "revert_in": None,
}


class NetworkInterface(BaseModel):
    """A network interface of the host."""

    model_config = ConfigDict(json_schema_extra={"examples": [_ETH0]})

    name: str = Field(description="The interface's name, such as eth0, end1 or wlan0.")
    mac: str = Field(description="Its link-layer address, a MAC address for Ethernet and Wi-Fi; empty when it has none.")
    # The operational state the kernel reports: UP, DOWN (also without a link), UNKNOWN, ...
    state: str = Field(
        description="The operational state that the kernel reports, such as UP, DOWN (also without a link) or UNKNOWN."
    )
    # Administratively up: what Disable and Enable change.
    enabled: bool = Field(description="Whether it is administratively up: what Disable and Enable change.")
    # IPv4 and IPv6, with their prefix lengths.
    ip_addresses: list[str] = Field(description="Its IPv4 and IPv6 addresses, each with its prefix length.")
    # Mb/s, while the link is up and the driver knows it.
    speed: int | None = Field(
        description="The link speed in Mb/s; null while the link is not UP, for Wi-Fi and when the driver does not "
                    "know it."
    )
    mtu: int = Field(description="The MTU in bytes.")
    type: str = Field(
        description="The link type that ip reports, such as ether, or none for a link without one (WireGuard); "
                    "unknown when ip reports no type."
    )
    # Seconds until a disable that waits to be kept comes back on by itself.
    revert_in: int | None = Field(
        description="Seconds until the automatic revert turns a disabled interface back on unless the disable is "
                    "kept; null when no disable waits to be kept."
    )


class InterfaceList(BaseModel):
    """The host's interfaces."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "interfaces": [
            _ETH0,
            {"name": "wlan0", "mac": "00:00:5e:00:53:02", "state": "DOWN", "enabled": False, "ip_addresses": [],
             "speed": None, "mtu": 1500, "type": "ether", "revert_in": 42},
        ],
        "revert_seconds": 60,
    }]})

    interfaces: list[NetworkInterface] = Field(description="The interfaces, without the loopback, dummy0 and Docker's.")
    # How long a disable waits to be kept.
    revert_seconds: int = Field(description="How long a disable waits to be kept, in seconds.")


class DNSConfig(BaseModel):
    """The host's DNS servers and search domains."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "nameservers": ["192.0.2.1", "2001:db8::1"], "search_domains": ["example.com"],
    }]})

    nameservers: list[str] = Field(
        description="The addresses of the DNS servers: systemd-resolved's without its fallback and link-local IPv6 "
                    "servers, else /etc/resolv.conf's without the local stub resolvers 127.0.0.53 and 127.0.0.11."
    )
    search_domains: list[str] = Field(description="The search domains, without routing-only domains.")


class HostnameConfig(BaseModel):
    """The host's name."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"hostname": "nas"}]})

    hostname: str = Field(
        description="The host's name. A new one is one label of letters, digits and hyphens, at most 63 characters, "
                    "with no hyphen at either end; the host's own is unknown when it cannot be read."
    )


class NetworkStats(BaseModel):
    """The traffic counters of one interface, as the host's kernel counts them."""

    interface: str = Field(description="The interface's name.")
    rx_bytes: int = Field(description="Bytes received.")
    tx_bytes: int = Field(description="Bytes sent.")
    rx_packets: int = Field(description="Packets received.")
    tx_packets: int = Field(description="Packets sent.")
    rx_errors: int = Field(description="Errors while receiving.")
    tx_errors: int = Field(description="Errors while sending.")


# The path parameter of the routes of one interface.
InterfaceName = Annotated[str, Path(description="The interface's name, such as eth0, end1 or wlan0.")]

# What every route of one interface answers for its name (security.validators.validate_interface_name,
# links._link), in the API documentation (ADR-0417).
_NAME_ERRORS = {
    400: "The name is not an interface's name, or it is the loopback's, dummy0 or one of Docker's: UNOS does not change it.",
    404: "The host has no interface of this name.",
}


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


@router.get(
    "/interfaces", response_model=InterfaceList, summary="List the host's interfaces",
    responses=errors({500: "ip could not list the host's interfaces, or its output could not be read."}),
)
async def get_interfaces(_=Depends(verify_token)):
    """The host's network interfaces, without the loopback, dummy0 and Docker's, and how long a disable
    waits to be kept."""
    result = await links.run(["ip", "-j", "addr", "show"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get network interfaces: {links.reason(result)}")
    entries = [
        e for e in _json_list(result.stdout, "network data")
        if isinstance(e, dict) and not links.is_hidden(e.get("ifname", ""))
    ]
    interfaces = await asyncio.gather(*(_interface(entry) for entry in entries))
    return InterfaceList(interfaces=list(interfaces), revert_seconds=links.REVERT_SECONDS)


@router.get(
    "/interfaces/{name}", response_model=NetworkInterface, summary="Read an interface",
    responses=errors({
        400: "The name is not an interface's name.",
        404: "The host has no interface of this name, or it is the loopback, dummy0 or one of Docker's.",
        500: "ip could not read the interface, or its output could not be read.",
    }),
)
async def get_interface(name: InterfaceName, _=Depends(verify_token)):
    """One network interface of the host."""
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


async def _audit_interface(request: Request, user, name: str, details: str, *, success: bool = True) -> None:
    await audit_change(AuditAction.NETWORK_CONFIG_CHANGED, request, user.username,
                       resource_type="network_interface", resource_id=name, details=details, success=success)


@router.post(
    "/interfaces/{name}/down", summary="Disable an interface",
    responses={
        **answer({"status": "success", "interface": "eth0", "state": "down", "revert_in": 60},
                 "success, the interface, down, and the seconds until the automatic revert turns it back on unless "
                 "the disable is kept."),
        **errors(_NAME_ERRORS, {
            409: "The interface is disabled already, carries VLANs, or is still coming back on from its last disable.",
            500: "A step on the host failed, such as reading the interface or its VLANs, saving its addresses and "
                 "routes, arming the automatic revert or disabling it: the answer says which.",
        }),
    },
)
async def bring_interface_down(request: Request, name: InterfaceName, user=Depends(verify_token)):
    """Disable an interface, after saving its addresses and routes and arming the automatic
    revert: unless POST /network/interfaces/{name}/down/confirm keeps the disable within revert_in
    seconds, the interface comes back on with them. The audit log gets network_config_changed.
    
    ADR-0244, ADR-0246, ADR-0351.
    """
    revert_in = await links.disable(name)
    await _audit_interface(
        request, user, name, f"Disabled; it comes back on by itself in {revert_in} seconds unless the disable is kept"
    )
    return {"status": "success", "interface": name, "state": "down", "revert_in": revert_in}


@router.post(
    "/interfaces/{name}/down/confirm", summary="Keep an interface's disable",
    responses={
        **answer({"status": "success", "interface": "eth0", "state": "down"},
                 "success, the interface, and down: it stays disabled."),
        **errors(_NAME_ERRORS, {
            409: "No disable of the interface waits to be kept: it was turned back on, or not disabled from UNOS.",
            500: "The interface could not be read, or the disable could not be kept and the automatic revert turns "
                 "it back on, or whether it was kept is not known.",
        }),
    },
)
async def keep_interface_down(request: Request, name: InterfaceName, user=Depends(verify_token)):
    """Keep a disable that waits to be kept: its automatic revert is called off and the interface
    stays disabled. The audit log gets network_config_changed.
    
    ADR-0244, ADR-0246.
    """
    await links.keep(name)
    await _audit_interface(request, user, name, "Disable kept: it stays off")
    return {"status": "success", "interface": name, "state": "down"}


@router.post(
    "/interfaces/{name}/up", summary="Enable an interface",
    responses={
        **answer({"status": "success", "interface": "eth0", "state": "up"},
                 "success, the interface, and up; also for an interface that was enabled already."),
        **errors(_NAME_ERRORS, {
            500: "The interface could not be read or turned on, or it is on but its saved addresses and routes "
                 "could not all be restored.",
        }),
    },
)
async def bring_interface_up(request: Request, name: InterfaceName, user=Depends(verify_token)):
    """Enable an interface, with the addresses and routes its disable saved; a disable that waits
    to be kept is called off. An interface that is enabled already is left as it is. The audit log
    gets network_config_changed when the interface was turned on, as failed when not all of them
    could be restored.
    
    ADR-0244, ADR-0246, ADR-0351.
    """
    try:
        enabled = await links.enable(name)
    except links.PartlyEnabled as exc:
        await _audit_interface(
            request, user, name,
            f"Enabled, but its saved addresses and routes could not all be restored: {exc.reason}", success=False,
        )
        raise
    if enabled:
        await _audit_interface(request, user, name, "Enabled")
    return {"status": "success", "interface": name, "state": "up"}


@router.get(
    "/stats", summary="Read the interfaces' traffic counters",
    responses={
        **answer(
            {"stats": [{"interface": "eth0", "rx_bytes": 9123456789, "tx_bytes": 1234567890, "rx_packets": 7345123,
                        "tx_packets": 2345678, "rx_errors": 0, "tx_errors": 0}]},
            "stats: for each interface of the host but the loopback, dummy0 and Docker's, its name and the bytes, packets "
            "and errors it received (rx) and sent (tx).",
        ),
        **errors({500: "The host's /proc/net/dev could not be read."}),
    },
)
async def get_network_stats(_=Depends(verify_token)):
    """The traffic counters of the host's interfaces, from its /proc/net/dev."""
    result = await links.run(["cat", "/proc/net/dev"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get network stats: {links.reason(result)}")
    stats = [
        NetworkStats(interface=name, **counters._asdict())
        for name, counters in links.parse_net_dev(result.stdout).items()
        if not links.is_hidden(name)
    ]
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


@router.get(
    "/dns", response_model=DNSConfig, summary="Read the host's DNS settings",
    responses=errors({500: "Neither resolvectl nor the host's /etc/resolv.conf could be read."}),
)
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


@router.get("/hostname", response_model=HostnameConfig, summary="Read the host's name")
async def get_hostname(_=Depends(verify_token)):
    """The host's name, as hostname prints it on the host; unknown when it cannot be read.
    
    ADR-0331.
    """
    result = await links.run(["hostname"])
    return HostnameConfig(hostname=result.stdout.strip() if result.ok else "unknown")


@router.put(
    "/hostname", summary="Set the host's name",
    responses={
        **answer({"status": "success", "hostname": "nas"}, "success, and the host's new name."),
        **errors(BODY, {
            400: "The name is not one label of letters, digits and hyphens of at most 63 characters, with no hyphen "
                 "at either end.",
            500: "hostnamectl could not set the name.",
        }),
    },
)
async def set_hostname(request: Request, config: HostnameConfig, user=Depends(verify_token)):
    """Set the host's name with hostnamectl, which also writes /etc/hostname: it lasts after a
    restart. A name other than the one before is written to the audit log (settings_changed).
    
    ADR-0331, ADR-0351.
    """
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


@router.get(
    "/routes", summary="List the host's routes",
    responses={
        **answer(
            {"routes": [
                {"dst": "default", "gateway": "192.0.2.1", "dev": "eth0", "protocol": "dhcp",
                 "prefsrc": "192.0.2.10", "metric": 100, "flags": []},
                {"dst": "192.0.2.0/24", "dev": "eth0", "protocol": "kernel", "scope": "link",
                 "prefsrc": "192.0.2.10", "metric": 100, "flags": []},
                {"dst": "2001:db8::/64", "dev": "eth0", "protocol": "ra", "metric": 100, "flags": [],
                 "pref": "medium"},
            ]},
            "routes: the routes of the main IPv4 table, then of the IPv6 one when the host has it, each as "
            "ip -j route prints it (dst, gateway, dev, protocol, scope, prefsrc, metric, flags and others), "
            "without those of Docker's interfaces.",
        ),
        **errors({500: "ip could not list the IPv4 routes, or its output could not be read."}),
    },
)
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


@router.get(
    "/gateway", summary="Read the host's default gateway",
    responses={
        **answer({"gateway": "192.0.2.1", "interface": "eth0"},
                 "gateway and interface of the IPv4 default route, one not through Docker's interfaces when there "
                 "is one; both null without a default route, the gateway null for a route without one."),
        **errors({500: "ip could not list the default routes, or its output could not be read."}),
    },
)
async def get_default_gateway(_=Depends(verify_token)):
    """The host's IPv4 default route: its gateway and its interface."""
    result = await links.run(["ip", "-j", "route", "show", "default"])
    if not result.ok:
        raise HTTPException(500, f"Failed to get gateway: {links.reason(result)}")
    routes = [r for r in _json_list(result.stdout, "routing data") if isinstance(r, dict)]
    route = next((r for r in routes if not _is_dockers(r.get("dev", ""))), routes[0] if routes else None)
    if route is None:
        return {"gateway": None, "interface": None}
    return {"gateway": route.get("gateway"), "interface": route.get("dev")}


@router.get(
    "/connections", summary="List the host's connections",
    responses={
        **answer(
            {"connections": [
                {"protocol": "tcp", "state": "ESTAB", "local": "192.0.2.10:445", "remote": "192.0.2.20:51234"},
                {"protocol": "udp", "state": "UNCONN", "local": "0.0.0.0:3702", "remote": "0.0.0.0:*"},
            ]},
            "connections: at most 50 of the host's TCP and UDP sockets, each with its protocol, its state, and "
            "its local and remote address and port as ss prints them.",
        ),
        **errors({500: "ss could not list the host's sockets."}),
    },
)
async def get_connections(_=Depends(verify_token)):
    """The host's TCP and UDP sockets (ss -tunap), the first 50."""
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

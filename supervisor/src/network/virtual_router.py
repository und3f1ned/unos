"""System > Network: the bonds, VLANs and bridges of the host (ADR-0424).

Administrators only (main.py's _ADMIN_ONLY); the `network` area of API tokens (ADR-0392). Every
change goes through src/network/virtual.py, which changes the host only through src/host.py.
A change that may cut the asking computer or the host's uplink off waits to be kept, and the host
undoes it unless POST /network/virtual-interfaces/confirm keeps it in time. Each change that was
made, and each Keep, writes an audit entry (network_interface_created, network_interface_deleted,
network_change_kept).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.config import JsonDict

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction
from ..auth.utils import client_ip, verify_token
from ..openapi import BODY, errors
from . import virtual
from .links import is_hidden

router = APIRouter(prefix="/network", tags=["network"])

_BOND: JsonDict = {
    "kind": "bond", "name": "bond0", "present": True, "state": "UP", "mode": "802.3ad", "ports": ["eth0", "eth1"],
    "parent": None, "vlan_id": None, "mtu": None, "addressing": "copied", "addresses": [], "copied_from": "eth0",
    "protected": True,
}
_VLAN: JsonDict = {
    "kind": "vlan", "name": "bond0.20", "present": True, "state": "UP", "mode": None, "ports": [],
    "parent": "bond0", "vlan_id": 20, "mtu": None, "addressing": "static", "addresses": ["198.51.100.10/24"],
    "copied_from": None, "protected": False,
}


class VirtualInterface(BaseModel):
    """A bond, VLAN or bridge that UNOS made on the host."""

    model_config = ConfigDict(json_schema_extra={"examples": [_BOND, _VLAN]})

    kind: Literal["bond", "vlan", "bridge"] = Field(description="bond, vlan or bridge.")
    name: str = Field(description="The interface's name.")
    present: bool = Field(description="Whether the host has the interface now.")
    state: str = Field(description="Its operational state as the kernel reports it, such as UP or DOWN; ABSENT "
                                   "when the host does not have it.")
    mode: str | None = Field(description="A bond's mode: 802.3ad (LACP) or balance-rr; null for the others.")
    ports: list[str] = Field(description="A bond's interfaces or a bridge's ports; empty for a VLAN.")
    parent: str | None = Field(description="A VLAN's interface or bond; null for the others.")
    vlan_id: int | None = Field(description="A VLAN's id, 1 to 4094; null for the others.")
    mtu: int | None = Field(description="The MTU in bytes that UNOS set; null for the kernel's.")
    addressing: Literal["none", "dhcp", "static", "copied"] = Field(
        description="How it gets addresses: none, dhcp (IPv4, without the server's gateway, routes and DNS), "
                    "static, or copied: the settings of the port it took them over from."
    )
    addresses: list[str] = Field(description="Its static addresses with their prefix lengths.")
    copied_from: str | None = Field(description="The port whose network settings it took over, or null.")
    protected: bool = Field(
        description="Whether it carries the default route or the asking computer's connection, or what they rest "
                    "on: a change of it waits to be kept."
    )


class HostInterface(BaseModel):
    """An interface of the host as a change of bonds, VLANs and bridges sees it."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "eth0", "kind": "physical", "wireless": False, "master": None, "parent": None,
        "managed": True, "addresses": ["192.0.2.10/24"], "protected": True,
    }]})

    name: str = Field(description="The interface's name.")
    kind: str = Field(description="physical (a network card), bond, bridge, vlan, or the kernel's kind of another "
                                  "virtual interface, such as dummy or wireguard.")
    wireless: bool = Field(description="Whether it is a wireless interface, which is no port of a bond or bridge.")
    master: str | None = Field(description="The bond or bridge it is a port of, or null.")
    parent: str | None = Field(description="The interface that a VLAN (or another stacked interface) is on, or null.")
    managed: bool = Field(description="Whether systemd-networkd manages it.")
    addresses: list[str] = Field(description="Its addresses but the link-local ones, with their prefix lengths.")
    protected: bool = Field(
        description="Whether it carries the default route or the asking computer's connection, or what they rest "
                    "on: a change that touches it waits to be kept."
    )


class WaitingChange(BaseModel):
    """A change of the bonds, VLANs and bridges."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"action": "create", "kind": "bond", "name": "bond0"}]})

    action: Literal["create", "delete"] = Field(description="create or delete.")
    kind: Literal["bond", "vlan", "bridge"] = Field(description="bond, vlan or bridge.")
    name: str = Field(description="The interface's name.")


class VirtualStatus(BaseModel):
    """The bonds, VLANs and bridges that UNOS made, and the change that waits to be kept."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "manager": "systemd-networkd", "supported": True, "interfaces": [_BOND, _VLAN],
        "host_interfaces": [
            {"name": "eth0", "kind": "physical", "wireless": False, "master": "bond0", "parent": None,
             "managed": True, "addresses": [], "protected": True},
        ],
        "unreadable": [], "revert_in": 87, "reverting": False, "revert_failed": False,
        "change": {"action": "create", "kind": "vlan", "name": "bond0.20"},
        "revert_seconds": 120,
    }]})

    manager: Literal["systemd-networkd", "NetworkManager", "ifupdown", "none"] = Field(
        description="What keeps the host's network: UNOS changes it only where systemd-networkd does and "
                    "NetworkManager does not run."
    )
    supported: bool = Field(description="Whether UNOS can make bonds, VLANs and bridges on this host.")
    interfaces: list[VirtualInterface] = Field(description="The bonds, VLANs and bridges that UNOS made.")
    host_interfaces: list[HostInterface] = Field(description="The host's interfaces but the loopback and Docker's.")
    unreadable: list[str] = Field(description="Metadata lines of UNOS's .netdev files that UNOS could not read.")
    revert_in: int | None = Field(description="Seconds until the host undoes the change that waits to be kept; null "
                                              "when none waits.")
    reverting: bool = Field(description="Whether the host is undoing a change now.")
    revert_failed: bool = Field(description="Whether the host's last revert could not undo everything.")
    change: WaitingChange | None = Field(description="The change that waits to be kept; null when none waits.")
    revert_seconds: int = Field(description="How long a change that may cut the connection waits to be kept.")


class _Addressing(BaseModel):
    mtu: int | None = Field(default=None, ge=virtual.MIN_MTU, le=virtual.MAX_MTU,
                            description="The MTU in bytes, 1280 to 9216; null for the kernel's.")
    addressing: Literal["none", "dhcp", "static"] = Field(
        default="none",
        description="How the new interface gets addresses: none, dhcp (IPv4, without the server's gateway, routes "
                    "and DNS, so it never takes the default route) or static. A bond or bridge that takes a port "
                    "carrying the default route or the asking computer's connection takes over that port's network "
                    "settings instead.",
    )
    addresses: list[str] = Field(default_factory=list, max_length=virtual.MAX_ADDRESSES,
                                 description="With static: 1 to 4 addresses with their prefix lengths, no gateway.")


class BondCreate(_Addressing):
    """A new bond of the host's wired network cards."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"name": "bond0", "mode": "802.3ad", "ports": ["eth0", "eth1"], "mtu": None, "addressing": "none",
         "addresses": []},
    ]})

    name: str = Field(description="The bond's name: 1 to 15 letters, digits, '_', '.' and '-', starting with a letter.")
    mode: Literal["802.3ad", "balance-rr"] = Field(
        description="802.3ad: LACP, which the switch must offer on the ports; balance-rr: round robin."
    )
    ports: list[str] = Field(min_length=2, max_length=virtual.MAX_PORTS,
                             description="2 to 8 wired network cards, each in no other bond or bridge.")


class VlanCreate(_Addressing):
    """A new VLAN on a wired network card or a bond."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"parent": "bond0", "vlan_id": 20, "name": None, "mtu": None, "addressing": "static",
         "addresses": ["198.51.100.10/24"]},
    ]})

    parent: str = Field(description="The wired network card or bond the VLAN is on.")
    vlan_id: int = Field(ge=1, le=4094, description="The VLAN id, 1 to 4094.")
    name: str | None = Field(default=None, description="The VLAN's name; null for <parent>.<id>.")


class BridgeCreate(_Addressing):
    """A new bridge, for containers and virtual machines."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"name": "br0", "ports": [], "mtu": None, "addressing": "none", "addresses": []},
    ]})

    name: str = Field(description="The bridge's name: 1 to 15 letters, digits, '_', '.' and '-', starting with a "
                                  "letter.")
    ports: list[str] = Field(default_factory=list, max_length=virtual.MAX_PORTS,
                             description="0 to 8 ports: wired network cards, bonds or VLANs, each in no other bond "
                                         "or bridge.")


class VirtualChange(BaseModel):
    """A change of the host's bonds, VLANs and bridges."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"status": "success", "action": "created", "kind": "bond", "name": "bond0", "revert_in": 120},
        {"status": "success", "action": "deleted", "kind": "bridge", "name": "br0", "revert_in": None},
    ]})

    status: Literal["success"] = Field(description="success.")
    action: Literal["created", "deleted"] = Field(description="created or deleted.")
    kind: Literal["bond", "vlan", "bridge"] = Field(description="bond, vlan or bridge.")
    name: str = Field(description="The interface's name.")
    revert_in: int | None = Field(
        description="Seconds until the host undoes the change unless POST /network/virtual-interfaces/confirm keeps "
                    "it; null for a change that cannot cut the connection, which is kept already."
    )


class VirtualKept(BaseModel):
    """A change that was kept."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"status": "success", "change": {"action": "create", "kind": "bond", "name": "bond0"}},
    ]})

    status: Literal["success"] = Field(description="success.")
    change: WaitingChange | None = Field(description="The change that was kept; null when its record was lost.")


_LOCAL = {503: "UNOS runs its host commands locally (UNOS_HOST_EXEC=local): it does not change this machine's network."}
_FAILED = {500: "A host command failed or systemd-networkd did not make the change in time: the answer says what was "
                "undone, or that the host undoes it by itself."}
_CHANGE_ERRORS = {
    **_LOCAL, **_FAILED,
    400: "A value is not one UNOS takes: a name, the MTU, the addresses, the mode, the number of ports or a port "
         "named twice.",
    404: "A port or the VLAN's interface is not on the host.",
    409: "Refused: systemd-networkd does not keep the host's network, a change waits to be kept or is being undone, "
         "the name is taken, the VLAN id is on the interface already, or a port cannot be one (in a bond or bridge "
         "already, with VLANs on it, wireless, or with addresses that systemd-networkd does not manage).",
}

InterfaceName = Annotated[str, Path(description="The name of the bond, VLAN or bridge that UNOS made.")]


def _view(item: virtual.Item, snap: virtual.Snapshot) -> VirtualInterface:
    link = snap.links.get(item.name)
    return VirtualInterface(
        kind=item.kind, name=item.name, present=link is not None, state=link.operstate if link else "ABSENT",
        mode=item.mode, ports=item.ports, parent=item.parent, vlan_id=item.vlan_id, mtu=item.mtu,
        addressing=item.addressing, addresses=item.addresses, copied_from=item.copied_from,
        protected=item.name in snap.protected,
    )


def _host_view(link: virtual.Link, snap: virtual.Snapshot) -> HostInterface:
    return HostInterface(
        name=link.name, kind=link.kind, wireless=link.wireless, master=link.master, parent=link.parent,
        managed=link.setup != "unmanaged", addresses=link.addresses, protected=link.name in snap.protected,
    )


def _waiting(change: virtual.Recorded | None) -> WaitingChange | None:
    return WaitingChange(action=change.action, kind=change.kind, name=change.name) if change else None


def _answer(change: virtual.Change) -> VirtualChange:
    return VirtualChange(status="success", action="created" if change.action == "create" else "deleted",
                         kind=change.item.kind, name=change.item.name, revert_in=change.revert_in)


def _waits(change: virtual.Change) -> str:
    if change.revert_in is None:
        return ""
    return f"; it is undone by itself in {change.revert_in} seconds unless it is kept"


async def _audit_created(request: Request, user, change: virtual.Change, what: str) -> None:
    await audit_change(AuditAction.NETWORK_INTERFACE_CREATED, request, user.username,
                       resource_type="network_interface", resource_id=change.item.name,
                       details=f"{what}{_waits(change)}")


@router.get(
    "/virtual-interfaces", response_model=VirtualStatus, summary="List the bonds, VLANs and bridges",
    responses=errors(_LOCAL, {500: "The host's interfaces, routes or UNOS's files could not be read."}),
)
async def get_virtual_interfaces(request: Request, _=Depends(verify_token)):
    """The bonds, VLANs and bridges that UNOS made on the host, the host's interfaces, what keeps the
    host's network, and the change that waits to be kept.

    ADR-0424.
    """
    snap, found = await virtual.status(client_ip(request))
    return VirtualStatus(
        manager=snap.manager, supported=snap.manager == "systemd-networkd",
        interfaces=[_view(item, snap) for item in snap.items.values()],
        host_interfaces=[_host_view(link, snap) for link in snap.links.values() if not is_hidden(link.name)],
        unreadable=snap.unreadable, revert_in=found.revert_in, reverting=found.reverting,
        revert_failed=found.failed, change=_waiting(found.change), revert_seconds=virtual.REVERT_SECONDS,
    )


@router.post(
    "/virtual-interfaces/bonds", response_model=VirtualChange, summary="Create a bond",
    responses=errors(BODY, _CHANGE_ERRORS),
)
async def create_bond(request: Request, spec: BondCreate, user=Depends(verify_token)):
    """Create a bond of two or more wired network cards with systemd-networkd. A bond that takes the
    card of the default route or of the asking computer's connection takes over its network
    settings and its MAC address, and waits to be kept: the host undoes it after revert_in seconds
    unless POST /network/virtual-interfaces/confirm keeps it. The audit log gets
    network_interface_created.

    ADR-0424.
    """
    change = await virtual.create_bond(spec.name, spec.mode, spec.ports, spec.mtu, spec.addressing, spec.addresses,
                                       client_ip(request))
    await _audit_created(request, user, change, f"Bond {spec.name} ({spec.mode}) of {', '.join(spec.ports)}")
    return _answer(change)


@router.post(
    "/virtual-interfaces/vlans", response_model=VirtualChange, summary="Create a VLAN",
    responses=errors(BODY, _CHANGE_ERRORS),
)
async def create_vlan(request: Request, spec: VlanCreate, user=Depends(verify_token)):
    """Create a VLAN on a wired network card or a bond with systemd-networkd. systemd-networkd
    applies the interface's settings again for it, so a VLAN on the interface of the default route
    or of the asking computer's connection waits to be kept. The audit log gets
    network_interface_created.

    ADR-0424.
    """
    change = await virtual.create_vlan(spec.parent, spec.vlan_id, spec.name, spec.mtu, spec.addressing,
                                       spec.addresses, client_ip(request))
    await _audit_created(request, user, change, f"VLAN {change.item.name} (id {spec.vlan_id}) on {spec.parent}")
    return _answer(change)


@router.post(
    "/virtual-interfaces/bridges", response_model=VirtualChange, summary="Create a bridge",
    responses=errors(BODY, _CHANGE_ERRORS),
)
async def create_bridge(request: Request, spec: BridgeCreate, user=Depends(verify_token)):
    """Create a bridge for containers and virtual machines with systemd-networkd, with no port or
    with wired network cards, bonds or VLANs as ports. A bridge that takes the interface of the
    default route or of the asking computer's connection takes over its settings and waits to be
    kept. The audit log gets network_interface_created.

    ADR-0424.
    """
    change = await virtual.create_bridge(spec.name, spec.ports, spec.mtu, spec.addressing, spec.addresses,
                                         client_ip(request))
    ports = ", ".join(spec.ports) if spec.ports else "no port"
    await _audit_created(request, user, change, f"Bridge {spec.name} of {ports}")
    return _answer(change)


@router.delete(
    "/virtual-interfaces/{name}", response_model=VirtualChange, summary="Delete a bond, VLAN or bridge",
    responses=errors({
        **_LOCAL, **_FAILED,
        400: "The name is not an interface's name.",
        404: "UNOS made no bond, VLAN or bridge of this name.",
        409: "Refused: systemd-networkd does not keep the host's network, a change waits to be kept or is being "
             "undone, the interface was not made in UNOS, VLANs are on it, or it is a port of a bridge.",
    }),
)
async def delete_virtual_interface(request: Request, name: InterfaceName, user=Depends(verify_token)):
    """Delete a bond, VLAN or bridge that UNOS made, with its files: its ports get the host's own
    settings back. A deletion that touches the interface of the default route or of the asking
    computer's connection waits to be kept. The audit log gets network_interface_deleted.

    ADR-0424.
    """
    change = await virtual.delete(name, client_ip(request))
    await audit_change(AuditAction.NETWORK_INTERFACE_DELETED, request, user.username,
                       resource_type="network_interface", resource_id=name,
                       details=f"{'VLAN' if change.item.kind == 'vlan' else change.item.kind.capitalize()} "
                               f"{name} deleted{_waits(change)}")
    return _answer(change)


@router.post(
    "/virtual-interfaces/confirm", response_model=VirtualKept, summary="Keep the network change",
    responses=errors(_LOCAL, {
        409: "No network change waits to be kept: it was kept already, or it was undone.",
        500: "The change could not be kept, so it is undone by itself; or whether it was kept is not known; or it is "
             "kept but could not be written for the next boot.",
    }),
)
async def keep_virtual_change(request: Request, user=Depends(verify_token)):
    """Keep the change that waits to be kept: the host's revert is called off, and the change is
    written where the host's boot puts it back. The audit log gets network_change_kept.

    ADR-0424.
    """
    change = await virtual.keep()
    what = virtual.describe(change.action, change.kind, change.name) if change else "the network change"
    await audit_change(AuditAction.NETWORK_CHANGE_KEPT, request, user.username, resource_type="network_interface",
                       resource_id=change.name if change else None, details=f"Kept: {what}")
    return VirtualKept(status="success", change=_waiting(change))

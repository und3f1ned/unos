"""System > Remote access: the WireGuard tunnel and the app names (ADR-0420). Administrators only
(main.py's _ADMIN_ONLY); the `network` area of API tokens (ADR-0392). Every change writes an audit
entry after it was made, for a real change only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.utils import client_ip, verify_token
from ..openapi import BODY
from . import app_names, remote_rules, wireguard
from .remote_schemas import (
    AppName,
    AppNameApp,
    AppNameCreate,
    AppNamesStatus,
    RemoteAccessChange,
    RemoteAccessDefaults,
    RemoteAccessEnable,
    RemoteAccessEndpoint,
    RemoteAccessStatus,
    RemotePeer,
    RemotePeerCreate,
    RemotePeerCreated,
)

router = APIRouter(prefix="/network", tags=["network"])

# FastAPI's responses: status -> what the route answers with it.
_Responses = dict[int | str, dict[str, Any]]
_LOCAL: _Responses = {503: {"description": "UNOS runs its host commands locally (UNOS_HOST_EXEC=local): not a NAS host"}}
_ERRORS: _Responses = {
    **_LOCAL,
    409: {"description": "Refused: wireguard-tools is missing, a value conflicts with the host, or a firewall "
                         "change waits to be kept"},
    500: {"description": "A host command failed; what was changed before it was undone"},
}
# A route that takes a body (src/request_limits.py).
_BODY: _Responses = {status: {"description": text} for status, text in BODY.items()}
_CADDY: _Responses = {
    502: {"description": "Caddy did not load the app names and serves what it served before"},
    503: {"description": "The web UI's Caddy cannot be reached, or UNOS runs its host commands locally"},
    504: {"description": "The web UI's Caddy did not answer in time"},
}


def _peer(peer: wireguard.Peer, live: dict) -> RemotePeer:
    handshake, received, sent = live.get(peer.public_key, (None, None, None))
    return RemotePeer(id=peer.id, name=peer.name, public_key=peer.public_key, address=str(peer.address),
                      latest_handshake=handshake, received=received, sent=sent)


async def _firewall_rule(state: wireguard.State) -> bool | None:
    if state.config is None or not await remote_rules._has_ufw():
        return None
    from ..security import firewall

    rules = (await firewall.read()).rules
    return not remote_rules.missing(rules, [remote_rules.remote_rule(state.config.port)], ipv6=False)


@router.get("/remote-access", response_model=RemoteAccessStatus, summary="Read remote access",
            description="Whether the host has wireguard-tools, whether the tunnel is on and up, its port, network, "
                        "endpoint and public key, the peers with their last handshake and traffic, and whether the "
                        "firewall lets the port in. No private key is ever answered.",
            responses={**_LOCAL, 500: {"description": "The host could not be read"}})
async def get_remote_access(_=Depends(verify_token)):
    state = await wireguard.status()
    config = state.config
    return RemoteAccessStatus(
        installed=state.installed, enabled=state.enabled, active=state.active, configured=config is not None,
        port=config.port if config else None, network=str(config.network) if config else None,
        address=str(config.address.ip) if config else None, endpoint=config.endpoint if config else None,
        public_key=state.public_key, peers=[_peer(p, state.live) for p in (config.peers if config else ())],
        firewall_rule=await _firewall_rule(state) if state.installed and state.enabled else None,
        defaults=RemoteAccessDefaults(port=wireguard.DEFAULT_PORT, network=wireguard.DEFAULT_NETWORK),
    )


@router.post("/remote-access/enable", response_model=RemoteAccessChange, summary="Turn remote access on",
             description="Make the server's key on the host (once), write the tunnel's configuration, let its port "
                         "through the host's ufw and enable wg-quick@unos-wg, which comes up at every boot. Turned off "
                         "again when bringing it up changed another interface's address or route. The port and the "
                         "network change only while it is off. The audit log gets remote_access_enabled.",
             responses={**_ERRORS, **_BODY})
async def enable_remote_access(request: Request, body: RemoteAccessEnable, user=Depends(verify_token)):
    changed, config = await wireguard.enable(body.port, wireguard.check_network(body.network), body.endpoint,
                                             client=client_ip(request))
    if changed:
        await audit_change(AuditAction.REMOTE_ACCESS_ENABLED, request, user.username, resource_type="remote_access",
                           resource_id=wireguard.INTERFACE,
                           details=f"Remote access was turned on: UDP port {config.port}, tunnel network "
                                   f"{config.network}, endpoint {config.endpoint}")
    return RemoteAccessChange(changed=changed)


@router.post("/remote-access/disable", response_model=RemoteAccessChange, summary="Turn remote access off",
             description="Stop and disable wg-quick@unos-wg and remove its firewall rule: every peer's tunnel ends. "
                         "The key, the configuration and the peers stay. The audit log gets remote_access_disabled.",
             responses=_ERRORS)
async def disable_remote_access(request: Request, user=Depends(verify_token)):
    changed, left = await wireguard.disable()
    if changed:
        await audit_change(AuditAction.REMOTE_ACCESS_DISABLED, request, user.username, resource_type="remote_access",
                           resource_id=wireguard.INTERFACE, details="Remote access was turned off",
                           severity=AuditSeverity.WARNING)
    if left:
        raise HTTPException(500, left)
    return RemoteAccessChange(changed=changed)


@router.put("/remote-access/endpoint", response_model=RemoteAccessChange, summary="Change the endpoint",
            description="The DNS name or IP address that new peers' configurations name. The audit log gets "
                        "remote_access_changed.", responses={**_ERRORS, **_BODY})
async def set_remote_access_endpoint(request: Request, body: RemoteAccessEndpoint, user=Depends(verify_token)):
    changed = await wireguard.set_endpoint(body.endpoint)
    if changed:
        await audit_change(AuditAction.REMOTE_ACCESS_CHANGED, request, user.username, resource_type="remote_access",
                           resource_id=wireguard.INTERFACE, details=f"The endpoint is now {body.endpoint}")
    return RemoteAccessChange(changed=changed)


@router.post("/remote-access/peers", response_model=RemotePeerCreated, status_code=201, summary="Add a peer",
             description="Add a device that may connect, with the lowest free address of the tunnel network. Its "
                         "configuration is answered once and kept nowhere; with the device's own public key it holds "
                         "no private key. The audit log gets remote_access_peer_added, without a key.",
             responses={**_ERRORS, **_BODY})
async def add_remote_peer(request: Request, body: RemotePeerCreate, user=Depends(verify_token)):
    peer, config = await wireguard.add_peer(body.name, body.public_key)
    await audit_change(AuditAction.REMOTE_ACCESS_PEER_ADDED, request, user.username, resource_type="remote_peer",
                       resource_id=peer.id,
                       details=f"Added the peer {peer.name} with the address {peer.address} and the public key "
                               f"{peer.public_key}")
    return RemotePeerCreated(peer=_peer(peer, {}), config=config)


@router.delete("/remote-access/peers/{peer_id}", response_model=RemoteAccessChange, summary="Revoke a peer",
               description="Remove the peer from the configuration and from the running tunnel: its tunnel ends at "
                           "once. The audit log gets remote_access_peer_revoked.",
               responses={**_ERRORS, 404: {"description": "No peer has this id"}})
async def revoke_remote_peer(
    request: Request,
    peer_id: str = Path(description="The peer's id, as GET /network/remote-access lists it.", examples=["wg-3f2a9c01b7d4"]),
    user=Depends(verify_token),
):
    peer = await wireguard.revoke_peer(peer_id)
    await audit_change(AuditAction.REMOTE_ACCESS_PEER_REVOKED, request, user.username, resource_type="remote_peer",
                       resource_id=peer.id, details=f"Revoked the peer {peer.name} ({peer.address})",
                       severity=AuditSeverity.WARNING)
    return RemoteAccessChange(changed=True)


# ==================== App names ====================


async def _app_names_status() -> AppNamesStatus:
    found = await app_names.status()
    published = set(found.apps)
    return AppNamesStatus(
        volume=found.volume, announcer=found.announcer, host_name=found.host_name, address=found.address,
        announced=found.announced,
        names=[AppName(label=e.label, name=app_names.fqdn(e.label, found.host_name), app=e.app, port=e.port,
                       installed=(e.app, e.port) in published) for e in found.entries],
        apps=[AppNameApp(name=name, port=port) for name, port in found.apps],
    )


@router.get("/app-names", response_model=AppNamesStatus, summary="Read the app names",
            description="The local names of apps, the address avahi announces them with and the host's address now, "
                        "and the published TCP ports of the installed apps.",
            responses={**_LOCAL, 500: {"description": "The host could not be read"}})
async def get_app_names(_=Depends(verify_token)):
    return await _app_names_status()


@router.post("/app-names", response_model=AppNamesStatus, status_code=201, summary="Give an app a name",
             description="Give a published TCP port of an installed app (its web port by default) the name "
                         "<label>-<host name>.local: Caddy routes it to that port over HTTPS from its local CA, avahi "
                         "announces it, and the firewall lets Caddy reach the port. The audit log gets app_names_changed.",
             responses={**_ERRORS, **_CADDY, **_BODY})
async def add_app_name(request: Request, body: AppNameCreate, user=Depends(verify_token)):
    entry = await app_names.add(body.label, body.app, body.port)
    await audit_change(AuditAction.APP_NAMES_CHANGED, request, user.username, resource_type="app_name",
                       resource_id=entry.label, details=f"Added the name {entry.label} for {entry.app} (port {entry.port})")
    return await _app_names_status()


@router.delete("/app-names/{label}", response_model=AppNamesStatus, summary="Remove an app name",
               description="Caddy, avahi and the firewall forget the name; the app's own port stays. The audit log "
                           "gets app_names_changed.",
               responses={**_ERRORS, **_CADDY, 404: {"description": "No app has this name"}})
async def remove_app_name(
    request: Request,
    label: str = Path(description="The name's first part, as GET /network/app-names lists it.", examples=["jellyfin"]),
    user=Depends(verify_token),
):
    entry = await app_names.remove(label)
    await audit_change(AuditAction.APP_NAMES_CHANGED, request, user.username, resource_type="app_name",
                       resource_id=entry.label, details=f"Removed the name {entry.label} of {entry.app}",
                       severity=AuditSeverity.WARNING)
    return await _app_names_status()


@router.post("/app-names/apply", response_model=AppNamesStatus, summary="Apply the app names again",
             description="Announce the names with the host's address of now and have Caddy load them again. The "
                         "audit log gets app_names_changed.",
             responses={**_ERRORS, **_CADDY})
async def apply_app_names(request: Request, user=Depends(verify_token)):
    await app_names.apply_again()
    await audit_change(AuditAction.APP_NAMES_CHANGED, request, user.username, resource_type="app_name",
                       details="The app names were announced again and Caddy loaded them again")
    return await _app_names_status()

"""UNOS's firewall rules for remote access and the app names (ADR-0420), by ADR-0416's own means.

Each is a rule of the host's ufw whose comment starts with ``UNOS:``, which System > Security does not
delete: ``UNOS: remote access`` lets WireGuard's UDP port in from anywhere while remote access is on;
``UNOS: app names`` lets the web UI's Caddy (WEBUI_ADDRESS, deploy/docker-compose.yml) reach the port
of each app that has a name. ufw keeps its rules while it is off, so they are added whenever the host
has ufw. The firewall's Enable and Reset add those that are missing (``missing_commands``, which
security/firewall.py's ``_missing_unos_rules`` asks). A change holds the firewall's lock and is refused
while a firewall change waits to be kept, so that the firewall's revert never takes it back. Adding an
allow rule cuts nobody off, so it arms no revert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException

from ..security import firewall
from ..security.schemas import FirewallRule

logger = logging.getLogger(__name__)

REMOTE_COMMENT = "UNOS: remote access"
APP_NAMES_COMMENT = "UNOS: app names"
OWN_COMMENTS = (REMOTE_COMMENT, APP_NAMES_COMMENT)
# The web UI's container on unos-net (deploy/docker-compose.yml): Caddy connects from it to the apps'
# ports on the host.
WEBUI_ADDRESS = "172.31.254.2"


class _Keep:
    """The current value: the sync reads it from the host."""


KEEP = _Keep()


@dataclass(frozen=True)
class Wanted:
    protocol: str
    port: str
    source: str | None
    comment: str


def remote_rule(port: int) -> Wanted:
    return Wanted("udp", str(port), None, REMOTE_COMMENT)


def app_rule(port: int) -> Wanted:
    return Wanted("tcp", str(port), WEBUI_ADDRESS, APP_NAMES_COMMENT)


def _matches(rule: FirewallRule, wanted: Wanted) -> bool:
    return (rule.action == "allow" and rule.direction == "in" and not rule.route and not rule.log
            and rule.protocol == wanted.protocol and rule.port == wanted.port and rule.destination is None
            and rule.source_port is None and rule.interface is None and rule.app is None
            and rule.source == wanted.source and rule.comment == wanted.comment)


def _families(wanted: Wanted, ipv6: bool) -> set[str]:
    return {"ipv4", "ipv6"} if wanted.source is None and ipv6 else {"ipv4"}


def _command(wanted: Wanted) -> list[str]:
    return firewall._ufw_add("allow", wanted.protocol, wanted.source, wanted.port, wanted.comment)


def missing(rules: list[FirewallRule], wanted: list[Wanted], ipv6: bool) -> list[list[str]]:
    """The ufw commands that add the wanted rules the host lacks."""
    commands = []
    for item in wanted:
        have = {rule.family for rule in rules if _matches(rule, item)}
        if not _families(item, ipv6) <= have:
            commands.append(_command(item))
    return commands


async def _remote_port() -> int | None:
    """The port of remote access while it is on, else None."""
    from . import wireguard

    config = await wireguard.read_config()
    if config is None:
        return None
    return config.port if (await wireguard.unit_state())[0] else None


def _app_ports() -> list[int]:
    from . import app_names

    return app_names.wanted_ports()


async def current_wanted() -> list[Wanted]:
    port = await _remote_port()
    return [*([remote_rule(port)] if port else []), *(app_rule(p) for p in _app_ports())]


async def missing_commands(state: "firewall.State") -> list[list[str]]:
    """For the firewall's Enable and Reset: the commands that add remote access's and the app names'
    rules the host lacks. None when they cannot be read: the firewall goes on with its own rules."""
    try:
        wanted = await current_wanted()
    except HTTPException as exc:
        logger.warning("The firewall goes on without the rules of remote access and the app names: %s", exc.detail)
        return []
    return missing(state.rules, wanted, state.defaults.ipv6)


async def _has_ufw() -> bool:
    return await firewall.installed()


async def refuse_while_firewall_waits() -> None:
    """409 while a firewall change waits to be kept or is being undone (ADR-0416)."""
    if not await _has_ufw():
        return
    async with firewall._lock():
        await firewall._refuse_while_waiting()


async def sync(*, remote_port: "int | None | _Keep" = KEEP, app_ports: "list[int] | _Keep" = KEEP) -> None:
    """Make the host's ufw hold the rules of remote access (``remote_port``, or none) and of the app
    names (``app_ports``) and no other rule with their comments. Nothing without ufw."""
    if not await _has_ufw():
        return
    async with firewall._lock():
        await firewall._refuse_while_waiting()
        # What the other feature wants is read under the lock (its changes hold remote access's lock too:
        # network/wireguard.lock), so that neither takes back the other's rule.
        port = await _remote_port() if isinstance(remote_port, _Keep) else remote_port
        ports = _app_ports() if isinstance(app_ports, _Keep) else app_ports
        wanted = [*([remote_rule(port)] if port else []), *(app_rule(p) for p in sorted(set(ports)))]
        state = await firewall.read()
        # One rule at a time: ufw's numbers change with each delete.
        for _ in range(len(state.rules) + 1):
            stale = next((rule for rule in state.rules if rule.comment in OWN_COMMENTS
                          and not any(_matches(rule, item) for item in wanted)), None)
            if stale is None:
                break
            result = await firewall.run(["ufw", "--force", "delete", str(stale.number)])
            if not result.ok:
                raise HTTPException(500, f"Failed to remove the firewall rule {stale.comment}: {firewall.reason(result)}")
            state = await firewall.read()
        for cmd in missing(state.rules, wanted, state.defaults.ipv6):
            result = await firewall.run(cmd)
            if not result.ok:
                raise HTTPException(500, f"Failed to add the firewall rule {cmd[-1]}: {firewall.reason(result)}")
        after = await firewall.read()
        if missing(after.rules, wanted, after.defaults.ipv6):
            raise HTTPException(500, "The firewall did not take the rules of remote access and the app names")
        if any(rule.comment in OWN_COMMENTS and not any(_matches(rule, item) for item in wanted)
               for rule in after.rules):
            raise HTTPException(500, "The firewall kept a rule of remote access or the app names that should go")

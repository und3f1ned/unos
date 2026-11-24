"""Where SMB listens: the network interfaces that an administrator chooses (ADR-0391).

No row of ``smb_bind_interfaces``: smbd listens on every address, Samba's default. With rows,
smb.conf binds smbd to the loopback and the chosen interfaces, by name (``smbconf.global_settings``),
and an apply that changes those lines restarts smbd (``service.install_smb_config``): smbd opens its
listening sockets only when it starts.

The host's interfaces come from ``ip -j addr show``, and smbd's listeners and the clients' open
connections to them from ``ss`` (ADR-0495), both run on
the host through src/host.py (ADR-0020), so in the host's network namespace, not the container's.
What Samba 4.22 binds, read from its source: the addresses of the named interfaces that are up and
broadcast-capable (or the loopback) when smbd reads the list, except IPv6 link-local ones.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import subprocess
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import host
from ..audit.schemas import AuditSeverity
from ..network import links
from ..security.validators import INTERFACE_NAME_PATTERN
from . import models, schemas, service, smb, smbconf

logger = logging.getLogger(__name__)

MAX_INTERFACES = 16
HOST_TIMEOUT = 15
IP_COMMAND = ["ip", "-j", "addr", "show"]
SMB_PORT = "445"  # smb ports of the rendered [global] (ADR-0275)
# The host's listening and established TCP sockets of its port 445, without a header, with numeric
# addresses and their programs: "LISTEN 0 50 192.0.2.5:445 0.0.0.0:* users:(("smbd",pid=900,fd=31))",
# "ESTAB 0 0 192.0.2.5:445 192.0.2.50:50112 users:(("smbd[192.0.2.50]",pid=4001,fd=35))".
SS_COMMAND = ["ss", "-H", "-n", "-t", "-p", "state", "listening", "state", "established",
              "(", "sport", "=", f":{SMB_PORT}", ")"]
_PROGRAM = re.compile(r'users:\(\("([^"]*)"')
WILDCARDS = ("0.0.0.0", "::")  # noqa: S104 - read from ss's lines, never bound

# The answers, which webui/tests/shares-fakes.ts repeats word for word (ADR-0189).
NAME_RULE = "is not an interface name: use up to 15 letters, digits, '.', '_' and '-', starting with a letter"
LOOPBACK_ALWAYS = "The loopback (lo) is always bound, for the NAS itself: choose interfaces of the network"
DOCKERS = "{name} is one of Docker's interfaces: choose interfaces of the network"
DUMMY = "{name} is a dummy interface, which no computer reaches: choose interfaces of the network"
POINT_TO_POINT = "{name} is a point-to-point interface, which Samba does not listen on"
NO_LAN = (
    "None of the chosen interfaces is on the host, enabled and with an address of the local network: "
    "SMB would answer no computer of the network"
)
HOST_UNREADABLE = "The host's network interfaces could not be read: {reason}"
ONLY_LOOPBACK = (
    "SMB listens only on the loopback: none of the stored network interfaces is valid; choose them again on "
    "the Shares page"
)
NOT_ON_HOST = "{name} is not on the host: smbd listens on it only once it is back and SMB has restarted"
NOT_LISTENING = (
    "smbd does not listen on {address} of {name}: the address came after smbd started; Restart SMB makes it "
    "listen there"
)
EVERY_ADDRESS = "smbd listens on every address, not only on the chosen interfaces: Restart SMB applies the choice"
OUTSIDE = "smbd listens on {address}, which is not an address of the chosen interfaces: Restart SMB applies the choice"
ONLY_SOME = "smbd listens only on {addresses}, not on every address: Restart SMB applies the choice"
NOWHERE = "smbd listens on no address: SMB is not running; Restart SMB starts it"
LISTENERS_UNREADABLE = "smbd's listening addresses could not be read: {reason}"

# The networks that hosts allow admits (ADR-0095).
_ADMITTED = tuple(ipaddress.ip_network(network) for network in smbconf.HOSTS_ALLOW.split())


class Unreadable(Exception):
    """A host command whose answer could not be read; the text says why."""


def no_lan_problem(names: list[str] | tuple[str, ...]) -> str:
    """The status problem of a choice of which no interface is on the host with a LAN address."""
    return (
        f"SMB answers no computer of the network: none of the chosen interfaces ({', '.join(names)}) is on the "
        "host, enabled and with an address of the local network; choose others on the Shares page"
    )


# --- names --------------------------------------------------------------------------------------


def name_problem(name: object) -> str | None:
    """Why ``name`` cannot be chosen whatever the host has, or None (smbconf.is_bindable_name)."""
    if not isinstance(name, str) or INTERFACE_NAME_PATTERN.fullmatch(name) is None:
        return f"{smbconf.note_value(name)} {NAME_RULE}"
    if name == smbconf.LOOPBACK:
        return LOOPBACK_ALWAYS
    if links.is_hidden(name):
        return (DUMMY if name == "dummy0" else DOCKERS).format(name=name)
    return None


def rendered_interfaces(names: list[object]) -> tuple[tuple[str, ...] | None, list[str]]:
    """The stored names, checked again (ADR-0144): the valid ones, sorted (None without a row), and a
    status problem for each one left out. Rows of which none is valid bind the loopback alone."""
    if not names:
        return None, []
    valid: set[str] = set()
    problems: list[str] = []
    for name in names:
        problem = name_problem(name)
        if problem is None:
            valid.add(name)  # type: ignore[arg-type]  # a name without a problem is a text
        else:
            problems.append(f"SMB interface {smbconf.note_value(name)} is left out of smb.conf: {problem}")
    if not valid:
        problems.append(ONLY_LOOPBACK)
    return tuple(sorted(valid)), problems


async def _stored(db: AsyncSession) -> list[object]:
    rows = await db.execute(select(models.SmbBindInterface.name).execution_options(**service._FRESH))
    return [row.name for row in rows.all()]


# --- the host -----------------------------------------------------------------------------------


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None


def is_lan_address(text: str) -> bool:
    """An address that hosts allow admits (ADR-0095), other than the loopback and IPv6 link-local,
    which Samba does not bind."""
    address = _address(text)
    if address is None or address.is_loopback or (address.version == 6 and address.is_link_local):
        return False
    return any(address in network for network in _ADMITTED)


def _sort_key(text: str) -> tuple[int, int, str]:
    address = _address(text)
    return (address.version, int(address), text) if address is not None else (9, 0, text)


def sorted_addresses(addresses) -> list[str]:
    return sorted(set(addresses), key=_sort_key)


@dataclass(frozen=True)
class HostInterface:
    name: str
    flags: frozenset[str]
    state: str
    addresses: tuple[tuple[str, int | None], ...]   # (address, prefix length), in ip's order

    @property
    def enabled(self) -> bool:
        return "UP" in self.flags

    @property
    def offered(self) -> bool:
        """Broadcast-capable: Samba skips the others (add_interface of source3/lib/interface.c)."""
        return "BROADCAST" in self.flags and "POINTOPOINT" not in self.flags

    def bound(self) -> list[str]:
        """The addresses that smbd binds for this interface when it starts with it chosen."""
        if not self.enabled or not ({"BROADCAST", "LOOPBACK"} & self.flags) or "POINTOPOINT" in self.flags:
            return []
        found = []
        for text, _ in self.addresses:
            address = _address(text)
            if address is not None and not (address.version == 6 and address.is_link_local):
                found.append(text)
        return found

    @property
    def lan(self) -> bool:
        return any(is_lan_address(text) for text, _ in self.addresses)

    @property
    def serves_lan(self) -> bool:
        """smbd would answer computers of the LAN here: enabled, broadcast-capable, a LAN address."""
        return self.enabled and self.offered and self.lan


def _command(cmd: list[str]) -> str:
    """The output of a host command that only reads; Unreadable when it fails."""
    try:
        result = host.run_on_host(cmd, timeout=HOST_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise Unreadable(f"the host did not answer within {HOST_TIMEOUT} seconds") from exc
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise Unreadable(str(exc) or type(exc).__name__) from exc
    if not result.ok:
        raise Unreadable((result.stderr or result.stdout).strip() or f"exit status {result.returncode}")
    return result.stdout or ""


def read_host_interfaces() -> list[HostInterface]:
    """The host's interfaces as ``ip -j addr show`` lists them (sync); Unreadable when it fails."""
    try:
        data = json.loads(_command(IP_COMMAND) or "[]")
    except ValueError as exc:
        raise Unreadable("ip printed no JSON") from exc
    if not isinstance(data, list):
        raise Unreadable("ip printed no list")
    found = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("ifname"), str) or not entry["ifname"]:
            continue
        flags = frozenset(flag for flag in entry.get("flags") or () if isinstance(flag, str))
        addresses = []
        for info in entry.get("addr_info") or ():
            if isinstance(info, dict) and info.get("family") in ("inet", "inet6") and isinstance(info.get("local"), str):
                prefix = info.get("prefixlen")
                addresses.append((info["local"], prefix if isinstance(prefix, int) else None))
        state = entry.get("operstate")
        found.append(HostInterface(entry["ifname"], flags, state if isinstance(state, str) else "", tuple(addresses)))
    return found


def _local_addresses(column: str) -> tuple[list[str], str] | None:
    """(addresses, port) of ss's local address column: 192.0.2.5:445, [fd00::5]:445, [fe80::5]%eth0:445,
    and *:445, an IPv6 socket on every address that also takes IPv4 (not IPV6_V6ONLY)."""
    address, sep, port = column.rpartition(":")
    if not sep:
        return None
    if address == "*":
        return list(WILDCARDS), port
    if address.startswith("["):
        address = address[1:].split("]", 1)[0]
    return [address.split("%", 1)[0]], port


def read_smbd_sockets() -> tuple[list[str], int]:
    """The addresses on which smbd listens on TCP 445, sorted, and the open SMB connections, which
    a restart of smbd closes: the established TCP connections to the host's port 445 (sync);
    Unreadable when ss fails. One ss, without smbstatus, whose start alone takes 75 to 165 ms on
    the BPI-W3 (ADR-0495).

    A listener whose program ss does not name counts as smbd's: smb ports gives smbd that port. A
    connection counts whatever ss names its process: smbd serves each client in a process that
    it names after the client."""
    found = []
    connections = 0
    for line in _command(SS_COMMAND).splitlines():
        columns = line.split()
        if len(columns) < 4:
            continue
        local = _local_addresses(columns[3])
        if local is None or local[1] != SMB_PORT:
            continue
        if columns[0] == "ESTAB":
            connections += 1
            continue
        program = _PROGRAM.search(line)
        if columns[0] != "LISTEN" or (program is not None and program.group(1) != "smbd"):
            continue
        found.extend(local[0])
    return sorted_addresses(found), connections


def _try(read):
    try:
        return read(), None
    except Unreadable as exc:
        return None, str(exc)


# --- the answer ---------------------------------------------------------------------------------


def _covered(address: str, listening: set[str]) -> bool:
    """Whether smbd answers on ``address``: a listener there, or on the wildcard of its family."""
    parsed = _address(address)
    if parsed is None:
        return False
    return address in listening or WILDCARDS[parsed.version == 6] in listening


def describe(
    chosen: tuple[str, ...] | None,
    host_interfaces: list[HostInterface] | None,
    host_error: str | None,
    listening: list[str] | None,
    listeners_error: str | None,
    sessions: int | None,
    restart_failed: bool = False,
) -> schemas.SmbInterfaces:
    """The answer of GET /shares/smb-interfaces from what was read (pure). ``restart_failed``: the
    restart that the last change of the interfaces needed failed, which GET /shares/smb-status names;
    Restart SMB is offered then too."""
    chosen_set = set(chosen or ())
    heard = set(listening or ())
    problems: list[str] = []
    if listeners_error is not None:
        problems.append(LISTENERS_UNREADABLE.format(reason=listeners_error))
    infos: list[schemas.SmbInterfaceInfo] = []
    allowed: set[str] = {"127.0.0.1", "::1"}
    missing_problems: list[str] = []
    for interface in host_interfaces or ():
        if interface.name == smbconf.LOOPBACK:
            allowed.update(interface.bound())
            continue
        if links.is_hidden(interface.name):
            continue
        is_chosen = interface.name in chosen_set
        missing: list[str] = []
        if is_chosen:
            allowed.update(interface.bound())
            if listening is not None:
                missing = [a for a in interface.bound() if is_lan_address(a) and not _covered(a, heard)]
        missing_problems.extend(NOT_LISTENING.format(address=a, name=interface.name) for a in missing)
        infos.append(schemas.SmbInterfaceInfo(
            name=interface.name,
            addresses=[f"{a}/{p}" if p is not None else a for a, p in interface.addresses],
            state=interface.state,
            present=True,
            enabled=interface.enabled,
            lan=interface.lan,
            offered=interface.offered,
            chosen=is_chosen,
            listening=[a for a, _ in interface.addresses if listening is not None and _covered(a, heard)],
            missing=missing,
        ))
    if host_interfaces is not None:
        present = {interface.name for interface in host_interfaces}
        for name in sorted(chosen_set - present):
            problems.append(NOT_ON_HOST.format(name=name))
            infos.append(schemas.SmbInterfaceInfo(
                name=name, addresses=[], state="", present=False, enabled=False, lan=False, offered=False,
                chosen=True, listening=[], missing=[],
            ))
    problems.extend(missing_problems)
    restart_needed = bool(missing_problems) or restart_failed
    if listening is not None:
        if not listening:
            problems.append(NOWHERE)
            restart_needed = True
        elif chosen is None:
            if not heard & set(WILDCARDS):
                problems.append(ONLY_SOME.format(addresses=", ".join(listening)))
                restart_needed = True
        elif heard & set(WILDCARDS):
            problems.append(EVERY_ADDRESS)
            restart_needed = True
        elif host_interfaces is not None:
            outside = [a for a in listening if a not in allowed]
            problems.extend(OUTSIDE.format(address=a) for a in outside)
            restart_needed = restart_needed or bool(outside)
    if chosen == ():
        problems.insert(0, ONLY_LOOPBACK)
    return schemas.SmbInterfaces(
        chosen=list(chosen or ()),
        bound=chosen is not None,
        interfaces=infos,
        host_error=HOST_UNREADABLE.format(reason=host_error) if host_error is not None else None,
        listening=listening,
        problems=problems,
        restart_needed=restart_needed,
        sessions=sessions,
    )


def _read_now() -> tuple[list[HostInterface] | None, str | None, list[str] | None, str | None, int | None]:
    """The host's interfaces, and smbd's listeners and open connections, read at once, from one
    worker thread."""
    (interfaces, host_error), (sockets, listeners_error) = host.at_once(
        lambda: _try(read_host_interfaces), lambda: _try(read_smbd_sockets)
    )
    listening, connections = sockets if sockets is not None else (None, None)
    return interfaces, host_error, listening, listeners_error, connections


async def state(db: AsyncSession) -> schemas.SmbInterfaces:
    """GET /shares/smb-interfaces: the choice, the host's interfaces, and smbd's listeners compared with it."""
    chosen, _ = rendered_interfaces(await _stored(db))
    last = service.last_apply()
    pending = bool(last.restart_pending and last.restart_failed)
    return describe(chosen, *await asyncio.to_thread(_read_now), restart_failed=pending)


async def status_problems(db: AsyncSession) -> list[str]:
    """The problem of GET /shares/smb-status while a choice holds no interface that is on the host,
    enabled and with a LAN address (a restore from other hardware, an unplugged adapter). Reads the
    host only while a choice exists; when it cannot, the Shares page's card says why."""
    chosen, _ = rendered_interfaces(await _stored(db))
    if not chosen:  # no choice, or only the loopback (rendered_interfaces names that)
        return []
    interfaces, _ = await asyncio.to_thread(_try, read_host_interfaces)
    if interfaces is None:
        return []
    serving = {interface.name for interface in interfaces if interface.serves_lan}
    return [] if serving & set(chosen) else [no_lan_problem(chosen)]


# --- changes ------------------------------------------------------------------------------------


@dataclass
class Change:
    """What a change did: the answer, and the audit entry's details (None: nothing changed)."""

    info: schemas.SmbInterfaces
    details: str | None
    failed: str | None = None


def _listens(names: list[str]) -> str:
    if not names:
        return "SMB listens on every interface"
    return f"SMB listens only on {', '.join(names)} and the loopback"


async def _check(names: list[str]) -> list[str]:
    """The names of a PUT, checked (400, or 503 while the host cannot be read), sorted, once each."""
    for name in names:
        problem = name_problem(name)
        if problem is not None:
            raise HTTPException(400, problem)
    wanted = sorted(set(names))
    if not wanted:
        return wanted
    interfaces, error = await asyncio.to_thread(_try, read_host_interfaces)
    if interfaces is None:
        raise HTTPException(503, HOST_UNREADABLE.format(reason=error))
    by_name = {interface.name: interface for interface in interfaces}
    for name in wanted:
        if name in by_name and not by_name[name].offered:
            raise HTTPException(400, POINT_TO_POINT.format(name=name))
    if not any(name in by_name and by_name[name].serves_lan for name in wanted):
        raise HTTPException(400, NO_LAN)
    return wanted


async def choose(db: AsyncSession, names: list[str]) -> Change:
    """PUT /shares/smb-interfaces: store the choice and apply smb.conf, which restarts smbd when the
    lines change (ADR-0391). An unchanged choice writes nothing. Takes smb_lock()."""
    wanted = await _check(names)
    async with service.smb_lock():
        try:
            await service._import_if_needed_locked(db)
            stored = await _stored(db)
            before, _ = rendered_interfaces(stored)
            if sorted(stored, key=str) == wanted:
                return Change(info=await state(db), details=None)
            await db.execute(delete(models.SmbBindInterface))
            db.add_all(models.SmbBindInterface(name=name) for name in wanted)
            await db.flush()
            result, _ = await service._stage_commit_install(db)
        except Exception:
            await service._rollback(db)
            raise
    if result.restarted:
        outcome = "smbd restarted"
    elif result.restart_failed is not None:
        outcome = f"smbd could not be restarted: {result.restart_failed}"
    else:
        outcome = "smbd was not restarted"
    previous = "every interface" if before is None else (", ".join(before) or "the loopback")
    info = await state(db)
    info.restarted = result.restarted
    info.warnings = list(result.warnings)
    return Change(info=info, details=f"{_listens(wanted)} (before: {previous}); {outcome}",
                  failed=result.restart_failed)


async def restart(db: AsyncSession) -> Change:
    """POST /shares/smb-interfaces/restart: restart smbd now, so that it binds what the chosen
    interfaces have now (ADR-0391); every open SMB connection ends. Takes smb_lock()."""
    async with service.smb_lock():
        sockets, _ = await asyncio.to_thread(_try, read_smbd_sockets)
        failed = await asyncio.to_thread(smb._restart_smbd)
        last = service.last_apply()
        last.restart_failed = failed
        if failed is None:
            # No connection outlives the restart, and every smbd process reads the installed file.
            last.restart_pending = last.reload_pending = last.reload_partial = False
            last.pending_close = set()
            last.close_after_full_reload = set()
        else:
            # smbd may be stopped: the next apply tries again.
            last.restart_pending = True
    if failed is not None:
        return Change(info=await state(db), details=f"smbd could not be restarted: {failed}", failed=failed)
    if sockets is None:
        ended = "the open SMB connections ended"
    else:
        ended = f"{sockets[1]} open SMB connection{'' if sockets[1] == 1 else 's'} ended"
    info = await state(db)
    info.restarted = True
    return Change(info=info, details=f"Restarted smbd: {ended}")


SEVERITY = AuditSeverity.WARNING  # a restart ends every open SMB connection

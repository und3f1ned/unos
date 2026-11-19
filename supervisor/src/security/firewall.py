"""The host's ufw firewall, changed only with the local networks let in and a revert armed (ADR-0416).

Every command runs on the host through src/host.py (ADR-0020), under the host's timeout(1), so that
none acts after its answer (ADR-0258). The rules are the host's: ufw's own records, the
``### tuple ###`` lines of /etc/ufw/user.rules and user6.rules. UNOS's rules let the local networks
of SMB's ``hosts allow`` (ADR-0095) reach the web UI, SSH, SMB and network discovery; an Enable adds
those that are missing before ufw goes on, and refuses where ufw would cut Docker's forwarding.

An Enable, and a rule change while ufw is on, first saves the rules files, whether ufw was on and
the FORWARD policies in STATE_DIR on the host, then arms a transient systemd timer that puts them
back after REVERT_SECONDS (REVERT_SCRIPT), as the network's revert does (ADR-0244), and only then
changes ufw. Keep (keep()) calls the revert off. A change that fails after the save is undone at
once (RESTORE_SCRIPT); when that fails too, or the host did not answer, the timer undoes it. A revert
that fails leaves the file ``failed``, which the status reports. ufw's own stop opens the FORWARD
chains (policy ACCEPT), which Docker closes, and ufw's start closes both (its DEFAULT_FORWARD_POLICY):
a save with ufw off keeps their policies in ``off4`` and ``off6`` too, which outlive Keep, and
OFF_SCRIPT, which every turning off runs, puts those back, or after a reboot the policies of ufw on.

UNOS's rules also hold those of remote access and the app names while they are on
(network/remote_rules.py, ADR-0420): an Enable and a Reset add them like the others.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import subprocess
import time
import weakref
from typing import NamedTuple
from collections.abc import Callable

from fastapi import HTTPException

from .. import host
from ..shares.smbconf import HOSTS_ALLOW
from .schemas import FirewallRule, FirewallRuleCreate
from .validators import sanitize_error_message

# On the host. /run is a tmpfs: the saved rules cost no writes to the system disk, and a reboot
# clears them (the revert's timer is gone then too).
STATE_DIR = "/run/unos/firewall"
UFW_DIR = "/etc/ufw"
UFW_DEFAULTS = "/etc/default/ufw"
UFW_CONF = "/etc/ufw/ufw.conf"
REVERT_SECONDS = 120
REVERT_UNIT = "unos-firewall-revert"
# Every command runs under the host's timeout(1): TERM after HOST_TIMEOUT seconds, KILL KILL_AFTER
# seconds later (ufw is a Python program, slow to start on a small board). The supervisor waits
# COMMAND_TIMEOUT seconds for nsenter: a command it gave up on may still run, so the revert stays.
HOST_TIMEOUT = 50
KILL_AFTER = 5
COMMAND_TIMEOUT = HOST_TIMEOUT + KILL_AFTER + 5
TIMED_OUT = 124
NO_ANSWER = -1
# The exit status of CANCEL_SCRIPT when no change waits to be kept.
NOTHING_PENDING = 3

# The local networks of SMB's hosts allow, without the loopback, which ufw always lets in.
LOCAL_NETWORKS = tuple(
    network for network in (ipaddress.ip_network(text) for text in HOSTS_ALLOW.split()) if not network.is_loopback
)
# UNOS's rules: TCP for the web UI (80, 443), SSH (22 and sshd's ports), SMB (445) and WS-Discovery's
# metadata (5357); UDP for WS-Discovery (3702) and LLMNR (5355) of wsdd2. ufw's own before rules let
# mDNS in (ADR-0275).
UNOS_TCP_PORTS = (22, 80, 443, 445, 5357)
UNOS_UDP_PORTS = (3702, 5355)
# iSCSI's targets listen on TCP 3260 of the LAN addresses (ADR-0418).
ISCSI_PORT = 3260
WEB_SSH_SMB = "UNOS: web UI, SSH and SMB"
DISCOVERY = "UNOS: network discovery"
ISCSI = "UNOS: iSCSI"
UNOS_PREFIX = "UNOS:"
# ufw takes at most 15 ports in a rule, a range counting as two.
MAX_PORTS = 15

# Texts that webui/tests/firewall.spec.ts repeats word for word (ADR-0189).
FIREWALL_OFF_DETAIL = (
    "The firewall is off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): "
    "this is not a NAS host"
)
UFW_MISSING = "ufw is not installed on the host: install the package ufw to use the firewall"
CHANGE_WAITS = "A firewall change waits to be kept: keep it, or wait until it is undone"
REVERT_RUNNING = "The last firewall change is being undone: try again in a few seconds"
NOTHING_TO_KEEP = "No firewall change waits to be kept: it was kept already, or it was undone"
UNOS_RULE = "UNOS keeps this rule: it lets the local networks reach the web UI, SSH, SMB and iSCSI"
RULE_EXISTS = "The host's firewall has this rule already"
DOCKER_NOT_IN_IPTABLES = (
    "UNOS does not turn ufw on here: Docker's rules are not in the FORWARD chain of iptables (Docker's "
    "nftables backend, or its iptables turned off), so ufw's FORWARD policy could cut the web UI off"
)
MANAGES_BUILTINS = (
    "UNOS does not turn ufw on here: MANAGE_BUILTINS=yes in /etc/default/ufw makes ufw flush Docker's rules"
)
FORWARDS = (
    "UNOS does not turn ufw on here: DEFAULT_FORWARD_POLICY in /etc/default/ufw lets forwarded traffic "
    "through, which Docker closes"
)

# The host scripts. They run with /bin/sh (dash on Debian and Ubuntu): $0 names the script, $1 is
# STATE_DIR, $2 is UFW_DIR. REVERT_SCRIPT is the command of a transient systemd unit, and systemd
# expands "${NAME}" and "$$" in the arguments of a command and "%" begins a specifier: the revert
# has none of them (tests/test_firewall_scripts.py).

# forward_policy TOOL: the policy of TOOL's FORWARD chain, ACCEPT or DROP, or nothing.
# ufw_off ACTION [P4 P6]: ufw --force ACTION (disable or reset), then the FORWARD policies of
# iptables and ip6tables set to P4 and P6, or to those from before ufw's stop, which sets them to
# ACCEPT. Return status 1: ufw failed; 4: a policy could not be set.
# restore: put the saved rules files back and bring ufw to the state saved with them: off when it
# was off (before the files go back, so that a failure never leaves ufw on with the old rules; the
# policies of ufw off are then forgotten), a reload when it was on. Nothing saved: another restore
# took it.
_FUNCTIONS = """\
forward_policy() {
  set -- $("$1" -S FORWARD 2> /dev/null | head -n 1)
  if [ "$1" = -P ] && [ "$2" = FORWARD ]; then
    case "$3" in ACCEPT|DROP) echo "$3" ;; esac
  fi
}
ufw_off() {
  p4=$2
  p6=$3
  case "$p4" in ACCEPT|DROP) ;; *) p4=$(forward_policy iptables) ;; esac
  case "$p6" in ACCEPT|DROP) ;; *) p6=$(forward_policy ip6tables) ;; esac
  ufw --force "$1" || return 1
  if [ -n "$p4" ]; then iptables -P FORWARD "$p4" || return 4; fi
  if [ -n "$p6" ]; then ip6tables -P FORWARD "$p6" || return 4; fi
  return 0
}
restore() {
  [ -e "$1/was" ] || return 0
  if [ "$(cat "$1/was")" != active ]; then
    ufw_off disable "$(cat "$1/forward4" 2> /dev/null)" "$(cat "$1/forward6" 2> /dev/null)" || return 1
    rm -f "$1/off4" "$1/off6"
  fi
  for f in user.rules user6.rules; do
    if [ -e "$1/$f" ]; then
      cp -p "$1/$f" "$2/$f.unos-revert" && mv -f "$2/$f.unos-revert" "$2/$f" || return 1
    fi
  done
  sync "$2/user.rules" "$2" 2> /dev/null || sync
  if [ "$(cat "$1/was")" = active ]; then
    ufw reload || return 1
  fi
  rm -f "$1/was" "$1/user.rules" "$1/user6.rules" "$1/forward4" "$1/forward6"
  return 0
}
"""

# $3: "active" or "inactive", whether ufw was on before the change. A revert that failed before is
# forgotten: this change saves the state as it is now. With ufw off, the FORWARD policies are also
# those that a turning off puts back (off4, off6); with ufw on, those of the save before stay.
SAVE_SCRIPT = _FUNCTIONS + """\
set -e
umask 077
mkdir -p "$1"
rm -f "$1/was" "$1/user.rules" "$1/user6.rules" "$1/forward4" "$1/forward6" "$1/failed"
cp -p "$2/user.rules" "$1/user.rules"
if [ -e "$2/user6.rules" ]; then cp -p "$2/user6.rules" "$1/user6.rules"; fi
forward_policy iptables > "$1/forward4"
forward_policy ip6tables > "$1/forward6"
if [ "$3" != active ]; then
  cp "$1/forward4" "$1/off4"
  cp "$1/forward6" "$1/off6"
fi
echo "$3" > "$1/was"
"""

# $3: the time of the revert, in seconds since the epoch. The marker "pending" says that a change
# waits to be kept.
MARK_SCRIPT = 'echo "$3" > "$1/pending"\n'

DISCARD_SCRIPT = 'rm -f "$1/was" "$1/user.rules" "$1/user6.rules" "$1/forward4" "$1/forward6"\n'

# Keep a change: the revert and a confirmation both start by removing the marker, and only one of
# them can (rm is atomic). Exit status 3: none waits.
CANCEL_SCRIPT = """\
rm "$1/pending" 2> /dev/null || exit 3
systemctl stop unos-firewall-revert.timer 2> /dev/null
rm -f "$1/was" "$1/user.rules" "$1/user6.rules" "$1/forward4" "$1/forward6"
exit 0
"""

# $3: "disable" or "reset". Off, nothing of a failed revert is left to report, and the policies of ufw
# off are used up.
OFF_SCRIPT = _FUNCTIONS + """\
ufw_off "$3" "$(cat "$1/off4" 2> /dev/null)" "$(cat "$1/off6" 2> /dev/null)" || exit
rm -f "$1/failed" "$1/off4" "$1/off6"
"""

RESTORE_SCRIPT = _FUNCTIONS + 'restore "$1" "$2"\n'

# The timer's command: nothing to do for a change that was kept; a restore that fails leaves
# "failed" for the status to report.
REVERT_SCRIPT = (
    'rm "$1/pending" 2> /dev/null || exit 0\n' + _FUNCTIONS
    + 'restore "$1" "$2" || { echo failed > "$1/failed"; exit 1; }\n'
)

SCRIPTS = {
    "unos-firewall-save": SAVE_SCRIPT,
    "unos-firewall-mark": MARK_SCRIPT,
    "unos-firewall-discard": DISCARD_SCRIPT,
    "unos-firewall-cancel": CANCEL_SCRIPT,
    "unos-firewall-off": OFF_SCRIPT,
    "unos-firewall-restore": RESTORE_SCRIPT,
    "unos-firewall-revert": REVERT_SCRIPT,
}

# One lock per event loop (an asyncio.Lock binds to the loop it is first used in; tests run
# several loops, cf. network.links).
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    """Serializes the changes of the firewall."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


class Defaults(NamedTuple):
    """What /etc/default/ufw sets."""

    incoming: str
    outgoing: str
    ipv6: bool
    manage_builtins: bool
    forward_policy: str


class Waiting(NamedTuple):
    """A change that waits to be kept, and its revert."""

    revert_in: int | None  # seconds until it is undone; None when none waits
    reverting: bool         # the revert runs now
    failed: bool            # the last revert could not put everything back
    stale: bool             # a marker without a timer or a running revert


class State(NamedTuple):
    """The host's ufw as UNOS reads it."""

    enabled: bool
    defaults: Defaults
    rules: list[FirewallRule]
    waiting: Waiting


class Change(NamedTuple):
    changed: bool
    revert_in: int | None


# ---------------------------------------------------------------------------
# Host commands
# ---------------------------------------------------------------------------


def _script(name: str, *args: str) -> list[str]:
    return ["/bin/sh", "-c", SCRIPTS[name], name, STATE_DIR, UFW_DIR, *args]


def revert_timer() -> list[str]:
    """The command that arms the revert on the host."""
    return [
        "systemd-run",
        f"--unit={REVERT_UNIT}",
        "--description=UNOS: undo the firewall change unless it is kept",
        f"--on-active={REVERT_SECONDS}",
        # The default accuracy of a timer is a minute.
        "--timer-property=AccuracySec=1s",
        # Unloaded, with its service, once it ran: the next change reuses the name.
        "--timer-property=RemainAfterElapse=no",
        # ufw's messages in English, whatever the host's locale.
        "--setenv=LC_ALL=C",
        "--collect",
        "--quiet",
        "--",
        *_script("unos-firewall-revert"),
    ]


async def run(cmd: list[str]) -> host.HostResult:
    """Run ``cmd`` on the host under its timeout(1); a command that could not run, or that the host
    did not answer in time (NO_ANSWER), is a failed result."""
    timed = ["timeout", f"--kill-after={KILL_AFTER}", str(HOST_TIMEOUT), "env", "LC_ALL=C", *cmd]
    try:
        result = await host.run_on_host_async(timed, timeout=COMMAND_TIMEOUT, errors="replace")
    except subprocess.TimeoutExpired:
        # Not str(exc): it quotes the command line, nsenter and a whole host script.
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
        raise HTTPException(503, FIREWALL_OFF_DETAIL)


async def installed() -> bool:
    """Whether the host has ufw (OPTIONAL_HOST_TOOLS). 500 when the host cannot be asked."""
    missing = await asyncio.to_thread(host.missing_host_binaries, ["ufw"])
    if missing is None:
        raise HTTPException(500, "Whether the host has ufw could not be checked")
    return not missing


async def _require_ufw() -> None:
    _refuse_locally()
    if not await installed():
        raise HTTPException(409, UFW_MISSING)


# ---------------------------------------------------------------------------
# Reading ufw
# ---------------------------------------------------------------------------


_POLICIES = {"ACCEPT": "allow", "DROP": "deny", "REJECT": "reject"}
_SETTING = re.compile(r'^\s*([A-Z_0-9]+)\s*=\s*"?([^"#\s]*)"?')


def _setting(text: str, name: str, default: str) -> str:
    """The value of the shell variable ``name`` in a file of ufw's settings."""
    for line in text.splitlines():
        found = _SETTING.match(line)
        if found and found.group(1) == name:
            return found.group(2)
    return default


def parse_defaults(text: str) -> Defaults:
    """What /etc/default/ufw sets, with ufw's defaults for what it leaves out."""
    settings = {}
    for line in text.splitlines():
        found = _SETTING.match(line)
        if found:
            settings[found.group(1)] = found.group(2)
    return Defaults(
        incoming=_POLICIES.get(settings.get("DEFAULT_INPUT_POLICY", "DROP").upper(), "deny"),
        outgoing=_POLICIES.get(settings.get("DEFAULT_OUTPUT_POLICY", "ACCEPT").upper(), "allow"),
        ipv6=settings.get("IPV6", "yes").lower() == "yes",
        manage_builtins=settings.get("MANAGE_BUILTINS", "no").lower() == "yes",
        forward_policy=settings.get("DEFAULT_FORWARD_POLICY", "DROP").upper(),
    )


def _decode_comment(text: str) -> str | None:
    """A rule's comment: ufw writes it in hex; another text is shown as it is."""
    if not text:
        return None
    try:
        return bytes.fromhex(text).decode("utf-8")
    except ValueError:
        return text


def _address(text: str) -> str | None:
    """A tuple's address; None for anywhere."""
    return None if text in ("0.0.0.0/0", "::/0", "any") else text


class _Tuple(NamedTuple):
    fields: tuple[str, ...]  # without the comment
    comment: str | None


def parse_tuples(text: str) -> list[_Tuple]:
    """The ``### tuple ###`` lines of a rules file, as ufw reads them: 6 to 9 fields, then the comment."""
    found = []
    for line in text.splitlines():
        if not line.startswith("### tuple ###"):
            continue
        body = line.removeprefix("### tuple ###").strip()
        body, _sep, comment = body.partition(" comment=")
        fields = tuple(body.split())
        if not 6 <= len(fields) <= 9:
            continue
        found.append(_Tuple(fields, _decode_comment(comment.strip())))
    return found


def _app_key(fields: tuple[str, ...]) -> str:
    """ufw's get_app_tuple of an application's rule, by which get_rule_by_number counts its rules once."""
    _action, _proto, dport, dst, sport, src, dapp, sapp = fields[:8]
    dapp, sapp = ("" if name == "-" else name for name in (dapp, sapp))
    key = f"{dapp} {dst} {sapp} {src}"
    if dapp == "":
        key = f"{dport} {dst} {sapp} {src}"
    if sapp == "":
        key = f"{dapp} {dst} {sport} {src}"
    direction = fields[8] if len(fields) == 9 else "in"
    return key + " " + direction.replace("!", " ")


def _rule(family: str, number: int, entry: _Tuple) -> FirewallRule:
    fields = entry.fields
    action, proto, dport, dst, sport, src = fields[:6]
    apps = fields[6:8] if len(fields) >= 8 else ("-", "-")
    direction_field = fields[-1] if len(fields) in (7, 9) else "in"
    route = action.startswith("route:")
    action = action.removeprefix("route:")
    action, _sep, log = action.partition("_")
    direction, _sep, interface = direction_field.partition("_")
    app = next((name.replace("%20", " ") for name in apps if name != "-"), None)
    comment = entry.comment
    ident = hashlib.sha256(f"{family} {' '.join(fields)}".encode()).hexdigest()[:12]
    return FirewallRule(
        id=f"fw-{ident}",
        number=number,
        family=family,
        action=action,
        direction=direction if direction in ("in", "out") else "in",
        route=route,
        log=bool(log),
        protocol=proto,
        port=None if dport == "any" else dport,
        destination=_address(dst),
        source_port=None if sport == "any" else sport,
        source=_address(src),
        interface=interface or None,
        app=app,
        comment=comment,
        unos=bool(comment) and comment.upper().startswith(UNOS_PREFIX),
    )


def parse_rules(v4: str, v6: str) -> list[FirewallRule]:
    """The host's rules in the order of ufw's numbers: IPv4, then IPv6, an application's rules once
    (ufw's get_rule_by_number). ``v6`` is empty where ufw does no IPv6."""
    rules: list[FirewallRule] = []
    seen_apps: set[str] = set()
    for family, text in (("ipv4", v4), ("ipv6", v6)):
        for entry in parse_tuples(text):
            if len(entry.fields) >= 8:
                key = _app_key(entry.fields)
                if key in seen_apps:
                    continue
                seen_apps.add(key)
            rules.append(_rule(family, len(rules) + 1, entry))
    return rules


async def _cat(path: str, *, missing_ok: bool = False) -> str:
    result = await run(["cat", path])
    if result.ok:
        return result.stdout
    if missing_ok and "No such file" in result.stderr:
        return ""
    raise HTTPException(500, f"Could not read the firewall: {reason(result)}")


async def waiting() -> Waiting:
    """The change that waits to be kept, as the host's revert state shows it."""
    marker = await run(["cat", f"{STATE_DIR}/pending"])
    deadline = None
    if marker.ok:
        try:
            deadline = int(marker.stdout.strip())
        except ValueError:
            deadline = None
    reverting = (await run(["systemctl", "is-active", "--quiet", f"{REVERT_UNIT}.service"])).ok
    failed = (await run(["test", "-e", f"{STATE_DIR}/failed"])).ok
    stale = False
    if marker.ok and not reverting:
        # A marker whose timer is gone (the supervisor stopped between the two): nothing undoes it.
        stale = not (await run(["systemctl", "is-active", "--quiet", f"{REVERT_UNIT}.timer"])).ok
    left = None if deadline is None or stale else max(0, deadline - int(time.time()))
    return Waiting(left, reverting, failed, stale)


async def read() -> State:
    """The host's ufw. 500 when it cannot be read."""
    result = await run(["ufw", "status"])
    if not result.ok:
        raise HTTPException(500, f"Could not read the firewall: {reason(result)}")
    enabled = result.stdout.lstrip().startswith("Status: active")
    defaults = parse_defaults(await _cat(UFW_DEFAULTS))
    v4 = await _cat(f"{UFW_DIR}/user.rules")
    v6 = await _cat(f"{UFW_DIR}/user6.rules", missing_ok=True) if defaults.ipv6 else ""
    return State(enabled, defaults, parse_rules(v4, v6), await waiting())


# ---------------------------------------------------------------------------
# UNOS's rules
# ---------------------------------------------------------------------------


async def _ssh_ports() -> list[int]:
    """The TCP ports of SSH: those that sshd listens on (ss) and those that its configuration names
    (sshd -T, which also holds where ssh.socket listens for it); none when neither can tell."""
    ports = set()
    listening = await run(["ss", "-Htlnp"])
    if listening.ok:
        for line in listening.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 6 and '(("sshd",' in line:
                port = parts[3].rpartition(":")[2]
                if port.isdigit():
                    ports.add(int(port))
    configured = await run(["sshd", "-T"])
    if configured.ok:
        for line in configured.stdout.splitlines():
            key, _sep, value = line.strip().partition(" ")
            if key == "port" and value.isdigit():
                ports.add(int(value))
    return sorted(port for port in ports if 0 < port < 65536)


def _ufw_add(action: str, protocol: str, source: str | None, port: str, comment: str | None) -> list[str]:
    """The ufw command of an incoming rule: every value checked before (FirewallRuleCreate)."""
    return [
        "ufw", action, "in",
        *(["proto", protocol] if protocol != "any" else []),
        "from", source or "any", "to", "any", "port", port,
        *(["comment", comment] if comment else []),
    ]


def _has(rules: list[FirewallRule], family: str, protocol: str, ports: str, network: str) -> bool:
    wanted = set(ports.split(","))
    for rule in rules:
        if (rule.family == family and rule.action == "allow" and not rule.route and not rule.log
                and rule.direction == "in" and rule.protocol == protocol and rule.port is not None
                and set(rule.port.split(",")) == wanted and rule.destination is None and rule.source_port is None
                and rule.interface is None and rule.app is None and rule.source is not None):
            try:
                if ipaddress.ip_network(rule.source, strict=False) == ipaddress.ip_network(network):
                    return True
            except ValueError:
                continue
    return False


async def _missing_unos_rules(state: State) -> list[list[str]]:
    """The commands that add the UNOS rules that the host lacks."""
    tcp = sorted({*UNOS_TCP_PORTS, *await _ssh_ports()})[:MAX_PORTS]
    tcp_ports = ",".join(str(port) for port in tcp)
    udp_ports = ",".join(str(port) for port in UNOS_UDP_PORTS)
    commands = []
    for network in LOCAL_NETWORKS:
        if network.version == 6 and not state.defaults.ipv6:
            continue
        family = f"ipv{network.version}"
        for protocol, ports, comment in (("tcp", tcp_ports, WEB_SSH_SMB), ("udp", udp_ports, DISCOVERY),
                                         ("tcp", str(ISCSI_PORT), ISCSI)):
            if not _has(state.rules, family, protocol, ports, str(network)):
                commands.append(_ufw_add("allow", protocol, str(network), ports, comment))
    # The rules of remote access and of the app names (ADR-0420), while they are on.
    from ..network import remote_rules

    return commands + await remote_rules.missing_commands(state)


def is_local(address: str | None) -> bool | None:
    """Whether ``address`` lies in the local networks that UNOS's rules let in, or is the loopback;
    None when it is not known."""
    if not address:
        return None
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return None
    if found.version == 6 and found.ipv4_mapped:
        found = found.ipv4_mapped
    return found.is_loopback or any(found in network for network in LOCAL_NETWORKS)


async def _refuse_where_ufw_would_cut_docker(defaults: Defaults) -> None:
    """409 where turning ufw on could cut the containers, the web UI's among them, off."""
    if defaults.manage_builtins:
        raise HTTPException(409, MANAGES_BUILTINS)
    if defaults.forward_policy != "DROP":
        raise HTTPException(409, FORWARDS)
    chain = await run(["iptables", "-S", "FORWARD"])
    if not chain.ok:
        raise HTTPException(500, f"The firewall stays off: Docker's rules could not be read ({reason(chain)})")
    if not any("-j DOCKER" in line for line in chain.stdout.splitlines()):
        raise HTTPException(409, DOCKER_NOT_IN_IPTABLES)


# ---------------------------------------------------------------------------
# The saved state and the revert
# ---------------------------------------------------------------------------


async def _refuse_while_waiting() -> None:
    """409 while a change waits to be kept, or while its revert runs; a stale marker is cleared."""
    found = await waiting()
    if found.reverting:
        raise HTTPException(409, REVERT_RUNNING)
    if found.stale:
        await run(_script("unos-firewall-cancel"))
    elif found.revert_in is not None:
        raise HTTPException(409, CHANGE_WAITS)


async def _save(was_on: bool) -> None:
    saved = await run(_script("unos-firewall-save", "active" if was_on else "inactive"))
    if not saved.ok:
        await run(_script("unos-firewall-discard"))
        raise HTTPException(500, f"The firewall stays as it is: its rules could not be saved ({reason(saved)})")


async def _arm(stays: str) -> None:
    """Write the marker and arm the timer; on failure the saved rules are put back and 500 says that
    the firewall ``stays`` as it was."""
    marked = await run(_script("unos-firewall-mark", str(int(time.time()) + REVERT_SECONDS)))
    armed = await run(revert_timer()) if marked.ok else marked
    if not armed.ok:
        # The saved rules first: the cancel forgets them.
        await run(_script("unos-firewall-restore"))
        await run(_script("unos-firewall-cancel"))
        raise HTTPException(500, f"The firewall {stays}: the automatic revert could not be set up ({reason(armed)})")


async def _undo(failed: str, *, armed: bool, answered: bool = True) -> HTTPException:
    """Put the saved rules back after ``failed``; the 500 that says what happened. When the host did
    not answer (``answered`` false), the command may still run there: an armed revert is left to undo
    it at its time."""
    if armed and not answered:
        return HTTPException(
            500, f"{failed}. Unless the change is kept, it is undone by itself within {REVERT_SECONDS} seconds",
        )
    restored = await run(_script("unos-firewall-restore"))
    if restored.ok:
        if armed:
            await run(_script("unos-firewall-cancel"))
        return HTTPException(500, f"{failed}. The firewall was put back as it was")
    if armed:
        return HTTPException(
            500, f"{failed}. It could not be put back ({reason(restored)}): "
                 f"it is undone by itself within {REVERT_SECONDS} seconds",
        )
    return HTTPException(500, f"{failed}. It could not be put back: {reason(restored)}")


async def _ufw(cmd: list[str], failed: str, *, armed: bool) -> None:
    result = await run(cmd)
    if not result.ok:
        raise await _undo(f"{failed}: {reason(result)}", armed=armed, answered=result.returncode != NO_ANSWER)


# ---------------------------------------------------------------------------
# The changes
# ---------------------------------------------------------------------------


async def status() -> tuple[bool, State | None]:
    """Whether the host has ufw, and its state when it has."""
    _refuse_locally()
    if not await installed():
        return False, None
    return True, await read()


async def enable() -> Change:
    """Turn ufw on with UNOS's rules, after arming the revert. A ufw that is on is left as it is."""
    await _require_ufw()
    async with _lock():
        state = await read()
        if state.enabled:
            return Change(False, None)
        await _refuse_while_waiting()
        await _refuse_where_ufw_would_cut_docker(state.defaults)
        await _save(False)
        for cmd in await _missing_unos_rules(state):
            await _ufw(cmd, "Failed to add UNOS's rules", armed=False)
        # ufw logs what it blocks to the kernel's log, which the journal writes to the system disk
        # while the NAS idles (ADR-0214): off, unless it is off already.
        if _setting(await _cat(UFW_CONF), "LOGLEVEL", "low").lower() != "off":
            await _ufw(["ufw", "logging", "off"], "Failed to turn ufw's logging off", armed=False)
        await _arm("stays off")
        await _ufw(["ufw", "--force", "enable"], "Failed to turn the firewall on", armed=True)
        after = await read()
        if not after.enabled or await _missing_unos_rules(after):
            raise await _undo("Failed to turn the firewall on: ufw is not on with UNOS's rules", armed=True)
        return Change(True, after.waiting.revert_in)


async def disable() -> bool:
    """Turn ufw off, keeping the FORWARD policies; whether it was on. Never refused for a change
    that waits to be kept."""
    await _require_ufw()
    async with _lock():
        if not (await read()).enabled:
            return False
        result = await run(_script("unos-firewall-off", "disable"))
        if not result.ok:
            raise HTTPException(500, f"Failed to turn the firewall off: {reason(result)}")
        return True


async def keep() -> None:
    """Keep the change that waits: its revert is called off."""
    await _require_ufw()
    async with _lock():
        result = await run(_script("unos-firewall-cancel"))
        if result.returncode == NOTHING_PENDING:
            raise HTTPException(409, NOTHING_TO_KEEP)
        if result.ok:
            return
        # The cancel's first step, the removal of the marker, is what keeps the change; it may have
        # run all the same (ADR-0246).
        marker = await run(["test", "-e", f"{STATE_DIR}/pending"])
        if marker.ok:
            raise HTTPException(500, f"The firewall change could not be kept, so it is undone by itself: {reason(result)}")
        if marker.returncode != 1 or marker.stderr.strip():
            raise HTTPException(
                500, f"Whether the firewall change was kept is not known: {reason(result)}. Refresh the page to see."
            )


Expectation = Callable[[list[FirewallRule], list[FirewallRule]], "HTTPException | None"]


def _same(before: list[FirewallRule], after: list[FirewallRule]) -> bool:
    return [(r.id, r.comment) for r in before] == [(r.id, r.comment) for r in after]


async def _change(state: State, cmd: list[str], failed: str, expect: Expectation) -> Change:
    """Run ``cmd``, which changes the rules, with the rules saved first and, while ufw is on, the revert
    armed; ``expect(before, after)`` says whether the rules changed as they should (None), else the
    HTTPException to answer, after the rules were put back unless they did not change at all."""
    await _save(state.enabled)
    if state.enabled:
        await _arm("stays as it is")
    await _ufw(cmd, failed, armed=state.enabled)
    after = await read()
    problem = expect(state.rules, after.rules)
    if problem is not None:
        if _same(state.rules, after.rules):
            await run(_script("unos-firewall-cancel" if state.enabled else "unos-firewall-discard"))
            raise problem
        undone = await _undo(problem.detail, armed=state.enabled)
        raise HTTPException(problem.status_code if undone.detail.endswith("put back as it was") else 500,
                            undone.detail)
    if not state.enabled:
        await run(_script("unos-firewall-discard"))
    return Change(True, after.waiting.revert_in if state.enabled else None)


def _ids(rules: list[FirewallRule]) -> set[str]:
    return {rule.id for rule in rules}


async def add_rule(spec: FirewallRuleCreate) -> tuple[Change, list[FirewallRule]]:
    """Add an incoming rule; the rules it added (one per address family)."""
    await _require_ufw()
    async with _lock():
        await _refuse_while_waiting()
        state = await read()
        if spec.source and ipaddress.ip_network(spec.source, strict=False).version == 6 and not state.defaults.ipv6:
            raise HTTPException(409, "ufw does not filter IPv6 on this host (IPV6=no in /etc/default/ufw)")
        added: list[FirewallRule] = []

        def expect(before: list[FirewallRule], after: list[FirewallRule]) -> HTTPException | None:
            comments = {rule.id: rule.comment for rule in before}
            if _ids(before) - _ids(after):
                return HTTPException(500, "Failed to add the rule: ufw removed other rules")
            if any(rule.id in comments and comments[rule.id] != rule.comment for rule in after):
                # ufw replaces the comment of a rule that matches but for its comment ("Rule updated").
                return HTTPException(409, RULE_EXISTS)
            new = [rule for rule in after if rule.id not in comments]
            if not new:
                return HTTPException(409, RULE_EXISTS)
            added.extend(new)
            return None

        cmd = _ufw_add(spec.action.value, spec.protocol.value, spec.source, spec.port, spec.comment)
        change = await _change(state, cmd, "Failed to add the rule", expect)
        return change, added


async def delete_rule(rule_id: str) -> tuple[Change, FirewallRule]:
    """Delete a rule of the host by its id; not one of UNOS's."""
    await _require_ufw()
    async with _lock():
        await _refuse_while_waiting()
        state = await read()
        rule = next((r for r in state.rules if r.id == rule_id), None)
        if rule is None:
            raise HTTPException(404, f"Rule not found: {rule_id}")
        if rule.unos:
            raise HTTPException(409, UNOS_RULE)

        def expect(before: list[FirewallRule], after: list[FirewallRule]) -> HTTPException | None:
            if _ids(after) != _ids(before) - {rule_id}:
                return HTTPException(500, "Failed to delete the rule: ufw deleted another one, so the host's rules were put back")
            return None

        change = await _change(state, ["ufw", "--force", "delete", str(rule.number)], "Failed to delete the rule", expect)
        return change, rule


class ResetFailed(HTTPException):
    """A reset that failed (500); ``reset_done`` when ufw's own reset had run (ADR-0402)."""

    def __init__(self, detail: str, *, reset_done: bool) -> None:
        super().__init__(500, detail)
        self.reset_done = reset_done


async def reset() -> None:
    """ufw's reset (off, its files kept as copies), its default policies and UNOS's rules. A change
    that waits to be kept is called off once ufw's reset has run: until then its revert stays."""
    await _require_ufw()
    async with _lock():
        if (await waiting()).reverting:
            raise HTTPException(409, REVERT_RUNNING)
        result = await run(_script("unos-firewall-off", "reset"))
        if not result.ok:
            raise ResetFailed(f"Failed to reset the firewall: {reason(result)}", reset_done=False)
        await run(_script("unos-firewall-cancel"))
        for cmd in (["ufw", "default", "deny", "incoming"], ["ufw", "default", "allow", "outgoing"]):
            step = await run(cmd)
            if not step.ok:
                raise ResetFailed(f"Failed to reset the firewall: {reason(step)}", reset_done=True)
        for cmd in await _missing_unos_rules(await read()):
            step = await run(cmd)
            if not step.ok:
                raise ResetFailed(f"Failed to reset the firewall: {reason(step)}", reset_done=True)


def describe(rule: FirewallRule | FirewallRuleCreate) -> str:
    """A rule for an audit entry: "allow in tcp port 8080 from 192.168.1.0/24 (Web server)"."""
    action = rule.action if isinstance(rule.action, str) else rule.action.value
    protocol = rule.protocol if isinstance(rule.protocol, str) else rule.protocol.value
    direction = getattr(rule, "direction", "in")
    parts = [action, direction]
    if protocol != "any":
        parts.append(protocol)
    if rule.port:
        parts.append(f"port {rule.port}")
    parts.append(f"from {rule.source}" if rule.source else "from anywhere")
    family = getattr(rule, "family", None)
    if family:
        parts.append(f"({'IPv4' if family == 'ipv4' else 'IPv6'})")
    if rule.comment:
        parts.append(f"({rule.comment})")
    return " ".join(parts)

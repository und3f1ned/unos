"""Disabling and enabling host network interfaces, with an automatic revert (ADR-0232).

A Disable saves the interface's addresses and routes on the host, arms a
transient systemd timer there that turns the interface back on after
REVERT_SECONDS, and only then disables it. Unless the change is kept in time
(keep()), the timer runs REVERT_SCRIPT: the link comes up and the saved
addresses and routes come back. ``ip link set up`` alone would not bring them
back: the kernel drops the routes through a link that goes down, the default
route among them, and its IPv6 addresses. The timer and the saved state live on
the host, so the revert also runs when the supervisor restarts or when the
connection to UNOS went through the disabled interface. Enable (enable())
undoes a disable the same way.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import weakref

from fastapi import HTTPException

from .. import host
from ..security.validators import sanitize_error_message, validate_interface_name

# On the host. /run is a tmpfs: the saved state costs no writes to the system
# disk, and a reboot, which brings every interface up as the host's network
# configuration says, clears it.
STATE_DIR = "/run/unos/network"
REVERT_SECONDS = 60
REVERT_UNIT = "unos-network-revert-{}"
# Seconds a host command may take; a restore retries for up to 8 seconds.
COMMAND_TIMEOUT = 15
RESTORE_TIMEOUT = 40
# The exit status of CANCEL_SCRIPT when no disable of the interface waits to be kept.
NOTHING_PENDING = 3

# Texts that webui/tests/network-fakes.ts repeats word for word (ADR-0189).
NOTHING_TO_KEEP = (
    "{name} has no disable waiting to be kept: it was turned back on, or it was not disabled from UNOS"
)
REVERT_NOT_ARMED = "{name} stays enabled: the automatic revert could not be set up ({reason})"
NOT_ENABLED = "{name} could not be enabled: {reason}"

# The host scripts. They run with /bin/sh (dash on Debian and Ubuntu): $0 names
# the script, $1 is the interface (validated), $2 is STATE_DIR. REVERT_SCRIPT is
# the command of a transient systemd unit, and systemd expands "${NAME}" and "$$"
# in the arguments of a command: no script here contains either
# (tests/test_network_scripts.py).

# $3: the time of the revert, in seconds since the epoch. The marker "pending",
# written last, says that a disable of $1 waits to be kept.
SAVE_SCRIPT = """\
set -e
umask 077
d="$2/$1"
mkdir -p "$d"
rm -f "$d/addr" "$d/route4" "$d/route6" "$d/pending"
ip addr save dev "$1" > "$d/addr"
ip -4 route save dev "$1" > "$d/route4"
ip -6 route save dev "$1" > "$d/route6" 2> /dev/null || rm -f "$d/route6"
echo "$3" > "$d/pending"
"""

# Keep a disable: the revert and a confirmation both start by removing the
# marker, and only one of them can (rm is atomic). Exit status 3: none waits.
CANCEL_SCRIPT = """\
rm "$2/$1/pending" 2> /dev/null || exit 3
systemctl stop "unos-network-revert-$1.timer" 2> /dev/null
exit 0
"""

# Turn the link on, then bring back what SAVE_SCRIPT saved. iproute2 skips
# addresses and routes that exist already, so a retry, or a network manager
# that put some back first, does no harm. A restored IPv6 address stays
# tentative for a moment (duplicate address detection), and a route with it as
# its source fails until then: 5 attempts, 2 seconds apart. Exit status 1: the
# link stays off; 2: it is on, but the saved state could not all be restored.
# When the saved files are gone, another restore of the interface (Enable, or
# the timer) took them.
RESTORE_SCRIPT = """\
d="$2/$1"
ip link set dev "$1" up || exit 1
n=0
while [ -e "$d/addr" ]; do
  if ip addr restore < "$d/addr" && ip route restore < "$d/route4" &&
    { [ ! -e "$d/route6" ] || ip route restore < "$d/route6"; }; then
    rm -f "$d/addr" "$d/route4" "$d/route6"
    exit 0
  fi
  n=$((n + 1))
  if [ "$n" -ge 5 ]; then
    rm -f "$d/addr" "$d/route4" "$d/route6"
    exit 2
  fi
  sleep 2
done
exit 0
"""

# The timer's command: nothing to do for a disable that was kept.
REVERT_SCRIPT = 'rm "$2/$1/pending" 2> /dev/null || exit 0\n' + RESTORE_SCRIPT

SCRIPTS = {
    "unos-network-save": SAVE_SCRIPT,
    "unos-network-cancel": CANCEL_SCRIPT,
    "unos-network-restore": RESTORE_SCRIPT,
    "unos-network-revert": REVERT_SCRIPT,
}

# One lock per event loop (an asyncio.Lock binds to the loop it is first used
# in; tests run several loops, cf. shares.service.smb_lock).
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    """Serializes the changes of interfaces."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def is_hidden(name: str) -> bool:
    """The loopback and Docker's interfaces: System > Network neither lists nor changes them."""
    return name in ("lo", "docker0", "dummy0") or name.startswith(("veth", "br-"))


def is_enabled(link: dict) -> bool:
    """Whether an `ip -j link` entry is administratively up (its operational state may differ)."""
    return "UP" in link.get("flags", [])


def _script(name_of_script: str, interface: str, *args: str) -> list[str]:
    return ["/bin/sh", "-c", SCRIPTS[name_of_script], name_of_script, interface, STATE_DIR, *args]


def revert_timer(name: str) -> list[str]:
    """The command that arms the revert of a disable of ``name`` on the host."""
    return [
        "systemd-run",
        f"--unit={REVERT_UNIT.format(name)}",
        f"--description=UNOS: turn {name} back on unless its disable is kept",
        f"--on-active={REVERT_SECONDS}",
        # The default accuracy of a timer is a minute.
        "--timer-property=AccuracySec=1s",
        # Unloaded, with its service, once it ran: the next disable reuses the name.
        "--timer-property=RemainAfterElapse=no",
        "--collect",
        "--quiet",
        "--",
        *_script("unos-network-revert", name),
    ]


async def run(cmd: list[str], *, timeout: float = COMMAND_TIMEOUT) -> host.HostResult:
    """Run ``cmd`` on the host; a command that could not run, or timed out, is a failed result."""
    try:
        return await host.run_on_host_async(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Not str(exc): it quotes the command line, nsenter and a whole host script.
        return host.HostResult(-1, "", f"the host did not answer within {timeout:g} seconds")
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        return host.HostResult(-1, "", sanitize_error_message(exc) or type(exc).__name__)


def reason(result: host.HostResult) -> str:
    """What a failed host command said, in one line, for an error message."""
    output = result.stderr if result.stderr.strip() else result.stdout
    # A restore that tried 5 times says the same 5 times.
    text = "; ".join(dict.fromkeys(line.strip() for line in output.splitlines() if line.strip()))
    return sanitize_error_message(RuntimeError(text)) if text else f"exit status {result.returncode}"


def _check_name(name: str) -> None:
    validate_interface_name(name)
    if is_hidden(name):
        raise HTTPException(400, f"{name} is the loopback or belongs to Docker: UNOS does not change it")


async def _link(name: str) -> dict:
    """The host's `ip -j link` entry of ``name``: 400 for a name UNOS does not take, 404 for none."""
    _check_name(name)
    result = await run(["ip", "-j", "link", "show", "dev", name])
    if not result.ok:
        if "does not exist" in result.stderr:
            raise HTTPException(404, f"Interface {name} not found")
        raise HTTPException(500, f"Failed to read interface {name}: {reason(result)}")
    try:
        found = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse network data")
    if not found:
        raise HTTPException(404, f"Interface {name} not found")
    return found[0]


async def _discard(name: str) -> None:
    """Forget the saved state of ``name`` (after a disable that did not happen)."""
    folder = f"{STATE_DIR}/{name}"
    await run(["rm", "-f", "--", *(f"{folder}/{part}" for part in ("addr", "route4", "route6", "pending"))])


async def disable(name: str) -> int:
    """Disable ``name`` on the host; return the seconds until it comes back on by itself.

    It is never disabled without its revert: when the state cannot be saved or
    the timer cannot be armed, the interface stays enabled.
    """
    async with _lock():
        link = await _link(name)
        if not is_enabled(link):
            raise HTTPException(409, f"{name} is already disabled")
        # A disable that someone undid outside UNOS before its time may have left its marker and timer.
        await run(_script("unos-network-cancel", name))
        # The revert of an earlier disable may still be restoring the state that SAVE_SCRIPT would replace.
        running = await run(["systemctl", "is-active", "--quiet", f"{REVERT_UNIT.format(name)}.service"])
        if running.ok:
            raise HTTPException(
                409, f"{name} is still coming back on from its last disable: try again in a few seconds"
            )
        saved = await run(_script("unos-network-save", name, str(int(time.time()) + REVERT_SECONDS)))
        if not saved.ok:
            await _discard(name)
            raise HTTPException(
                500, f"{name} stays enabled: its addresses and routes could not be saved ({reason(saved)})"
            )
        armed = await run(revert_timer(name))
        if not armed.ok:
            await _discard(name)
            raise HTTPException(500, REVERT_NOT_ARMED.format(name=name, reason=reason(armed)))
        try:
            down = await host.run_on_host_async(
                ["ip", "link", "set", "dev", name, "down"], timeout=COMMAND_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            # The link may have gone down: the revert stays armed.
            raise HTTPException(
                500,
                f"{name} may be disabled: the host did not answer in time. Unless the change is kept, "
                f"{name} comes back on by itself within {REVERT_SECONDS} seconds",
            )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
            down = host.HostResult(-1, "", sanitize_error_message(exc) or type(exc).__name__)
        if not down.ok:
            await run(_script("unos-network-cancel", name))
            await _discard(name)
            raise HTTPException(500, f"{name} could not be disabled: {reason(down)}")
        return REVERT_SECONDS


async def keep(name: str) -> None:
    """Keep a disable of ``name`` that waits to be kept: its revert is called off.

    An interface that someone turned back on outside UNOS has no disable to
    keep; its revert is called off all the same.
    """
    async with _lock():
        link = await _link(name)
        result = await run(_script("unos-network-cancel", name))
        if result.returncode == NOTHING_PENDING or is_enabled(link):
            raise HTTPException(409, NOTHING_TO_KEEP.format(name=name))
        if not result.ok:
            raise HTTPException(
                500, f"The disable of {name} could not be kept, so {name} comes back on by itself: {reason(result)}"
            )


async def enable(name: str) -> None:
    """Enable ``name`` and bring back the addresses and routes its disable saved.

    A disable that waits to be kept is called off once the link is on. Until
    then its timer stays armed: when the restore cannot turn the link on, or
    does not answer, the timer tries again at its time. An interface that is
    enabled already is left as it is: its addresses and routes are live, and no
    saved state is put over them.
    """
    async with _lock():
        link = await _link(name)
        if is_enabled(link):
            # Turned on outside UNOS: a disable that waits to be kept has nothing left to undo.
            await run(_script("unos-network-cancel", name))
            return
        result = await run(_script("unos-network-restore", name), timeout=RESTORE_TIMEOUT)
        if result.ok or result.returncode == 2:
            # The link is on: the disable is undone now, not by its timer.
            await run(_script("unos-network-cancel", name))
        if result.returncode == 2:
            raise HTTPException(
                500,
                f"{name} is enabled, but its saved addresses and routes could not all be restored: "
                f"{reason(result)}",
            )
        if not result.ok:
            raise HTTPException(500, NOT_ENABLED.format(name=name, reason=reason(result)))


async def revert_in(name: str) -> int | None:
    """Seconds until a disable of ``name`` that waits to be kept is undone; None when none waits."""
    if "/" in name or name in ("", ".", ".."):
        return None
    result = await run(["cat", f"{STATE_DIR}/{name}/pending"])
    if not result.ok:
        return None
    try:
        deadline = int(result.stdout.strip())
    except ValueError:
        return None
    return max(0, deadline - int(time.time()))

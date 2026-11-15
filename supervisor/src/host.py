"""Run commands and access files on the host, not in the supervisor container.

The supervisor runs in a privileged container with ``pid: host``, so PID 1 is
the host's init process. Anything that must change the real host (mounts,
``/etc/fstab``, Samba reloads, ...) has to run in the host's namespaces;
running it inside the container only changes the container and is lost on the
next restart.

Every host-affecting operation should go through this module so there is one
place to audit, test and mock. Arguments are always passed as a list (never
through a shell), so callers still have to validate untrusted values, but
they cannot inject extra shell syntax.

Set ``UNOS_HOST_EXEC=local`` to run commands directly (no nsenter), e.g. when
the supervisor runs natively on the host during development.

btrfs scrubs, error stats resets, a balance's status, pause and cancel, and
smartctl run in the container itself,
on the pools and disks that the privileged container sees:
:func:`run_in_container` runs them, and at their timeout it kills them
without waiting for them (ADR-0290).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import IO, Any

logger = logging.getLogger(__name__)

# Programs the supervisor runs on the host through this module, with the
# Debian/Ubuntu package that installs them. Callers: storage/pools.py,
# storage/balances.py (systemd-run, which runs a pool's balance in a unit of
# its own that outlives the supervisor's container: ADR-0377),
# storage/disks.py (lsns and findmnt --task: the mount namespaces of other
# processes, ADR-0288), shares/smb.py, shares/accounts.py (SMB accounts: getent
# and the shadow tools, smbpasswd, pdbedit, smbstatus), health/router.py and
# network/ (ip, ss, systemd-run, hostnamectl; resolvectl only where
# systemd-resolved runs, else /etc/resolv.conf), power/router.py (shutdown,
# busctl, systemctl, ip, ethtool: ADR-0233), logs/router.py (journalctl, under
# timeout: ADR-0257, ADR-0258), system/router.py (timedatectl under timeout, hostname:
# ADR-0331) and notifications/router.py (curl, which sends a notification channel's
# request from the host's network when the container connects nowhere, and ends
# itself at its --max-time: ADR-0336). cat, od (the export files: shares/nfs.py), test,
# mkdir, rmdir, rm, find, sync, sleep, timeout, hostname, readlink (the time zone:
# scheduler/router.py, ADR-0266) and sh also run there but come from essential
# packages. The startup check (main.check_host_tools) warns about the ones the
# host lacks.
HOST_TOOLS: dict[str, str] = {
    "btrfs": "btrfs-progs",
    "mkfs.btrfs": "btrfs-progs",
    "chattr": "e2fsprogs",
    "findmnt": "util-linux",
    "lsns": "util-linux",
    "lsblk": "util-linux",
    "blkid": "util-linux",
    "wipefs": "util-linux",
    "mount": "mount",
    "umount": "mount",
    "swapon": "mount",
    "udevadm": "udev",
    "systemctl": "systemd",
    "testparm": "samba-common-bin",
    "smbcontrol": "samba-common-bin",
    "getent": "libc-bin",
    "groupadd": "passwd",
    "useradd": "passwd",
    "userdel": "passwd",
    "usermod": "passwd",
    "smbpasswd": "samba-common-bin",
    "pdbedit": "samba",
    "smbstatus": "samba",
    "ip": "iproute2",
    "ss": "iproute2",
    "systemd-run": "systemd",
    "hostnamectl": "systemd",
    "timedatectl": "systemd",
    "shutdown": "systemd-sysv",
    "busctl": "systemd",
    "ethtool": "ethtool",
    "journalctl": "systemd",
    "curl": "curl",
}

# Programs of host services that deploy/install.sh does not install, with their
# packages: a host without them is a normal installation, so the startup check
# leaves them out, and the feature that runs one says when the host lacks it.
# shares/nfs.py runs exportfs (under timeout), and the NFS tab names a missing
# NFS server (ADR-0292). power/router.py runs NUT's upsc (under timeout), and the
# UPS status names a missing NUT client (ADR-0356).
OPTIONAL_HOST_TOOLS: dict[str, str] = {
    "exportfs": "nfs-kernel-server",
    "upsc": "nut-client",
}

# Enter all of PID 1's namespaces except the user and cgroup namespaces.
_NSENTER_PREFIX = [
    "nsenter",
    "--target",
    "1",
    "--mount",
    "--uts",
    "--ipc",
    "--net",
    "--pid",
    "--",
]


class HostCommandError(RuntimeError):
    """A host command exited with a non-zero status."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Host command failed ({returncode}): {shlex.join(cmd)}: {stderr.strip()}"
        )


@dataclass(frozen=True)
class HostResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def runs_locally() -> bool:
    """Whether commands run directly, not in the host's namespaces (``UNOS_HOST_EXEC=local``).

    "The host" is then the machine or container the supervisor itself runs
    on: the local stand-in on a workstation (scripts/standin/), or the
    unprivileged supervisor of the compose test stack. Callers that must
    never act on such a machine, such as the power actions, check it first.
    """
    return os.environ.get("UNOS_HOST_EXEC", "").lower() == "local"


def _prefix() -> list[str]:
    if runs_locally():
        return []
    return list(_NSENTER_PREFIX)


def _validate_cmd(cmd: list[str]) -> None:
    if not cmd or not all(isinstance(part, str) for part in cmd):
        raise ValueError("Host command must be a non-empty list of strings")
    if any("\x00" in part for part in cmd):
        raise ValueError("Host command arguments must not contain NUL bytes")


def run_on_host(
    cmd: list[str],
    *,
    timeout: float = 60,
    check: bool = False,
    input: str | None = None,
    errors: str | None = None,
) -> HostResult:
    """Run ``cmd`` in the host's namespaces and return its output.

    With ``check=True`` a non-zero exit status raises :class:`HostCommandError`.
    A timeout raises :class:`subprocess.TimeoutExpired`. ``errors`` is the
    error handler for decoding the output: strict by default (output that is
    not valid in the locale's encoding raises UnicodeDecodeError); "replace"
    for commands that print names anyone can choose, such as file names.
    """
    _validate_cmd(cmd)
    proc = subprocess.run(
        _prefix() + cmd,
        capture_output=True,
        text=True,
        errors=errors,
        timeout=timeout,
        input=input,
    )
    result = HostResult(proc.returncode, proc.stdout, proc.stderr)
    if check and not result.ok:
        raise HostCommandError(cmd, result.returncode, result.stderr)
    return result


async def run_on_host_async(
    cmd: list[str],
    *,
    timeout: float = 60,
    check: bool = False,
    input: str | None = None,
    errors: str | None = None,
) -> HostResult:
    """Async wrapper around :func:`run_on_host` that does not block the event loop."""
    return await asyncio.to_thread(
        run_on_host, cmd, timeout=timeout, check=check, input=input, errors=errors
    )


# The commands of run_in_container, by their argument lists: those running,
# and those that were killed at their timeout and have not ended yet, each
# with whether a run that was not started because of it has been logged.
_runs = threading.Condition()
_running: set[tuple[str, ...]] = set()
_stuck: dict[tuple[str, ...], bool] = {}


def run_in_container(
    cmd: list[str],
    *,
    timeout: float,
    stdout: int | IO[Any] = subprocess.PIPE,
    stderr: int | IO[Any] = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` in the supervisor's own container; at its timeout, kill it and return at once.

    For btrfs and smartctl, which reach the host's pools and disks through
    the privileged container (ADR-0290). ``stdout`` and ``stderr`` are
    pipes, whose text is returned (bytes that are not valid in the
    locale's encoding become U+FFFD), or what Popen takes, such as
    ``subprocess.DEVNULL`` or a file. The command reads nothing.

    The command runs in a session of its own. At ``timeout`` its process
    group, the command and the processes it started, is killed with
    SIGKILL, and :class:`subprocess.TimeoutExpired` is raised at once: a
    daemon thread waits for the command to end. One that hangs in the
    kernel, on a disk or a filesystem that stopped answering, ends only when
    the kernel lets it go.

    One run of a command (the same ``cmd``) at a time: a second run waits for
    the first, at most ``timeout``. While a run that was killed has not
    ended, the command is not started: TimeoutExpired is raised at once, and
    logged once for each such run. So a pool or disk that hangs keeps at most
    one process of each command.
    """
    _validate_cmd(cmd)
    key = tuple(cmd)
    with _runs:
        if not _runs.wait_for(lambda: key not in _running, timeout) or key in _stuck:
            if not _stuck.get(key):
                logger.warning("Not started: %s: an earlier run of it has not ended", shlex.join(cmd))
                if key in _stuck:
                    _stuck[key] = True
            raise subprocess.TimeoutExpired(cmd, timeout)
        _running.add(key)
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_and_leave(process, key)
            logger.warning(
                "Killed %s: it did not answer within %s seconds", shlex.join(cmd), timeout
            )
            raise
        except BaseException:
            _kill_and_leave(process, key)
            raise
    finally:
        with _runs:
            _running.discard(key)
            _runs.notify_all()
    return subprocess.CompletedProcess(cmd, process.returncode, out, err)


def _kill_and_leave(process: subprocess.Popen[str], key: tuple[str, ...]) -> None:
    """Kill the process group of ``process`` and leave the wait for its end to a daemon thread."""
    if process.returncode is not None:  # it ended and was waited for already
        return
    # Its process group id is its pid, which no other process can take until
    # it is waited for.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    with _runs:
        _stuck[key] = False
    try:
        threading.Thread(
            target=_wait_for_killed,
            args=(process, key),
            name=f"unos-reap:{process.pid}",
            daemon=True,
        ).start()
    except BaseException:
        with _runs:
            _stuck.pop(key, None)
        raise


def _wait_for_killed(process: subprocess.Popen[str], key: tuple[str, ...]) -> None:
    try:
        process.wait()
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        with _runs:
            _stuck.pop(key, None)


# The longest line that a command of stream_on_host may print; a longer one is skipped.
STREAM_LINE_LIMIT = 1024 * 1024
# Seconds a stopped command of stream_on_host has for SIGTERM before its group gets SIGKILL.
STREAM_STOP_WAIT = 5.0


class HostStream:
    """A command of :func:`stream_on_host` while it runs."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def readline(self) -> str | None:
        """The next line of the command's output, without its line break; None once its output
        ended. Bytes that are not UTF-8 become U+FFFD. A line longer than STREAM_LINE_LIMIT is
        dropped, or, when asyncio had not read it to its end, what follows of it comes as a line
        of its own: the caller skips a line it cannot read."""
        assert self._process.stdout is not None
        while True:
            try:
                line = await self._process.stdout.readline()
            except ValueError:  # over the limit: asyncio dropped what it had read of the line
                continue
            if not line:
                return None
            return line.decode("utf-8", errors="replace").rstrip("\n")

    async def result(self) -> HostResult:
        """Once the output ended: the exit status and the error output (the output is read)."""
        assert self._process.stderr is not None
        stderr = await self._process.stderr.read()
        returncode = await self._process.wait()
        return HostResult(returncode, "", stderr.decode("utf-8", errors="replace"))

    async def stop(self) -> None:
        """End the command: SIGTERM to its process group, SIGKILL after STREAM_STOP_WAIT seconds.

        The group holds what nsenter forked for ``--pid`` too: nsenter does not pass a signal on
        (ADR-0258), and the command would run on, on the host. The group's id is the pid of
        nsenter, which no other process can take before nsenter is waited for.
        """
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self._process.returncode is not None:
                return
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._process.pid, sig)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process.wait(), STREAM_STOP_WAIT)


async def stream_on_host(cmd: list[str]) -> HostStream:
    """Start ``cmd`` in the host's namespaces for a caller that reads its output while it runs.

    For a command that runs until it is stopped, such as ``journalctl --follow`` (ADR-0366):
    no timeout ends it, :meth:`HostStream.stop` does. It runs in a session of its own, so that
    stop ends its process group. OSError when it cannot start.
    """
    _validate_cmd(cmd)
    process = await asyncio.create_subprocess_exec(
        *_prefix(),
        *cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        limit=STREAM_LINE_LIMIT,
    )
    return HostStream(process)


def read_host_file(path: str, *, timeout: float = 10) -> str:
    """Return the contents of an absolute path on the host filesystem."""
    if not path.startswith("/"):
        raise ValueError("Host paths must be absolute")
    return run_on_host(["cat", "--", path], timeout=timeout, check=True).stdout


def write_host_file_atomic(
    path: str, content: str, *, mode: str = "0644", timeout: float = 30, errors: str | None = None
) -> None:
    """Atomically and durably replace a file on the host filesystem.

    The content is written to a temporary file next to ``path``, flushed to
    disk, and renamed over the original. Readers therefore see either the old
    or the new file, never a truncated one. ``errors`` is the error handler for
    encoding ``content``: strict by default; "surrogateescape" writes back the
    bytes of a file that was read with it, also those that are not UTF-8.

    The directory is flushed after the rename as well. Until then the rename
    only lives in the filesystem journal, and ext4 commits the journal every
    ``commit=`` seconds: Armbian mounts SD-card roots with ``commit=600``, so a
    power cut within ten minutes would bring back the old file (for
    ``/etc/fstab``: a pool that is no longer mounted at boot).
    """
    if not path.startswith("/"):
        raise ValueError("Host paths must be absolute")
    script = (
        'set -e; tmp="$1.unos-tmp.$$"; '
        'cat > "$tmp"; chmod "$2" "$tmp"; sync "$tmp" 2>/dev/null || sync; '
        'mv -f "$tmp" "$1"; '
        'sync "$(dirname "$1")" 2>/dev/null || sync'
    )
    run_on_host(
        ["/bin/sh", "-c", script, "unos-write", path, mode],
        input=content,
        timeout=timeout,
        check=True,
        errors=errors,
    )


def sync_host_path(path: str, *, timeout: float = 60) -> bool:
    """Flush a file or directory on the host to stable storage; never raises.

    ``sync PATH`` fsyncs it. For a directory that makes changes of its
    entries (mkdir, rmdir, rename) and of its inode flags (``chattr``)
    durable; ext4 commits its whole journal for it. Falls back to a global
    ``sync``. Returns False when neither worked.
    """
    if not path.startswith("/"):
        raise ValueError("Host paths must be absolute")
    return _try_on_host(["sync", path], timeout) or _try_on_host(["sync"], timeout)


def _try_on_host(cmd: list[str], timeout: float) -> bool:
    """Run a host command; True if it succeeded, False on failure, timeout or nsenter error."""
    try:
        return run_on_host(cmd, timeout=timeout).ok
    except (OSError, subprocess.SubprocessError):
        return False


def missing_host_binaries(names: list[str], *, timeout: float = 10) -> list[str] | None:
    """Return the ``names`` the host cannot find on its PATH, in the given order.

    One host command checks them all; the names reach the shell as positional
    arguments and are never interpolated into the script. Never raises: when
    the check itself cannot run (nsenter or the shell fails, timeout), it logs
    why and returns None.
    """
    names = list(dict.fromkeys(names))
    if not names:
        return []
    # "--" and printf (not echo): a name is never taken for an option, and
    # dash's echo would interpret backslashes in it.
    script = 'for c do command -v -- "$c" >/dev/null 2>&1 || printf "%s\\n" "$c"; done'
    try:
        result = run_on_host(["/bin/sh", "-c", script, "sh", *names], timeout=timeout)
    except Exception as exc:
        logger.warning("Could not check which programs the host has: %s", exc)
        return None
    if not result.ok:
        logger.warning(
            "Could not check which programs the host has (exit status %d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        return None
    reported = set(result.stdout.splitlines())
    return [name for name in names if name in reported]


def time_zone_name(*, timeout: float = 10) -> str | None:
    """The name of the host's time zone, such as ``Europe/Berlin``, or None.

    Read where /etc/localtime links to below a ``zoneinfo`` folder, as timedatectl reads
    it (``timedatectl set-timezone`` writes that link); a zone of its ``posix/`` or
    ``right/`` folder (time with leap seconds) counts as the zone itself, whose civil
    time it shows. None when the host has no such link: no /etc/localtime (the host's clock
    then shows UTC), a copied file instead of a link, or a link elsewhere. When the host
    cannot be asked, raises what run_on_host raises (OSError, subprocess.TimeoutExpired), and
    HostCommandError when the command fails with a message: readlink without -v reports
    nothing, so the message is nsenter's, which could not enter the host's namespaces.
    """
    cmd = ["readlink", "/etc/localtime"]
    # "replace": a target that is not UTF-8 names no zone, which the caller then reports.
    result = run_on_host(cmd, timeout=timeout, errors="replace")
    if not result.ok:
        if result.stderr.strip():
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return None
    _, found, name = result.stdout.strip().rpartition("zoneinfo/")
    for folder in ("posix/", "right/"):
        name = name.removeprefix(folder)
    return name if found and name else None

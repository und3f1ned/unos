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
on the pools and disks that the privileged container sees, and so does the
pgrep that looks for a disk's stuck power read (the container has the host's
process IDs, ``pid: host``):
:func:`run_in_container` runs them, and at their timeout it kills them
without waiting for them (ADR-0290). So does openssl, on the certificates of
the container's /data/certs (ADR-0416). smartctl goes through :func:`run_smartctl`,
which first makes the container's node of the disk the host's, so that a disk
attached after the container started is read too (ADR-0414).
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import re
import shlex
import signal
import stat
import subprocess
import threading
from dataclasses import dataclass
from typing import IO, Any

logger = logging.getLogger(__name__)

# Programs the supervisor runs on the host through this module, with the
# Debian/Ubuntu package that installs them. Callers: storage/pools.py,
# storage/balances.py (systemd-run, which runs a pool's balance in a unit of
# its own that outlives the supervisor's container: ADR-0379), storage/devices.py
# (btrfs device add, and systemd-run for a disk's removal from a pool: ADR-0411),
# storage/disks.py (lsns and findmnt --task: the mount namespaces of other
# processes, ADR-0288), shares/smb.py, shares/accounts.py (SMB accounts: getent
# and the shadow tools, smbpasswd, pdbedit, smbstatus), health/router.py and
# network/ (ip, ss, systemd-run, hostnamectl; resolvectl only where
# systemd-resolved runs, else /etc/resolv.conf), apps/preflight.py (ss: the host
# ports that an app's install would take; sh with df: the free space of the system
# disk before its pulls, ADR-0381), power/router.py (shutdown,
# busctl, systemctl, ip, ethtool: ADR-0233), power/schedule.py (shutdown, busctl and ss; sh
# with cat, which read and set the RTC's wake alarm: ADR-0407), logs/router.py (journalctl, under
# timeout: ADR-0257, ADR-0258), system/router.py (timedatectl under timeout, hostname:
# ADR-0331), security/firewall.py (ss: the ports that sshd listens on, ADR-0416) and
# notifications/router.py (curl, which sends a notification channel's
# request from the host's network when the container connects nowhere, and ends
# itself at its --max-time: ADR-0336) and shares/iscsi.py (modprobe, mount and mountpoint for LIO's
# modules and configfs; lsattr and chattr, which keep the folder of the LUN files immutable: ADR-0418).
# cat, od (the export files: shares/nfs.py), stat, truncate, grep and find (shares/iscsi.py), test,
# mkdir, rmdir, rm, find, sync, sleep, timeout, hostname, readlink (the time zone:
# scheduler/router.py, ADR-0266), df (apps/preflight.py) and sh (also
# apps/hardware.py, with cat: the device
# tree and the device nodes of the host's SoC, ADR-0382) also run there but come from
# essential packages. The startup check (main.check_host_tools) warns about the ones the
# host lacks.
HOST_TOOLS: dict[str, str] = {
    "btrfs": "btrfs-progs",
    "mkfs.btrfs": "btrfs-progs",
    "chattr": "e2fsprogs",
    "lsattr": "e2fsprogs",
    "modprobe": "kmod",
    "mountpoint": "util-linux",
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
# UPS status names a missing NUT client (ADR-0356). storage/spindown.py runs hdparm
# -S (under timeout) for a disk's idle timeout, and the Storage page names a missing
# hdparm (ADR-0378): install.sh installs it, but a host set up by hand may lack it.
# storage/disks.py runs hdparm -C (under timeout) for a disk's power state, one
# per disk at a time and none while a timed-out one still runs (ADR-0378), and
# smartctl -n standby -i in the container without it. security/firewall.py runs ufw, and the
# firewall card names a missing ufw (ADR-0416); it reads iptables' FORWARD chain (iptables, which
# the ufw and Docker packages depend on; ip6tables too, in its host scripts) and asks sshd -T for
# SSH's ports (none without openssh-server), and runs systemd-run and systemctl for the revert.
# network/wireguard.py runs wg and wg-quick (through the unit wg-quick@unos-wg) for remote access, and
# the remote access card names a missing wireguard-tools; network/app_names.py's unit runs
# avahi-publish, and the app names card names a missing avahi-utils (ADR-0420). The off-device copy
# over SFTP runs the host's ssh, which restic starts through program_on_host, and Save names a
# missing openssh-client (offsite/ssh.py, ADR-0422).
OPTIONAL_HOST_TOOLS: dict[str, str] = {
    "exportfs": "nfs-kernel-server",
    "upsc": "nut-client",
    "hdparm": "hdparm",
    "ufw": "ufw",
    "iptables": "iptables",
    "sshd": "openssh-server",
    "wg": "wireguard-tools",
    "wg-quick": "wireguard-tools",
    "avahi-publish": "avahi-utils",
    "ssh": "openssh-client",
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


# The namespaces of PID 1 that a program of the host enters when another program of the container
# starts it (program_on_host): its files and its network, nothing else.
_PROGRAM_PREFIX = ["nsenter", "--target", "1", "--mount", "--net", "--"]


def program_on_host(argv: list[str]) -> list[str]:
    """The argv that runs the host's program ARGV, for a program of the container that starts it
    itself: restic's sftp.command runs the host's ssh (ADR-0422). nsenter enters the host's mount and
    network namespaces only, and execs ARGV without a fork of its own; the files that ARGV names in
    this container go through container_path. ``UNOS_HOST_EXEC=local``: ARGV as it is."""
    _validate_cmd(argv)
    if runs_locally():
        return list(argv)
    return [*_PROGRAM_PREFIX, *argv]


def container_path(path: str) -> str:
    """PATH, an absolute path of this container, as a program of the host's mount namespace opens it:
    through /proc/<the supervisor's PID>/root (pid: host, so the PID is the host's). With
    ``UNOS_HOST_EXEC=local``, PATH itself."""
    if not path.startswith("/"):
        raise ValueError("container_path takes an absolute path")
    if runs_locally():
        return path
    return f"/proc/{os.getpid()}/root{path}"


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
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` in the supervisor's own container; at its timeout, kill it and return at once.

    For btrfs and smartctl, which reach the host's pools and disks through
    the privileged container (ADR-0290), and openssl, which reads and makes
    the certificates of the container's /data/certs (ADR-0416). ``stdout``
    and ``stderr`` are pipes, whose text is returned (bytes that are not
    valid in the locale's encoding become U+FFFD), or what Popen takes, such
    as ``subprocess.DEVNULL`` or a file. The command reads ``input``, or
    nothing.

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
            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            out, err = process.communicate(input=input, timeout=timeout)
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


# The container's own /dev, a tmpfs that Docker fills with the host's device nodes once, when the
# container starts, and the host's block devices in sysfs, which the container sees as the host
# does: the kernel's block devices are in no namespace (ADR-0414). HOST_DEV is the host's /dev
# through PID 1's root (pid: host): a CONTAINER_DEV on the same filesystem is the host's own, which
# UNOS never writes. Tests give them folders of their own (tests/conftest.py).
CONTAINER_DEV = "/dev"
SYS_BLOCK = "/sys/block"
HOST_DEV = "/proc/1/root/dev"
# What _container_node_number answers for an entry of the container's /dev that is no block device.
_NOT_A_BLOCK_NODE = (-1, -1)
# A whole disk's node as smartctl is given it: /dev/<name>, a name without a slash or a dot.
_DISK_NODE = re.compile(r"/dev/([a-z][a-z0-9]{0,31})")
# The node changes of container_disk_node, one at a time; the disks whose node could not be made
# right, each logged once until it is.
_disk_nodes = threading.Lock()
_disk_node_problems: set[str] = set()


class DiskNodeError(OSError):
    """The container's node of a disk could not be made the host's (ADR-0414); smartctl did not
    run. Its strerror says why."""


class NoHostDisk(DiskNodeError):
    """The host has no whole disk of the name (a partition, or a disk that left after it was listed);
    smartctl did not run."""


def run_smartctl(options: list[str], device: str, *, timeout: float) -> subprocess.CompletedProcess[str]:
    """``smartctl <options> <device>`` in the container (run_in_container, ADR-0290), on a whole
    disk of the host given as ``/dev/<name>``, once the container's node of that disk is the host's
    (container_disk_node, ADR-0414): so a disk attached after the supervisor started, such as a USB
    disk, is read and tested too. NoHostDisk or DiskNodeError, and smartctl does not run, when the
    node cannot be made right."""
    container_disk_node(device)
    return run_in_container(["smartctl", *options, device], timeout=timeout)


def container_disk_node(device: str) -> None:
    """Make the container's node ``/dev/<name>`` the host's whole disk ``<name>`` (ADR-0414).

    Docker gives the privileged container a node of each device the host had when it started, and
    none of a disk attached later. The host's sysfs names the disk's device number
    (``/sys/block/<name>/dev``): a node with that number is left as it is (no write, no log); a
    missing one is made, and one with another number, or anything else at that name, is replaced
    (a disk that took the name of one detached since, whose number another device may have now): a
    block device node, mode 0600, made under a temporary name and renamed into place, in the
    container's /dev, a tmpfs (RAM). Nothing here opens the disk or sends it a command: a sleeping
    disk stays asleep (ADR-0378).

    Nothing changes where host commands run locally (``UNOS_HOST_EXEC=local``: the stand-in and the
    compose test stack), for a device that is not ``/dev/<name>``, which smartctl then gets as
    before, where the container has no sysfs of block devices, and where its /dev is the host's own
    (bind-mounted), whose nodes the kernel keeps. NoHostDisk when the host has no whole disk of that
    name; DiskNodeError when sysfs or the node cannot be read or made, logged once for each disk
    until its node is right again.
    """
    if runs_locally():
        return
    found = _DISK_NODE.fullmatch(device)
    if found is None:
        return
    name = found[1]
    path = os.path.join(CONTAINER_DEV, name)
    with _disk_nodes:
        try:
            try:
                number = _host_disk_number(name)
            except FileNotFoundError:  # no sysfs of block devices here: /dev stays as Docker made it
                return
            if number is not None:
                node = _container_node_number(path)
                if node != number:
                    if not _dev_is_the_containers():
                        return
                    _make_container_node(path, number)
                    logger.info(
                        "%s %s in the container as the host's disk %d:%d",
                        "Made" if node is None else "Replaced", device, *number,
                    )
        except OSError as exc:
            if name not in _disk_node_problems:
                _disk_node_problems.add(name)
                logger.warning("Could not make the container's node of %s the host's disk: %s", device, exc)
            raise DiskNodeError(exc.errno, f"The container's node of {device} could not be made: {exc}") from exc
        _disk_node_problems.discard(name)
    if number is None:
        raise NoHostDisk(errno.ENODEV, f"The host has no disk {name}")


def _host_disk_number(name: str) -> tuple[int, int] | None:
    """The device number (major, minor) of the host's whole disk ``name`` (``/sys/block/<name>/dev``,
    "8:16"), or None when the host has none of that name. FileNotFoundError when SYS_BLOCK itself
    is missing; OSError when the number cannot be read."""
    if not os.path.isdir(SYS_BLOCK):
        raise FileNotFoundError(errno.ENOENT, "No sysfs of block devices", SYS_BLOCK)
    try:
        with open(os.path.join(SYS_BLOCK, name, "dev"), encoding="ascii") as file:
            text = file.read()
    except FileNotFoundError:
        return None
    found = re.fullmatch(r"(\d+):(\d+)\n?", text)
    if found is None:
        raise OSError(errno.EINVAL, f"No device number in {SYS_BLOCK}/{name}/dev: {text!r}")
    return int(found[1]), int(found[2])


def _container_node_number(path: str) -> tuple[int, int] | None:
    """The device number (major, minor) of the block device node at ``path``; None when nothing is
    there, and _NOT_A_BLOCK_NODE for anything else (a file, a link, a character device)."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISBLK(status.st_mode):
        return _NOT_A_BLOCK_NODE
    return os.major(status.st_rdev), os.minor(status.st_rdev)


def _dev_is_the_containers() -> bool:
    """Whether CONTAINER_DEV is a filesystem of its own (Docker's tmpfs), not the host's /dev
    (HOST_DEV): a bind mount of the host's /dev, or a supervisor that runs on the host itself, gets
    no node of UNOS's. OSError when either cannot be read."""
    return os.stat(CONTAINER_DEV).st_dev != os.stat(HOST_DEV).st_dev


def _make_container_node(path: str, number: tuple[int, int]) -> None:
    """Put a block device node of ``number`` (major, minor) at ``path``, mode 0600: made under a
    temporary name beside it and renamed over what is there, so that smartctl finds the old entry
    or the new node, never none."""
    temporary = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.unos-new")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(temporary)
    os.mknod(temporary, stat.S_IFBLK | 0o600, os.makedev(*number))
    try:
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


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

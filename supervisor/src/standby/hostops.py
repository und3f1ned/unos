"""Every host action of the SD card standby, through ``src/host.py`` (ADR-0020), with a dry run.

``StandbyHost`` runs reads always and changes only when it is no dry run: a dry run records the
command of each change in its plan and goes on as if it worked, so that the API can answer what Set
up the standby, a sync, a drill, the return or Remove the standby would do on this host. A change
that fails raises ``StandbyFailed`` with what failed; a change after ``stop`` was set raises
``StandbyStopped``, so that the switch from a failing eMMC stops a sync before its next step.

Long commands (rsync, docker save) run in the transient unit ``unos-standby-sync.service``
(``systemd-run --wait --pipe``) with a deadline of their own: ``kill_sync`` ends one at once, which
the timeout of ``run_on_host`` could not (it ends only the command that enters the host, ADR-0258).
The unit sees the host read-only but the card (``ProtectSystem=strict``, ``ReadWritePaths=`` the
card's folder, ADR-0487): a read through a read-only mount sets no access time, so rsync's reads of
the eMMC's root write nothing to it, and no command of a sync can write the eMMC.
"""

from __future__ import annotations

import shlex
import subprocess
import threading

from .. import host
from ..config import get_settings

# The card's two mounts, on the eMMC's system and on the standby alike: the FAT partition and the
# btrfs top level. The supervisor's container sees them too, through its bind of /mnt (rshared).
STANDBY_DIR = f"{get_settings().mounts_base_path.rstrip('/')}/.unos-standby"
BOOT_DIR = f"{STANDBY_DIR}/boot"
CARD_DIR = f"{STANDBY_DIR}/card"
SYNC_UNIT = "unos-standby-sync"
# The sync's unit sees the host read-only but the card's mounts: no access time of the eMMC's files
# and folders is written by its reads (ADR-0487).
READ_ONLY_HOST = ("--property=ProtectSystem=strict", f"--property=ReadWritePaths=-{STANDBY_DIR}")
SYSTEMD_RUN_GRACE = 60


class StandbyError(Exception):
    """An action of the standby that is refused (409) or failed on the host (500)."""

    status = 409


class StandbyRefused(StandbyError):
    status = 409


class StandbyFailed(StandbyError):
    status = 500


class StandbyStopped(StandbyError):
    """A sync stopped by the switch from a failing eMMC."""

    status = 409


def _why(result: host.HostResult) -> str:
    return result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"exit status {result.returncode}"


class StandbyHost:
    def __init__(self, *, dry_run: bool = False, stop: threading.Event | None = None):
        self.dry_run = dry_run
        self.stop = stop
        self.plan: list[str] = []

    # --- reads -------------------------------------------------------------------------------------

    def read(self, cmd: list[str], *, timeout: float = 30, errors: str | None = "replace") -> host.HostResult:
        """A read of the host, also in a dry run; a timeout or a failure to start is a failed read."""
        try:
            return host.run_on_host(cmd, timeout=timeout, errors=errors)
        except subprocess.TimeoutExpired:
            return host.HostResult(124, "", f"{shlex.join(cmd)} did not answer within {timeout:g} s")
        except OSError as e:
            return host.HostResult(126, "", f"{shlex.join(cmd)} could not run: {e}")

    def read_file(self, path: str, *, timeout: float = 30) -> str | None:
        """The text of a host file; None when it cannot be read (missing among others)."""
        result = self.read(["cat", "--", path], timeout=timeout)
        return result.stdout if result.ok else None

    def exists(self, path: str) -> bool:
        return self.read(["test", "-e", path]).ok

    def long_read(self, cmd: list[str], *, timeout: float) -> host.HostResult:
        """A long read (rsync's dry run) in the sync's unit, also in a dry run."""
        self._check_stop()
        return self._in_unit(cmd, timeout=timeout)

    # --- changes -----------------------------------------------------------------------------------

    def change(self, cmd: list[str], *, what: str, timeout: float = 60, input: str | None = None,
               errors: str | None = None) -> host.HostResult:
        self._check_stop()
        if self.dry_run:
            self.plan.append(shlex.join(cmd))
            return host.HostResult(0, "", "")
        try:
            result = host.run_on_host(cmd, timeout=timeout, input=input, errors=errors)
        except subprocess.TimeoutExpired:
            raise StandbyFailed(f"{what}: {shlex.join(cmd)} did not end within {timeout:g} s") from None
        except OSError as e:
            raise StandbyFailed(f"{what}: {shlex.join(cmd)} could not run: {e}") from e
        if not result.ok:
            raise StandbyFailed(f"{what}: {_why(result)}")
        return result

    def long_change(self, cmd: list[str], *, what: str, timeout: float) -> host.HostResult:
        """A long change (rsync, docker save) in the sync's unit; any status but 0 fails it."""
        self._check_stop()
        if self.dry_run:
            self.plan.append(shlex.join(cmd))
            return host.HostResult(0, "", "")
        result = self._in_unit(cmd, timeout=timeout)
        self._check_stop()
        if not result.ok:
            raise StandbyFailed(f"{what}: {_why(result)} (exit status {result.returncode})")
        return result

    def write_file(self, path: str, content: str | bytes, *, what: str, mode: str = "0644") -> None:
        """Replace a host file atomically and durably (host.write_host_file_atomic); bytes as they are."""
        self._check_stop()
        if self.dry_run:
            self.plan.append(f"write {path}")
            return
        text = content.decode("utf-8", "surrogateescape") if isinstance(content, bytes) else content
        try:
            host.write_host_file_atomic(path, text, mode=mode, errors="surrogateescape")
        except host.HostCommandError as e:
            raise StandbyFailed(f"{what}: {e.stderr.strip() or e}") from None
        except (OSError, subprocess.SubprocessError) as e:
            raise StandbyFailed(f"{what}: {e}") from e

    # --- the sync's unit ---------------------------------------------------------------------------

    def _in_unit(self, cmd: list[str], *, timeout: float) -> host.HostResult:
        wrapped = [
            "systemd-run", f"--unit={SYNC_UNIT}", "--wait", "--pipe", "--quiet", "--collect",
            f"--property=RuntimeMaxSec={int(timeout)}", *READ_ONLY_HOST, "--", *cmd,
        ]
        try:
            return host.run_on_host(wrapped, timeout=timeout + SYSTEMD_RUN_GRACE, errors="replace")
        except subprocess.TimeoutExpired:
            return host.HostResult(124, "", f"{cmd[0]} did not end within {timeout:g} s")
        except OSError as e:
            return host.HostResult(126, "", f"{cmd[0]} could not run: {e}")

    def _check_stop(self) -> None:
        if self.stop is not None and self.stop.is_set():
            raise StandbyStopped("The sync was stopped: UNOS switches to the SD standby")


def kill_sync() -> None:
    """End a command of the sync's unit at once; never raises, waits at most a few seconds."""
    try:
        host.run_on_host(["systemctl", "kill", "--signal=SIGKILL", f"{SYNC_UNIT}.service"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass

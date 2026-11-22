"""The release installer: deploy/update.sh on the host (ADR-0050, ADR-0443).

The updater (updater.py) runs the job: the release's images first, then a copy of the database
with SQLite's backup API (ADR-0306), then the switch, which it hands over. The installer runs
update.sh's steps on the host, where one home holds the switch, its health check over HTTP and
HTTPS, its rollback and the update lock for an update by hand and one from the web UI:

- ``fetch_release``: ``update.sh --fetch <tag>``, the tag and the images of the release on this
  host, from GHCR, which .github/workflows/release.yml publishes them to. Nothing else changes.
- ``start_switch``: ``update.sh --in-app update|rollback`` in a transient systemd unit of the host
  (``systemd-run``), outside the containers that it replaces: the switch, its health check and its
  rollback outlive the supervisor's container. update.sh writes how it ended into the result file.
- ``switch_running``: whether that unit still runs.

``unavailable_reason`` says why this host cannot install from the web UI; Install Update, Rollback
and the automatic install then answer it, and System > Updates names update.sh. Tests replace
``current`` with a fake.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal, Protocol

from .. import host
from ..config import get_settings

# A release: a git tag v<x.y.z> (ADR-0050), the version its images carry.
RELEASE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
# What uname -m says on the platforms that release.yml builds images for: linux/amd64, linux/arm64,
# linux/riscv64 (deploy.riscv64-release-images).
RELEASE_MACHINES = ("x86_64", "aarch64", "riscv64")
# Whether the image that runs is 64-bit: a 32-bit userland on a 64-bit Arm kernel says aarch64 but runs
# arm images, which no release has.
USERLAND_64_BIT = sys.maxsize > 2**32
# The host's checkout of install.sh (UNOS_INSTALL_DIR) and its update script.
INSTALL_DIR = get_settings().install_dir
# The host's files as the supervisor's container sees them: pid: host makes PID 1 the host's init.
HOST_ROOT = "/proc/1/root"
# Getting a release's images may take up to an hour (ADR-0443); systemd-run returns once update.sh
# runs in its unit.
FETCH_TIMEOUT_SECONDS = 3600.0
KILL_AFTER_SECONDS = 30
SYSTEMD_RUN_TIMEOUT_SECONDS = 60.0
SYSTEMCTL_TIMEOUT_SECONDS = 30.0
# The states of a unit in which its update.sh has not ended yet.
UNIT_RUNS = {"active", "activating", "deactivating", "reloading"}
# How long a stop of the unit (systemctl stop, a shutdown of the host) waits for update.sh's rollback,
# which waits up to 680 s for the supervisor and 300 s for the health check, after a compose up that
# may have waited as long: below the updater's FOLLOW_TIMEOUT_SECONDS.
UNIT_STOP_TIMEOUT_SECONDS = 2400
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


class InstallerError(RuntimeError):
    """A step of update.sh that failed, with its message."""


class ReleaseInstaller(Protocol):
    def unavailable_reason(self) -> str | None:
        """Why this NAS cannot install releases; None when it can."""

    async def fetch_release(self, tag: str) -> None:
        """Have the release's images here; raise when they cannot be had. Nothing else changes."""

    async def start_switch(self, unit: str, action: Literal["update", "rollback"], tag: str, backup: Path,
                           result: Path) -> None:
        """Start the switch to ``tag`` in the host's unit ``unit``, with the backup folder ``backup``, its
        result in ``result``; raise when it did not start."""

    async def switch_running(self, unit: str) -> bool:
        """Whether the unit of a switch still runs (True when that cannot be told)."""


def script_error(output: str) -> str | None:
    """The first error that update.sh printed ([ERROR] lines), without its colours."""
    for line in ANSI_ESCAPE.sub("", output).splitlines():
        if "[ERROR] " in line:
            return line.split("[ERROR] ", 1)[1].strip()
    return None


class HostUpdate:
    """deploy/update.sh of the host's checkout, run through src/host.py (ADR-0020)."""

    def __init__(self, install_dir: str = INSTALL_DIR):
        self.install_dir = install_dir
        self.script = f"{install_dir}/deploy/update.sh"
        self.command = f"sudo {self.script}"

    def _seen_from_here(self, path: str) -> str:
        return path if host.runs_locally() else f"{HOST_ROOT}{path}"

    def unavailable_reason(self) -> str | None:
        if host.runs_locally():
            return ("This supervisor runs outside its container (UNOS_HOST_EXEC=local: the local stand-in or the "
                    "compose test stack), where nothing is installed.")
        machine = platform.machine()
        if machine not in RELEASE_MACHINES or not USERLAND_64_BIT:
            userland = "" if USERLAND_64_BIT else " with a 32-bit userland"
            return (f"Releases publish images for amd64, arm64 and riscv64 only, not for this host's {machine}"
                    f"{userland}: update with {self.command}, which builds them here.")
        version = os.environ.get("UNOS_VERSION", "").strip() or "unknown"
        release = RELEASE_TAG.fullmatch(version) is not None
        if os.environ.get("UNOS_RELEASE", "").strip() != version:
            # Built on this host: a release's image says that it is one (UNOS_RELEASE, release.yml).
            built_here = ("the supervisor's image was built on this host, not pulled from a release on GHCR: this "
                          "is a dev device deployed by scripts/dev/deploy.sh (deploy it with that), or a build by "
                          "hand.")
            if release:
                return built_here[0].upper() + built_here[1:]
            return f"UNOS runs {version}, which is not a release (a tag v<x.y.z>), and {built_here}"
        if not release:
            return (f"UNOS runs {version}, which is not a release (a tag v<x.y.z>): the web interface installs "
                    f"releases only over a release. Install one with {self.command}.")
        if not os.path.isfile(self._seen_from_here(self.script)):
            return (f"{self.script} is not on this host (UNOS_INSTALL_DIR): it switches the containers to a "
                    "release, and install.sh puts it there.")
        return None

    async def fetch_release(self, tag: str) -> None:
        _check_tag(tag)
        try:
            # Under the host's timeout: the timeout of run_on_host ends only nsenter (ADR-0258).
            result = await host.run_on_host_async(
                ["timeout", f"--kill-after={KILL_AFTER_SECONDS}", str(int(FETCH_TIMEOUT_SECONDS)),
                 "env", f"UNOS_INSTALL_DIR={self.install_dir}", self.script, "--fetch", tag],
                timeout=FETCH_TIMEOUT_SECONDS + 2 * KILL_AFTER_SECONDS, errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise InstallerError(f"Getting the images of {tag} took longer than {FETCH_TIMEOUT_SECONDS:g} "
                                 "seconds") from None
        except OSError as e:
            raise InstallerError(f"update.sh --fetch could not run: {e}") from e
        if result.returncode == 124:
            raise InstallerError(f"Getting the images of {tag} took longer than {FETCH_TIMEOUT_SECONDS:g} seconds")
        if not result.ok:
            why = script_error(result.stdout + "\n" + result.stderr) or result.stderr.strip() \
                or f"exit status {result.returncode}"
            raise InstallerError(f"Getting the images of {tag} failed: {why}")

    async def start_switch(self, unit: str, action: Literal["update", "rollback"], tag: str, backup: Path,
                           result: Path) -> None:
        _check_tag(tag)
        if action not in ("update", "rollback") or not re.fullmatch(r"unos-[a-z]+-[0-9-]+", unit):
            raise ValueError("an update or a rollback in a unit unos-<action>-<time>")
        try:
            started = await host.run_on_host_async([
                "systemd-run",
                f"--unit={unit}",
                f"--description=UNOS: the in-app {action} to {tag}",
                # Unloaded once update.sh ended, also after a failure: its result is in the file.
                "--collect",
                "--quiet",
                # A stop sends SIGTERM to update.sh alone, which then rolls back with its signals ignored,
                # and SIGKILL to the whole unit only after UNIT_STOP_TIMEOUT_SECONDS.
                "--property=KillMode=mixed",
                f"--property=TimeoutStopSec={UNIT_STOP_TIMEOUT_SECONDS}",
                # A system unit has no HOME of its own; git and docker read their settings there.
                "--setenv=HOME=/root",
                f"--setenv=UNOS_INSTALL_DIR={self.install_dir}",
                self.script, "--in-app", action, "--backup", str(backup), "--result", str(result), tag,
            ], timeout=SYSTEMD_RUN_TIMEOUT_SECONDS, errors="replace")
        except subprocess.TimeoutExpired:
            raise InstallerError(f"systemd-run did not answer within {SYSTEMD_RUN_TIMEOUT_SECONDS:g} seconds: the "
                                 f"unit {unit} may not have started") from None
        except OSError as e:
            raise InstallerError(f"systemd-run could not run: {e}") from e
        if not started.ok:
            raise InstallerError(f"The unit {unit} did not start: "
                                 f"{started.stderr.strip() or f'exit status {started.returncode}'}")

    async def switch_running(self, unit: str) -> bool:
        try:
            shown = await host.run_on_host_async(
                ["systemctl", "show", "--property=ActiveState", "--value", unit],
                timeout=SYSTEMCTL_TIMEOUT_SECONDS, errors="replace",
            )
        except (OSError, subprocess.SubprocessError):
            return True
        if not shown.ok:
            return True
        return shown.stdout.strip() in UNIT_RUNS


def _check_tag(tag: str) -> None:
    if not RELEASE_TAG.fullmatch(tag):
        raise InstallerError(f"{tag!r} is not a release (a tag v<x.y.z>)")


current: ReleaseInstaller = HostUpdate()

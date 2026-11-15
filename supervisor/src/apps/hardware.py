"""The hardware of the host's SoC that apps get (ADR-0377).

A template's HardwareProfile names a SoC by a compatible of the host's device tree, and the
device nodes without which the app cannot use that hardware. A profile applies on a host whose
device tree names its SoC and that has every node it requires: there the app's container gets
those of the profile's device nodes that the host has; a profile whose app must read paths that
Docker masks gets Docker's masks without them, and runs without Docker's AppArmor profile where
Docker applies one. Where no profile applies, the app is installed as on every host, and the
catalog API names no profile. The device tree and the nodes are the host's, read through
src/host.py (ADR-0020); a read that fails counts as a host without a device tree or without the
nodes, and is logged; a host without a device tree (a PC) is not.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

from .. import host
from .schemas import AppTemplate, HardwareProfile

logger = logging.getLogger(__name__)

DEVICE_TREE_COMPATIBLE = "/proc/device-tree/compatible"
HOST_TIMEOUT = 15

# The paths that Docker 29 masks in every unprivileged container (defaultLinuxMaskedPaths of
# moby's daemon/pkg/oci/defaults.go). A container created with MaskedPaths of its own gets
# those in place of Docker's, so a profile's container gets all of these but the profile's
# unmasked paths. Docker also masks /sys/devices/system/cpu/cpu<N>/thermal_throttle of each CPU
# that has one: only x86 CPUs have them, and an x86 host has no device tree, so no profile
# applies there.
DOCKER_MASKED_PATHS = (
    "/proc/acpi",
    "/proc/asound",
    "/proc/interrupts",
    "/proc/kcore",
    "/proc/keys",
    "/proc/latency_stats",
    "/proc/sched_debug",
    "/proc/scsi",
    "/proc/timer_list",
    "/proc/timer_stats",
    "/sys/devices/virtual/powercap",
    "/sys/firmware",
)

# Prints those of its arguments that exist on the host, one per line. The paths reach the
# shell as positional arguments, never as script text.
_PRESENT_SCRIPT = 'for p do if [ -e "$p" ]; then printf "%s\\n" "$p"; fi; done'
# Prints the file $1, the device tree's compatible; exits 3 where the host has none, a status
# that neither nsenter nor cat uses: a host without a device tree is told from a read that fails.
DEVICE_TREE_SCRIPT = '[ -e "$1" ] || exit 3; exec cat -- "$1"'
_NO_DEVICE_TREE = 3


def host_socs() -> frozenset[str]:
    """The names in the host's device tree compatible: none on a host without a device tree (a
    PC) or whose device tree cannot be read. Blocks: call it in a worker thread."""
    try:
        result = host.run_on_host(
            ["/bin/sh", "-c", DEVICE_TREE_SCRIPT, "unos-device-tree", DEVICE_TREE_COMPATIBLE], timeout=HOST_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read the host's device tree: %s", exc)
        return frozenset()
    if result.returncode == _NO_DEVICE_TREE:
        return frozenset()
    if not result.ok:
        logger.warning(
            "Could not read the host's device tree (exit status %d): %s", result.returncode, result.stderr.strip()
        )
        return frozenset()
    return frozenset(name for name in result.stdout.split("\0") if name)


def _present_on_host(paths: list[str]) -> list[str]:
    """Those of ``paths`` that exist on the host, in their order; none when the host cannot say."""
    try:
        result = host.run_on_host(["/bin/sh", "-c", _PRESENT_SCRIPT, "unos-devices", *paths], timeout=HOST_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not look for the device nodes of the host's SoC: %s", exc)
        return []
    if not result.ok:
        logger.warning("Could not look for the device nodes of the host's SoC: %s", result.stderr.strip())
        return []
    found = set(result.stdout.splitlines())
    return [path for path in paths if path in found]


@dataclass(frozen=True)
class HostHardware:
    """What the host has of the hardware profiles it was asked about: the names of its SoC, and
    those of the profiles' nodes that it has (looked for only for the profiles of its SoC)."""

    socs: frozenset[str] = frozenset()
    nodes: frozenset[str] = frozenset()

    def applies(self, profile: HardwareProfile) -> bool:
        """Whether ``profile`` applies here: its SoC, with every node it requires."""
        return profile.soc in self.socs and all(node in self.nodes for node in profile.requires)


def read_host_hardware(profiles: list[HardwareProfile]) -> HostHardware:
    """The host's SoC and nodes for ``profiles``: one read of its device tree, and one look for
    the nodes of the profiles of its SoC, if any; nothing is read for no profiles. Blocks: call
    it in a worker thread."""
    if not profiles:
        return HostHardware()
    socs = host_socs()
    wanted = list(dict.fromkeys(node for p in profiles if p.soc in socs for node in (*p.devices, *p.requires)))
    return HostHardware(socs, frozenset(_present_on_host(wanted)) if wanted else frozenset())


def for_host(template: AppTemplate, hardware: HostHardware) -> AppTemplate:
    """The template as the catalog API answers it on a host that has ``hardware``: with the
    hardware profiles that apply there only."""
    if not template.hardware:
        return template
    return template.model_copy(update={"hardware": [p for p in template.hardware if hardware.applies(p)]})


@dataclass(frozen=True)
class ContainerHardware:
    """What an app's container gets of the host's hardware: the host's device nodes, each passed
    in under its own path, read and write; Docker's MaskedPaths (None: Docker's defaults) and
    its security options."""

    devices: list[str] = field(default_factory=list)
    masked_paths: list[str] | None = None
    security_opt: list[str] = field(default_factory=list)


def container_hardware(template: AppTemplate) -> ContainerHardware:
    """What the container of ``template`` gets on this host: nothing for a template without
    hardware profiles, whose install reads nothing of the host, or where none applies. Blocks:
    call it in a worker thread."""
    hardware = read_host_hardware(template.hardware)
    profiles = [p for p in template.hardware if hardware.applies(p)]
    if not profiles:
        return ContainerHardware()
    unmasked = {path for p in profiles for path in p.unmasked_paths}
    return ContainerHardware(
        devices=[node for node in dict.fromkeys(node for p in profiles for node in p.devices) if node in hardware.nodes],
        masked_paths=[path for path in DOCKER_MASKED_PATHS if path not in unmasked] if unmasked else None,
        security_opt=["apparmor=unconfined"] if any(p.apparmor_unconfined for p in profiles) else [],
    )

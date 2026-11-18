"""The platforms that the catalog's images publish, and the host's (ADR-0403).

``image_platforms.json`` records, for every image reference of the catalog, the Linux platforms
that its registry's manifest list publishes ("linux/amd64", "linux/arm/v7"); the weekly audit
(``scripts/catalog-platforms.py``, ADR-0218) compares it with the registries. An app is available
on a host when every one of its containers' images publishes the host's platform. Nothing here
reads Docker or the registries; an image with no record, or a host whose platform is not known,
is left to Docker (never a guess that blocks an install). An image recorded with no platform at
all (its registry's list names none) runs on no host.
"""

from __future__ import annotations

import functools
import json
import platform
from pathlib import Path

RECORDED = Path(__file__).with_name("image_platforms.json")

# platform.machine() of a Linux host (also macOS's arm64 and Windows' AMD64) to Docker's platform.
_MACHINES = {
    "aarch64": "linux/arm64",
    "arm64": "linux/arm64",
    "x86_64": "linux/amd64",
    "amd64": "linux/amd64",
    "riscv64": "linux/riscv64",
    "armv7l": "linux/arm/v7",
    "armv8l": "linux/arm/v7",
    "armv6l": "linux/arm/v6",
    "i386": "linux/386",
    "i686": "linux/386",
    "ppc64le": "linux/ppc64le",
    "s390x": "linux/s390x",
}
# The older variants of 32-bit arm that Docker also runs on a host of the key's variant.
_ARM_COMPATIBLE = {
    "linux/arm/v7": {"linux/arm/v7", "linux/arm/v6", "linux/arm/v5"},
    "linux/arm/v6": {"linux/arm/v6", "linux/arm/v5"},
}


@functools.lru_cache(maxsize=1)
def _load() -> dict[str, list[str]]:
    return json.loads(RECORDED.read_text())["images"]


def recorded() -> dict[str, list[str]]:
    """The recorded platforms by image reference."""
    return _load()


def image_platforms(image: str) -> list[str]:
    """The platforms recorded for ``image``; none when nothing is recorded."""
    return recorded().get(image, [])


def platform_of_machine(machine: str) -> str | None:
    """Docker's platform for a ``platform.machine()``; None for one that is not known."""
    return _MACHINES.get(machine.lower())


def host_platform() -> str | None:
    """The platform of the host UNOS runs on (the kernel's machine, which the supervisor's
    container shares), or None when it cannot be named."""
    return platform_of_machine(platform.machine())


def runs_on(published: list[str], host: str) -> bool:
    """Whether an image that publishes ``published`` runs on a host of platform ``host``."""
    return any(p in published for p in _ARM_COMPATIBLE.get(host, {host}))


def images_lacking(images: list[str], host: str | None) -> list[str]:
    """Those of ``images`` (each once) that are recorded and publish no build for ``host``; none
    for a host that has no name."""
    if host is None:
        return []
    known = recorded()
    return [image for image in dict.fromkeys(images) if image in known and not runs_on(known[image], host)]


def lacking(template, host: str | None) -> list[str]:
    """The images of ``template``'s containers (the main one's and the companions') that are
    recorded and publish no build for ``host``."""
    return images_lacking([spec.image for spec in (template, *template.companions)], host)

"""The release installer: the part of Install Update and Rollback that the ADRs leave open (ADR-0443).

The updater (updater.py) runs what is settled: the release's images first, then a copy of the
database with SQLite's backup API, the switch, the health check over HTTP and HTTPS within a
deadline, and the rollback on a failure: the copy put back through the open database and the
version before started again (ADR-0049, ADR-0050, ADR-0306). The installer does the steps that
touch the release's images and the containers:

- ``fetch_release``: the release's images on this NAS, before anything changes. Releases are git
  tags (ADR-0050), but GHCR has no images yet (the CI workflow that would publish them is off,
  ADR-0230), and building them on the device writes hundreds of MB to its SD card (ADR-0214).
- ``switch_to`` and ``switch_back``: the containers of the release, and those of the version
  before. The supervisor that runs the update is one of those containers.
- ``healthy``: whether the release answers over HTTP and HTTPS, as update.sh asks it.

Neither choice is settled, so ``current`` is ``Undecided``: Install Update, Rollback and the
automatic install answer its reason, and System > Updates names update.sh (ADR-0049) instead.
Tests replace ``current`` with a fake.
"""

from __future__ import annotations

from typing import Protocol

from .schemas import ReleaseInfo

UNDECIDED_REASON = (
    "Installing a release from the web UI waits for two decisions: where a release's images come from "
    "while GHCR has none, and how the supervisor replaces its own container. Update with "
    "sudo /opt/unos/deploy/update.sh."
)


class ReleaseInstaller(Protocol):
    def unavailable_reason(self) -> str | None:
        """Why this NAS cannot install releases; None when it can."""

    async def fetch_release(self, release: ReleaseInfo) -> None:
        """Have the release's images here; raise UpdateError when they cannot be had."""

    async def switch_to(self, release: ReleaseInfo) -> None:
        """Start the release's containers in place of the running ones."""

    async def healthy(self) -> bool:
        """Whether UNOS answers over HTTP and HTTPS."""

    async def switch_back(self, version: str) -> None:
        """Start the containers of ``version``, the one that ran before the update, again."""


class Undecided:
    """The supervisor's installer while the choices above wait for the owner."""

    def unavailable_reason(self) -> str | None:
        return UNDECIDED_REASON

    async def fetch_release(self, release: ReleaseInfo) -> None:
        raise RuntimeError(UNDECIDED_REASON)

    async def switch_to(self, release: ReleaseInfo) -> None:
        raise RuntimeError(UNDECIDED_REASON)

    async def healthy(self) -> bool:
        raise RuntimeError(UNDECIDED_REASON)

    async def switch_back(self, version: str) -> None:
        raise RuntimeError(UNDECIDED_REASON)


current: ReleaseInstaller = Undecided()

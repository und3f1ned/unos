"""The return from the SD standby's root to the eMMC with the card's changes (system.sd-standby-failback).

On the card's root, the return is offered only once the eMMC reads and its root passes ``fsck.ext4 -n``
(``check``): the root's line of the eMMC's own fstab lines, which the sync keeps in the generation
(``/var/lib/unos-standby/emmc-fstab``), names the filesystem; blkid finds it, it must be ext4 and not
mounted, and e2fsck's read-only check must end with status 0. Otherwise the card stays the system disk,
and no standby is left.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..times import utc_now, utc_text
from . import conditions
from .generation import LIB
from .hostops import StandbyHost, StandbyRefused

EMMC_FSTAB = f"{LIB}/emmc-fstab"
# fsck.ext4 -n of a filesystem with errors reads all its metadata: minutes on an eMMC.
FSCK_SECONDS = 900
_DEVICE = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9/_.-]*")
_SPEC = re.compile(r"(UUID|PARTUUID|LABEL|PARTLABEL)=[A-Za-z0-9._-]{1,64}")


@dataclass
class EmmcCheck:
    ok: bool
    detail: str
    device: str | None
    at: str

    def as_record(self) -> dict[str, object]:
        return {"ok": self.ok, "detail": self.detail, "device": self.device, "at": self.at}


def root_line(own_lines: str) -> tuple[str, str]:
    """The spec and the type of the eMMC's root in its own fstab lines; StandbyRefused without one."""
    for line in own_lines.splitlines():
        fields = line.split()
        if fields and not fields[0].startswith("#") and len(fields) >= 3 and fields[1] == "/":
            return fields[0], fields[2]
    raise StandbyRefused("The card's copy holds no fstab line of the eMMC's root")


def find_root(ops: StandbyHost) -> str:
    """The eMMC's root device, unmounted and of ext4; StandbyRefused when it cannot be used (blocking)."""
    own = ops.read_file(EMMC_FSTAB)
    if own is None:
        raise StandbyRefused("The card's copy holds no fstab line of the eMMC's root")
    spec, fstype = root_line(own)
    if fstype != "ext4":
        raise StandbyRefused(f"The eMMC's root is {fstype}, not ext4: only an ext4 root returns")
    if _DEVICE.fullmatch(spec):
        device = spec if ops.read(["test", "-b", spec]).ok else ""
    elif _SPEC.fullmatch(spec):
        found = ops.read(["blkid", "-l", "-o", "device", "-t", spec])
        device = found.stdout.strip() if found.ok else ""
    else:
        raise StandbyRefused(f"The eMMC's root is named {spec} in its fstab line, which UNOS does not read")
    if not _DEVICE.fullmatch(device):
        raise StandbyRefused(f"The eMMC's root {spec} is not found: the eMMC does not read")
    mounts = conditions._parse_mountinfo(ops.read_file(conditions.MOUNTINFO) or "")
    for mount in mounts:
        if mount.source == device:
            raise StandbyRefused(f"The eMMC's root {device} is mounted at {mount.target}: unmount it first")
    return device


def check(ops: StandbyHost | None = None) -> EmmcCheck:
    """Whether the eMMC reads and its root passes ``fsck.ext4 -n`` (blocking): reads only."""
    ops = ops or StandbyHost()
    at = utc_text(utc_now())
    try:
        device = find_root(ops)
    except StandbyRefused as e:
        return EmmcCheck(ok=False, detail=str(e), device=None, at=at)
    result = ops.read(["timeout", "--kill-after=10", str(FSCK_SECONDS), "fsck.ext4", "-n", device],
                      timeout=FSCK_SECONDS + 30)
    if result.ok:
        return EmmcCheck(ok=True, detail=f"The eMMC's root {device} passes fsck.ext4 -n", device=device, at=at)
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()]
    said = lines[-1] if lines else ""
    status = "it did not end in time" if result.returncode == 124 else f"exit status {result.returncode}"
    return EmmcCheck(ok=False, detail=f"The eMMC's root {device} does not pass fsck.ext4 -n ({status})"
                     + (f": {said}" if said else ""), device=device, at=at)

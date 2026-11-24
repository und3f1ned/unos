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


# --- the return with the card's changes ----------------------------------------------------------------
#
# The supervisor stops with Docker, so the return runs in the transient unit unos-standby-return
# (host/unos-standby-return.sh): the supervisor writes the script, return.env and the files it names into
# RUN_DIR, in the host's RAM, and starts it. A unit that fails starts Docker and Samba again and leaves its
# result there, which the next start of the supervisor on the card records; one that ends well reboots.

RUN_DIR = "/run/unos-standby-return"
RESULT = f"{RUN_DIR}/result"
UNIT = "unos-standby-return"
MOUNT = f"{RUN_DIR}/emmc"
SCRIPT = "unos-standby-return.sh"
# Where the eMMC's Docker keeps UNOS's compose volumes, by their short name: kept at the sync (fix-ups).
EMMC_VOLUMES = f"{LIB}/emmc-volumes"
# The units that the copy stops: Docker, with UNOS's containers, and Samba.
UNITS = ("docker.socket", "docker.service", "smbd.service", "nmbd.service")
START_DELAY_SECONDS = 5
UNIT_STOP_SECONDS = 900
_PLAIN = re.compile(r"/[A-Za-z0-9._/@+-]*")


@dataclass
class Plan:
    device: str
    files: dict[str, str]  # name in RUN_DIR -> text
    settings: dict[str, str]  # return.env
    lines: list[str]  # what the unit runs, for the dry run


def emmc_fstab(card_fstab: str, own_lines: str) -> str:
    """The eMMC's /etc/fstab: the standby's, whose root line gives way to the eMMC's own lines that the sync
    kept (its root, /boot, swap and its other filesystems), so that a pool added on the card comes along."""
    root_line(own_lines)
    own = "".join(line + "\n" for line in own_lines.splitlines() if line.strip())
    out: list[str] = []
    placed = False
    for line in card_fstab.splitlines():
        fields = line.split()
        if fields and not fields[0].startswith("#") and len(fields) >= 3 and fields[1] == "/":
            if not placed:
                out.append(own)
                placed = True
            continue
        out.append(line + "\n")
    if not placed:
        raise StandbyRefused("The standby's /etc/fstab has no root line")
    return "".join(out)


def _volumes(ops: StandbyHost, stamp: str) -> list[tuple[str, str, str]]:
    """(the card's volume, the eMMC's, the backup folder or "") of each of UNOS's compose volumes. A volume
    of the eMMC's Docker on a pool goes there, its replaced files kept beside; one on the eMMC goes below
    MOUNT, without a backup (no writes to the eMMC beyond the copy). StandbyRefused when one cannot go."""
    import json

    from . import apps as standby_apps
    from .generation import SUPERVISOR_CONTAINER

    text = ops.read_file(EMMC_VOLUMES)
    try:
        emmc = json.loads(text) if text else None
    except json.JSONDecodeError:
        emmc = None
    if not isinstance(emmc, dict):
        raise StandbyRefused("The card's copy does not say where the eMMC's Docker keeps UNOS's volumes")
    found = ops.read(["docker", "inspect", SUPERVISOR_CONTAINER])
    try:
        project = str(((json.loads(found.stdout)[0].get("Config") or {}).get("Labels") or {}).get(
            "com.docker.compose.project") or "") if found.ok else ""
    except (json.JSONDecodeError, IndexError, AttributeError):
        project = ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise StandbyRefused("UNOS's compose project cannot be read from its container")
    listed = ops.read(["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}",
                       "--format", "{{.Name}}"])
    if not listed.ok:
        raise StandbyRefused(f"UNOS's volumes cannot be listed: {listed.stderr.strip()}")
    pools = standby_apps.pools_base()
    pairs = []
    for name in sorted(listed.stdout.split()):
        short = name.removeprefix(f"{project}_")
        source = ops.read(["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", name]).stdout.strip()
        target = emmc.get(short)
        if not _PLAIN.fullmatch(source):
            raise StandbyRefused(f"The volume {name} cannot be read")
        if not isinstance(target, str) or not _PLAIN.fullmatch(target) or not target.endswith("/_data"):
            raise StandbyRefused(f"The eMMC's Docker has no volume {short}")
        relative = standby_apps._relative(target, pools)
        if relative is None:
            pairs.append((source, f"{MOUNT}{target}", ""))
            continue
        pool = relative.split("/", 1)[0]
        why = standby_apps.pool_unfit(pool)
        if why:
            raise StandbyRefused(f"UNOS's volumes go back to the eMMC's Docker on the pool {pool}, but {why}")
        volume = target.rsplit("/", 1)[0]
        folder, _, volume_name = volume.rpartition("/")
        pairs.append((source, target, f"{folder}.before-failback-{stamp}/{volume_name}"))
    if not pairs:
        raise StandbyRefused("UNOS's volumes are not found on the card")
    return pairs


def plan(*, device: str, card_device: str, sector: int, generation: int | None, username: str,
         ops: StandbyHost | None = None) -> Plan:
    """The files and settings of the unit (blocking, reads only); StandbyRefused when a condition of the copy
    is not met."""
    import json
    import shlex

    from . import apps as standby_apps
    from . import bootscript
    from .generation import (CONTAINERD_CONFIG, DOCKER_CONFIG, DRILL, EXCLUDES, FSTAB, HOST_SCRIPTS, MARK, STATE_FILE,
                             VOLATILE, _swap_files, mountpoints)

    ops = ops or StandbyHost()
    if not re.fullmatch(r"/dev/mmcblk[0-9]+", card_device):
        raise StandbyRefused("The standby's card is not mounted")
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    own = ops.read_file(EMMC_FSTAB)
    card_fstab = ops.read_file(FSTAB)
    if own is None or card_fstab is None:
        raise StandbyRefused("The standby's /etc/fstab or the eMMC's own lines cannot be read")
    fstab = emmc_fstab(card_fstab, own)
    volumes = _volumes(ops, stamp)
    mounts = conditions._parse_mountinfo(ops.read_file(conditions.MOUNTINFO) or "")
    # The eMMC's other filesystems (a boot partition of its own, as an extlinux board may have) stay as they are,
    # and so do its swap files, which the copy left out, and, beside a boot partition of its own, its kernel's
    # modules: the kernel on that partition and its modules stay a pair.
    others, swaps = [], []
    for line in own.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0].startswith("#"):
            continue
        if fields[2] == "swap" and _PLAIN.fullmatch(fields[0]) and not fields[0].startswith("/dev/"):
            swaps.append(fields[0])
        elif fields[1] != "/" and fields[2] != "swap" and _PLAIN.fullmatch(fields[1]):
            others.append(fields[1])
    modules = ["/lib/modules", "/usr/lib/modules"] if "/boot" in others else []
    points = mountpoints(mounts, card_fstab)
    excludes = [*EXCLUDES, *VOLATILE, *points, *_swap_files(ops), DOCKER_CONFIG, CONTAINERD_CONFIG, *others,
                *swaps, *modules]
    # state.json and its digest as the host has its bytes, read together under the lock of its writers.
    from .service import _stored_lock

    with _stored_lock:
        stored_text = ops.read_file(STATE_FILE)
        digest = ops.read(["sha256sum", "--", STATE_FILE]).stdout.split()
    try:
        stored = json.loads(stored_text) if stored_text else None
    except json.JSONDecodeError:
        stored = None
    if stored_text is None or not isinstance(stored, dict) or not digest or not re.fullmatch(r"[0-9a-f]{64}", digest[0]):
        raise StandbyRefused("The standby's state.json cannot be read")
    # The run goes and the return is recorded, for the eMMC's system: its first sync writes a new generation
    # and empties the copy's Docker data. The critical apps' records stay: those that run from the card stay
    # there until their pool's return.
    after = {key: value for key, value in stored.items() if key != "standby_ran"}
    after["emmc_return"] = {"at": utc_text(utc_now()), "generation": generation, "by": username}
    files = {
        SCRIPT: (HOST_SCRIPTS / SCRIPT).read_text(encoding="utf-8"),
        "excludes": "".join(f"{pattern}\n" for pattern in dict.fromkeys(excludes)),
        "volumes": "".join(f"{source}\t{target}\t{backup}\n" for source, target, backup in volumes),
        "fstab": fstab,
        "state.json": json.dumps(after, indent=1, sort_keys=True) + "\n",
        "counter": bootscript.counter_block(0).rstrip(b"\0").decode(),
        # The card's mount points, which the copy leaves out: the eMMC gets those it lacks (a pool made on the
        # card), immutable below the pools' folder as UNOS makes them (ADR-0034).
        "mountpoints": "".join(f"{point}\n" for point in points),
    }
    settings = {"EMMC": device, "MOUNT": MOUNT, "CARD": card_device, "SECTOR": str(sector), "MARK": MARK,
                "DRILL": DRILL, "STATE": STATE_FILE,
                "STATE_SHA": digest[0], "UNITS": " ".join(UNITS), "DELAY": str(START_DELAY_SECONDS),
                "POOLS": standby_apps.pools_base()}
    files["return.env"] = "".join(f"{key}={shlex.quote(value)}\n" for key, value in settings.items())
    lines = [
        f"fsck.ext4 -n {device}", f"systemctl stop {' '.join(UNITS)} (those that run)",
        f"mount -t ext4 -o noatime {device} {MOUNT}",
        f"rsync -aHAX --numeric-ids --one-file-system --delete --omit-dir-times --inplace --no-whole-file "
        f"--exclude-from=excludes / {MOUNT}/ (without {len(excludes)} paths and the eMMC's drop-ins that hold "
        "Docker until its pool is mounted)",
        *(f"rsync -aHAX --numeric-ids --delete {f'--backup --backup-dir={backup}' if backup else '--inplace'} "
          f"{source}/ {target}/" for source, target, backup in volumes),
        f"write {MOUNT}/etc/fstab", f"rsync --dry-run / {MOUNT}/: nothing left to copy", f"umount {MOUNT}",
        f"fsck.ext4 -f -n {device}", f"rm -f {DRILL}", f"dd of={card_device} seek={sector}: the tries counter at 0",
        f"write {STATE_FILE} without standby_ran", f"rm -f {MARK}", "systemctl reboot"]
    return Plan(device=device, files=files, settings=settings, lines=lines)


def stage(found: Plan) -> list[str]:
    """The unit's folder in the host's RAM, then the unit started (blocking); the commands it ran."""
    from .hostops import StandbyFailed

    ops = StandbyHost()
    ops.change(["rm", "-rf", "--one-file-system", "--", RUN_DIR], what=f"{RUN_DIR} cannot be emptied")
    ops.change(["mkdir", "-p", "-m", "0700", RUN_DIR], what=f"{RUN_DIR} cannot be made")
    for name, text in found.files.items():
        ops.write_file(f"{RUN_DIR}/{name}", text, what=f"{RUN_DIR}/{name}", mode="0600")
    try:
        ops.change([
            "systemd-run", f"--unit={UNIT}", "--description=UNOS: the return from the SD standby to the eMMC",
            # Unloaded once the script ended, also after a failure: its result is in the folder.
            "--collect", "--quiet", f"--property=TimeoutStopSec={UNIT_STOP_SECONDS}", "--setenv=HOME=/root",
            "/bin/sh", f"{RUN_DIR}/{SCRIPT}", RUN_DIR,
        ], what=f"The unit {UNIT} did not start", timeout=60)
    except StandbyFailed:
        forget_result()
        raise
    return ops.plan


def unit_runs(ops: StandbyHost | None = None) -> bool:
    """Whether the return's unit runs, or its folder is there without a result (True when that cannot be told)."""
    ops = ops or StandbyHost()
    shown = ops.read(["systemctl", "show", "--property=ActiveState", "--value", f"{UNIT}.service"])
    if not shown.ok:
        return True
    if shown.stdout.strip() in ("active", "activating", "deactivating", "reloading"):
        return True
    return False


def read_result(ops: StandbyHost | None = None) -> dict[str, str] | None:
    """How a return's unit ended in this boot (its result in RAM): failed, its step and why; None without one
    (no return ran, or one runs)."""
    ops = ops or StandbyHost()
    text = ops.read_file(RESULT)
    if text is None:
        return None
    lines = (text.split("\n") + ["", "", ""])[:3]
    outcome, step, message = (line.strip() for line in lines)
    if outcome not in ("failed", "returned"):
        return {"outcome": "failed", "step": "", "message": f"The unit {UNIT} wrote an unknown result"}
    return {"outcome": outcome, "step": step, "message": message}


def forget_result() -> None:
    """The unit's folder goes (blocking); never raises."""
    from .hostops import StandbyError

    # Never into the eMMC's root, should the unit have left it mounted there.
    try:
        StandbyHost().change(["rm", "-rf", "--one-file-system", "--", RUN_DIR], what=f"{RUN_DIR} cannot be removed")
    except StandbyError as e:
        import logging

        logging.getLogger(__name__).warning("%s", e)

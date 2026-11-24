"""The sync of the SD card standby: a new generation of the system's copy, made current only once
complete (ADR-0471).

The card's btrfs top level holds the generations ``@sys-<n>``, each a copy of the eMMC's root
filesystem (``rsync -aHAX --delete --one-file-system`` of ``/``, without what EXCLUDES and the mount
points leave out, which rsync neither copies nor deletes), UNOS's compose volumes under
``/var/lib/unos-standby/volumes`` (the database copied by SQLite's backup API first), ``docker save``
archives of UNOS's images under ``/var/lib/unos-standby/images`` and the fix-ups: the card's fstab,
the eMMC's own fstab lines kept for the return, ``unos-standby.service`` with its script,
``unos.service`` masked, Docker's and containerd's settings with their roots on the card and without
the drop-ins that wait for the pool (ADR-0488), and the mount points, the pools' and the standby's
immutable. The card's FAT partition holds the boot folders ``unos/boot-<n>/`` (what the eMMC's boot
script loads, links resolved, of the device trees only the board's and its overlays, and an
armbianEnv.txt naming the card's btrfs; on an extlinux board the files of the eMMC's extlinux label and
a boot.txt that names them and the card's command line, ADR-0489) and the generation files.

A sync first compares, with reads only: rsync's dry run of the root and of each volume, a digest of
the database's content, the images' ids, the boot files and the fix-ups. When nothing changed it
writes nothing; what only follows (ROOT_FOLLOWERS, VOLUME_FOLLOWERS: what changes at every boot or
start, and the audit log) is no change on its own and comes along with any other. Otherwise it snapshots the current generation, copies only what changed (``--inplace
--no-whole-file``: the changed blocks), writes ``unos/next.txt``, renames ``current.txt`` to
``previous.txt`` and ``next.txt`` to ``current.txt``, and only then deletes the generation before the
previous one. Any rsync status but 0 fails the sync before the switch, so a sync cut off, or reading
a failing eMMC, leaves the previous generation current.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import sqlite3
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import host, paths
from ..config import get_settings
from . import bootscript
from .hostops import BOOT_DIR, CARD_DIR, STANDBY_DIR, StandbyError, StandbyFailed, StandbyHost

logger = logging.getLogger(__name__)

GENERATION_PREFIX = "@sys-"
UNOS_DIR = f"{BOOT_DIR}/unos"
CURRENT = f"{UNOS_DIR}/current.txt"
PREVIOUS = f"{UNOS_DIR}/previous.txt"
NEXT = f"{UNOS_DIR}/next.txt"
MARK = f"{UNOS_DIR}/emmc-failed"
DRILL = f"{UNOS_DIR}/drill.txt"
STATE_FILE = f"{CARD_DIR}/state.json"
# In a generation: the standby's own folder, its script and its unit.
LIB = "/var/lib/unos-standby"
SCRIPT_DIR = "/usr/local/lib/unos-standby"
STANDBY_SCRIPT = f"{SCRIPT_DIR}/unos-standby.sh"
UNIT_DIR = "/etc/systemd/system"
STANDBY_UNIT = "unos-standby.service"
FSTAB = get_settings().fstab_path
DATABASE = paths.DATABASE
DATABASE_NAME = DATABASE.name
SUPERVISOR_CONTAINER = "unos-supervisor"  # the compose file's container_name
WEBUI_CONTAINER = get_settings().webui_container
HOST_SCRIPTS = Path(__file__).resolve().parent / "host"

# What the copy of the root leaves out (ADR-0471): Docker's and containerd's data, the logs, caches
# and temporary files, apt's lists, the update's database copies, the standby's own folder and the
# fix-ups, which are never copied in either direction.
EXCLUDES = (
    "/var/lib/docker", "/var/lib/containerd", "/var/log*", "/var/cache", "/var/tmp", "/var/lib/apt/lists",
    "/var/lib/unos/backups", LIB, SCRIPT_DIR, FSTAB, f"{UNIT_DIR}/unos.service", f"{UNIT_DIR}/{STANDBY_UNIT}",
    f"{UNIT_DIR}/multi-user.target.wants/{STANDBY_UNIT}",
    # The eMMC's own units of the standby, which a sync may rewrite and which never run on the card.
    *(path for unit in ("unos-standby-boot-ok.service", "unos-standby-watch.service")
      for path in (f"{UNIT_DIR}/{unit}", f"{UNIT_DIR}/multi-user.target.wants/{unit}")),
)
# State that the host rewrites on its own while it idles and that the card's system makes anew
# (ADR-0481): without it, no sync would ever find the root unchanged.
VOLATILE = (
    "/var/lib/systemd/timesync", "/var/lib/systemd/random-seed", "/var/lib/systemd/timers", "/var/lib/systemd/pstore/*",
    "/var/lib/logrotate", "/var/lib/NetworkManager/timestamps", "/var/lib/apt/daily_lock", "/var/lock/*",
    "/root/.docker/buildx", "/root/.docker/.token_seed",
)
# What the host rewrites at every boot (measured on the BPI-W3: Armbian's services save the clock, the
# LEDs, the sound state and rewrite logrotate's and journald's settings; NFS its state), the audit log
# of UNOS's data volume, which Sync now itself appends to, the SMART history, to which the monitor adds
# a reading at every start of the supervisor, and the configuration that Caddy saves at every start,
# which it makes again from its Caddyfile: a change of these alone is no reason
# for a new generation, and they are copied with any other change (ADR-0488). Measured on the BPI-F3 too:
# Armbian touches its release file, systemd saves the backlight and rfkill states, and smartd its
# attribute state (ADR-0489). Paths as rsync itemizes them; a folder ends with /.
ROOT_FOLLOWERS = (
    "etc/fake-hwclock.data", "etc/armbian-leds.conf", "etc/logrotate.conf", "etc/logrotate.d/",
    "etc/systemd/journald.conf", "etc/armbianmonitor/", "var/lib/alsa/", "var/lib/nfs/",
    "etc/armbian-release", "var/lib/systemd/backlight/", "var/lib/systemd/rfkill/", "var/lib/smartmontools/",
)
VOLUME_FOLLOWERS = {"unos-data": ("audit/", "smart_history.json"), "caddy-config": ("caddy/autosave.json",)}
PARTS = ("system", "volumes", "database", "images", "boot", "fixups")
# Directories' times are not compared nor copied: the fix-ups' files change them in the copy, and
# what a directory holds is compared file by file.
ROOT_RSYNC = ("rsync", "-aHAX", "--numeric-ids", "--one-file-system", "--delete", "--omit-dir-times")
COPY_OPTIONS = ("--inplace", "--no-whole-file")
COMPARE_OPTIONS = ("--dry-run", "--itemize-changes")
# What the eMMC's boot script loads from its /boot, links resolved on the card's FAT.
BOOT_FILES = ("Image", "uInitrd", "dtb", "boot.scr", "boot.bmp", "fixup.scr", "overlay-user")
_FDTFILE = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9._-]+\.dtb")
# The boot files on the card's FAT: links resolved, the times kept (2 s steps on FAT), no owners.
BOOT_RSYNC = ("rsync", "-rLt", "--modify-window=1")
# The file of an extlinux board's boot folder that names its files and the card's command line (ADR-0489).
BOOT_TXT = "boot.txt"
LOADER_VERSION = f"{CARD_DIR}/loader-version"
BOOT_SCR = f"{BOOT_DIR}/boot.scr"
BOOT_OK_UNIT = "unos-standby-boot-ok.service"
BOOT_OK_SCRIPT = f"{SCRIPT_DIR}/unos-standby-boot-ok.sh"
# The eMMC's system: the switch to the card when the pool that holds Docker fails (ADR-0488).
WATCH_UNIT = "unos-standby-watch.service"
WATCH_SCRIPT = f"{SCRIPT_DIR}/unos-standby-watch.sh"
# Docker's and containerd's settings on the eMMC name their roots on a pool, and drop-ins keep them
# down while it is not mounted (ADR-0486); on the card's root they keep their data on the card, at the
# defaults below, and start without the pool (ADR-0488). Rendered by the fix-ups, never copied.
DOCKER_CONFIG = "/etc/docker/daemon.json"
CONTAINERD_CONFIG = "/etc/containerd/config.toml"
CARD_DOCKER_ROOT = "/var/lib/docker"
CARD_CONTAINERD_ROOT = "/var/lib/containerd"
DROPIN_DIRS = (f"{UNIT_DIR}/docker.service.d", f"{UNIT_DIR}/containerd.service.d")
_TOML_ROOT = re.compile(r"\s*root\s*=")
# The first changed paths of a sync, for its record.
SAMPLE = 10
RSYNC_TIMEOUT = 2 * 3600
SAVE_TIMEOUT = 3600
# Mount points whose folder needs these modes on the card; any other gets 0755.
_MOUNTPOINT_MODES = {"/proc": "0555", "/sys": "0555", "/tmp": "1777"}
_PLAIN_PATH = re.compile(r"/[A-Za-z0-9._/-]*")
_GENERATION_NAME = re.compile(rf"{re.escape(GENERATION_PREFIX)}([0-9]+)")


@dataclass
class Outcome:
    """What a sync did: wrote a new generation, found nothing changed, or failed."""

    result: str  # wrote, unchanged
    generation: int | None
    parts: list[str] = field(default_factory=list)
    mib: float | None = None
    plan: list[str] = field(default_factory=list)
    # The first paths that the comparison of the system found changed.
    changes: list[str] = field(default_factory=list)


@dataclass
class _Context:
    card_source: str
    card_disk: str
    card_uuid: str
    mounts: list[Any]
    fstab: str
    project: str
    workdir: str
    images: dict[str, str]  # short name -> image id, of the compose file's image names
    image_names: dict[str, str]  # short name -> image name
    volumes: dict[str, str]  # short name -> mount point
    current: tuple[int, int, str] | None
    previous: tuple[int, int, str] | None
    existing: list[int]
    docker_root: str
    # What names the eMMC's partitions in an fstab: UUID=, PARTUUID=, LABEL= and their paths.
    emmc_specs: set[str]
    # Docker's and containerd's settings as the card's root gets them (path -> text), and the drop-ins
    # that keep them down without the pool, which the card's root lacks.
    docker_files: dict[str, str]
    pool_dropins: list[str]
    facts: Any  # conditions.Facts: the profile and the eMMC's boot partition
    # The eMMC's extlinux label on an extlinux board (ADR-0489); None for Armbian's boot.scr.
    label: bootscript.ExtlinuxLabel | None = None


def generation_path(number: int) -> str:
    return f"{CARD_DIR}/{GENERATION_PREFIX}{number}"


def read_generations(ops: StandbyHost) -> tuple[tuple[int, int, str] | None, tuple[int, int, str] | None]:
    current = ops.read_file(CURRENT)
    previous = ops.read_file(PREVIOUS)
    return (bootscript.parse_generation(current) if current else None,
            bootscript.parse_generation(previous) if previous else None)


# --- what the fix-ups render ----------------------------------------------------------------------


def card_fstab(emmc_fstab: str, card_uuid: str, emmc_specs: set[str] | None = None) -> tuple[str, str]:
    """The card's fstab and the eMMC's own lines: those of the root, of /boot, of swap and of any
    other filesystem of the eMMC (``emmc_specs``) give way to the card's root line; every other line
    (the pools', the standby's own) stays."""
    root_line = f"UUID={card_uuid} / btrfs defaults,noatime,compress=zstd:1 0 0\n"
    out: list[str] = []
    own: list[str] = []
    placed = False
    for line in emmc_fstab.splitlines(keepends=True):
        fields = line.split()
        if fields and not fields[0].startswith("#") and len(fields) >= 3 and (
                fields[1] in ("/", "/boot", "/boot/efi", "/boot/firmware") or fields[2] == "swap"
                or fields[0] in (emmc_specs or set())):
            own.append(line if line.endswith("\n") else line + "\n")
            if fields[1] == "/" and not placed:
                out.append(root_line)
                placed = True
            continue
        out.append(line if line.endswith("\n") else line + "\n")
    if not placed:
        out.insert(0, root_line)
    return "".join(out), "".join(own)


def card_boot_text(label: bootscript.ExtlinuxLabel, prefix: str, card_uuid: str) -> str:
    """boot.txt of an extlinux board's boot folder: the label's files relative to the eMMC's boot folder
    ``prefix``, as the boot folder holds them, and its append line with root= naming the card's btrfs,
    rootfstype=btrfs and no rootflags=, which the chooser adds with the generation's subvolume (ADR-0489)."""
    from .conditions import boot_relative

    words: list[str] = []
    for word in label.append.split():
        key = word.split("=", 1)[0]
        if key == "root" and f"root=UUID={card_uuid}" not in words:
            words.append(f"root=UUID={card_uuid}")
        elif key not in ("root", "rootfstype", "rootflags"):
            words.append(word)
    if f"root=UUID={card_uuid}" not in words:
        words.insert(0, f"root=UUID={card_uuid}")
    words.append("rootfstype=btrfs")
    kernel, initrd, fdt = (boot_relative(path, prefix) for path in (label.kernel, label.initrd, label.fdt))
    return bootscript.boot_text(kernel, initrd, fdt, " ".join(words))


def extlinux_sources(label: bootscript.ExtlinuxLabel, prefix: str) -> tuple[list[str], list[str]]:
    """What a boot folder copies of the eMMC's /boot for an extlinux label, and rsync's filter: a file at
    the top whole, of a folder (dtb/) only the file the label names."""
    from .conditions import boot_relative

    sources: list[str] = []
    includes: list[str] = []
    folders: list[str] = []
    for path in (label.kernel, label.initrd, label.fdt):
        relative = boot_relative(path, prefix)
        parts = relative.split("/")
        sources.append(f"/boot/{parts[0]}")
        if len(parts) > 1:
            includes += [f"--include=/{'/'.join(parts[:depth])}/" for depth in range(1, len(parts))]
            includes.append(f"--include=/{relative}")
            folders.append(parts[0])
    return (list(dict.fromkeys(sources)),
            [*dict.fromkeys(includes), *(f"--exclude=/{folder}/**" for folder in dict.fromkeys(folders))])


def card_armbian_env(emmc_env: str, card_uuid: str) -> str:
    """armbianEnv.txt of a boot folder: the eMMC's, with rootdev and rootfstype naming the card's btrfs."""
    lines, seen = [], set()
    for line in emmc_env.splitlines():
        key = line.split("=", 1)[0].strip()
        if key == "rootdev":
            line = f"rootdev=UUID={card_uuid}"
        elif key == "rootfstype":
            line = "rootfstype=btrfs"
        seen.add(key)
        lines.append(line)
    if "rootdev" not in seen:
        lines.append(f"rootdev=UUID={card_uuid}")
    if "rootfstype" not in seen:
        lines.append("rootfstype=btrfs")
    return "\n".join(lines) + "\n"


def standby_unit(workdir: str, project: str) -> str:
    return f"""[Unit]
Description=UNOS on the SD card standby: keep the card's root, load UNOS's images, seed its volumes, start it
ConditionKernelCommandLine=unos.root=card
Wants=docker.service network-online.target
After=docker.service network-online.target
RequiresMountsFor={BOOT_DIR} {CARD_DIR}

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=UNOS_COMPOSE_DIR={workdir} UNOS_COMPOSE_PROJECT={project}
ExecStart={STANDBY_SCRIPT}
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
"""


def card_daemon_json(text: str) -> str:
    """daemon.json of the card's root: the eMMC's, with Docker's data root on the card."""
    try:
        config = json.loads(text)
    except json.JSONDecodeError as e:
        raise StandbyFailed(f"{DOCKER_CONFIG} cannot be read: {e}") from None
    if not isinstance(config, dict):
        raise StandbyFailed(f"{DOCKER_CONFIG} holds no JSON object")
    config["data-root"] = CARD_DOCKER_ROOT
    return json.dumps(config, indent=2, sort_keys=True) + "\n"


def card_containerd_config(text: str) -> str:
    """config.toml of the card's root: the eMMC's, with containerd's root (its top-level key) on the card."""
    lines, top = [], True
    for line in text.splitlines():
        if line.strip().startswith("["):
            top = False
        if top and _TOML_ROOT.match(line):
            line = f'root = "{CARD_CONTAINERD_ROOT}"'
        lines.append(line)
    return "\n".join(lines) + "\n"


def host_script(name: str) -> str:
    return (HOST_SCRIPTS / name).read_text(encoding="utf-8")


def mountpoints(mounts: list[Any], fstab: str) -> list[str]:
    """The folders on the root filesystem that something mounts: the mounts on it now and the fstab's
    mount points, without those below what the copy leaves out. Excluded from the copy, made by the
    fix-ups."""
    from .conditions import mount_of

    root = [mount for mount in mounts if mount.target == "/"]
    found = set()
    for mount in mounts:
        if mount.target == "/" or not _PLAIN_PATH.fullmatch(mount.target):
            continue
        others = [other for other in mounts if other is not mount]
        if root and mount_of(others, mount.target) is root[-1]:
            found.add(mount.target)
    for line in fstab.splitlines():
        fields = line.split()
        if (fields and not fields[0].startswith("#") and len(fields) >= 3 and fields[1] != "/"
                and fields[2] != "swap" and _PLAIN_PATH.fullmatch(fields[1])):
            found.add(fields[1].rstrip("/"))
    excluded = [pattern.rstrip("*") for pattern in EXCLUDES]
    return sorted(path for path in found if not any(path == ex or path.startswith(ex.rstrip("/") + "/")
                                                    for ex in excluded))


def immutable_mountpoints(points: list[str]) -> list[str]:
    """Those of ``points`` that stay immutable while nothing is mounted on them: the pools' and the
    standby's (ADR-0034, ADR-0471)."""
    pools = get_settings().pools_base_path.rstrip("/") + "/"
    return [point for point in points if point.startswith(pools) or point.startswith(STANDBY_DIR + "/")]


def fixup_digest(fstab_text: str, own_lines: str, unit: str, script: str, points: list[str],
                 docker_files: dict[str, str] | None = None, pool_dropins: list[str] | None = None) -> str:
    manifest = json.dumps([fstab_text, own_lines, unit, script, points, docker_files or {}, pool_dropins or []],
                          sort_keys=True)
    return hashlib.sha256(manifest.encode()).hexdigest()


# --- the database ------------------------------------------------------------------------------------


def staged_database() -> Path:
    return Path(tempfile.gettempdir()) / "unos-standby" / DATABASE_NAME


def stage_database() -> tuple[Path, str]:
    """A copy of the database by SQLite's backup API in the supervisor's TMPDIR (RAM), and a digest of
    its content, so that an unchanged database is not copied again."""
    staged = staged_database()
    staged.parent.mkdir(mode=0o700, exist_ok=True)
    staged.unlink(missing_ok=True)
    from ..backup.router import copy_database

    copy_database(DATABASE, staged)
    digest = hashlib.sha256()
    connection = sqlite3.connect(f"file:{staged}?mode=ro", uri=True)
    try:
        for statement in connection.iterdump():
            digest.update(statement.encode())
            digest.update(b"\n")
    finally:
        connection.close()
    return staged, digest.hexdigest()


# --- the context -------------------------------------------------------------------------------------


def _json(ops: StandbyHost, cmd: list[str], what: str) -> Any:
    result = ops.read(cmd, timeout=60)
    if not result.ok:
        raise StandbyFailed(f"{what}: {result.stderr.strip() or f'exit status {result.returncode}'}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise StandbyFailed(f"{what}: {e}") from None


def _context(ops: StandbyHost) -> _Context:
    from .conditions import MOUNTINFO, _parse_mountinfo

    mountinfo = ops.read_file(MOUNTINFO)
    if mountinfo is None:
        raise StandbyFailed("The host's mount table cannot be read")
    mounts = _parse_mountinfo(mountinfo)
    card = [mount for mount in mounts if mount.target == CARD_DIR]
    boot = [mount for mount in mounts if mount.target == BOOT_DIR]
    if not card or not boot:
        raise StandbyFailed(f"The standby's card is not mounted at {CARD_DIR} and {BOOT_DIR}")
    source = card[-1].source
    match = re.fullmatch(r"/dev/(mmcblk[0-9]+)p[0-9]+", source)
    if match is None:
        raise StandbyFailed(f"{CARD_DIR} is mounted from {source}, not from an SD card")
    uuid = ops.read(["blkid", "-s", "UUID", "-o", "value", source]).stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", uuid):
        raise StandbyFailed(f"The UUID of {source} cannot be read")
    fstab = ops.read_file(FSTAB)
    if fstab is None:
        raise StandbyFailed(f"{FSTAB} cannot be read")
    containers = _json(ops, ["docker", "inspect", SUPERVISOR_CONTAINER, WEBUI_CONTAINER], "UNOS's containers")
    labels = (containers[0].get("Config") or {}).get("Labels") or {}
    project = labels.get("com.docker.compose.project") or ""
    workdir = labels.get("com.docker.compose.project.working_dir") or ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) or not _PLAIN_PATH.fullmatch(workdir):
        raise StandbyFailed("UNOS's compose project cannot be read from its containers")
    image_names: dict[str, str] = {}
    images: dict[str, str] = {}
    for container in containers:
        config = container.get("Config") or {}
        service = (config.get("Labels") or {}).get("com.docker.compose.service") or ""
        name = config.get("Image") or ""
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", service) or not re.fullmatch(r"[a-z0-9][a-z0-9_./:-]*", name):
            raise StandbyFailed("The image names of UNOS's containers cannot be read")
        image_id = ops.read(["docker", "image", "inspect", "--format", "{{.Id}}", name]).stdout.strip()
        if not image_id.startswith("sha256:"):
            raise StandbyFailed(f"The image {name} cannot be read")
        image_names[service], images[service] = name, image_id
    listed = ops.read(["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}",
                       "--format", "{{.Name}}"])
    if not listed.ok:
        raise StandbyFailed(f"UNOS's volumes cannot be listed: {listed.stderr.strip()}")
    volumes = {}
    for name in listed.stdout.split():
        short = name.removeprefix(f"{project}_")
        mountpoint = ops.read(["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", name]).stdout.strip()
        if not _PLAIN_PATH.fullmatch(mountpoint) or not re.fullmatch(r"[A-Za-z0-9._-]+", short):
            raise StandbyFailed(f"The volume {name} cannot be read")
        volumes[short] = mountpoint
    if DATABASE.parent.name and "unos-data" not in volumes:
        raise StandbyFailed("UNOS's data volume unos-data is not among its compose volumes")
    current, previous = read_generations(ops)
    listing = ops.read(["ls", "-1", "--", CARD_DIR])
    if not listing.ok:
        raise StandbyFailed(f"{CARD_DIR} cannot be read: {listing.stderr.strip()}")
    existing = sorted(int(m.group(1)) for name in listing.stdout.split() if (m := _GENERATION_NAME.fullmatch(name)))
    docker_root = ops.read(["docker", "info", "--format", "{{.DockerRootDir}}"]).stdout.strip()
    if not _PLAIN_PATH.fullmatch(docker_root):
        raise StandbyFailed("Docker's data root cannot be read")
    from .conditions import Facts, _read_profile, _read_root

    facts = Facts()
    _read_profile(ops, facts)
    _read_root(ops, facts)
    if facts.profile is None or not facts.root_ok or facts.root_disk is None:
        raise StandbyFailed(f"The eMMC's system cannot be read: {facts.profile_detail}; {facts.root_detail}")
    tree = _json(ops, ["lsblk", "-J", "-o", "PATH,UUID,PARTUUID,LABEL", f"/dev/{facts.root_disk}"], "The eMMC's partitions")
    specs: set[str] = set()
    pending = list(tree.get("blockdevices") or [])
    while pending:
        node = pending.pop()
        pending.extend(node.get("children") or [])
        for key, prefix in (("uuid", "UUID="), ("partuuid", "PARTUUID="), ("label", "LABEL=")):
            if node.get(key):
                specs.add(f"{prefix}{node[key]}")
        if node.get("path"):
            specs.add(str(node["path"]))
    label = None
    if facts.profile.emmc_boot == "extlinux":
        from .conditions import read_emmc_label

        try:
            label = read_emmc_label(ops, facts)
        except StandbyError as e:
            raise StandbyFailed(str(e)) from None
    docker_files: dict[str, str] = {}
    for path, render in ((DOCKER_CONFIG, card_daemon_json), (CONTAINERD_CONFIG, card_containerd_config)):
        text = ops.read_file(path)
        if text is not None:
            docker_files[path] = render(text)
    return _Context(card_source=source, card_disk=match.group(1), card_uuid=uuid.lower(), mounts=mounts,
                    fstab=fstab, project=project, workdir=workdir, images=images, image_names=image_names,
                    volumes=volumes, current=current, previous=previous, existing=existing,
                    docker_root=docker_root.rstrip("/") or "/", emmc_specs=specs, facts=facts,
                    docker_files=docker_files, pool_dropins=_pool_dropins(ops), label=label)


def _pool_dropins(ops: StandbyHost) -> list[str]:
    """The drop-ins of docker.service and containerd.service that wait for their roots' mounts
    (RequiresMountsFor=, ADR-0486): the card's root runs both without the pool."""
    found = []
    for folder in DROPIN_DIRS:
        listing = ops.read(["ls", "-1", "--", folder])
        for name in listing.stdout.split() if listing.ok else []:
            path = f"{folder}/{name}"
            if name.endswith(".conf") and _PLAIN_PATH.fullmatch(path) and "RequiresMountsFor=" in (ops.read_file(path) or ""):
                found.append(path)
    return sorted(found)


def _swap_files(ops: StandbyHost) -> list[str]:
    text = ops.read_file("/proc/swaps") or ""
    return [line.split()[0] for line in text.splitlines()[1:]
            if len(line.split()) > 1 and line.split()[1] == "file" and _PLAIN_PATH.fullmatch(line.split()[0])]


def _follows(line: str, followers: tuple[str, ...]) -> bool:
    """Whether a line of rsync's itemized changes names one of ``followers``."""
    path = line.split(None, 1)[-1].split(" -> ", 1)[0].rstrip("/")
    return any(path == follower.rstrip("/") or (follower.endswith("/") and path.startswith(follower))
               for follower in followers)


def _changes(result: Any, what: str) -> list[str]:
    """The lines of rsync's dry run: what it would change."""
    if not result.ok:
        raise StandbyFailed(f"{what}: rsync ended with exit status {result.returncode}: "
                            f"{(result.stderr.strip().splitlines() or [''])[-1]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _boot_sources(ops: StandbyHost) -> list[str]:
    return [f"/boot/{name}" for name in BOOT_FILES if ops.exists(f"/boot/{name}")]


def boot_filters(emmc_env: str) -> list[str]:
    """rsync's filter of the boot folder: of /boot/dtb only the board's device tree (armbianEnv.txt's
    fdtfile) and the overlays beside it, which Armbian's boot script loads. /boot/dtb holds every
    board's of the vendor (118 MiB on the W3), and three boot folders, which a new kernel needs beside
    the current and the previous one, must fit the card's 512 MiB FAT partition (ADR-0488). Without a
    plain fdtfile, the whole folder."""
    values = dict(line.split("=", 1) for line in emmc_env.splitlines() if "=" in line)
    fdtfile = values.get("fdtfile", "").strip()
    if not _FDTFILE.fullmatch(fdtfile):
        return []
    vendor = fdtfile.split("/", 1)[0]
    return ["--include=/dtb/", f"--include=/dtb/{vendor}/", f"--include=/dtb/{fdtfile}",
            f"--include=/dtb/{vendor}/overlay/***", "--exclude=/dtb/**"]


def _sectors_written(disk: str) -> int | None:
    stat = host.block_device_attribute(disk, "stat")
    fields = (stat or "").split()
    return int(fields[6]) if len(fields) > 6 and fields[6].isdigit() else None


# --- the sync ----------------------------------------------------------------------------------------


def _refresh_card_files(ops: StandbyHost, context: _Context, *, apply: bool) -> list[str]:
    """What the card and the eMMC's system hold besides the generations, rewritten only when it
    differs: the board's loader after its package's version changed, the eMMC's units of the standby with
    their scripts (an update of UNOS may change them), and the chooser boot.scr after them. Without
    ``apply`` it only reads, and names what differs."""
    from .card import emmc_units

    facts, profile = context.facts, context.facts.profile
    parts: list[str] = []
    from .conditions import loader_version

    version = loader_version(ops, profile)
    stored = (ops.read_file(LOADER_VERSION) or "").strip()
    if version and version != stored and not apply:
        parts.append("loader")
    elif version and version != stored:
        from .conditions import _read_loader

        _read_loader(ops, facts)
        if not facts.loader_ok:
            raise StandbyFailed(f"The new loader cannot be written: {facts.loader_detail}")
        from .card import write_loader

        write_loader(ops, profile, f"/dev/{context.card_disk}")
        ops.write_file(LOADER_VERSION, version + "\n", what="The loader's version")
        parts.append("loader")
    enable = []
    for path, text, mode in emmc_units(profile):
        before = ops.read_file(path)
        name = path.rsplit("/", 1)[1]
        # A unit is enabled when it was missing, and the one that sets the card's counter back also when it
        # is there but not enabled (a Set up that stopped before it enabled it, ADR-0490).
        disabled = (before is None and name.endswith(".service")) or (
            name == BOOT_OK_UNIT and not ops.read(["systemctl", "is-enabled", "--quiet", name]).ok)
        if before != text or disabled:
            if apply:
                if before != text:
                    ops.write_file(path, text, what=f"{path} of the eMMC's system", mode=mode)
                if disabled:
                    enable.append(name)
            if "units" not in parts:
                parts.append("units")
    if "units" in parts and apply:
        ops.change(["systemctl", "daemon-reload"], what="The standby's units")
        if enable:
            ops.change(["systemctl", "enable", *enable], what="The standby's units")
    # The chooser after the units that set its tries counter back (ADR-0490).
    image = bootscript.script_image(bootscript.chooser_text(profile, emmc_part=facts.boot_number,
                                                           emmc_prefix=facts.boot_prefix, emmc_label=context.label))
    shown = ops.read(["sha256sum", "--", BOOT_SCR]).stdout.split()
    if not shown or shown[0] != hashlib.sha256(image).hexdigest():
        if apply:
            ops.write_file(BOOT_SCR, image, what="The chooser boot.scr")
        parts.append("chooser")
    return parts


def sync(*, dry_run: bool = False, force: bool = False, clean_docker: bool = False,
         stop: threading.Event | None = None) -> Outcome:
    """One sync (blocking: run it in a worker thread). ``force`` writes a new generation even when
    nothing changed (Set up the standby, Discard the standby's changes); ``clean_docker`` empties the
    copy's Docker data root (after a standby that ran). StandbyFailed when it failed: then the previous
    generation stays current."""
    ops = StandbyHost(dry_run=dry_run, stop=stop)
    context = _context(ops)
    staged: Path | None = None
    try:
        staged, db_digest = stage_database()
        return _sync(ops, context, staged, db_digest, dry_run=dry_run, force=force, clean_docker=clean_docker)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _sync(ops: StandbyHost, context: _Context, staged: Path, db_digest: str, *, dry_run: bool, force: bool,
          clean_docker: bool) -> Outcome:
    points = mountpoints(context.mounts, context.fstab)
    swaps = _swap_files(ops)
    docker = [context.docker_root] if context.docker_root not in ("/", "/var/lib/docker") else []
    excludes = [f"--exclude={pattern}" for pattern in (*EXCLUDES, *VOLATILE, *docker, *points, *swaps, DOCKER_CONFIG,
                                                       CONTAINERD_CONFIG, *context.pool_dropins)]
    fstab_text, own_lines = card_fstab(context.fstab, context.card_uuid, context.emmc_specs)
    unit = standby_unit(context.workdir, context.project)
    script = host_script("unos-standby.sh")
    digest = fixup_digest(fstab_text, own_lines, unit, script, points, context.docker_files, context.pool_dropins)
    boot_sources, boot_rsync, boot_file = _boot_folder(ops, context)

    card_parts = _refresh_card_files(ops, context, apply=False)
    current = context.current
    parts: list[str] = []
    changes: list[str] = []
    if current is not None and force:
        # A new generation of everything, with the current boot folder when /boot did not change: a
        # third folder might not fit the FAT partition.
        parts = [part for part in PARTS if part != "boot" or _boot_changed(ops, boot_rsync, boot_sources, current,
                                                                            boot_file)]
    elif current is None:
        parts = list(PARTS)
    else:
        gen = generation_path(current[0])
        found = _changes(ops.long_read([*ROOT_RSYNC, *COMPARE_OPTIONS, *excludes, "/", f"{gen}/"],
                                       timeout=RSYNC_TIMEOUT), "The comparison of the system's copy")
        changes = [line for line in found if not _follows(line, ROOT_FOLLOWERS)]
        # What only follows a change: copied when another part is written.
        following = ["system"] if found and not changes else []
        if changes:
            parts.append("system")
        for short, mountpoint in context.volumes.items():
            lines = _changes(ops.long_read(_volume_rsync(short, mountpoint, gen, compare=True), timeout=RSYNC_TIMEOUT),
                             f"The comparison of the volume {short}")
            real = [line for line in lines if not _follows(line, VOLUME_FOLLOWERS.get(short, ()))]
            changes += [f"{short}: {line}" for line in real]
            if real:
                parts.append("volumes")
                break
            if lines and "volumes" not in following:
                following.append("volumes")
        if (ops.read_file(f"{gen}{LIB}/volumes/unos-data/{DATABASE_NAME}.sha256") or "").strip() != db_digest:
            parts.append("database")
        for short, image_id in context.images.items():
            if (ops.read_file(f"{gen}{LIB}/images/{short}.id") or "").strip() != image_id:
                parts.append("images")
                break
        if _boot_changed(ops, boot_rsync, boot_sources, current, boot_file):
            parts.append("boot")
        if (ops.read_file(f"{gen}{LIB}/fixups.sha256") or "").strip() != digest:
            parts.append("fixups")
        if parts:
            parts = sorted({*parts, *following}, key=PARTS.index)
    if not parts and not card_parts:
        return Outcome(result="unchanged", generation=current[0] if current else None)
    before = None
    if not dry_run:
        ops.change(["sync"], what="The flush before the sync")
        before = _sectors_written(context.card_disk)
    if card_parts:
        _refresh_card_files(ops, context, apply=True)
    if not parts:
        return Outcome(result="wrote", generation=current[0] if current else None, parts=card_parts,
                       mib=_mib(ops, context, before, dry_run), plan=ops.plan)

    number = max([*context.existing, current[0] if current else 0]) + 1
    keep = {gen[0] for gen in (current, context.previous) if gen}
    for stale in context.existing:  # a generation that an interrupted sync left
        if stale not in keep:
            ops.change(["btrfs", "subvolume", "delete", generation_path(stale)], what="The removal of an old generation")
    new = generation_path(number)
    if current is None:
        ops.change(["btrfs", "subvolume", "create", new], what="The new generation")
    else:
        ops.change(["btrfs", "subvolume", "snapshot", generation_path(current[0]), new], what="The new generation")
    new_bootdir: str | None = None
    committed = False
    try:
        if "system" in parts:
            _free_old_mountpoints(ops, new, points)
            ops.long_change([*ROOT_RSYNC, *COPY_OPTIONS, *excludes, "/", f"{new}/"], what="The copy of the system",
                            timeout=RSYNC_TIMEOUT)
        ops.change(["mkdir", "-p", f"{new}{LIB}/volumes", f"{new}{LIB}/images"], what="The standby's folder")
        if "volumes" in parts:
            for short, mountpoint in context.volumes.items():
                ops.change(["mkdir", "-p", f"{new}{LIB}/volumes/{short}"], what=f"The copy of the volume {short}")
                ops.long_change(_volume_rsync(short, mountpoint, new, compare=False), what=f"The copy of the volume {short}",
                                timeout=RSYNC_TIMEOUT)
        if "database" in parts:
            target = f"{new}{LIB}/volumes/unos-data"
            ops.change(["mkdir", "-p", target], what="The copy of the database")
            ops.long_change(["rsync", *COPY_OPTIONS, host.container_path(str(staged)), f"{target}/{DATABASE_NAME}"],
                            what="The copy of the database", timeout=RSYNC_TIMEOUT)
            ops.write_file(f"{target}/{DATABASE_NAME}.sha256", db_digest + "\n", what="The copy of the database")
        if "images" in parts:
            for short, image_id in context.images.items():
                archive = f"{new}{LIB}/images/{short}.tar.gz"
                if (ops.read_file(f"{new}{LIB}/images/{short}.id") or "").strip() == image_id and not dry_run:
                    continue
                ops.long_change(["bash", "-c", 'set -o pipefail; docker save "$1" | gzip -1 > "$2.tmp" && mv -f "$2.tmp" "$2"',
                                 "unos-standby", context.image_names[short], archive],
                                what=f"The archive of the image {context.image_names[short]}", timeout=SAVE_TIMEOUT)
                ops.write_file(f"{new}{LIB}/images/{short}.id", image_id + "\n", what="The archive's image id")
        if clean_docker:
            ops.change(["rm", "-rf", "--", f"{new}/var/lib/docker", f"{new}/var/lib/containerd"],
                       what="The emptying of the copy's Docker data root")
        _fixups(ops, new, number, parts, fstab_text, own_lines, unit, script, points, digest, context)
        if "boot" in parts or current is None:
            new_bootdir = f"unos/boot-{number}/"
            ops.change(["mkdir", "-p", f"{BOOT_DIR}/{new_bootdir}"], what="The boot folder")
            ops.long_change([*boot_rsync, *boot_sources, f"{BOOT_DIR}/{new_bootdir}"],
                            what="The copy of the boot folder", timeout=RSYNC_TIMEOUT)
            ops.write_file(f"{BOOT_DIR}/{new_bootdir}{boot_file[0]}", boot_file[1], what=f"The boot folder's {boot_file[0]}")
            bootdir_name = new_bootdir
        else:
            bootdir_name = current[2] if current else f"unos/boot-{number}/"
        rootid = "0" if dry_run else ops.read(["btrfs", "inspect-internal", "rootid", new]).stdout.strip()
        if not rootid.isdigit() or (not dry_run and int(rootid) < 256):
            raise StandbyFailed(f"The subvolume id of {new} cannot be read")
        ops.change(["sync"], what="The flush of the new generation")
        # The switch: only a complete generation becomes current.
        ops.write_file(NEXT, bootscript.generation_text(number, int(rootid), bootdir_name), what="unos/next.txt")
        if current is not None:
            ops.change(["mv", "-f", CURRENT, PREVIOUS], what="The switch of the generation")
        ops.change(["mv", "-f", NEXT, CURRENT], what="The switch of the generation")
        committed = True
    except Exception:
        if not dry_run and not committed:
            _remove_quietly(new, new_bootdir)
        raise
    # The new generation is current: what follows only tidies, and a failure of it fails nothing. The
    # generation before the previous one goes; previous.txt names the one that was current.
    tidy = StandbyHost(dry_run=dry_run)
    try:
        tidy.change(["sync"], what="The flush of the switch")
        for old in context.existing:
            if current is not None and old not in (number, current[0]):
                tidy.change(["btrfs", "subvolume", "delete", generation_path(old)], what="The removal of an old generation")
        _drop_old_boot_folders(tidy, {bootdir_name, current[2] if current else ""})
    except StandbyError as e:
        logger.warning("The SD standby's old generations could not be removed: %s", e)
    ops.plan.extend(tidy.plan)
    return Outcome(result="wrote", generation=number, parts=[*card_parts, *parts], mib=_mib(tidy, context, before, dry_run),
                   plan=ops.plan, changes=changes[:SAMPLE])


def _boot_folder(ops: StandbyHost, context: _Context) -> tuple[list[str], list[str], tuple[str, str]]:
    """What a boot folder copies of the eMMC's /boot, its rsync with the filter, and the file it renders
    (name, text): armbianEnv.txt for Armbian's boot.scr, boot.txt for an extlinux label (ADR-0489)."""
    label = context.label
    if label is not None:
        prefix = context.facts.boot_prefix
        try:
            sources, filters = extlinux_sources(label, prefix)
            text = card_boot_text(label, prefix, context.card_uuid)
        except ValueError as e:
            raise StandbyFailed(f"The boot folder cannot be made: {e}") from None
        missing = [source for source in sources if not ops.exists(source)]
        if missing:
            raise StandbyFailed(f"The eMMC's /boot lacks {', '.join(missing)}")
        return sources, [*BOOT_RSYNC, *filters], (BOOT_TXT, text)
    emmc_env = ops.read_file("/boot/armbianEnv.txt")
    if emmc_env is None:
        raise StandbyFailed("/boot/armbianEnv.txt of the eMMC cannot be read")
    boot_sources = _boot_sources(ops)
    if not {"/boot/Image", "/boot/uInitrd", "/boot/dtb", "/boot/boot.scr"} <= set(boot_sources):
        raise StandbyFailed("The eMMC's /boot lacks Image, uInitrd, dtb or boot.scr")
    return boot_sources, [*BOOT_RSYNC, *boot_filters(emmc_env)], ("armbianEnv.txt", card_armbian_env(emmc_env, context.card_uuid))


def _boot_changed(ops: StandbyHost, boot_rsync: list[str], boot_sources: list[str], current: tuple[int, int, str],
                  boot_file: tuple[str, str]) -> bool:
    """Whether the eMMC's boot files differ from the current generation's boot folder."""
    bootdir = f"{BOOT_DIR}/{current[2]}"
    name, text = boot_file
    return bool(_changes(ops.long_read([*boot_rsync, "--delete", f"--exclude=/{name}", *COMPARE_OPTIONS,
                                        *boot_sources, bootdir], timeout=RSYNC_TIMEOUT), "The comparison of the boot folder")
                or ops.read_file(f"{bootdir}{name}") != text)


def _mib(ops: StandbyHost, context: _Context, before: int | None, dry_run: bool) -> float | None:
    """The MiB written to the card since ``before``, after a flush; None when it is not known."""
    if dry_run or before is None:
        return None
    try:
        ops.change(["sync"], what="The flush after the sync")
    except StandbyError:
        return None
    after = _sectors_written(context.card_disk)
    return round((after - before) * 512 / 1024 / 1024, 1) if after is not None else None


def _free_old_mountpoints(ops: StandbyHost, new: str, points: list[str]) -> None:
    """The immutable mount points of the copy that are mount points no more (a pool removed): free them,
    so that rsync can delete or update them."""
    old = (ops.read_file(f"{new}{LIB}/mountpoints") or "").split()
    for point in sorted(set(immutable_mountpoints(old)) - set(points)):
        if _PLAIN_PATH.fullmatch(point) and ops.exists(f"{new}{point}"):
            ops.change(["chattr", "-i", f"{new}{point}"], what=f"The old mount point {point}")


def _volume_rsync(short: str, mountpoint: str, gen: str, *, compare: bool) -> list[str]:
    """A volume's rsync. Folders' times are neither compared nor copied: the database's copy and its
    digest, written into the copy of unos-data, change that folder's time (ADR-0488)."""
    options = COMPARE_OPTIONS if compare else COPY_OPTIONS
    database = [f"--exclude=/{DATABASE_NAME}*"] if short == "unos-data" else []
    return ["rsync", "-aHAX", "--numeric-ids", "--delete", "--omit-dir-times", *options, *database, f"{mountpoint}/",
            f"{gen}{LIB}/volumes/{short}/"]


def _fixups(ops: StandbyHost, new: str, number: int, parts: list[str], fstab_text: str, own_lines: str, unit: str,
            script: str, points: list[str], digest: str, context: _Context) -> None:
    ops.write_file(f"{new}{LIB}/generation", f"{number}\n", what="The generation's number")
    ops.change(["rm", "-f", "--", f"{new}{LIB}/seeded"], what="The generation's volumes to seed")
    if "fixups" not in parts:
        return
    ops.write_file(f"{new}/etc/fstab", fstab_text, what="The card's fstab")
    ops.write_file(f"{new}{LIB}/emmc-fstab", own_lines, what="The eMMC's fstab lines")
    ops.change(["mkdir", "-p", f"{new}{SCRIPT_DIR}", f"{new}{UNIT_DIR}/multi-user.target.wants"], what="The standby's unit")
    ops.write_file(f"{new}{STANDBY_SCRIPT}", script, what="The standby's script", mode="0755")
    ops.write_file(f"{new}{UNIT_DIR}/{STANDBY_UNIT}", unit, what="The standby's unit")
    ops.change(["ln", "-sfn", f"{UNIT_DIR}/{STANDBY_UNIT}", f"{new}{UNIT_DIR}/multi-user.target.wants/{STANDBY_UNIT}"],
               what="The standby's unit")
    ops.change(["ln", "-sfn", "/dev/null", f"{new}{UNIT_DIR}/unos.service"], what="unos.service masked on the card")
    for path, text in context.docker_files.items():
        ops.change(["mkdir", "-p", f"{new}{path.rsplit('/', 1)[0]}"], what=f"{path} of the card's root")
        ops.write_file(f"{new}{path}", text, what=f"{path} of the card's root")
    if context.pool_dropins:
        ops.change(["rm", "-f", "--", *(f"{new}{path}" for path in context.pool_dropins)],
                   what="The drop-ins that wait for Docker's pool, left out on the card's root")
    ops.write_file(f"{new}{LIB}/mountpoints", "".join(f"{point}\n" for point in points), what="The copy's mount points")
    immutable = set(immutable_mountpoints(points))
    for point in points:
        target = f"{new}{point}"
        if ops.exists(target):
            continue
        ops.change(["mkdir", "-p", "-m", _MOUNTPOINT_MODES.get(point, "0755"), target], what=f"The mount point {point}")
        if point in immutable:
            ops.change(["chattr", "+i", target], what=f"The mount point {point}")
    ops.write_file(f"{new}{LIB}/fixups.sha256", digest + "\n", what="The fix-ups' digest")


def _drop_old_boot_folders(ops: StandbyHost, keep: set[str]) -> None:
    listing = ops.read(["ls", "-1", "--", UNOS_DIR])
    for name in listing.stdout.split() if listing.ok else []:
        if re.fullmatch(r"boot-[0-9]+", name) and f"unos/{name}/" not in keep:
            ops.change(["rm", "-rf", "--", f"{UNOS_DIR}/{name}"], what="The removal of an old boot folder")


def _remove_quietly(new: str, bootdir: str | None) -> None:
    """Remove what a failed sync made; what cannot be removed now goes at the next sync."""
    for cmd in (["btrfs", "subvolume", "delete", new],
                *([["rm", "-rf", "--", f"{BOOT_DIR}/{bootdir}"]] if bootdir else [])):
        try:
            host.run_on_host(cmd, timeout=120)
        except (OSError, Exception):  # the next sync removes it
            logger.warning("Could not remove %s after a failed sync", shlex.join(cmd))


def database_copy_path(number: int) -> str:
    """The database's copy in a generation, as the switch writes it (the container sees the card at the same path)."""
    return f"{generation_path(number)}{LIB}/volumes/unos-data/{DATABASE_NAME}"


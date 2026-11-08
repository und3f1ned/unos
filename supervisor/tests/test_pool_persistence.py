"""Pool persistence across reboots: host fstab, host mounts, guard, reconcile, health.

Every host command goes to ``FakeHost`` (an in-memory host with files, block
devices, btrfs filesystems and a mount table) through a patched
``src.host.run_on_host``; ``subprocess.run`` itself is blocked, so no test can
reach the real machine. ``FakeHost.changes`` records the commands that change
the host so tests can assert them exactly. Docker is replaced by
``FakeDocker`` (``FakeHost.docker``), so no test talks to a Docker daemon,
and the apply of smb.conf after a pool change is only counted
(``FakeHost.smb_applies``).
Apps of a pool are started by background threads after it is mounted; the
``fake_host`` fixture waits for them, so none outlives a test's patches.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import stat
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import docker
import pytest
from docker.models.containers import Container
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.config import get_settings
from src.host import HostCommandError, HostResult
from src.main import app
from src.shares import service as shares_service
from src.storage import disks as disks_module
from src.storage import pools
from src.storage.schemas import PoolInfo

apps_router = importlib.import_module("src.apps.router")
REAL_LIST_MOUNTED_POOLS = apps_router.list_mounted_pools
health_router = importlib.import_module("src.health.router")
storage_router = importlib.import_module("src.storage.router")

ROOT_UUID = "6f1d2c3b-0000-4000-8000-000000000001"
DATA_UUID = "3a1c9f6e-1234-4c3b-9b0a-8f3c2a1b4c5d"
OLD_UUID = "99999999-8888-4777-8666-555555555555"
MEDIA_UUID = "4b2d0a7f-2345-4d4c-8c1b-9a4d3b2c5d6e"
GONE_UUID = "5c3e1b80-3456-4e5d-9d2c-ab5e4c3d6e7f"
NEW_UUID = "0f6b6d9e-1c7e-4b83-9d0a-2c3f7f1b5a11"

BASE_FSTAB = (
    "# /etc/fstab: static file system information.\n"
    f"UUID={ROOT_UUID} / ext4 defaults,noatime,commit=600,errors=remount-ro 0 1\n"
    "tmpfs /tmp tmpfs defaults,nosuid 0 0\n"
)
OPTIONS = (
    "defaults,noatime,nofail,x-systemd.device-timeout=15s,x-systemd.before=docker.service"
)


def marker(name: str) -> str:
    return f"# unos-pool:{name} (managed by UNOS)\n"


def entry(name: str, uuid: str) -> str:
    return f"UUID={uuid} /mnt/pools/{name} btrfs {OPTIONS} 0 0\n"


def _forbidden_subprocess(*args, **kwargs):
    raise AssertionError(f"test tried to run a real command: {args!r}")


class FakeDocker:
    """The low-level Docker API calls the pool code makes (``pools._docker_api``)."""

    def __init__(self) -> None:
        self.containers_: list[dict] = []  # inspect format: Id, Name, Config.Labels, State, ...
        self.volumes: dict[str, dict] = {}  # name -> `docker volume inspect` format
        self.started: list[str] = []
        self.unavailable = False

    def add_volume(self, name, *, driver="local", options=None) -> dict:
        """A Docker volume; returns the mount of it to pass to ``add(mounts=...)``."""
        self.volumes[name] = {
            "Name": name, "Driver": driver, "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
            "Options": options, "Scope": "local", "Labels": None,
        }
        return {"Type": "volume", "Name": name, "Source": f"/var/lib/docker/volumes/{name}/_data",
                "Destination": f"/{name}", "Driver": driver, "Mode": "z", "RW": True,
                "Propagation": ""}

    def add(self, name, *, sources=(), labels=None, running=True, error="", policy="unless-stopped",
            mounts=None):
        """Add a container; ``mounts`` (inspect format) replaces the binds made from ``sources``."""
        self.containers_.append({
            "Id": f"{name}-id",
            "Name": f"/{name}",
            "Config": {"Labels": dict(labels or {})},
            "Mounts": list(mounts) if mounts is not None else [
                {"Type": "bind", "Source": source, "Destination": f"/data{index}",
                 "Propagation": "rprivate"}
                for index, source in enumerate(sources)
            ],
            "State": {"Status": "running" if running else "exited", "Running": running,
                      "Error": error},
            "HostConfig": {"RestartPolicy": {"Name": policy}},
        })

    def api(self):
        if self.unavailable:
            raise docker.errors.DockerException("Error while fetching server API version")
        return nullcontext(self)

    # -- docker.APIClient methods ------------------------------------------

    def _summary(self, container):
        return {
            "Id": container["Id"],
            "Names": [container["Name"]],
            "Labels": container["Config"]["Labels"],
            "Mounts": container["Mounts"],
            "State": container["State"]["Status"],
        }

    def containers(self, all=False, filters=None):
        result = []
        for container in self.containers_:
            if not all and not container["State"]["Running"]:
                continue
            label = (filters or {}).get("label")
            if label:
                key, _, value = label.partition("=")
                if container["Config"]["Labels"].get(key) != value:
                    continue
            result.append(self._summary(container))
        return result

    def inspect_container(self, container_id):
        return next(c for c in self.containers_ if c["Id"] == container_id)

    def inspect_volume(self, name):
        if name not in self.volumes:
            raise docker.errors.NotFound(f"get {name}: no such volume")
        return self.volumes[name]

    def start(self, container_id):
        container = self.inspect_container(container_id)
        container["State"].update(Status="running", Running=True, Error="")
        self.started.append(container["Name"].lstrip("/"))


class FakeHost:
    """In-memory stand-in for the host behind ``src.host.run_on_host``."""

    MUTATING = frozenset({"mkfs.btrfs", "mount", "umount", "chattr", "mkdir", "rmdir", "systemctl"})

    def __init__(self) -> None:
        self.files: dict[str, str] = {
            "/etc/fstab": BASE_FSTAB,
            "/proc/cmdline": f"console=ttyS2,1500000 root=UUID={ROOT_UUID} rootwait",
        }
        self.dirs: set[str] = {"/mnt", "/mnt/pools"}
        self.immutable: set[str] = set()
        self.nonempty: set[str] = set()
        # Files in a directory of the host's root filesystem (what a pool
        # mounted on that directory would hide).
        self.contents: dict[str, list[str]] = {}
        self.tree: list[dict] = []
        self.filesystems: list[dict] = []
        self.mounts: list[dict] = []
        self.swaps: list[str] = []
        self.signatures: dict[str, list[dict]] = {}
        self.device_errors: dict[str, int] = {}
        self.busy: set[str] = set()
        # UUIDs of filesystems that stay mounted in another mount namespace
        # (e.g. a container's private bind mount) after the host unmounts them.
        self.held: set[str] = set()
        self.fail: dict[tuple[str, ...], tuple[int, str]] = {}
        self.calls: list[list[str]] = []
        self.changes: list[list[str]] = []
        self.mkfs_uuid = NEW_UUID
        # Programs the host lacks (host.missing_host_binaries; timeout(1) cannot run them).
        self.missing_programs: set[str] = set()
        self.docker = FakeDocker()
        # How often the storage endpoints applied smb.conf after a pool change (install_fake_host).
        self.smb_applies = 0
        self._minor = 0
        # The host boots from the SD card (ext4 root on mmcblk1p1), like the
        # ArmSoM board, and swaps to zram.
        self.add_disk("mmcblk1", size=64_000_000_000, rota=False, pttype="dos")
        self.add_partition("mmcblk1", "mmcblk1p1", fstype="ext4", uuid=ROOT_UUID)
        self.add_mount("/", "/dev/mmcblk1p1", "ext4", ROOT_UUID)
        self.add_disk("zram0", size=4_000_000_000, rota=False)
        self.swaps.append("/dev/zram0")

    # -- building the host ------------------------------------------------

    def _majmin(self, name: str) -> str:
        self._minor += 1
        major = {"sd": 8, "nv": 259, "mm": 179, "zr": 252, "md": 9}.get(name[:2], 254)
        return f"{major}:{self._minor}"

    def add_disk(self, name, *, size=4_000_787_030_016, rota=True, model=None, serial=None,
                 pttype=None, fstype=None, label=None, uuid=None, wwn=None):
        node = {
            "name": name, "kname": name, "path": f"/dev/{name}", "pkname": None,
            "type": "disk", "size": size, "rota": rota, "model": model, "serial": serial,
            "wwn": wwn, "fstype": fstype, "label": label, "uuid": uuid, "partuuid": None,
            "pttype": pttype, "maj:min": self._majmin(name),
        }
        self.tree.append(node)
        return node

    def add_partition(self, parent, name, *, size=1_000_000_000, fstype=None, label=None,
                      uuid=None, partuuid=None):
        disk = self.node(parent)
        node = {
            "name": name, "kname": name, "path": f"/dev/{name}", "pkname": parent,
            "type": "part", "size": size, "rota": disk["rota"], "model": None, "serial": None,
            "fstype": fstype, "label": label, "uuid": uuid, "partuuid": partuuid,
            "pttype": disk.get("pttype"), "maj:min": self._majmin(name),
        }
        disk.setdefault("children", []).append(node)
        return node

    def add_holder(self, parent, name, kind, *, fstype=None, uuid=None):
        holder = {
            "name": name, "kname": name, "path": f"/dev/{name}", "pkname": parent,
            "type": kind, "size": 1_000_000_000, "rota": True, "model": None, "serial": None,
            "fstype": fstype, "label": None, "uuid": uuid, "partuuid": None, "pttype": None,
            "maj:min": self._majmin(name),
        }
        self.node(parent).setdefault("children", []).append(holder)
        return holder

    def add_btrfs(self, label, uuid, devices, *, missing=0, used=1_048_576, profile="single"):
        fs = {"label": label, "uuid": uuid, "devices": list(devices), "missing": missing,
              "used": used, "profile": profile}
        self.filesystems.append(fs)
        return fs

    def add_mount(self, target, source, fstype, uuid=None, majmin=None, *,
                  vfs_options="rw,noatime", fs_options="rw"):
        if majmin is None:
            node = self._node_by_path(source)
            majmin = "0:44" if fstype == "btrfs" or node is None else node["maj:min"]
        self.mounts.append({"target": target, "source": source, "fstype": fstype,
                            "uuid": uuid, "maj:min": majmin,
                            "vfs-options": vfs_options, "fs-options": fs_options})
        self.dirs.add(target)

    def mount_pool(self, name, target=None, **options):
        """Mount an existing fake btrfs filesystem at /mnt/pools/<name> (or ``target``)."""
        fs = self.fs_by_label(name)
        self.add_mount(target or f"/mnt/pools/{name}", fs["devices"][0], "btrfs", fs["uuid"],
                       **options)

    def node(self, name):
        for top in self.tree:
            for node in self._walk(top):
                if node["name"] == name:
                    return node
        raise KeyError(name)

    def fs_by_label(self, label):
        return next(fs for fs in self.filesystems if fs["label"] == label)

    def mounted(self, target):
        return any(m["target"] == target for m in self.mounts)

    @property
    def fstab(self) -> str:
        return self.files["/etc/fstab"]

    # -- internals ----------------------------------------------------------

    def _walk(self, node):
        yield node
        for child in node.get("children") or []:
            yield from self._walk(child)

    def _node_by_path(self, path):
        for top in self.tree:
            for node in self._walk(top):
                if node["path"] == path:
                    return node
        return None

    def _fs_of(self, path):
        return next((fs for fs in self.filesystems if path in fs["devices"]), None)

    @staticmethod
    def _result(returncode=0, stdout="", stderr=""):
        return HostResult(returncode, stdout, stderr)

    # -- command dispatch ---------------------------------------------------

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        if input is not None:
            # As subprocess.run(text=True, errors=errors) encodes stdin (UTF-8 locale): strict
            # by default, so text with surrogate escapes needs errors="surrogateescape".
            input.encode("utf-8", errors or "strict")
        self.calls.append(cmd)
        result = None
        for prefix, (code, stderr) in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                result = self._result(code, "", stderr)
                break
        if result is None:
            result = self._dispatch(cmd, input)
        result = self._decoded(result, errors)
        if check and not result.ok:
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return result

    @staticmethod
    def _decoded(result, errors):
        """The output as subprocess.run(text=True, errors=errors) decodes it (UTF-8 locale).

        Fake output holds bytes that are not UTF-8 as surrogate escapes
        (``b"x\\xff".decode("utf-8", "surrogateescape")``): strict decoding,
        the default, raises UnicodeDecodeError, like real output with them.
        """
        def decode(text):
            return text.encode("utf-8", "surrogateescape").decode("utf-8", errors or "strict")

        return HostResult(result.returncode, decode(result.stdout), decode(result.stderr))

    def _dispatch(self, cmd, input):
        prog = cmd[0]
        if prog == "cat":
            path = cmd[-1]
            if path in self.files:
                return self._result(stdout=self.files[path])
            return self._result(1, stderr=f"cat: {path}: No such file or directory")
        if prog == "/bin/sh" and "unos-write" in cmd:
            path = cmd[cmd.index("unos-write") + 1]
            self.files[path] = input
            self.changes.append(["write", path])
            return self._result()
        if prog == "/bin/sh" and "unos-nfs-files" in cmd:
            return self._result(stdout=self._export_files(*cmd[-2:]))
        if prog == "/bin/sh" and cmd[3:4] == ["sh"] and "command -v" in cmd[2]:
            # host.missing_host_binaries: the names the host cannot find, one per line.
            return self._result(stdout="".join(f"{name}\n" for name in cmd[4:] if name in self.missing_programs))
        if prog == "timeout":
            # timeout [--kill-after=N] DURATION COMMAND...: the command, or 127 when it is missing.
            args = cmd[1:]
            while args[0].startswith("-"):
                args = args[1:]
            program = args[1]
            if program in self.missing_programs:
                return self._result(127, stderr=f"timeout: failed to run command '{program}': No such file or directory")
            return self._dispatch(args[1:], input)
        if prog == "exportfs":
            assert cmd == ["exportfs", "-ra"], cmd
            self.changes.append(cmd)
            return self._result()
        if prog == "test":
            flag, path = cmd[1], cmd[2]
            if path.startswith("/sys/fs/btrfs/"):
                # sysfs lists every btrfs filesystem mounted in any namespace.
                uuid = path.rsplit("/", 1)[-1]
                active = uuid in self.held or any(m["uuid"] == uuid for m in self.mounts)
                return self._result(0 if active else 1)
            exists = path in self.dirs if flag == "-d" else (path in self.files or path in self.dirs)
            return self._result(0 if exists else 1)
        if prog == "sync":
            return self._result()  # flushes only: recorded in calls, not in changes
        if prog == "find":
            assert cmd[2:] == ["-mindepth", "1", "-maxdepth", "1", "-print0"], cmd
            target = cmd[1]
            assert not self.mounted(target), f"find in mounted {target} lists the pool"
            if target not in self.dirs:
                return self._result(1, stderr=f"find: '{target}': No such file or directory")
            names = self.contents.get(target) or (["something"] if target in self.nonempty else [])
            return self._result(stdout="".join(f"{target}/{name}\0" for name in names))
        if prog == "findmnt":
            # findmnt -J prints the bytes of a name as they are, also bytes that
            # are not UTF-8 (it escapes only quotes, backslashes and control
            # characters).
            return self._result(stdout=json.dumps({"filesystems": self.mounts}, ensure_ascii=False))
        if prog == "lsblk":
            return self._result(stdout=self._lsblk())
        if prog == "swapon":
            return self._result(stdout="".join(f"{s}\n" for s in self.swaps))
        if prog == "wipefs":
            assert "--no-act" in cmd and not {"-a", "--all"} & set(cmd), cmd
            sigs = self.signatures.get(cmd[-1])
            return self._result(stdout=json.dumps({"signatures": sigs}) if sigs else "")
        if prog == "blkid":
            fs = self._fs_of(cmd[-1])
            return self._result(0 if fs else 2, stdout=f"{fs['uuid']}\n" if fs else "")
        if prog in ("udevadm",):
            return self._result()
        if prog == "btrfs":
            return self._btrfs(cmd)
        if prog in self.MUTATING:
            self.changes.append(cmd)
            return getattr(self, "_" + prog.replace(".", "_"))(cmd)
        raise AssertionError(f"unexpected host command: {cmd}")

    def _export_files(self, main, directory):
        """What shares/nfs.py's listing script prints: for the main export file, then the
        *.exports files of its directory that do not start with a dot, the name and the bytes
        as `od -An -v -tx1` prints them, each followed by a NUL."""
        names = [main] if main in self.files else []
        for path in sorted(self.files):
            folder, _, name = path.rpartition("/")
            if folder == directory and name.endswith(".exports") and not name.startswith("."):
                names.append(path)
        out = []
        for name in names:
            data = self.files[name].encode("utf-8", "surrogateescape")
            rows = [" ".join(f"{b:02x}" for b in data[i:i + 16]) for i in range(0, len(data), 16)]
            out.append(f"{name}\0" + "".join(f" {row}\n" for row in rows) + "\0")
        return "".join(out)

    def _lsblk(self):
        def render(node):
            out = {k: v for k, v in node.items() if k != "children"}
            fs = self._fs_of(node["path"])
            if fs:
                out.update(fstype="btrfs", label=fs["label"], uuid=fs["uuid"])
            points = [m["target"] for m in self.mounts if m["source"] == node["path"]]
            if node["path"] in self.swaps:
                points.append("[SWAP]")
            out["mountpoints"] = points or [None]
            if node.get("children"):
                out["children"] = [render(child) for child in node["children"]]
            return out

        # Like findmnt, lsblk -J prints labels, models and mountpoints with
        # their bytes as they are, also bytes that are not UTF-8.
        return json.dumps({"blockdevices": [render(node) for node in self.tree]}, ensure_ascii=False)

    def _btrfs(self, cmd):
        sub = cmd[1:3]
        if sub == ["filesystem", "show"]:
            return self._result(stdout=self._btrfs_show())
        if sub == ["device", "scan"]:
            return self._result()
        target = cmd[-1]
        mount = next((m for m in self.mounts if m["target"] == target and m["fstype"] == "btrfs"), None)
        fs = next((f for f in self.filesystems if mount and f["uuid"] == mount["uuid"]), None)
        if fs is None:
            return self._result(1, stderr=f"ERROR: '{target}' is not a mounted btrfs device")
        if sub == ["filesystem", "usage"]:
            size = sum(self._node_by_path(d)["size"] for d in fs["devices"])
            data = "RAID1" if fs["profile"] == "raid1" else fs["profile"]
            return self._result(stdout=(
                "Overall:\n"
                f"    Device size:\t\t{size}\n"
                "    Device allocated:\t\t2155872256\n"
                f"    Used:\t\t\t{fs['used']}\n"
                f"Data,{data}: Size:1073741824, Used:{fs['used']} (0.10%)\n"
            ))
        if sub == ["device", "stats"]:
            lines = []
            for index, device in enumerate(fs["devices"]):
                errors = self.device_errors.get(target, 0) if index == 0 else 0
                for counter in ("write_io_errs", "read_io_errs", "flush_io_errs"):
                    lines.append(f"[{device}].{counter:<16} {errors if counter == 'write_io_errs' else 0}")
                lines.append(f"[{device}].corruption_errs  0")
                lines.append(f"[{device}].generation_errs  0")
            return self._result(stdout="\n".join(lines) + "\n")
        raise AssertionError(f"unexpected btrfs command: {cmd}")

    def _btrfs_show(self):
        chunks = []
        for fs in self.filesystems:
            label = f"'{fs['label']}'" if fs["label"] else "none"
            total = len(fs["devices"]) + fs["missing"]
            lines = [f"Label: {label}  uuid: {fs['uuid']}",
                     f"\tTotal devices {total} FS bytes used {fs['used']}"]
            for devid, device in enumerate(fs["devices"], 1):
                size = self._node_by_path(device)["size"]
                lines.append(f"\tdevid {devid:4d} size {size} used 2155872256 path {device}")
            if fs["missing"]:
                lines.append("\t*** Some devices missing")
            chunks.append("\n".join(lines) + "\n")
        return "\n".join(chunks)

    def _mkfs_btrfs(self, cmd):
        args = cmd[1:]
        devices = [a for a in args if a.startswith("/dev/")]
        label = args[args.index("-L") + 1]
        profile = args[args.index("-d") + 1]
        for device in devices:
            node = self._node_by_path(device)
            if any(m["source"] == device for m in self.mounts):
                return self._result(1, stderr=f"ERROR: {device} is mounted")
            existing = self._fs_of(device) or self.signatures.get(device) or node.get("fstype") \
                or node.get("children") or node.get("pttype")
            if existing and "-f" not in args:
                return self._result(1, stderr=f"ERROR: {device} appears to contain an existing filesystem")
        for fs in self.filesystems:
            lost = [d for d in fs["devices"] if d in devices]
            fs["devices"] = [d for d in fs["devices"] if d not in devices]
            fs["missing"] += len(lost) if fs["devices"] else 0
        self.filesystems = [fs for fs in self.filesystems if fs["devices"]]
        for device in devices:
            node = self._node_by_path(device)
            node.pop("children", None)
            node.update(fstype=None, label=None, uuid=None, pttype=None)
            self.signatures.pop(device, None)
        self.add_btrfs(label, self.mkfs_uuid, devices, used=16384, profile=profile)
        return self._result(stdout=(
            "btrfs-progs v6.14\nSee https://btrfs.readthedocs.io for more information.\n\n"
            f"Label:              {label}\nUUID:               {self.mkfs_uuid}\n"
            "Node size:          16384\n"
        ))

    def _fstab_source(self, target):
        for line in self.fstab.splitlines():
            fields = line.split()
            if fields and not fields[0].startswith("#") and len(fields) > 1 and fields[1] == target:
                return fields[0]
        return None

    def _mount(self, cmd):
        if cmd[1] == "-t":
            assert cmd[:5] == ["mount", "-t", "btrfs", "-o", "noatime"], cmd
            source, target = cmd[5], cmd[6]
        else:
            target = cmd[1]
            source = self._fstab_source(target)
            if source is None:
                return self._result(1, stderr=f"mount: {target}: can't find in /etc/fstab.")
        uuid = source[len("UUID="):] if source.startswith("UUID=") else None
        fs = next((f for f in self.filesystems if f["uuid"] == uuid), None)
        if fs is None:
            return self._result(32, stderr=f"mount: {target}: can't find UUID={uuid}.")
        if fs["missing"]:
            return self._result(32, stderr=f"mount: {target}: wrong fs type, bad option, bad superblock")
        if target not in self.dirs:
            return self._result(32, stderr=f"mount: {target}: mount point does not exist.")
        self.add_mount(target, fs["devices"][0], "btrfs", fs["uuid"])
        return self._result()

    def _umount(self, cmd):
        target = cmd[1]
        if target in self.busy:
            return self._result(32, stderr=f"umount: {target}: target is busy.")
        for index in range(len(self.mounts) - 1, -1, -1):
            if self.mounts[index]["target"] == target:
                del self.mounts[index]
                return self._result()
        return self._result(32, stderr=f"umount: {target}: not mounted.")

    def _chattr(self, cmd):
        flag, target = cmd[1], cmd[2]
        if flag == "+i":
            # Setting +i on a mounted pool would make the pool root immutable.
            assert not self.mounted(target), f"chattr +i on mounted {target}"
            if target not in self.dirs:
                return self._result(1, stderr=f"chattr: No such file or directory while trying to stat {target}")
            self.immutable.add(target)
        else:
            self.immutable.discard(target)
        return self._result()

    def _mkdir(self, cmd):
        self.dirs.add(cmd[-1])
        return self._result()

    def _rmdir(self, cmd):
        target = cmd[-1]
        if target in self.immutable:
            return self._result(1, stderr=f"rmdir: failed to remove '{target}': Operation not permitted")
        if self.mounted(target):
            return self._result(1, stderr=f"rmdir: failed to remove '{target}': Device or resource busy")
        if target in self.nonempty or self.contents.get(target):
            return self._result(1, stderr=f"rmdir: failed to remove '{target}': Directory not empty")
        self.dirs.discard(target)
        return self._result()

    def _systemctl(self, cmd):
        return self._result()

    async def count_smb_apply(self) -> None:
        """Stands in for shares.service.apply_after_pool_change (install_fake_host)."""
        self.smb_applies += 1


def install_fake_host(monkeypatch) -> FakeHost:
    """Route all host commands to a new FakeHost and block real subprocesses and Docker."""
    fake = FakeHost()
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(subprocess, "run", _forbidden_subprocess)
    monkeypatch.setattr(pools, "_docker_api", fake.docker.api)
    monkeypatch.setattr(pools.settings, "pools_base_path", "/mnt/pools")
    # The checks before an app starts (apps.router.prepare_app_start) would look
    # at the pools below /mnt/pools of the machine running the tests, and create
    # folders there; on the device those pools are real. No pool is mounted for them.
    monkeypatch.setattr(apps_router, "list_mounted_pools", lambda: [])
    # The apply of smb.conf after a pool change (ADR-0248) would read the smb.conf
    # and the database of the machine running the tests, and run Samba's commands,
    # which FakeHost does not know: it is only counted here. test_smb_integration.py
    # runs it, with the Samba fake.
    monkeypatch.setattr(shares_service, "apply_after_pool_change", fake.count_smb_apply)
    return fake


@contextmanager
def admin_client() -> Iterator[TestClient]:
    """TestClient on the real app with an administrator logged in."""
    user = SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True)
    app.dependency_overrides[verify_token] = lambda: user
    storage_router.limiter.reset()
    client = TestClient(app)
    client.user = user
    try:
        yield client
    finally:
        app.dependency_overrides.pop(verify_token, None)
        storage_router.limiter.reset()


def wait_for_app_starts(pool: str | None = None, timeout: float = 5) -> None:
    """Wait for the threads that start the apps of mounted pools (pools._start_pool_apps_later).

    With ``pool``, only for the thread that goes through that pool's apps,
    not for the checks it gave up on.
    """
    prefix = pools._APP_THREAD_NAME

    def awaited(thread: threading.Thread) -> bool:
        return thread.name == f"{prefix}:{pool}" if pool else thread.name.startswith(prefix)

    deadline = time.monotonic() + timeout
    while True:
        busy = [thread for thread in threading.enumerate() if awaited(thread)]
        if not busy:
            return
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"still running: {', '.join(thread.name for thread in busy)}"
        busy[0].join(remaining)


def app_threads() -> dict[str, bool]:
    """The running threads that start the apps of mounted pools: name -> daemon thread."""
    return {
        thread.name: thread.daemon
        for thread in threading.enumerate()
        if thread.name.startswith(pools._APP_THREAD_NAME)
    }


@pytest.fixture
def fake_host(monkeypatch):
    fake = install_fake_host(monkeypatch)
    yield fake
    # Before the patches are undone: a thread left behind would reach the
    # real Docker daemon and the pools of the machine running the tests.
    wait_for_app_starts()


@pytest.fixture
def data_pool(fake_host):
    """A single-disk pool 'data' on /dev/sda: created earlier, not mounted, no fstab entry.

    This is the state found on the device: after a reboot the pool is not
    mounted and /mnt/pools/data is an empty directory on the SD card.
    """
    fake_host.add_disk("sda", model="WDC WD40EFRX", serial="WD-1")
    fake_host.add_btrfs("data", DATA_UUID, ["/dev/sda"])
    fake_host.dirs.add("/mnt/pools/data")
    return fake_host


# ---------------------------------------------------------------------------
# fstab text handling (pure functions)
# ---------------------------------------------------------------------------


class TestFstabText:
    def test_add_appends_marker_and_entry_after_untouched_lines(self, fake_host):
        result = pools.set_managed_fstab_entry(BASE_FSTAB, "data", DATA_UUID)
        assert result == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    def test_entry_format(self, fake_host):
        assert pools.fstab_marker("data") == "# unos-pool:data (managed by UNOS)"
        # nofail: a missing disk does not block the boot. x-systemd.before:
        # nofail drops the ordering before local-fs.target, so without it
        # Docker could start the apps before their pool is mounted.
        assert pools.fstab_entry("data", DATA_UUID) == (
            f"UUID={DATA_UUID} /mnt/pools/data btrfs "
            "defaults,noatime,nofail,x-systemd.device-timeout=15s,"
            "x-systemd.before=docker.service 0 0"
        )

    def test_add_is_idempotent(self, fake_host):
        once = pools.set_managed_fstab_entry(BASE_FSTAB, "data", DATA_UUID)
        assert pools.set_managed_fstab_entry(once, "data", DATA_UUID) == once

    def test_add_to_file_without_trailing_newline(self, fake_host):
        text = BASE_FSTAB.rstrip("\n")
        result = pools.set_managed_fstab_entry(text, "data", DATA_UUID)
        assert result == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    def test_update_rewrites_the_entry_in_place(self, fake_host):
        before = BASE_FSTAB + marker("data") + entry("data", OLD_UUID) + "# trailing comment\n"
        result = pools.set_managed_fstab_entry(before, "data", DATA_UUID)
        assert result == BASE_FSTAB + marker("data") + entry("data", DATA_UUID) + "# trailing comment\n"

    def test_remove_drops_only_the_managed_lines(self, fake_host):
        unrelated = (
            "LABEL=data /srv/legacy ext4 defaults 0 2\n"  # the old code removed any "LABEL=data" line
            "# UUID=1234 /mnt/pools/data btrfs defaults 0 0\n"
        )
        text = (
            BASE_FSTAB + marker("data") + entry("data", DATA_UUID) + unrelated
            + marker("data2") + entry("data2", MEDIA_UUID)
        )
        result = pools.remove_managed_fstab_entry(text, "data")
        assert result == BASE_FSTAB + unrelated + marker("data2") + entry("data2", MEDIA_UUID)

    def test_remove_is_idempotent(self, fake_host):
        text = BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
        once = pools.remove_managed_fstab_entry(text, "data")
        assert once == BASE_FSTAB
        assert pools.remove_managed_fstab_entry(once, "data") == BASE_FSTAB

    def test_orphaned_marker_is_repaired_and_removed(self, fake_host):
        # The admin commented out our entry line; the marker stays ours, the
        # commented line is not.
        text = BASE_FSTAB + marker("data") + "#" + entry("data", DATA_UUID)
        assert pools.parse_managed_fstab(text)["data"].has_entry is False
        repaired = pools.set_managed_fstab_entry(text, "data", DATA_UUID)
        assert repaired == BASE_FSTAB + marker("data") + entry("data", DATA_UUID) + "#" + entry("data", DATA_UUID)
        assert pools.remove_managed_fstab_entry(text, "data") == BASE_FSTAB + "#" + entry("data", DATA_UUID)

    def test_parse_managed_entries(self, fake_host):
        text = BASE_FSTAB + marker("data") + entry("data", DATA_UUID.upper()) + marker("media") + entry("media", MEDIA_UUID)
        entries = pools.parse_managed_fstab(text)
        assert set(entries) == {"data", "media"}
        assert entries["data"].uuid == DATA_UUID
        assert entries["data"].target == "/mnt/pools/data"
        assert entries["media"].has_entry is True

    def test_unmanaged_entries_for_a_target(self, fake_host):
        text = (
            BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
            + f"UUID={MEDIA_UUID} /mnt/pools/media/ btrfs defaults 0 0\n"
        )
        assert pools.unmanaged_fstab_entries(text, "/mnt/pools/data") == []
        assert pools.unmanaged_fstab_entries(text, "/mnt/pools/media") == [
            f"UUID={MEDIA_UUID} /mnt/pools/media/ btrfs defaults 0 0"
        ]


# ---------------------------------------------------------------------------
# mount / unmount / delete on the host
# ---------------------------------------------------------------------------


class TestMountPool:
    def test_mount_mounts_by_uuid_on_the_host_and_persists(self, data_pool):
        info = pools.mount_pool("data")

        assert data_pool.changes == [
            ["mkdir", "-p", "/mnt/pools/data"],
            ["chattr", "+i", "/mnt/pools/data"],  # guarded until the mount is on top
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
            ["write", "/etc/fstab.unos-backup"],
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
        ]
        assert data_pool.files["/etc/fstab.unos-backup"] == BASE_FSTAB
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
        assert info.mounted and info.persistent
        assert info.mountpoint == "/mnt/pools/data"
        assert info.health == "healthy"
        assert info.raid_level == "single"

    def test_mount_is_idempotent(self, data_pool):
        pools.mount_pool("data")
        data_pool.changes.clear()
        info = pools.mount_pool("data")
        assert data_pool.changes == []
        assert info.mounted and info.persistent

    def test_backup_is_written_only_once(self, data_pool):
        data_pool.add_disk("sdb")
        data_pool.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
        pools.mount_pool("data")
        pools.mount_pool("media")
        writes = [c for c in data_pool.changes if c[0] == "write"]
        assert writes == [
            ["write", "/etc/fstab.unos-backup"],
            ["write", "/etc/fstab"],
            ["write", "/etc/fstab"],
        ]
        assert data_pool.files["/etc/fstab.unos-backup"] == BASE_FSTAB
        assert data_pool.fstab == (
            BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
            + marker("media") + entry("media", MEDIA_UUID)
        )

    def test_stale_entry_is_replaced_before_mounting(self, data_pool):
        data_pool.files["/etc/fstab"] = BASE_FSTAB + marker("data") + entry("data", OLD_UUID)
        data_pool.files["/etc/fstab.unos-backup"] = BASE_FSTAB

        info = pools.mount_pool("data")

        assert data_pool.changes == [
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
            ["mkdir", "-p", "/mnt/pools/data"],
            ["chattr", "+i", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
        assert info.persistent

    def test_refuses_target_listed_in_an_unmanaged_fstab_entry(self, data_pool):
        data_pool.files["/etc/fstab"] = BASE_FSTAB + "LABEL=data /mnt/pools/data btrfs defaults 0 0\n"
        with pytest.raises(pools.PoolConflictError, match="does not manage"):
            pools.mount_pool("data")
        assert data_pool.changes == []

    def test_refuses_pool_with_missing_devices(self, data_pool):
        data_pool.fs_by_label("data")["missing"] = 1
        with pytest.raises(pools.PoolConflictError, match="missing 1 device"):
            pools.mount_pool("data")
        assert data_pool.changes == []

    def test_refuses_duplicate_labels(self, data_pool):
        data_pool.add_disk("sdb")
        data_pool.add_btrfs("data", MEDIA_UUID, ["/dev/sdb"])
        with pytest.raises(pools.PoolConflictError, match="labelled 'data'"):
            pools.mount_pool("data")
        assert data_pool.changes == []

    def test_refuses_target_occupied_by_another_filesystem(self, data_pool):
        data_pool.add_disk("sdb")
        data_pool.add_mount("/mnt/pools/data", "/dev/sdb", "ext4")
        with pytest.raises(pools.PoolConflictError, match="Another filesystem"):
            pools.mount_pool("data")

    def test_unknown_pool(self, fake_host):
        with pytest.raises(pools.PoolNotFoundError):
            pools.mount_pool("nope")

    def test_failed_mount_leaves_fstab_alone_and_guard_in_place(self, data_pool):
        data_pool.fail[("mount",)] = (32, "mount: /mnt/pools/data: wrong fs type")
        with pytest.raises(RuntimeError, match="wrong fs type"):
            pools.mount_pool("data")
        assert data_pool.fstab == BASE_FSTAB
        assert "/mnt/pools/data" in data_pool.immutable

    def test_refuses_to_hide_files_in_the_mountpoint(self, data_pool):
        # Written to the boot disk by the old guest share while 'data' was not mounted.
        data_pool.contents["/mnt/pools/data"] = ["notes.txt", "Movies", ".DS_Store"]
        with pytest.raises(pools.PoolConflictError) as excinfo:
            pools.mount_pool("data")
        message = str(excinfo.value)
        assert "/mnt/pools/data is not empty" in message
        assert "3 item(s)" in message and ".DS_Store, Movies, notes.txt" in message
        assert "chattr -i /mnt/pools/data" in message
        assert data_pool.changes == []
        assert data_pool.fstab == BASE_FSTAB

    def test_names_only_a_few_hidden_files(self, data_pool):
        data_pool.contents["/mnt/pools/data"] = [f"file{i:02d}" for i in range(12)]
        with pytest.raises(pools.PoolConflictError, match=r"file04 and 7 more"):
            pools.mount_pool("data")

    def test_hidden_file_whose_name_is_not_utf8(self, data_pool):
        # b"caf\xe9" (Latin-1), as an SMB client could write it: the mount was
        # refused with "'utf-8' codec can't decode byte 0xe9" (HTTP 400).
        data_pool.contents["/mnt/pools/data"] = [b"caf\xe9".decode("utf-8", "surrogateescape")]
        with pytest.raises(pools.PoolConflictError, match="1 item\\(s\\) on the host's boot disk \\(caf\ufffd\\)"):
            pools.mount_pool("data")
        assert data_pool.changes == []

    def test_unreadable_mountpoint_is_not_mounted_over(self, data_pool):
        data_pool.fail[("find",)] = (1, "find: '/mnt/pools/data': Input/output error")
        with pytest.raises(RuntimeError, match="Could not check whether /mnt/pools/data is empty"):
            pools.mount_pool("data")
        assert data_pool.changes == []

    def test_missing_mountpoint_counts_as_empty(self, data_pool):
        data_pool.dirs.discard("/mnt/pools/data")
        assert pools.mount_pool("data").mounted
        assert data_pool.changes[0] == ["mkdir", "-p", "/mnt/pools/data"]


class TestUnmountAndDelete:
    @pytest.fixture
    def mounted(self, data_pool):
        pools.mount_pool("data")
        wait_for_app_starts()
        data_pool.changes.clear()
        return data_pool

    def test_unmount_unmounts_forgets_entry_and_guards(self, mounted):
        info = pools.unmount_pool("data")

        assert mounted.changes == [
            ["umount", "/mnt/pools/data"],
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
            ["chattr", "+i", "/mnt/pools/data"],
        ]
        assert mounted.fstab == BASE_FSTAB
        assert not info.mounted and not info.persistent
        assert info.health == "unmounted"
        assert info.device_errors == 0

    def test_unmount_busy_pool_is_a_conflict_and_keeps_everything(self, mounted):
        mounted.busy.add("/mnt/pools/data")
        with pytest.raises(pools.PoolBusyError, match="busy"):
            pools.unmount_pool("data")
        assert mounted.mounted("/mnt/pools/data")
        assert mounted.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    def test_unmount_removes_stacked_mounts(self, mounted):
        mounted.mount_pool("data")  # mounted twice, e.g. by systemd and by UNOS
        pools.unmount_pool("data")
        assert not mounted.mounted("/mnt/pools/data")
        assert mounted.changes.count(["umount", "/mnt/pools/data"]) == 2

    def test_delete_forgets_the_pool_but_keeps_the_data(self, mounted):
        pools.delete_pool("data")

        assert mounted.changes == [
            ["umount", "/mnt/pools/data"],
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
            ["chattr", "-i", "/mnt/pools/data"],
            ["rmdir", "/mnt/pools/data"],
        ]
        assert mounted.fstab == BASE_FSTAB
        assert "/mnt/pools/data" not in mounted.dirs
        assert not any(c[0] in ("mkfs.btrfs", "wipefs") for c in mounted.calls)
        # The filesystem is still there and can be mounted again.
        [listed] = pools.list_pools()
        assert (listed.name, listed.health, listed.uuid) == ("data", "unmounted", DATA_UUID)
        assert pools.mount_pool("data").mounted

    def test_delete_keeps_a_non_empty_directory_guarded(self, mounted):
        mounted.nonempty.add("/mnt/pools/data")
        pools.delete_pool("data")
        assert mounted.changes[-3:] == [
            ["chattr", "-i", "/mnt/pools/data"],
            ["rmdir", "/mnt/pools/data"],
            ["chattr", "+i", "/mnt/pools/data"],
        ]
        assert "/mnt/pools/data" in mounted.dirs

    def test_delete_busy_pool_keeps_the_fstab_entry(self, mounted):
        mounted.busy.add("/mnt/pools/data")
        with pytest.raises(pools.PoolBusyError):
            pools.delete_pool("data")
        assert mounted.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    def test_delete_cleans_up_a_pool_whose_disks_are_gone(self, fake_host):
        fake_host.files["/etc/fstab"] = BASE_FSTAB + marker("gone") + entry("gone", GONE_UUID)
        fake_host.files["/etc/fstab.unos-backup"] = BASE_FSTAB
        fake_host.dirs.add("/mnt/pools/gone")
        pools.delete_pool("gone")
        assert fake_host.fstab == BASE_FSTAB
        assert "/mnt/pools/gone" not in fake_host.dirs

    # -- containers keep a pool mounted after the host unmounts it ---------

    @staticmethod
    def assert_still_mounted_and_persistent(host):
        assert host.mounted("/mnt/pools/data")
        assert host.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
        assert pools.get_pool("data").persistent

    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_refuses_while_a_container_binds_a_directory_of_the_pool(self, mounted, operation):
        mounted.docker.add("transmission", sources=["/mnt/pools/data/apps/transmission/config"])
        mounted.docker.add("jellyfin", sources=["/mnt/pools/data"])
        with pytest.raises(pools.PoolBusyError, match=r"running containers \(jellyfin, transmission\)"):
            operation("data")
        assert mounted.changes == []  # not even an umount
        self.assert_still_mounted_and_persistent(mounted)

    # A Docker volume of the local driver that binds a folder of the pool
    # (driver_opts type: none, o: bind, device: <folder>): Docker reports the
    # volume's own folder below /var/lib/docker/volumes as the Source, and the
    # volume's mount on the host passed the check after the umount as one the
    # administrator made ("also mounted elsewhere on the host").
    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_refuses_while_a_containers_docker_volume_binds_a_folder_of_the_pool(self, mounted, operation):
        mounted.add_mount("/var/lib/docker/volumes/paperless_media/_data",
                          "/dev/sda[/apps/paperless/media]", "btrfs", DATA_UUID)
        options = {"type": "none", "o": "bind", "device": "/mnt/pools/data/apps/paperless/media"}
        mounted.docker.add("paperless", mounts=[
            mounted.docker.add_volume("paperless_media", options=options),
            mounted.docker.add_volume("paperless_db"),  # a plain named volume
        ])
        with pytest.raises(pools.PoolBusyError, match=r"running containers \(paperless\)"):
            operation("data")
        assert mounted.changes == []
        self.assert_still_mounted_and_persistent(mounted)

    def test_docker_volumes_that_bind_no_folder_of_the_pool_do_not_count(self, mounted):
        mounted.docker.add("app", mounts=[
            mounted.docker.add_volume("db"),
            mounted.docker.add_volume("other", options={"type": "none", "o": "bind", "device": "/mnt/pools/database"}),
        ])
        assert not pools.unmount_pool("data").mounted

    def test_docker_volume_that_cannot_be_inspected_leaves_the_check_after_unmounting(self, mounted, caplog):
        mounted.docker.add("app", mounts=[mounted.docker.add_volume("media")])
        mounted.docker.volumes.clear()  # inspect_volume: 404
        mounted.held.add(DATA_UUID)
        with pytest.raises(pools.PoolBusyError, match="mounted again"):
            pools.unmount_pool("data")
        assert "Could not list the running containers or their volumes" in caplog.text
        self.assert_still_mounted_and_persistent(mounted)

    def test_refuses_while_an_app_is_labelled_with_the_pool(self, mounted):
        mounted.docker.add("immich", labels={"unos.app": "immich", "unos.app.pool": "data"})
        with pytest.raises(pools.PoolBusyError, match="immich"):
            pools.unmount_pool("data")
        assert mounted.changes == []

    def test_ignores_containers_of_other_pools_and_stopped_containers(self, mounted):
        mounted.docker.add("unos-supervisor", sources=["/mnt", "/var/run/docker.sock"])
        mounted.docker.add("media-app", sources=["/mnt/pools/database"])  # not below /mnt/pools/data
        mounted.docker.add("stopped", sources=["/mnt/pools/data/apps/x"], running=False)
        info = pools.unmount_pool("data")
        assert not info.mounted
        assert mounted.fstab == BASE_FSTAB

    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_mounts_again_when_the_filesystem_stays_in_use(self, mounted, operation, caplog):
        # A container Docker did not report (another daemon, a private bind
        # mount) still uses the filesystem: the host umount succeeds anyway.
        mounted.held.add(DATA_UUID)
        with pytest.raises(pools.PoolBusyError, match="still in use outside the host.*mounted again"):
            operation("data")
        assert mounted.changes == [
            ["umount", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        self.assert_still_mounted_and_persistent(mounted)
        assert "/mnt/pools/data" in mounted.dirs

    def test_reports_when_mounting_again_fails(self, mounted):
        mounted.held.add(DATA_UUID)
        mounted.fail[("mount",)] = (32, "mount: /mnt/pools/data: wrong fs type")
        with pytest.raises(pools.PoolBusyError, match="mounting it again failed: .*wrong fs type"):
            pools.unmount_pool("data")
        # The entry is kept, so the pool is mounted again at the next boot.
        assert mounted.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    def test_filesystem_also_mounted_elsewhere_on_the_host_is_not_busy(self, mounted):
        mounted.mount_pool("data", target="/srv/data")  # the administrator's own mount
        info = pools.unmount_pool("data")
        assert not info.mounted and not mounted.mounted("/mnt/pools/data")
        assert mounted.mounted("/srv/data")

    def test_without_docker_the_check_after_unmounting_still_applies(self, mounted, caplog):
        mounted.docker.unavailable = True
        mounted.held.add(DATA_UUID)
        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            with pytest.raises(pools.PoolBusyError, match="mounted again"):
                pools.unmount_pool("data")
        assert "Could not list the running containers" in caplog.text
        self.assert_still_mounted_and_persistent(mounted)

    def test_without_docker_an_unused_pool_is_unmounted(self, mounted):
        mounted.docker.unavailable = True
        assert not pools.unmount_pool("data").mounted


class TestMountpointGuard:
    def test_guard_skips_mounted_target(self, data_pool):
        data_pool.mount_pool("data")
        assert pools.guard_mountpoint("/mnt/pools/data") is False
        assert data_pool.changes == []

    def test_guard_skips_missing_directory(self, fake_host):
        assert pools.guard_mountpoint("/mnt/pools/nothing") is False
        assert fake_host.changes == []

    def test_guard_tolerates_chattr_failure(self, data_pool, caplog):
        data_pool.fail[("chattr",)] = (1, "chattr: Operation not supported while reading flags")
        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            assert pools.guard_mountpoint("/mnt/pools/data") is False
        assert "chattr +i" in caplog.text

    def test_guard_sets_immutable_flag(self, data_pool):
        assert pools.guard_mountpoint("/mnt/pools/data") is True
        assert data_pool.changes == [["chattr", "+i", "/mnt/pools/data"]]

    def test_guard_is_flushed_to_disk(self, data_pool):
        # With ext4 commit=600 an unflushed flag can vanish in a power cut.
        pools.guard_mountpoint("/mnt/pools/data")
        chattr = data_pool.calls.index(["chattr", "+i", "/mnt/pools/data"])
        assert data_pool.calls[chattr + 1] == ["sync", "/mnt/pools/data"]

    def test_guard_falls_back_to_a_global_sync(self, data_pool):
        data_pool.fail[("sync", "/mnt/pools/data")] = (1, "sync: error syncing '/mnt/pools/data'")
        assert pools.guard_mountpoint("/mnt/pools/data") is True
        assert data_pool.calls[-2:] == [["sync", "/mnt/pools/data"], ["sync"]]

    def test_removing_a_mountpoint_is_flushed_to_disk(self, data_pool):
        pools.delete_pool("data")
        assert data_pool.calls[-2:] == [["rmdir", "/mnt/pools/data"], ["sync", "/mnt/pools"]]


class TestDurableHostWrites:
    """write_host_file_atomic must make the rename itself durable (ext4 commit=600)."""

    @pytest.fixture
    def local_host(self, monkeypatch, tmp_path):
        """Run host commands locally, with a ``sync`` that logs its arguments."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "sync.log"
        target = tmp_path / "etc" / "fstab"
        fake_sync = bindir / "sync"
        # Logs each call and whether the new file is already in place.
        fake_sync.write_text(
            "#!/bin/sh\n"
            f'echo "sync $*" >> "{log}"\n'
            f'if [ -e "{target}" ]; then echo "  target exists" >> "{log}"; fi\n'
        )
        fake_sync.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        target.parent.mkdir()
        return SimpleNamespace(target=target, log=log)

    def test_directory_is_flushed_after_the_rename(self, local_host):
        host_module.write_host_file_atomic(str(local_host.target), "new\n", mode="0640")

        assert local_host.target.read_text() == "new\n"
        assert stat.S_IMODE(local_host.target.stat().st_mode) == 0o640
        lines = local_host.log.read_text().splitlines()
        assert len(lines) == 3, lines
        assert lines[0].startswith(f"sync {local_host.target}.unos-tmp.")  # the data first
        assert lines[1:] == [f"sync {local_host.target.parent}", "  target exists"]
        assert list(local_host.target.parent.iterdir()) == [local_host.target]

    def test_sync_host_path(self, local_host):
        assert host_module.sync_host_path(str(local_host.target.parent)) is True
        assert local_host.log.read_text().splitlines() == [f"sync {local_host.target.parent}"]
        with pytest.raises(ValueError, match="absolute"):
            host_module.sync_host_path("relative/path")


# ---------------------------------------------------------------------------
# startup reconciliation
# ---------------------------------------------------------------------------


@pytest.fixture
def three_pools(data_pool):
    """data: entry, not mounted; media: entry, mounted; gone: entry, disk missing."""
    data_pool.add_disk("sdb")
    data_pool.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
    data_pool.mount_pool("media")
    data_pool.files["/etc/fstab"] = (
        BASE_FSTAB
        + marker("data") + entry("data", DATA_UUID)
        + marker("media") + entry("media", MEDIA_UUID)
        + marker("gone") + entry("gone", GONE_UUID)
    )
    return data_pool


class TestReconcile:
    def test_mounts_every_entry_the_host_did_not_mount(self, three_pools, caplog):
        fstab_before = three_pools.fstab
        with caplog.at_level(logging.INFO, logger="src.storage.pools"):
            results = pools.reconcile_pools()

        assert results == [
            {"pool": "data", "target": "/mnt/pools/data", "status": "mounted"},
            {"pool": "media", "target": "/mnt/pools/media", "status": "already_mounted"},
            {"pool": "gone", "target": "/mnt/pools/gone", "status": "failed",
             "error": f"mount: /mnt/pools/gone: can't find UUID={GONE_UUID}."},
        ]
        assert three_pools.changes == [
            ["mkdir", "-p", "/mnt/pools/data"],
            ["chattr", "+i", "/mnt/pools/data"],
            ["mount", "/mnt/pools/data"],
            ["mkdir", "-p", "/mnt/pools/gone"],
            ["chattr", "+i", "/mnt/pools/gone"],
            ["mount", "/mnt/pools/gone"],
        ]
        assert ["btrfs", "device", "scan"] in three_pools.calls
        assert three_pools.mounted("/mnt/pools/data")
        # The pool that could not be mounted stays guarded.
        assert "/mnt/pools/gone" in three_pools.immutable
        assert three_pools.fstab == fstab_before
        assert "Pool 'data' was not mounted at startup" in caplog.text
        assert "Could not mount pool 'gone'" in caplog.text

    def test_guards_a_pool_without_managed_entry_but_does_not_mount_it(self, data_pool, caplog):
        # The device after the update: 'data' was created by an older version,
        # is not mounted and has no fstab entry; the old guest share [DATA]
        # still points at /mnt/pools/data on the SD card.
        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            results = pools.reconcile_pools()

        assert results == [{"pool": "data", "target": "/mnt/pools/data", "status": "unmanaged"}]
        assert data_pool.changes == [["chattr", "+i", "/mnt/pools/data"]]
        assert ["sync", "/mnt/pools/data"] in data_pool.calls
        assert not data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB
        assert "Pool 'data'" in caplog.text and "no UNOS-managed entry" in caplog.text
        assert "/mnt/pools/data was write-protected" in caplog.text

    def test_guard_pass_skips_mounted_pools_and_the_root_filesystem(self, data_pool):
        data_pool.mount_pool("data")  # mounted by the administrator, without a UNOS entry
        data_pool.add_disk("nvme0n1", rota=False)
        data_pool.add_btrfs("rootfs", NEW_UUID, ["/dev/nvme0n1"])
        data_pool.add_mount("/boot", "/dev/nvme0n1", "btrfs", NEW_UUID)
        data_pool.add_disk("sdb")
        data_pool.add_btrfs("not a pool name", OLD_UUID, ["/dev/sdb"])
        assert pools.reconcile_pools() == []
        assert data_pool.changes == []

    def test_guard_pass_without_a_mountpoint_directory(self, data_pool, caplog):
        data_pool.dirs.discard("/mnt/pools/data")
        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            results = pools.reconcile_pools()
        assert [r["status"] for r in results] == ["unmanaged"]
        assert data_pool.changes == []  # nothing to protect, nothing created
        assert "write-protected" not in caplog.text

    def test_guard_pass_survives_btrfs_errors(self, three_pools, caplog):
        three_pools.fail[("btrfs", "filesystem", "show")] = (1, "ERROR: cannot scan")
        results = pools.reconcile_pools()
        assert [r["status"] for r in results] == ["mounted", "already_mounted", "failed"]
        assert "Could not check for pools that are not mounted" in caplog.text

    def test_mounts_over_files_but_reports_them(self, three_pools, caplog):
        # The host would mount the entry at boot anyway; hiding the files is
        # better than leaving the pool unmounted, but it must not be silent.
        three_pools.contents["/mnt/pools/data"] = ["leftover.mkv"]
        results = pools.reconcile_pools()
        assert results[0] == {"pool": "data", "target": "/mnt/pools/data", "status": "mounted",
                              "hidden_files": "leftover.mkv"}
        assert three_pools.mounted("/mnt/pools/data")
        assert "mounted over 1 item(s) in /mnt/pools/data" in caplog.text

    def test_starts_the_apps_docker_could_not_start_without_the_pool(self, three_pools, caplog):
        docker_ = three_pools.docker
        missing = "error while mounting volume: bind source path does not exist: /mnt/pools/data/apps/x"
        docker_.add("jellyfin", labels={"unos.app.pool": "data"}, running=False, error=missing)
        docker_.add("sonarr", labels={"unos.app.pool": "data"}, running=False, error=missing,
                    policy="always")
        docker_.add("stopped-by-admin", labels={"unos.app.pool": "data"}, running=False)
        docker_.add("one-shot", labels={"unos.app.pool": "data"}, running=False, error=missing,
                    policy="no")
        docker_.add("running", labels={"unos.app.pool": "data"})
        docker_.add("media-app", labels={"unos.app.pool": "media"}, running=False, error=missing)

        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            results = pools.reconcile_pools()
            wait_for_app_starts()  # in the background, after the pools are mounted

        assert [r["status"] for r in results] == ["mounted", "already_mounted", "failed"]
        # Not media-app: 'media' was already mounted at boot.
        assert docker_.started == ["jellyfin", "sonarr"]
        assert "Started app jellyfin" in caplog.text

    def test_app_start_failures_are_logged_not_raised(self, three_pools, monkeypatch, caplog):
        three_pools.docker.add("jellyfin", labels={"unos.app.pool": "data"}, running=False,
                               error="bind source path does not exist")

        def broken_start(container_id):
            raise docker.errors.APIError("port is already allocated")

        monkeypatch.setattr(three_pools.docker, "start", broken_start)
        results = pools.reconcile_pools()
        wait_for_app_starts()
        assert results[0]["status"] == "mounted"
        assert three_pools.docker.started == []
        assert "Could not start app jellyfin of pool 'data'" in caplog.text

    def test_without_docker_the_pools_are_still_mounted(self, three_pools):
        three_pools.docker.unavailable = True
        results = pools.reconcile_pools()
        assert [r["status"] for r in results] == ["mounted", "already_mounted", "failed"]

    def test_never_raises_when_the_fstab_is_unreadable(self, data_pool, caplog):
        data_pool.fail[("cat",)] = (1, "cat: /etc/fstab: Permission denied")
        assert pools.reconcile_pools() == []
        assert "Pool mount check skipped" in caplog.text

    def test_never_raises_when_a_mount_command_blows_up(self, three_pools, monkeypatch):
        def run(cmd, **kwargs):
            if cmd[0] == "mount":
                raise subprocess.TimeoutExpired(cmd, 120)
            return three_pools.run(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", run)
        results = pools.reconcile_pools()
        assert [r["status"] for r in results] == ["failed", "already_mounted", "failed"]
        assert "timed out" in results[0]["error"]

    async def test_async_entry_point(self, three_pools):
        results = await pools.reconcile_pools_on_startup()
        assert [r["status"] for r in results] == ["mounted", "already_mounted", "failed"]

    async def test_async_entry_point_swallows_unexpected_errors(self, monkeypatch):
        def broken():
            raise RuntimeError("boom")

        monkeypatch.setattr(pools, "reconcile_pools", broken)
        assert await pools.reconcile_pools_on_startup() == []


# ---------------------------------------------------------------------------
# apps started after their pool is mounted get the checks of the Apps page
# ---------------------------------------------------------------------------

MISSING_SOURCE = "error while mounting volume: bind source path does not exist: /mnt/pools/data/apps/x"


def _app_labels(app: str, pool: str = "data") -> dict[str, str]:
    return {"unos.app": app, "unos.app.pool": pool}


def _bind(source, destination: str, *, rw: bool = True) -> dict:
    """A bind mount as ``docker inspect`` reports it."""
    return {"Type": "bind", "Source": str(source), "Destination": destination, "Mode": "",
            "RW": rw, "Propagation": "rprivate"}


@pytest.fixture
def app_checks(fake_host, monkeypatch):
    """Replace ``apps.router.prepare_app_start``; ``refuse`` maps app names to the error raised.

    ``pool_mounted`` records whether the app's pool was mounted when its
    checks finished, ``mounted_pools`` which pools were; like the real
    checks, they refuse every app of an unmounted pool. The checks of the
    apps in ``hang`` block, as on a pool disk that stopped answering, until
    ``release`` is set (5 seconds at most); ``hanging`` is set once one blocks.
    """
    checks = SimpleNamespace(seen=[], refuse={}, pool_mounted=[], mounted_pools=[], hang=set(),
                             hanging=threading.Event(), release=threading.Event())

    def prepare_app_start(container, pools=None):
        # The pool code has only ``docker inspect`` data; the app is not started yet.
        assert isinstance(container, Container)
        assert container.attrs["State"]["Running"] is False
        assert container.name not in fake_host.docker.started
        checks.seen.append(container.name)
        if container.name in checks.hang:
            checks.hanging.set()
            checks.release.wait(5)
        mounted = fake_host.mounted(f"/mnt/pools/{container.labels['unos.app.pool']}")
        checks.pool_mounted.append(mounted)
        checks.mounted_pools.append(
            sorted(m["target"] for m in fake_host.mounts if m["target"].startswith("/mnt/pools/"))
        )
        if container.name in checks.refuse:
            raise checks.refuse[container.name]
        if not mounted:
            raise HTTPException(409, f"{container.name} keeps its data on a pool that is not mounted")

    monkeypatch.setattr(apps_router, "prepare_app_start", prepare_app_start)
    yield checks
    checks.release.set()  # before fake_host waits for the threads that run the checks


class TestAppChecksBeforeStart:
    @pytest.fixture
    def stranded(self, three_pools):
        """Apps of 'data' that Docker could not start at boot because 'data' was not mounted."""
        for name in ("jellyfin", "sonarr", "radarr"):
            three_pools.docker.add(name, labels=_app_labels(name), running=False, error=MISSING_SOURCE)
        three_pools.docker.add("stopped-by-admin", labels=_app_labels("plex"), running=False)
        return three_pools

    def test_reconcile_checks_each_app_right_before_starting_it(self, stranded, app_checks):
        results = pools.reconcile_pools()
        wait_for_app_starts()

        assert results[0] == {"pool": "data", "target": "/mnt/pools/data", "status": "mounted"}
        assert app_checks.seen == ["jellyfin", "sonarr", "radarr"]
        assert app_checks.pool_mounted == [True, True, True]
        assert stranded.docker.started == ["jellyfin", "sonarr", "radarr"]

    def test_reconcile_leaves_apps_that_fail_the_checks_stopped(self, stranded, app_checks, caplog):
        app_checks.refuse["jellyfin"] = HTTPException(409, "/mnt/pools/data/media is a symbolic link")
        app_checks.refuse["sonarr"] = OSError(5, "Input/output error")

        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            results = pools.reconcile_pools()
            wait_for_app_starts()

        assert stranded.docker.started == ["radarr"]
        assert results[0] == {"pool": "data", "target": "/mnt/pools/data", "status": "mounted"}
        assert ("Did not start app jellyfin of pool 'data': /mnt/pools/data/media is a symbolic link"
                in caplog.text)
        assert "Could not start app sonarr of pool 'data': [Errno 5] Input/output error" in caplog.text

    def test_reconcile_starts_apps_once_every_pool_is_mounted(self, three_pools, app_checks):
        # An app can keep data on more than one pool, e.g. its media library on 'media'.
        three_pools.mounts = [m for m in three_pools.mounts if m["target"] != "/mnt/pools/media"]
        three_pools.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                               error=MISSING_SOURCE)

        results = pools.reconcile_pools()
        wait_for_app_starts()

        assert [r["status"] for r in results] == ["mounted", "mounted", "failed"]
        assert app_checks.mounted_pools == [["/mnt/pools/data", "/mnt/pools/media"]]
        assert three_pools.docker.started == ["jellyfin"]

    def test_checks_that_do_not_finish_skip_the_pools_remaining_apps(
        self, stranded, app_checks, monkeypatch, caplog
    ):
        # sonarr's checks hang, as on a pool disk that mounted but stopped answering.
        monkeypatch.setattr(pools, "APP_CHECK_TIMEOUT", 0.5)
        app_checks.hang.add("sonarr")

        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            pools.reconcile_pools()
            wait_for_app_starts("data")  # gives up on sonarr, whose checks still hang

        assert app_checks.hanging.is_set()
        assert app_checks.seen == ["jellyfin", "sonarr"]  # radarr is not even checked
        assert stranded.docker.started == ["jellyfin"]
        assert ("The checks before starting app sonarr of pool 'data' did not finish within "
                "0.5 seconds" in caplog.text)
        assert "Not starting it or the pool's other apps" in caplog.text
        # The checks that never finish keep a daemon thread, which never keeps
        # the supervisor from exiting.
        assert app_threads() == {"unos-pool-apps:data:sonarr": True}

        # Checks that finish too late start nothing.
        app_checks.release.set()
        wait_for_app_starts()
        assert app_checks.pool_mounted == [True, True]
        assert stranded.docker.started == ["jellyfin"]

    def test_the_real_checks_run(self, fake_host, tmp_path, monkeypatch, caplog):
        # A link planted in the pool keeps its app stopped; a missing folder
        # on the app's own pool is created again, as for a start from the Apps page.
        # The pool is a real directory here, so the pools base moves to tmp_path.
        data = tmp_path / "pools" / "data"
        (data / "apps/jellyfin/config").mkdir(parents=True)
        (data / "apps/jellyfin/cache").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (data / "media").symlink_to(outside)
        monkeypatch.setattr(get_settings(), "pools_base_path", str(data.parent))
        monkeypatch.setattr(os.path, "ismount", lambda path: os.fspath(path) == str(data))
        monkeypatch.setattr(apps_router, "list_mounted_pools", REAL_LIST_MOUNTED_POOLS)
        fake_host.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE, mounts=[
                                 _bind(data / "apps/jellyfin/config", "/config"),
                                 _bind(data / "apps/jellyfin/cache", "/cache"),
                                 _bind(data / "media", "/media", rw=False),
                             ])
        fake_host.docker.add("portainer", labels=_app_labels("portainer"), running=False,
                             error=MISSING_SOURCE, mounts=[
                                 _bind(data / "apps/portainer/data", "/data"),
                                 _bind("/var/run/docker.sock", "/var/run/docker.sock"),
                             ])

        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            started = pools._start_pool_apps("data")

        assert started == ["portainer"]
        assert fake_host.docker.started == ["portainer"]
        assert (data / "apps/portainer/data").is_dir()
        assert list(outside.iterdir()) == []
        assert "Did not start app jellyfin of pool 'data'" in caplog.text
        assert "symbolic link" in caplog.text

    def test_mount_starts_the_apps_docker_could_not_start(self, data_pool, app_checks, caplog):
        data_pool.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE)
        data_pool.docker.add("stopped-by-admin", labels=_app_labels("plex"), running=False)
        data_pool.docker.add("media-app", labels=_app_labels("sonarr", "media"), running=False,
                             error=MISSING_SOURCE)

        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            info = pools.mount_pool("data")
            wait_for_app_starts()

        assert info.mounted and info.persistent
        assert app_checks.seen == ["jellyfin"]
        # Checked (and then started) only after this call mounted the pool.
        assert app_checks.pool_mounted == [True]
        assert data_pool.docker.started == ["jellyfin"]
        assert "Started app jellyfin" in caplog.text

    def test_mount_neither_waits_for_app_checks_nor_holds_the_pool_lock(self, data_pool, app_checks):
        # The checks read the pool's files: on a disk that mounted but stopped
        # answering they hang, which must hold up neither the mount request
        # nor any other pool operation.
        data_pool.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE)
        app_checks.hang.add("jellyfin")

        started = time.monotonic()
        info = pools.mount_pool("data")
        assert time.monotonic() - started < 2
        assert info.mounted and info.persistent
        assert app_checks.hanging.wait(5)
        # Neither would keep the supervisor from exiting.
        assert app_threads() == {"unos-pool-apps:data": True, "unos-pool-apps:data:jellyfin": True}

        started = time.monotonic()
        assert [pool.name for pool in pools.list_pools()] == ["data"]
        assert not pools.unmount_pool("data").mounted  # needs the pool lock
        assert time.monotonic() - started < 2

        app_checks.release.set()
        wait_for_app_starts()
        # Checked right before the start, the app finds its pool unmounted.
        assert app_checks.pool_mounted == [False]
        assert data_pool.docker.started == []

    def test_mount_succeeds_when_no_thread_can_be_started(self, data_pool, app_checks, monkeypatch,
                                                         caplog):
        def no_thread(thread):
            raise RuntimeError("can't start new thread")

        data_pool.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE)
        monkeypatch.setattr(threading.Thread, "start", no_thread)

        assert pools.mount_pool("data").mounted
        assert "Could not start the apps of pool 'data': can't start new thread" in caplog.text
        assert app_checks.seen == []

    def test_mounting_a_mounted_pool_starts_nothing(self, data_pool, app_checks):
        data_pool.mount_pool("data")  # mounted by the host, without a UNOS entry
        data_pool.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE)

        info = pools.mount_pool("data")
        wait_for_app_starts()

        assert info.mounted and info.persistent
        assert app_checks.seen == []
        assert data_pool.docker.started == []

    @pytest.mark.parametrize("failure", ["checks", "unexpected", "docker-start", "docker-down"])
    def test_app_failures_never_fail_the_mount(self, data_pool, app_checks, monkeypatch, caplog, failure):
        data_pool.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE)
        if failure == "checks":
            app_checks.refuse["jellyfin"] = HTTPException(409, "not on a mounted storage pool")
        elif failure == "unexpected":
            app_checks.refuse["jellyfin"] = RuntimeError("boom")
        elif failure == "docker-start":
            def broken_start(container_id):
                raise docker.errors.APIError("port is already allocated")

            monkeypatch.setattr(data_pool.docker, "start", broken_start)
        else:
            data_pool.docker.unavailable = True

        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            info = pools.mount_pool("data")
            wait_for_app_starts()

        assert info.mounted and info.persistent
        assert data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
        assert data_pool.docker.started == []
        assert "app" in caplog.text and "pool 'data'" in caplog.text

    def test_mount_endpoint_succeeds_when_an_app_fails_its_checks(self, api, data_pool, app_checks):
        data_pool.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                             error=MISSING_SOURCE)
        app_checks.refuse["jellyfin"] = HTTPException(409, "/mnt/pools/data/media is a symbolic link")

        response = api.post("/api/v1/storage/pools/data/mount")
        wait_for_app_starts()

        assert response.status_code == 200, response.text
        assert response.json()["mounted"] is True
        assert app_checks.seen == ["jellyfin"]
        assert data_pool.docker.started == []


class TestLifespanRunsReconcile:
    @pytest.fixture
    def quiet_loops(self, monkeypatch):
        """Replace the background loops so the lifespan can run without side effects."""
        main = importlib.import_module("src.main")  # the module, not the app

        async def noop() -> None:
            return None

        # check_host_tools would run a real host command (nsenter) in a unit test,
        # and so would reapply_wol_settings (ethtool) with Wake-on-LAN choices.
        for name in ("start_scheduler", "stop_scheduler", "start_health_monitors",
                     "stop_health_monitors", "start_ups_monitor", "stop_ups_monitor",
                     "check_host_tools", "reapply_wol_settings"):
            monkeypatch.setattr(main, name, noop)
        # The SMB startup reconcile would import /tmp/test_smb.conf and run host commands.
        from unittest.mock import AsyncMock

        monkeypatch.setattr(main, "reconcile_smb_on_startup", AsyncMock(return_value=None))
        return main

    def test_lifespan_reconciles_pools(self, quiet_loops, monkeypatch):
        calls = []

        async def fake_reconcile():
            calls.append("reconcile")
            return []

        monkeypatch.delenv("UNOS_DISABLE_BACKGROUND_TASKS", raising=False)
        monkeypatch.setattr(quiet_loops, "reconcile_pools_on_startup", fake_reconcile)
        with TestClient(quiet_loops.app):
            assert calls == ["reconcile"]

    def test_disabled_background_tasks_skip_reconcile(self, quiet_loops, monkeypatch):
        calls = []

        async def fake_reconcile():
            calls.append("reconcile")
            return []

        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
        monkeypatch.setattr(quiet_loops, "reconcile_pools_on_startup", fake_reconcile)
        with TestClient(quiet_loops.app):
            pass
        assert calls == []

    def test_startup_does_not_wait_for_the_checks_of_apps(
        self, quiet_loops, three_pools, app_checks, monkeypatch
    ):
        # 'data' mounts at startup, but its disk stops answering: the checks
        # before starting jellyfin hang. The API must come up regardless.
        three_pools.docker.add("jellyfin", labels=_app_labels("jellyfin"), running=False,
                               error=MISSING_SOURCE)
        app_checks.hang.add("jellyfin")
        monkeypatch.delenv("UNOS_DISABLE_BACKGROUND_TASKS", raising=False)

        started = time.monotonic()
        with TestClient(quiet_loops.app) as client:
            startup = time.monotonic() - started
            assert three_pools.mounted("/mnt/pools/data")  # mounted before startup completed
            assert app_checks.hanging.wait(5)
            assert client.get("/api/v1/health").status_code == 200
        assert startup < 2

        app_checks.release.set()
        wait_for_app_starts()
        assert three_pools.docker.started == ["jellyfin"]


# ---------------------------------------------------------------------------
# pool health
# ---------------------------------------------------------------------------


class TestPoolHealth:
    @pytest.mark.parametrize(
        ("mounted", "missing", "errors", "expected"),
        [
            (None, 0, 0, "unknown"),
            (False, 0, None, "unmounted"),
            (False, 1, None, "unmounted"),
            (True, 1, 0, "degraded"),
            (True, 1, None, "degraded"),
            (True, 0, None, "unknown"),
            (True, 0, 3, "error"),
            (True, 0, 0, "healthy"),
        ],
    )
    def test_classification(self, mounted, missing, errors, expected):
        assert pools.classify_pool_health(
            mounted=mounted, missing_devices=missing, device_errors=errors
        ) == expected

    @pytest.mark.parametrize(
        ("mounted", "missing", "errors", "expected"),
        [
            (True, 0, 0, "error"),  # no counter moves when btrfs aborts a transaction
            (True, 1, 0, "error"),
            (True, 0, None, "error"),
            (False, 0, None, "unmounted"),
            (None, 0, None, "unknown"),
        ],
    )
    def test_forced_read_only_classification(self, mounted, missing, errors, expected):
        assert pools.classify_pool_health(
            mounted=mounted, missing_devices=missing, device_errors=errors, forced_read_only=True
        ) == expected

    @pytest.mark.parametrize(
        ("vfs", "fs", "read_only", "forced"),
        [
            ("rw,noatime", "rw,space_cache=v2", False, False),
            ("rw,noatime", "ro,space_cache=v2", True, True),  # switched by btrfs after an error
            ("ro,noatime", "ro,space_cache=v2", True, False),  # mount -o ro
            (None, None, False, False),  # old findmnt without the columns
        ],
    )
    def test_host_mount_read_only_flags(self, fake_host, vfs, fs, read_only, forced):
        fake_host.add_disk("sdb")
        fake_host.add_mount("/srv/x", "/dev/sdb", "btrfs", MEDIA_UUID, vfs_options=vfs, fs_options=fs)
        [mount] = [m for m in disks_module.host_mounts() if m.target == "/srv/x"]
        assert (mount.read_only, mount.forced_read_only) == (read_only, forced)
        assert ["findmnt", "-J", "-l", "-o", "TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN,VFS-OPTIONS,FS-OPTIONS"] \
            in fake_host.calls

    def test_parse_btrfs_show(self):
        output = (
            f"Label: 'data'  uuid: {DATA_UUID}\n"
            "\tTotal devices 2 FS bytes used 1048576\n"
            "\tdevid    1 size 4000787030016 used 2155872256 path /dev/sda\n"
            "\tdevid    2 size 4000787030016 used 2155872256 path /dev/sdb\n"
            "\n"
            f"Label: none  uuid: {GONE_UUID}\n"
            "\tTotal devices 1 FS bytes used 0\n"
            "\tdevid    1 size 1000 used 0 path /dev/sdc1\n"
            "\n"
            f"Label: 'my media'  uuid: {MEDIA_UUID.upper()}\n"
            "\tTotal devices 2 FS bytes used 4096\n"
            "\tdevid    1 size 2000 used 0 path /dev/sdd\n"
            "\t*** Some devices missing\n"
            "\n"
            f"Label: 'mirror'  uuid: {OLD_UUID}\n"
            "\tTotal devices 2 FS bytes used 4096\n"
            "\tdevid    1 size 3000 used 0 path /dev/sde\n"
            "\tdevid    2 size 0 used 0 path <missing disk> MISSING\n"
        )
        data, unlabeled, media, mirror = pools.parse_btrfs_show(output)
        assert (data.label, data.uuid, data.devices, data.missing_devices) == (
            "data", DATA_UUID, ["/dev/sda", "/dev/sdb"], 0)
        assert data.devices_size == 2 * 4000787030016 and data.bytes_used == 1048576
        assert (unlabeled.label, unlabeled.devices) == (None, ["/dev/sdc1"])
        assert (media.label, media.uuid, media.missing_devices) == ("my media", MEDIA_UUID, 1)
        assert (mirror.devices, mirror.missing_devices) == (["/dev/sde"], 1)

    def test_list_pools_reports_state_of_each_pool(self, data_pool):
        data_pool.add_disk("sdb")
        data_pool.add_disk("sdc")
        data_pool.add_disk("sdd")
        data_pool.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
        data_pool.add_btrfs("broken", OLD_UUID, ["/dev/sdc"])
        data_pool.add_btrfs("half", GONE_UUID, ["/dev/sdd"], missing=1, profile="raid1")
        data_pool.add_btrfs(None, NEW_UUID, [])  # unlabeled filesystems are not pools
        for name in ("media", "broken", "half"):
            data_pool.mount_pool(name)
        data_pool.device_errors["/mnt/pools/broken"] = 7
        data_pool.files["/etc/fstab"] = BASE_FSTAB + marker("media") + entry("media", MEDIA_UUID)

        result = {p.name: p for p in pools.list_pools()}

        assert list(result) == ["data", "media", "broken", "half"]
        assert (result["data"].health, result["data"].mounted, result["data"].mountpoint) == (
            "unmounted", False, None)
        assert result["data"].total_size == 4_000_787_030_016  # from btrfs show while unmounted
        assert (result["media"].health, result["media"].persistent) == ("healthy", True)
        assert (result["broken"].health, result["broken"].device_errors) == ("error", 7)
        assert (result["half"].health, result["half"].missing_devices) == ("degraded", 1)
        assert result["half"].raid_level == "raid1"
        assert not result["broken"].persistent

    def test_unreadable_error_counters_are_unknown_not_healthy(self, data_pool):
        data_pool.mount_pool("data")
        data_pool.fail[("btrfs", "device", "stats")] = (1, "ERROR: cannot get stats")
        info = pools.get_pool("data")
        assert (info.mounted, info.health, info.device_errors) == (True, "unknown", 0)

    def test_unreadable_mount_table_is_unknown(self, data_pool):
        data_pool.fail[("findmnt",)] = (1, "findmnt: cannot open /proc/self/mountinfo")
        [info] = pools.list_pools()
        assert (info.mounted, info.health) == (False, "unknown")

    def test_btrfs_root_filesystem_is_not_a_pool(self, fake_host):
        fake_host.add_disk("nvme0n1", rota=False)
        fake_host.add_btrfs("rootfs", NEW_UUID, ["/dev/nvme0n1"])
        fake_host.add_mount("/boot", "/dev/nvme0n1", "btrfs", NEW_UUID)
        assert pools.list_pools() == []
        with pytest.raises(pools.PoolNotFoundError):
            pools.get_pool("rootfs")

    def test_get_pool_unknown(self, fake_host):
        with pytest.raises(pools.PoolNotFoundError):
            pools.get_pool("nope")


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def api(fake_host):
    with admin_client() as client:
        yield client


class TestPoolEndpoints:
    def test_mount_and_unmount(self, api, data_pool):
        response = api.post("/api/v1/storage/pools/data/mount")
        assert response.status_code == 200, response.text
        body = PoolInfo(**response.json())
        assert (body.mounted, body.persistent, body.health) == (True, True, "healthy")

        response = api.post("/api/v1/storage/pools/data/unmount")
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["mounted"], body["persistent"], body["health"]) == (False, False, "unmounted")
        assert data_pool.fstab == BASE_FSTAB

    def test_unmount_busy_is_409(self, api, data_pool):
        pools.mount_pool("data")
        data_pool.busy.add("/mnt/pools/data")
        response = api.post("/api/v1/storage/pools/data/unmount")
        assert response.status_code == 409
        assert "busy" in response.json()["detail"]

    @pytest.mark.parametrize(
        ("method", "url"),
        [("post", "/api/v1/storage/pools/data/unmount"), ("delete", "/api/v1/storage/pools/data")],
    )
    def test_pool_used_by_a_container_is_409(self, api, data_pool, method, url):
        pools.mount_pool("data")
        data_pool.docker.add("transmission", sources=["/mnt/pools/data/apps/transmission"])
        response = getattr(api, method)(url)
        assert response.status_code == 409
        assert "transmission" in response.json()["detail"]
        assert data_pool.mounted("/mnt/pools/data")

    def test_pool_still_in_use_after_unmounting_is_409(self, api, data_pool):
        pools.mount_pool("data")
        data_pool.held.add(DATA_UUID)
        response = api.post("/api/v1/storage/pools/data/unmount")
        assert response.status_code == 409
        assert "mounted again" in response.json()["detail"]
        assert api.get("/api/v1/storage/pools/data").json()["mounted"] is True

    def test_mount_over_files_is_409(self, api, data_pool):
        data_pool.contents["/mnt/pools/data"] = ["notes.txt"]
        response = api.post("/api/v1/storage/pools/data/mount")
        assert response.status_code == 409
        assert "notes.txt" in response.json()["detail"]
        assert data_pool.changes == []

    def test_mount_unknown_pool_is_404(self, api, fake_host):
        assert api.post("/api/v1/storage/pools/nope/mount").status_code == 404

    def test_mount_conflict_is_409(self, api, data_pool):
        data_pool.fs_by_label("data")["missing"] = 1
        assert api.post("/api/v1/storage/pools/data/mount").status_code == 409

    def test_invalid_pool_name_is_400(self, api, fake_host):
        assert api.post("/api/v1/storage/pools/bad%20name/mount").status_code == 400

    def test_list_and_get_pools_expose_new_fields(self, api, data_pool):
        response = api.get("/api/v1/storage/pools")
        assert response.status_code == 200
        [pool] = response.json()
        assert {"mounted", "persistent", "health", "missing_devices", "device_errors"} <= set(pool)
        assert pool["health"] == "unmounted"
        assert api.get("/api/v1/storage/pools/data").json()["uuid"] == DATA_UUID
        assert api.get("/api/v1/storage/pools/nope").status_code == 404

    def test_delete_is_non_destructive(self, api, data_pool):
        pools.mount_pool("data")
        response = api.delete("/api/v1/storage/pools/data")
        assert response.status_code == 200
        assert response.json() == {"status": "deleted"}
        assert not any(c[0] in ("mkfs.btrfs", "wipefs") for c in data_pool.calls)
        assert data_pool.fstab == BASE_FSTAB

    def test_the_endpoints_that_change_the_managed_pools_apply_smb_conf(self, api, data_pool):
        """ADR-0248: the managed pools decide which SMB shares are served, so create, mount,
        unmount and remove apply smb.conf, also when they fail (an operation may change the fstab
        before it fails); reads and requests refused before the operation do not. What the apply
        does is tested in test_smb_integration.py."""
        def applies(method, url, status, **kwargs) -> int:
            before = data_pool.smb_applies
            response = getattr(api, method)(url, **kwargs)
            assert response.status_code == status, response.text
            return data_pool.smb_applies - before

        data_pool.add_disk("sdb")
        tank = {"name": "tank", "devices": ["/dev/sdb"], "raid_level": "single", "confirm_name": "tank"}
        assert applies("get", "/api/v1/storage/pools", 200) == 0
        assert applies("get", "/api/v1/storage/pools/data", 200) == 0
        assert applies("post", "/api/v1/storage/pools/data/mount", 200) == 1
        data_pool.busy.add("/mnt/pools/data")
        assert applies("post", "/api/v1/storage/pools/data/unmount", 409) == 1
        data_pool.busy.clear()
        assert applies("post", "/api/v1/storage/pools/data/unmount", 200) == 1
        assert applies("delete", "/api/v1/storage/pools/data", 200) == 1
        assert applies("post", "/api/v1/storage/pools", 400, json={**tank, "confirm_name": "other"}) == 0
        assert applies("post", "/api/v1/storage/pools", 200, json=tank) == 1
        assert applies("post", "/api/v1/storage/pools/bad%20name/unmount", 400) == 0

    def test_non_admin_is_forbidden(self, api, data_pool):
        api.user.is_admin = False
        for method, url in (
            ("post", "/api/v1/storage/pools/data/mount"),
            ("post", "/api/v1/storage/pools/data/unmount"),
            ("get", "/api/v1/storage/pools"),
        ):
            assert getattr(api, method)(url).status_code == 403
        assert data_pool.changes == []

    def test_anonymous_is_rejected(self, fake_host, data_pool):
        client = TestClient(app)
        assert client.post("/api/v1/storage/pools/data/mount").status_code in (401, 403)
        assert data_pool.changes == []


# ---------------------------------------------------------------------------
# RAID monitor (health router)
# ---------------------------------------------------------------------------


class TestRaidMonitorHealth:
    """The RAID monitor (alerts, /health/raid, /health/full) watches the btrfs pools."""

    @pytest.fixture
    def mirror(self, fake_host):
        """'mirror': raid1 on sdb + sdc, mounted at /mnt/pools/mirror."""
        fake_host.add_disk("sdb")
        fake_host.add_disk("sdc")
        fake_host.add_btrfs("mirror", MEDIA_UUID, ["/dev/sdb", "/dev/sdc"], profile="raid1")
        fake_host.mount_pool("mirror")
        return fake_host

    @staticmethod
    async def one_round(monkeypatch):
        """Run one round of the RAID monitor loop; returns the alerts it sent."""
        alerts = []

        async def fake_alert(pool, status, errors, details):
            alerts.append((pool, status, errors, details))

        async def stop_after_one_round(_seconds):
            health_router._raid_monitor_running = False

        monkeypatch.setattr(health_router, "send_raid_alert", fake_alert)
        monkeypatch.setattr(health_router, "load_previous_raid_status", dict)
        monkeypatch.setattr(health_router, "save_raid_status", lambda status: None)
        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)
        monkeypatch.setattr(health_router, "_raid_monitor_running", True)
        await health_router.raid_monitor_loop()
        return alerts

    def test_parse_device_stats_returns_none_on_failure(self, fake_host):
        assert health_router.parse_btrfs_device_stats("/nowhere") is None

    async def test_only_btrfs_pools_are_monitored(self, data_pool):
        # App data an older version kept in a plain directory on the SD card.
        data_pool.dirs.add("/mnt/pools/default")
        data_pool.contents["/mnt/pools/default"] = ["transmission"]
        assert [info.pool for info in await health_router.check_raid_health()] == ["data"]

    async def test_unmounted_pool_is_not_healthy(self, data_pool):
        [info] = await health_router.check_raid_health()
        assert (info.pool, info.status, info.total_errors, info.devices) == ("data", "unmounted", 0, [])
        assert info.mount_point == "/mnt/pools/data"

    async def test_mounted_pool_reports_counters(self, data_pool):
        data_pool.mount_pool("data")
        data_pool.device_errors["/mnt/pools/data"] = 4
        [info] = await health_router.check_raid_health()
        # Same health as the Storage page reports for the pool.
        assert (info.status, info.total_errors, info.profile) == ("error", 4, "single")
        assert pools.get_pool("data").health == "error"
        [device] = info.devices
        assert (device.device, device.write_io_errs, device.has_errors) == ("/dev/sda", 4, True)

    async def test_healthy_mirror(self, mirror):
        [info] = await health_router.check_raid_health()
        assert (info.pool, info.status, info.profile, info.missing_devices) == (
            "mirror", "healthy", "raid1", 0)
        assert [d.device for d in info.devices] == ["/dev/sdb", "/dev/sdc"]

    async def test_mounted_pool_with_a_missing_device_is_degraded(self, mirror, monkeypatch):
        # The USB disk sdc vanished: btrfs still reports zero error counters
        # until I/O fails, but 'btrfs filesystem show' lists it as missing.
        fs = mirror.fs_by_label("mirror")
        fs["devices"].remove("/dev/sdc")
        fs["missing"] = 1
        [info] = await health_router.check_raid_health()
        assert (info.status, info.missing_devices, info.total_errors) == ("degraded", 1, 0)

        [(pool, status, errors, details)] = await self.one_round(monkeypatch)
        assert (pool, status) == ("mirror", "degraded")
        assert "1 device(s) of the pool are missing." in details

    async def test_filesystem_forced_read_only_is_an_error(self, mirror, monkeypatch):
        # A transaction abort makes btrfs read-only; no error counter moves.
        mirror.mounts[-1]["fs-options"] = "ro,space_cache=v2,subvolid=5,subvol=/"
        [info] = await health_router.check_raid_health()
        assert (info.status, info.read_only, info.total_errors) == ("error", True, 0)
        storage_view = pools.get_pool("mirror")
        assert (storage_view.health, storage_view.read_only) == ("error", True)

        [(_, status, _, details)] = await self.one_round(monkeypatch)
        assert status == "error"
        assert "btrfs made the filesystem read-only after a serious error" in details
        assert info.forced_read_only

    async def test_forced_read_only_is_alerted_after_an_earlier_problem(self, mirror, monkeypatch):
        # The status file keeps the previous round, as on the device.
        store: dict = {}
        alerts = []

        async def fake_alert(pool, status, errors, details):
            alerts.append((pool, status, errors, details))

        async def stop_after_one_round(_seconds):
            health_router._raid_monitor_running = False

        def save(status):
            store.clear()
            store.update(status)

        monkeypatch.setattr(health_router, "send_raid_alert", fake_alert)
        monkeypatch.setattr(health_router, "load_previous_raid_status", lambda: dict(store))
        monkeypatch.setattr(health_router, "save_raid_status", save)
        monkeypatch.setattr(health_router.asyncio, "sleep", stop_after_one_round)

        async def one_round():
            alerts.clear()
            monkeypatch.setattr(health_router, "_raid_monitor_running", True)
            await health_router.raid_monitor_loop()
            return list(alerts)

        # Round 1: one disk of the mirror is gone.
        fs = mirror.fs_by_label("mirror")
        fs["devices"].remove("/dev/sdc")
        fs["missing"] = 1
        [(_, status, _, _)] = await one_round()
        assert status == "degraded"
        # Round 2: btrfs then makes the filesystem read-only; no counter moves.
        mirror.mounts[-1]["fs-options"] = "ro,space_cache=v2,subvolid=5,subvol=/"
        [(_, status, errors, details)] = await one_round()
        assert (status, errors) == ("error", 0)
        assert "btrfs made the filesystem read-only after a serious error" in details
        # Round 3: nothing new, no alert.
        assert await one_round() == []

    async def test_deliberately_read_only_mount_with_errors_is_not_called_forced(
        self, mirror, monkeypatch
    ):
        # Mounted read-only on purpose; the error counters make it an error,
        # but btrfs did not force anything.
        mirror.mounts[-1].update({"vfs-options": "ro,noatime", "fs-options": "ro"})
        mirror.device_errors["/mnt/pools/mirror"] = 3
        [info] = await health_router.check_raid_health()
        assert (info.status, info.read_only, info.forced_read_only) == ("error", True, False)

        [(_, status, errors, details)] = await self.one_round(monkeypatch)
        assert (status, errors) == ("error", 3)
        assert "The filesystem is mounted read-only." in details
        assert "serious error" not in details

    async def test_deliberately_read_only_mount_is_not_an_error(self, mirror):
        mirror.mounts[-1].update({"vfs-options": "ro,noatime", "fs-options": "ro"})
        [info] = await health_router.check_raid_health()
        assert (info.status, info.read_only) == ("healthy", True)

    async def test_failing_stats_are_unknown(self, data_pool):
        data_pool.mount_pool("data")
        data_pool.fail[("btrfs", "device", "stats")] = (1, "ERROR: ioctl failed")
        [info] = await health_router.check_raid_health()
        assert (info.status, info.total_errors, info.devices) == ("unknown", 0, [])

    async def test_unreadable_mount_table_is_unknown(self, data_pool):
        data_pool.fail[("findmnt",)] = (1, "findmnt: failed")
        [info] = await health_router.check_raid_health()
        assert info.status == "unknown"

    async def test_unmounted_pool_triggers_an_alert(self, data_pool, monkeypatch):
        data_pool.dirs.add("/mnt/pools/default")  # not a pool: no alert for it
        alerts = await self.one_round(monkeypatch)
        assert alerts == [("data", "unmounted", 0, "Nothing is mounted at /mnt/pools/data.")]

    def test_endpoints(self, api, data_pool):
        data_pool.dirs.add("/mnt/pools/default")
        response = api.get("/api/v1/health/raid")
        assert response.status_code == 200, response.text
        assert [pool["pool"] for pool in response.json()] == ["data"]
        assert api.get("/api/v1/health/raid/data").json()["status"] == "unmounted"
        assert api.get("/api/v1/health/raid/default").status_code == 404
        assert api.get("/api/v1/health/raid/bad%20name").status_code == 400

    def test_endpoint_reports_btrfs_failures(self, api, data_pool):
        data_pool.fail[("btrfs", "filesystem", "show")] = (1, "ERROR: cannot scan")
        response = api.get("/api/v1/health/raid")
        assert response.status_code == 500
        assert "cannot scan" in response.json()["detail"]

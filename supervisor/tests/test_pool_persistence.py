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
import re
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
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
from datetime import UTC

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
# The root of a container's mount namespace, the overlay of its image (Docker's overlay2), as
# findmnt --task prints it for a process in that namespace.
CONTAINER_ROOT = {"target": "/", "source": "overlay", "fstype": "overlay", "uuid": None, "maj:min": "0:51",
                  "fsroot": "/", "vfs-options": "rw,relatime", "fs-options": "rw"}
# The mount table of a container that keeps nothing on a pool (the web UI's).
WEBUI_MOUNTS = [
    CONTAINER_ROOT,
    {"target": "/srv", "source": "/dev/mmcblk1p1[/opt/unos/webui/build]", "fstype": "ext4",
     "uuid": ROOT_UUID, "maj:min": "179:2", "fsroot": "/opt/unos/webui/build",
     "vfs-options": "ro,relatime", "fs-options": "rw,commit=600"},
]


def marker(name: str) -> str:
    return f"# unos-pool:{name} (managed by UNOS)\n"


def entry(name: str, uuid: str) -> str:
    return f"UUID={uuid} /mnt/pools/{name} btrfs {OPTIONS} 0 0\n"


def _forbidden_subprocess(*args, **kwargs):
    raise AssertionError(f"test tried to run a real command: {args!r}")


@dataclass(frozen=True)
class HttpAnswer:
    """What a service answers the host's curl (FakeHost.network): its status and body."""

    status: int
    body: str = ""


@dataclass(frozen=True)
class CurlFailure:
    """A request of the host's curl that got no answer (FakeHost.network): curl's exit status and
    message, which it prints as ``curl: (<status>) <message>``."""

    code: int
    message: str


# In a double-quoted value of curl's config, a backslash before t, n, r or v stands for that
# control character, and before any other character for the character itself.
_CURL_CONFIG_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "v": "\v"}
# The options of a config that curl reads as the request (notifications/router.py, ADR-0336).
_CURL_REQUEST_OPTIONS = {"url": "url", "request": "method", "data-raw": "body"}


def parse_curl_config(config: str) -> SimpleNamespace:
    """The request that a config of ``curl --config -`` describes, read as curl reads it.

    One option per line, ``name = "value"``, the value in double quotes with curl's escapes; a
    header is ``Name: value``, or ``Name;`` for one without a value. The request has the url, the
    method, the headers as (name, value) in their order, the body, and the config itself. A line
    that is not such an option, or an option that is not part of a request, fails the test.
    """
    request = SimpleNamespace(url=None, method=None, headers=[], body=None, config=config)
    for line in config.split("\n"):  # curl reads up to each line break
        if not line:
            continue
        name, separator, quoted = line.partition(" = ")
        assert separator and quoted.startswith('"'), f"not a quoted option of curl's config: {line!r}"
        value, index = [], 1
        while index < len(quoted) and quoted[index] != '"':
            if quoted[index] == "\\" and index + 1 < len(quoted):
                index += 1
                value.append(_CURL_CONFIG_ESCAPES.get(quoted[index], quoted[index]))
            else:
                value.append(quoted[index])
            index += 1
        assert quoted[index:] == '"', f"the value does not end the line: {line!r}"
        if name == "header":
            header = "".join(value)
            header_name, colon, header_value = header.partition(": ")
            if not colon:
                assert header.endswith(";"), f"not a header: {line!r}"
                header_name = header[:-1]
            request.headers.append((header_name, header_value))
        else:
            assert name in _CURL_REQUEST_OPTIONS, f"curl option the fake does not know: {line!r}"
            setattr(request, _CURL_REQUEST_OPTIONS[name], "".join(value))
    return request


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
        # Symbolic links and their targets. /etc/localtime names the time zone, as on
        # a Debian host installed in UTC; timedatectl set-timezone writes a relative link.
        self.links: dict[str, str] = {"/etc/localtime": "/usr/share/zoneinfo/Etc/UTC"}
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
        # UUIDs of filesystems that stay mounted after the host unmounts them where
        # no mount table shows them (e.g. a mount namespace that no process is in):
        # only sysfs knows (pools._filesystem_active).
        self.held: set[str] = set()
        # The mount namespaces of host processes other than the host's (PID 1's), by
        # the PID that lsns names for each: their mount tables, as findmnt --task
        # prints them. A systemd service with sandboxing keeps a copy of a pool's
        # mount there, which the host's umount does not reach (copy_into_namespace);
        # a container keeps a bind mount of a folder of the pool (bind_into_namespace).
        self.namespaces: dict[int, list[dict]] = {}
        # Processes that lsns lists but that exit before their mount table is read.
        self.exited: set[int] = set()
        # Namespaces whose copies of a mount are peers: unmounting the copy in one
        # of them unmounts it in the others as well (mount propagation).
        self.namespace_peers: list[set[int]] = []
        self.fail: dict[tuple[str, ...], tuple[int, str]] = {}
        self.calls: list[list[str]] = []
        self.changes: list[list[str]] = []
        self.mkfs_uuid = NEW_UUID
        # Programs the host lacks (host.missing_host_binaries; timeout(1) cannot run them).
        self.missing_programs: set[str] = set()
        # What the host's network answers its curl, which sends a notification channel's request
        # when the container cannot connect (ADR-0336): a function of the request (as
        # parse_curl_config reads it) that returns HttpAnswer or CurlFailure. None: the test
        # expects no curl, and one fails it.
        self.network = None
        # The requests that curl was given, in order.
        self.http_requests: list[SimpleNamespace] = []
        self.docker = FakeDocker()
        # Whether a directory of the host is the root of a btrfs subvolume (stat prints its inode
        # number, 256): test_volumes.FakeSubvolumes knows them.
        self.subvolume_root = lambda path: False
        # How often the storage endpoints applied smb.conf after a pool change (install_fake_host).
        self.smb_applies = 0
        # The power of the whole disks (ADR-0378): the paths of those in standby. A command that
        # reads a sleeping disk, or sends it a command that spins it up, wakes it and is recorded
        # in woken as (disk, command): btrfs filesystem show without --mounted (libblkid reads
        # every block device), wipefs, blkid -p, mount, mkfs.btrfs and hdparm -S. hdparm -C reads
        # a disk's power mode on the host and leaves it asleep; smartctl runs in the container
        # (run_in_container), and only with -n standby or -n idle, which leave them asleep too.
        self.asleep: set[str] = set()
        self.woken: list[tuple[str, list[str]]] = []
        # The power condition of an EPC disk (ACS: Extended Power Conditions), by its path, where it
        # is one that CHECK POWER MODE answers with a value hdparm -C does not name (" drive state
        # is:  unknown", exit status 0): "idle_a", "idle_b", "idle_c" (81h-83h, awake) or "standby_y"
        # (01h; such a disk is in asleep too). smartctl -n names them; a command that reads the disk
        # makes it active.
        self.epc: dict[str, str] = {}
        # Disks that stopped answering: hdparm -C on one hangs in the kernel, where the host's timeout
        # cannot end it, so run_on_host's limit runs out (TimeoutExpired), and the hdparm stays on the
        # host (stuck_hdparm: the disks whose hdparm -C still runs there, which pgrep finds).
        self.hung: set[str] = set()
        self.stuck_hdparm: set[str] = set()
        # The disks whose identity smartctl read (IDENTIFY DEVICE, which restarts a disk's standby
        # timer), in order.
        self.identified: list[str] = []
        # The standby timer that hdparm -S gave each disk, by its path: the -S value.
        self.standby_timers: dict[str, int] = {}
        # The automatic offline data collection of the ATA disks that have it (SFF-8035i; smartctl -c
        # reads it, -o on and -o off switch it, ADR-0378), by the disk's path: True while it is on (the
        # drive scans its surface every 4 hours, suspends the scan for each command and resumes it, so
        # that it does not sleep meanwhile), False while off. A disk not here has no such feature: its
        # capabilities lack "Auto Offline data collection on/off support", and -o is refused.
        self.offline_collection: dict[str, bool] = {}
        # The ATA disks without working SMART, by their path: "disabled" for one whose SMART is turned off
        # (smartctl -s off, a BIOS or an enclosure), "unsupported" for one without SMART. Neither runs an
        # automatic offline data collection: it is a SMART timer function, and SMART DISABLE OPERATIONS
        # stops it.
        self.smart_off: dict[str, str] = {}
        # A function of a command run in the container that answers it instead of the fake (a
        # CompletedProcess), or raises (subprocess.TimeoutExpired, as run_in_container at its timeout);
        # None from it: the fake answers.
        self.container_answer = None
        # udev's ID_SERIAL_SHORT of a disk, by its path, where it is not lsblk's serial (None: none).
        self.udev_serials: dict[str, str | None] = {}
        # The commands run in the container (smartctl), in order.
        self.container_calls: list[list[str]] = []
        # The supervisor's container's own /dev (ADR-0414): the device number ("8:1") of each whole
        # disk's node, by its path. Docker made a node of every disk that the host had when the
        # container started (add_disk), none of a disk attached later (attach_disk), and kept the
        # node of one detached since (detach_disk). smartctl opens the node, so it reads the disk
        # that has the node's number now (run_in_container). host.container_disk_node changes it
        # through the fake's host_disk_number, container_node_number and make_container_node
        # (install_fake_host), each node it made recorded in nodes_made as (path, number); smartctl
        # fails the test unless container_disk_node asked for its disk just before (disk_asked).
        self.container_dev: dict[str, str] = {}
        self.nodes_made: list[tuple[str, str]] = []
        self.disk_asked: str | None = None
        # The SMART self-tests started (smartctl -t), as (disk, test type).
        self.self_tests: list[tuple[str, str]] = []
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
                 pttype=None, fstype=None, label=None, uuid=None, wwn=None, majmin=None,
                 attached_later=False):
        """A whole disk of the host, there when the supervisor's container started: its /dev has
        the disk's node (container_dev), unless ``attached_later`` (attach_disk). ``majmin`` is its
        device number, by default one that no other device of the fake has."""
        node = {
            "name": name, "kname": name, "path": f"/dev/{name}", "pkname": None,
            "type": "disk", "size": size, "rota": rota, "model": model, "serial": serial,
            "wwn": wwn, "fstype": fstype, "label": label, "uuid": uuid, "partuuid": None,
            "pttype": pttype, "maj:min": majmin or self._majmin(name),
        }
        self.tree.append(node)
        if not attached_later:
            self.container_dev[node["path"]] = node["maj:min"]
        return node

    def attach_disk(self, name, **kwargs):
        """A whole disk attached after the supervisor's container started, such as a USB disk: the
        host lists it, and the container's /dev has no node of it, or still the node of the disk
        that had its name before (detach_disk)."""
        return self.add_disk(name, attached_later=True, **kwargs)

    def detach_disk(self, name):
        """The whole disk ``name`` leaves the host; the container's /dev keeps its node."""
        self.tree = [top for top in self.tree if top["name"] != name]

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

    def add_btrfs(self, label, uuid, devices, *, missing=0, used=1_048_576, profile="single",
                  default_subvolume=5):
        """A btrfs filesystem; ``default_subvolume`` is the one a mount without subvol= shows
        (5, the top level, unless ``btrfs subvolume set-default`` changed it)."""
        # The anonymous device number that the mounts of the filesystem show (MAJ:MIN).
        fs = {"label": label, "uuid": uuid, "devices": list(devices), "missing": missing,
              "used": used, "profile": profile, "default_subvolume": default_subvolume,
              "maj:min": f"0:{44 + len(self.filesystems)}"}
        self.filesystems.append(fs)
        return fs

    def add_mount(self, target, source, fstype, uuid=None, majmin=None, *,
                  vfs_options="rw,noatime", fs_options="rw"):
        if majmin is None:
            node = self._node_by_path(source)
            fs = next((f for f in self.filesystems if f["uuid"] == uuid), None) if fstype == "btrfs" else None
            majmin = fs["maj:min"] if fs else "0:44" if fstype == "btrfs" or node is None else node["maj:min"]
        # findmnt names the folder that a mount shows in SOURCE ("/dev/sda[/folder]")
        # and in FSROOT; "/" is the filesystem's root.
        fsroot = source[source.index("[") + 1:-1] if source and source.endswith("]") and "[" in source else "/"
        self.mounts.append({"target": target, "source": source, "fstype": fstype,
                            "uuid": uuid, "maj:min": majmin, "fsroot": fsroot,
                            "vfs-options": vfs_options, "fs-options": fs_options})
        self.dirs.add(target)

    def mount_pool(self, name, target=None, **options):
        """Mount an existing fake btrfs filesystem at /mnt/pools/<name> (or ``target``)."""
        fs = self.fs_by_label(name)
        self.add_mount(target or f"/mnt/pools/{name}", fs["devices"][0], "btrfs", fs["uuid"],
                       **options)

    def _host_mount(self, target):
        return next(m for m in self.mounts if m["target"] == target)

    def copy_into_namespace(self, pid, target="/mnt/pools/data"):
        """Process ``pid``'s mount namespace keeps a copy of the host's mount at ``target``.

        The same filesystem, root and mountpoint. A new namespace is a service's:
        systemd made it as a copy of the host's when a service with sandboxing
        started, so its root is a copy of the host's root mount too. In a
        container's namespace (container_namespace) the copy is what a bind of a
        parent folder of ``target`` without propagation gives
        (``-v /mnt/pools:/mnt/pools``, Docker's default rprivate).
        """
        self.namespaces.setdefault(pid, [dict(self._host_mount("/"))]).append(dict(self._host_mount(target)))

    def container_namespace(self, pid):
        """Process ``pid`` is a container's: the root of its mount namespace is the container's overlay."""
        self.namespaces[pid] = [dict(CONTAINER_ROOT)]

    def bind_into_namespace(self, pid, folder, at, target="/mnt/pools/data"):
        """Process ``pid``'s mount namespace has ``folder`` of the filesystem at ``target`` bound at ``at``.

        As a container has a folder of a pool bound (``-v /mnt/pools/data/downloads:/downloads``):
        a new namespace is a container's (container_namespace).
        """
        mount = self._host_mount(target)
        source = mount["source"] if folder == "/" else f"{mount['source']}[{folder}]"
        self.namespaces.setdefault(pid, [dict(CONTAINER_ROOT)]).append(
            {**mount, "target": at, "source": source, "fsroot": folder})

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

    def _resolve(self, path):
        """``path`` (absolute) with the symbolic links of ``links`` resolved, as realpath -m does."""
        assert path.startswith("/"), path
        todo = [part for part in path.split("/") if part]
        resolved: list[str] = []
        hops = 0
        while todo:
            part = todo.pop(0)
            if part == ".":
                continue
            if part == "..":
                resolved = resolved[:-1]
                continue
            link = "/" + "/".join([*resolved, part])
            if link in self.links:
                hops += 1
                assert hops < 40, f"a loop of symbolic links: {path}"
                target = self.links[link]
                todo = [p for p in target.split("/") if p] + todo
                if target.startswith("/"):
                    resolved = []
                continue
            resolved.append(part)
        return "/" + "/".join(resolved)

    def _node_by_path(self, path):
        for top in self.tree:
            for node in self._walk(top):
                if node["path"] == path:
                    return node
        return None

    def _node_by_number(self, majmin):
        """The block device of the host that has the device number ``majmin`` now, or None."""
        for top in self.tree:
            for node in self._walk(top):
                if majmin is not None and node.get("maj:min") == majmin:
                    return node
        return None

    # -- the container's /dev (host.container_disk_node, ADR-0414) --------------

    # host.container_disk_node joins its CONTAINER_DEV (a missing folder of the test's own,
    # tests/conftest.py) and the disk's name: the fake keeps its nodes by /dev/<name>.

    def host_disk_number(self, name):
        """host._host_disk_number: /sys/block/<name>/dev of the host, which names whole disks and
        the other devices that are no partition, as (major, minor)."""
        self.disk_asked = f"/dev/{name}"
        node = next((n for top in self.tree for n in self._walk(top) if n["name"] == name), None)
        if node is None or node["type"] == "part":
            return None
        major, minor = node["maj:min"].split(":")
        return int(major), int(minor)

    def container_node_number(self, path):
        """host._container_node_number: the number of the container's node of that name."""
        majmin = self.container_dev.get(f"/dev/{os.path.basename(path)}")
        if majmin is None:
            return None
        major, minor = majmin.split(":")
        return int(major), int(minor)

    def make_container_node(self, path, number):
        """host._make_container_node: the node of that name gets ``number``."""
        majmin = "{}:{}".format(*number)
        self.container_dev[f"/dev/{os.path.basename(path)}"] = majmin
        self.nodes_made.append((f"/dev/{os.path.basename(path)}", majmin))

    def _opened_by_smartctl(self, device):
        """The whole disk that smartctl opens as ``device`` in the container: the one that has the
        number of the container's node now, or None (no node, nothing with its number, or no whole
        disk). Only after host.container_disk_node asked sysfs for that disk (host.run_smartctl)."""
        asked, self.disk_asked = self.disk_asked, None
        assert asked == device, f"smartctl ran on {device} without host.container_disk_node"
        node = self._node_by_number(self.container_dev.get(device))
        return node if node is not None and node["type"] == "disk" else None

    def _fs_of(self, path):
        return next((fs for fs in self.filesystems if path in fs["devices"]), None)

    def _disk_of(self, path):
        """The path of the whole disk that ``path`` (a disk or a partition) is on, or None."""
        for top in self.tree:
            if any(node["path"] == path for node in self._walk(top)):
                return top["path"]
        return None

    def _wake(self, path, cmd):
        """A command that reads ``path`` or spins up its disk: a sleeping disk wakes (asleep)."""
        disk = self._disk_of(path)
        self.epc.pop(disk, None)
        if disk in self.asleep:
            self.asleep.discard(disk)
            self.woken.append((disk, list(cmd)))

    def run_in_container(self, cmd, *, timeout, **kwargs):
        """host.run_in_container: smartctl -n standby or -n idle, which reads a disk's power mode
        first and leaves the disk alone in the modes that -n names, with exit status 2 and the
        message "Device is in <MODE> mode" in the JSON's (smartctl's names: SLEEP, STANDBY and
        STANDBY_Y for -n standby, and IDLE_A, IDLE_B and IDLE_C too for -n idle; an awake disk of
        the fake is ACTIVE or IDLE, FFh, which neither stops), and answers -i (the identity: the
        power read of an NVMe disk, of a host without hdparm and of an EPC disk), -a (the SMART
        data), and -c, also with -i (the SMART capabilities, with the automatic offline data
        collection of offline_collection, and the identity's SMART support), also after -o on or
        -o off, which switch it first, of a disk it goes on to read; and smartctl -t short, long or
        conveyance, which starts a self-test (self_tests) and wakes the disk. smartctl opens the
        container's node of the disk, which reads the disk that has the node's number now
        (_opened_by_smartctl): without one, "No such device", as for a disk attached after the
        supervisor started whose node host.container_disk_node did not make (ADR-0414). And pgrep -f
        '^hdparm -C <disk>$', which finds the hdparm -C of a disk that still runs on the host
        (stuck_hdparm; the container shares the host's process IDs). Any other command fails the test, as smartctl without -n, which would
        wake a sleeping disk. container_answer answers a command instead, when it does."""
        cmd = list(cmd)
        assert timeout, cmd
        self.container_calls.append(cmd)
        if self.container_answer is not None and (answer := self.container_answer(cmd)) is not None:
            return answer
        if cmd[:2] == ["pgrep", "-f"] and len(cmd) == 3:
            found = re.fullmatch(r"\^hdparm -C (/dev/[a-z0-9]+)\$", cmd[2])
            assert found, cmd
            running = found[1] in self.stuck_hdparm
            return subprocess.CompletedProcess(cmd, 0 if running else 1, "4242\n" if running else "", "")
        if cmd[:2] == ["smartctl", "-t"] and len(cmd) == 4 and cmd[2] in ("short", "long", "conveyance"):
            node = self._opened_by_smartctl(cmd[-1])
            if node is None:
                return subprocess.CompletedProcess(
                    cmd, 2, f"Smartctl open device: {cmd[-1]} failed: No such device\n", "")
            self._wake(node["path"], cmd)
            self.self_tests.append((node["path"], cmd[2]))
            return subprocess.CompletedProcess(cmd, 0, "Testing has begun.\n", "")
        if cmd[:2] != ["smartctl", "-n"] or cmd[2] not in ("standby", "idle") \
                or cmd[3:-1] not in (["-i", "-j"], ["-j", "-a"],
                                     *([*switch, *info, "-c", "-j"] for switch in ([], ["-o", "on"], ["-o", "off"])
                                       for info in ([], ["-i"]))):
            raise AssertionError(f"test tried to run a real command: {cmd!r}")
        node = self._opened_by_smartctl(cmd[-1])
        if node is None:
            message = f"Smartctl open device: {cmd[-1]} failed: No such device"
            return subprocess.CompletedProcess(cmd, 2, json.dumps(
                {"smartctl": {"messages": [{"string": message, "severity": "error"}], "exit_status": 2}}), "")
        device = node["path"]
        condition = self.epc.get(device)
        mode = condition.upper() if condition else "STANDBY" if device in self.asleep else None
        if mode is not None and (cmd[2] == "idle" or not mode.startswith("IDLE")):
            return subprocess.CompletedProcess(cmd, 2, json.dumps({
                "smartctl": {"messages": [{"string": f"Device is in {mode} mode, exit(2)", "severity": "information"}],
                             "exit_status": 2},
                "device": {"name": device, "type": "sat"},
            }), "")
        self.identified.append(device)
        if "-c" in cmd:
            return self._smart_capabilities(cmd, device, node)
        data = {"device": {"name": device, "type": "sat"}, "model_name": node.get("model") or "Fake Disk",
                "serial_number": node.get("serial") or "", "firmware_version": "1.0",
                "user_capacity": {"bytes": node["size"]}, "smart_status": {"passed": True},
                "temperature": {"current": 31}}
        if "-a" in cmd:
            data["ata_smart_attributes"] = {"table": [
                {"id": 9, "name": "Power_On_Hours", "value": 99, "worst": 99, "thresh": 0, "raw": {"value": 51832}},
                {"id": 12, "name": "Power_Cycle_Count", "value": 99, "worst": 99, "thresh": 0, "raw": {"value": 9}},
            ]}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(data), "")

    def _smart_capabilities(self, cmd, device, node):
        """smartctl -c -j, with or without -i, after -o on or -o off: the JSON of smartmontools'
        ataprint.cpp, whose ata_smart_data.offline_data_collection.status.value has bit 7 set while
        the automatic offline data collection is on (0x84 as on the dev device's disk: suspended by a
        command of the host, 0x05 once it was turned off), and whose capabilities.values[0] has bit 1
        for a disk that can switch it (0x7b as on the dev device's disk; 0x59 without). -i adds
        smart_support. An NVMe disk has none of it. -o on a disk without the feature is refused by the
        disk: exit status bit 2, as smartctl's FAILSMART. A disk of smart_off reads no SMART data:
        without SMART, smartctl writes smart_support.available false and fails (FAILSMART); with SMART
        disabled it stops at "SMART Disabled", a message that -j drops, before -o and with exit status
        0, and writes smart_support (enabled false) only for -i."""
        if node["name"].startswith("nvme"):
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                {"device": {"name": device, "type": "nvme", "protocol": "NVMe"}, "smartctl": {"exit_status": 0}}), "")
        ata = {"name": device, "info_name": f"{device} [SAT]", "type": "sat", "protocol": "ATA"}
        if device in self.smart_off:
            unsupported = self.smart_off[device] == "unsupported"
            data = {"device": ata, "smartctl": {"exit_status": 4 if unsupported else 0}}
            if unsupported:
                data["smart_support"] = {"available": False}
            elif "-i" in cmd:
                data["smart_support"] = {"available": True, "enabled": False}
            return subprocess.CompletedProcess(cmd, data["smartctl"]["exit_status"], json.dumps(data), "")
        returncode = 0
        if "-o" in cmd:
            if device in self.offline_collection:
                self.offline_collection[device] = cmd[cmd.index("-o") + 1] == "on"
                self.changes.append(["smartctl", "-o", cmd[cmd.index("-o") + 1], device])
            else:
                returncode = 4
        on = self.offline_collection.get(device)
        status = 0x00 if on is None else 0x84 if on else 0x05
        capabilities = 0x59 if on is None else 0x7b
        data = {
            "smartctl": {"exit_status": returncode},
            "device": ata,
            "ata_smart_data": {
                "offline_data_collection": {"status": {"value": status}, "completion_seconds": 14760},
                "capabilities": {"values": [capabilities, 3], "exec_offline_immediate_supported": True,
                                 "offline_is_aborted_upon_new_cmd": False},
            },
        }
        if "-i" in cmd:
            data["smart_support"] = {"available": True, "enabled": True}
        return subprocess.CompletedProcess(cmd, returncode, json.dumps(data), "")

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
        if (hung := self._hung_power_read(cmd)) is not None:
            self.stuck_hdparm.add(hung)
            raise subprocess.TimeoutExpired(cmd, timeout)
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

    def _hung_power_read(self, cmd):
        """The disk of ``cmd`` when it is hdparm -C (under timeout) of a disk in hung, else None."""
        args = cmd[1:] if cmd[:1] == ["timeout"] else None
        while args and args[0].startswith("-"):
            args = args[1:]
        if args and args[1:3] == ["hdparm", "-C"] and args[-1] in self.hung:
            return args[-1]
        return None

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
        if prog == "readlink":
            path = cmd[-1]
            if path in self.links:
                return self._result(stdout=f"{self.links[path]}\n")
            return self._result(1)  # GNU readlink without -v: not a link, or no such file
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
                mounts = [*self.mounts, *(m for table in self.namespaces.values() for m in table)]
                active = uuid in self.held or any(m["uuid"] == uuid for m in mounts)
                return self._result(0 if active else 1)
            exists = path in self.dirs if flag == "-d" else (path in self.files or path in self.dirs)
            return self._result(0 if exists else 1)
        if prog == "realpath":
            # GNU realpath --canonicalize-missing --zero: each path with its symbolic links (links)
            # resolved and the parts that do not exist kept, each ended by a NUL.
            assert cmd[1:4] == ["--canonicalize-missing", "--zero", "--"] and len(cmd) > 4, cmd
            return self._result(stdout="".join(f"{self._resolve(path)}\0" for path in cmd[4:]))
        if prog == "stat":
            # GNU stat without -L: the entry itself, a symbolic link too. "%i %F": its inode
            # number (256 for the root of a btrfs subvolume) and its kind.
            assert cmd[1:3] == ["--format=%i %F", "--"] and len(cmd) == 4, cmd
            path = cmd[3]
            if path in self.links:
                return self._result(stdout="1001 symbolic link\n")
            if path in self.files:
                return self._result(stdout="1002 regular file\n")
            if path in self.dirs:
                return self._result(stdout=f"{256 if self.subvolume_root(path) else 1003} directory\n")
            return self._result(1, stderr=f"stat: cannot statx '{path}': No such file or directory")
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
            table = self.mounts
            if "--task" in cmd:  # the mount table of another process's namespace
                pid = int(cmd[cmd.index("--task") + 1])
                if pid not in self.namespaces or pid in self.exited:
                    return self._result(1, stderr=f"findmnt: can't read /proc/{pid}/mountinfo: No such file or directory")
                table = self.namespaces[pid]
            return self._result(stdout=json.dumps({"filesystems": table}, ensure_ascii=False))
        if prog == "lsns":
            assert cmd == ["lsns", "--json", "--type", "mnt", "--output", "NS,PID"], cmd
            # One row per mount namespace, with its lowest PID: the host's has PID 1.
            pids = [1, *sorted(set(self.namespaces) | self.exited, reverse=True)]
            rows = [{"ns": 4026531841 + index, "pid": pid} for index, pid in enumerate(pids)]
            return self._result(stdout=json.dumps({"namespaces": rows}))
        if prog == "lsblk":
            return self._result(stdout=self._lsblk())
        if prog == "swapon":
            return self._result(stdout="".join(f"{s}\n" for s in self.swaps))
        if prog == "wipefs":
            assert "--no-act" in cmd and not {"-a", "--all"} & set(cmd), cmd
            self._wake(cmd[-1], cmd)
            sigs = self.signatures.get(cmd[-1])
            return self._result(stdout=json.dumps({"signatures": sigs}) if sigs else "")
        if prog == "blkid":
            if "-p" in cmd:  # probes the device itself, not the cache
                self._wake(cmd[-1], cmd)
            fs = self._fs_of(cmd[-1])
            return self._result(0 if fs else 2, stdout=f"{fs['uuid']}\n" if fs else "")
        if prog == "udevadm" and cmd[1:2] == ["info"]:
            # The properties of the udev database (no disk is read): ID_SERIAL_SHORT, the serial
            # that lsblk reports for a SATA disk, unless udev_serials says otherwise.
            assert cmd[2:4] == ["--query=property", "--name"] and len(cmd) == 5, cmd
            node = self._node_by_path(cmd[4])
            if node is None:
                return self._result(1, stderr=f"Unknown device \"{cmd[4]}\": No such device")
            serial = self.udev_serials.get(cmd[4], node.get("serial"))
            lines = [f"DEVNAME={cmd[4]}", f"DEVTYPE={node['type']}"]
            if serial:
                lines.append(f"ID_SERIAL_SHORT={serial}")
            return self._result(stdout="".join(f"{line}\n" for line in lines))
        if prog in ("udevadm",):
            return self._result()
        if prog == "hdparm":
            return self._hdparm(cmd)
        if prog == "rm":
            assert cmd[1:3] == ["-f", "--"], cmd
            for path in cmd[3:]:
                if self.files.pop(path, None) is not None:
                    self.changes.append(["rm", path])
            return self._result()
        if prog == "btrfs":
            return self._btrfs(cmd)
        if prog in self.MUTATING:
            self.changes.append(cmd)
            return getattr(self, "_" + prog.replace(".", "_"))(cmd)
        if prog == "curl":
            return self._curl(cmd, input)
        raise AssertionError(f"unexpected host command: {cmd}")

    # The options of curl's command line that the fake knows: those with a value, and the flags.
    _CURL_VALUES = frozenset({"--max-time", "--proto", "--config", "--write-out"})
    _CURL_FLAGS = frozenset({"--disable", "--silent", "--show-error", "--globoff"})

    def _curl(self, cmd, config):
        """curl as the notification senders run it (ADR-0336): the request in its config on standard
        input, the answer from ``network``, and stdout as curl prints it with ``--write-out``: the
        body, then the format with the status, which is 000 without an answer."""
        if "curl" in self.missing_programs:
            # What nsenter prints when the host has no such program.
            return self._result(127, stderr="nsenter: failed to execute curl: No such file or directory")
        assert self.network is not None, f"unexpected host command: {cmd}"
        options, index = {}, 1
        while index < len(cmd):
            if cmd[index] in self._CURL_VALUES:
                options[cmd[index]] = cmd[index + 1]
                index += 2
            else:
                assert cmd[index] in self._CURL_FLAGS, f"curl option the fake does not know: {cmd[index]}"
                index += 1
        assert options.get("--config") == "-" and config is not None, f"curl without its config on stdin: {cmd}"
        request = parse_curl_config(config)
        self.http_requests.append(request)
        answer = self.network(request)
        status_format = options.get("--write-out", "")
        if isinstance(answer, CurlFailure):
            return self._result(answer.code, status_format.replace("%{http_code}", "000"),
                                f"curl: ({answer.code}) {answer.message}\n")
        return self._result(stdout=answer.body + status_format.replace("%{http_code}", f"{answer.status:03d}"))

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
            if "--mounted" not in cmd:
                # Without --mounted, btrfs reads the superblock of every block device that
                # libblkid finds: a disk that sleeps spins up (ADR-0378).
                for disk in sorted(self.asleep):
                    self._wake(disk, cmd)
            shown = self._btrfs_show(mounted_only="--mounted" in cmd)
            # With --mounted, btrfs-progs exits 1 without a word when no btrfs filesystem is
            # mounted: cmd_filesystem_show returns btrfs_scan_kernel's "none found".
            return self._result(1 if "--mounted" in cmd and not shown else 0, stdout=shown)
        if sub == ["device", "scan"]:
            return self._result()
        if sub == ["filesystem", "label"]:
            return self._btrfs_label(cmd)
        target = cmd[-1]
        mount = next((m for m in self.mounts if m["target"] == target and m["fstype"] == "btrfs"), None)
        fs = next((f for f in self.filesystems if mount and f["uuid"] == mount["uuid"]), None)
        if fs is None:
            return self._result(1, stderr=f"ERROR: '{target}' is not a mounted btrfs device")
        if sub == ["inspect-internal", "rootid"]:
            # The subvolume a mount without subvol= shows: the filesystem's default one.
            assert len(cmd) == 4, cmd
            return self._result(stdout=f"{fs['default_subvolume']}\n")
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

    def _btrfs_label(self, cmd):
        """``btrfs filesystem label <mountpoint> <label>``: the kernel sets it on the mounted filesystem.

        Only through a mount: given a device, btrfs-progs writes the superblocks
        itself, which UNOS never has it do (ADR-0283).
        """
        assert len(cmd) == 5, cmd
        target, label = cmd[3], cmd[4]
        mount = next((m for m in self.mounts if m["target"] == target and m["fstype"] == "btrfs"), None)
        assert mount is not None, f"label set through {target}, which is not a btrfs mount: {cmd}"
        if "ro" in f"{mount['vfs-options']},{mount['fs-options']}".split(","):
            return self._result(1, stderr="ERROR: unable to set label for "
                                f"{target}: Read-only file system")
        self.changes.append(cmd)
        next(f for f in self.filesystems if f["uuid"] == mount["uuid"])["label"] = label
        return self._result()

    def _btrfs_show(self, mounted_only=False):
        chunks = []
        for fs in self.filesystems:
            if mounted_only and not any(m["fstype"] == "btrfs" and m["uuid"] == fs["uuid"] for m in self.mounts):
                continue
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

    def _hdparm(self, cmd):
        """hdparm -S <value> <disk>: the IDLE command with a standby timer, which spins up a
        sleeping disk. hdparm -C <disk>: CHECK POWER MODE, which reads the disk's power mode and
        leaves it asleep (" drive state is:  standby", or "active/idle"); an NVMe disk takes no ATA
        command, so hdparm fails for it with "unknown", and an EPC disk in a power condition that
        hdparm does not name (epc) is "unknown" with exit status 0. A disk in hung never answers
        (run). UNOS runs no other option."""
        if "hdparm" in self.missing_programs:
            return self._result(127, stderr="nsenter: failed to execute hdparm: No such file or directory")
        if cmd[1:2] == ["-C"]:
            assert len(cmd) == 3, cmd
            device = cmd[2]
            node = self._node_by_path(device)
            if node is None:
                return self._result(2, stderr=f"{device}: No such file or directory\n")
            if node["name"].startswith("nvme"):
                return self._result(25, stdout=f"\n{device}:\n drive state is:  unknown\n",
                                    stderr=" HDIO_DRIVE_CMD(checkpowermode) failed: Inappropriate ioctl for device\n")
            disk = self._disk_of(device)
            state = "unknown" if disk in self.epc else "standby" if disk in self.asleep else "active/idle"
            return self._result(stdout=f"\n{device}:\n drive state is:  {state}\n")
        assert len(cmd) == 4 and cmd[1] == "-S" and cmd[2].isdigit(), cmd
        device = cmd[3]
        if self._node_by_path(device) is None:
            return self._result(2, stderr=f"{device}: No such file or directory")
        self._wake(device, cmd)
        self.standby_timers[device] = int(cmd[2])
        self.changes.append(cmd)
        return self._result(stdout=f"\n{device}:\n setting standby to {cmd[2]}\n")

    def _mkfs_btrfs(self, cmd):
        args = cmd[1:]
        devices = [a for a in args if a.startswith("/dev/")]
        for device in devices:
            self._wake(device, cmd)
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
            # nodiscard for a pool with an NVMe drive on the SoCs of pools.NVME_NO_TRIM_SOCS (ADR-0370).
            assert cmd[:4] == ["mount", "-t", "btrfs", "-o"] and cmd[4] in ("noatime", "noatime,nodiscard"), cmd
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
        for device in fs["devices"]:
            self._wake(device, cmd)
        if fs["missing"]:
            return self._result(32, stderr=f"mount: {target}: wrong fs type, bad option, bad superblock")
        if target not in self.dirs:
            return self._result(32, stderr=f"mount: {target}: mount point does not exist.")
        self.add_mount(target, fs["devices"][0], "btrfs", fs["uuid"])
        return self._result()

    def _umount(self, cmd):
        assert not {"-l", "--lazy"} & set(cmd), f"a lazy unmount: {cmd}"
        if cmd[1] == "--namespace":
            return self._umount_in_namespace(int(cmd[2]), cmd[3])
        target = cmd[1]
        if target in self.busy:
            return self._result(32, stderr=f"umount: {target}: target is busy.")
        for index in range(len(self.mounts) - 1, -1, -1):
            if self.mounts[index]["target"] == target:
                del self.mounts[index]
                return self._result()
        return self._result(32, stderr=f"umount: {target}: not mounted.")

    def _umount_in_namespace(self, pid, target):
        """``umount --namespace <pid> <target>``: the host's umount, in process ``pid``'s namespace."""
        if pid not in self.namespaces or pid in self.exited:
            return self._result(32, stderr=f"umount: failed to set target namespace to /proc/{pid}/ns/mnt: "
                                           "No such file or directory")
        if not self._drop_namespace_mount(pid, target):
            return self._result(32, stderr=f"umount: {target}: not mounted.")
        for group in self.namespace_peers:  # the unmount propagates to the copy's peers
            for other in group - {pid} if pid in group else ():
                self._drop_namespace_mount(other, target)
        return self._result()

    def _drop_namespace_mount(self, pid, target) -> bool:
        """Remove the topmost mount at ``target`` from process ``pid``'s mount table."""
        table = self.namespaces.get(pid, [])
        for index in range(len(table) - 1, -1, -1):
            if table[index]["target"] == target:
                del table[index]
                return True
        return False

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
    # btrfs scrubs and smartctl, which run in the container (ADR-0290): only smartctl -n standby,
    # which leaves a sleeping disk asleep, and -t, which asks for a self-test, are answered
    # (FakeHost.run_in_container).
    monkeypatch.setattr(host_module, "run_in_container", fake.run_in_container)
    # The container's node of a disk before smartctl opens it (ADR-0414): the fake's sysfs and
    # /dev, never this machine's.
    monkeypatch.setattr(host_module, "_host_disk_number", fake.host_disk_number)
    monkeypatch.setattr(host_module, "_dev_is_the_containers", lambda: True)
    monkeypatch.setattr(host_module, "_container_node_number", fake.container_node_number)
    monkeypatch.setattr(host_module, "_make_container_node", fake.make_container_node)
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


def _add_nvme_pool(host):
    """A single-disk pool 'fast' on the NVMe drive nvme0n1, not mounted."""
    host.add_disk("nvme0n1", rota=False, size=256_060_514_304, model="Patriot M.2 P300 256GB")
    host.add_btrfs("fast", MEDIA_UUID, ["/dev/nvme0n1"])
    host.dirs.add("/mnt/pools/fast")


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

    @pytest.mark.parametrize(
        "compatible",
        ["spacemit,k1-x\0", "bananapi,bpi-f3\0spacemit,k1\0"],
        ids=["vendor-kernel", "mainline-kernel"],
    )
    def test_on_a_spacemit_k1_an_nvme_pool_mounts_without_discard_or_fstrim(self, data_pool, compatible):
        # The K1's NVMe controller hangs on a TRIM of a whole device, and kernels from 6.2 mount
        # btrfs on an SSD with discard=async: on the K1 a pool on an NVMe drive mounts with
        # nodiscard, now and at boot, and the weekly fstrim of fstrim.timer skips it
        # (X-fstrim.notrim). Its device tree names the SoC "spacemit,k1-x" under the vendor's
        # kernel and "spacemit,k1" under the mainline one (ADR-0370).
        data_pool.files["/proc/device-tree/compatible"] = compatible
        _add_nvme_pool(data_pool)

        pools.mount_pool("fast")

        assert ["mount", "-t", "btrfs", "-o", "noatime,nodiscard", f"UUID={MEDIA_UUID}",
                "/mnt/pools/fast"] in data_pool.changes
        assert data_pool.fstab == BASE_FSTAB + marker("fast") + (
            f"UUID={MEDIA_UUID} /mnt/pools/fast btrfs {OPTIONS},nodiscard,X-fstrim.notrim 0 0\n"
        )

    @pytest.mark.parametrize(
        ("compatible", "pool"),
        [("spacemit,k1-x\0", "data"), ("rockchip,rk3588-evb1-v10\0rockchip,rk3588\0", "fast"),
         ("rockchip,rk3588-armsom-w3\0rockchip,rk3588\0", "fast")],
        ids=["k1-sata-pool", "rk3588-nvme-pool", "bpi-w3-nvme-pool"],
    )
    def test_other_pools_keep_the_default_discard(self, data_pool, compatible, pool):
        # Only NVMe drives hang on the K1: a SATA or USB disk there, and an NVMe drive on another
        # SoC, keep btrfs's discard and the weekly fstrim (ADR-0370).
        data_pool.files["/proc/device-tree/compatible"] = compatible
        _add_nvme_pool(data_pool)
        uuid = {"data": DATA_UUID, "fast": MEDIA_UUID}[pool]

        pools.mount_pool(pool)

        assert ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={uuid}", f"/mnt/pools/{pool}"] in data_pool.changes
        assert data_pool.fstab == BASE_FSTAB + marker(pool) + entry(pool, uuid)

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

    def test_refuses_a_filesystem_that_mounts_another_subvolume_by_default(self, data_pool):
        # Made elsewhere, with 'btrfs subvolume set-default': a mount without subvol=
        # shows subvolume 256, and volumes and share folders need the top level (ADR-0283).
        data_pool.fs_by_label("data")["default_subvolume"] = 256
        with pytest.raises(pools.PoolConflictError) as excinfo:
            pools.mount_pool("data")
        assert str(excinfo.value).startswith(
            "Pool 'data' was not mounted: its filesystem mounts subvolume 256 by default, not its top level"
        )
        assert "btrfs subvolume set-default 5" in str(excinfo.value)
        assert data_pool.changes == [
            ["mkdir", "-p", "/mnt/pools/data"],
            ["chattr", "+i", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
            ["umount", "/mnt/pools/data"],
        ]
        assert not data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB
        assert "/mnt/pools/data" in data_pool.immutable  # still guarded: the mount was on top

    def test_a_stale_entry_comes_back_when_the_mount_shows_another_subvolume(self, data_pool):
        data_pool.files["/etc/fstab"] = BASE_FSTAB + marker("data") + entry("data", OLD_UUID)
        data_pool.files["/etc/fstab.unos-backup"] = BASE_FSTAB
        data_pool.fs_by_label("data")["default_subvolume"] = 256
        with pytest.raises(pools.PoolConflictError, match="subvolume 256"):
            pools.mount_pool("data")
        # Replaced before the mount (test_stale_entry_is_replaced_before_mounting), put back after it.
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", OLD_UUID)
        assert not data_pool.mounted("/mnt/pools/data")

    def test_a_mountpoint_made_for_a_mount_that_is_undone_is_removed_again(self, data_pool):
        data_pool.dirs.discard("/mnt/pools/data")
        data_pool.fs_by_label("data")["default_subvolume"] = 256
        with pytest.raises(pools.PoolConflictError, match="It was unmounted again, with its label and "
                           "the host's /etc/fstab as they were"):
            pools.mount_pool("data")
        assert data_pool.changes[-3:] == [
            ["umount", "/mnt/pools/data"], ["chattr", "-i", "/mnt/pools/data"], ["rmdir", "/mnt/pools/data"]]
        assert "/mnt/pools/data" not in data_pool.dirs
        assert data_pool.fstab == BASE_FSTAB

    def test_a_stale_entry_comes_back_when_the_mount_fails(self, data_pool):
        data_pool.files["/etc/fstab"] = BASE_FSTAB + marker("data") + entry("data", OLD_UUID)
        data_pool.files["/etc/fstab.unos-backup"] = BASE_FSTAB
        data_pool.fail[("mount",)] = (32, "mount: /mnt/pools/data: wrong fs type")
        with pytest.raises(RuntimeError, match="wrong fs type"):
            pools.mount_pool("data")
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", OLD_UUID)
        assert "/mnt/pools/data" in data_pool.immutable  # the pool's mountpoint, as before

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
        assert pools.delete_pool("data") is True

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

    # delete_pool says whether it changed anything: the endpoint writes pool_deleted only then
    # (ADR-0350).
    @pytest.mark.parametrize("left", ["fstab entry", "mountpoint"])
    def test_delete_of_a_pool_that_is_not_mounted_says_that_it_removed_what_was_left(self, fake_host, left):
        if left == "fstab entry":
            fake_host.files["/etc/fstab"] = BASE_FSTAB + marker("gone") + entry("gone", GONE_UUID)
        else:
            fake_host.dirs.add("/mnt/pools/gone")

        assert pools.delete_pool("gone") is True
        assert fake_host.fstab == BASE_FSTAB
        assert "/mnt/pools/gone" not in fake_host.dirs

    def test_delete_of_a_name_that_is_no_pool_says_that_it_changed_nothing(self, fake_host):
        assert pools.delete_pool("gone") is False
        assert fake_host.changes == []

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

    # storage.edge-cases, step 4: Docker reports a bind mount's source as it was written. A
    # symbolic link on the host, or a folder of the pool bound elsewhere on the host, led the
    # lexical comparison past the container: the pool was unmounted while it used it.
    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    @pytest.mark.parametrize("case", ["symbolic link", "folder bound on the host"])
    def test_refuses_a_container_that_reaches_the_pool_through_a_link_or_another_mount(
        self, mounted, operation, case
    ):
        if case == "symbolic link":
            mounted.links["/srv/data"] = "/mnt/pools/data"
            mounted.docker.add("syncthing", sources=["/srv/data/sync"])
        else:
            mounted.add_mount("/srv/sync", "/dev/sda[/sync]", "btrfs", DATA_UUID)
            mounted.docker.add("syncthing", sources=["/srv/sync"])
        with pytest.raises(pools.PoolBusyError, match=r"running containers \(syncthing\)"):
            operation("data")
        assert mounted.changes == []
        self.assert_still_mounted_and_persistent(mounted)

    # storage.edge-cases, step 4: realpath runs on the host over every folder that a running
    # container mounts. On a network or FUSE mount that hangs it did not return: the pools' lock
    # stayed held until subprocess.TimeoutExpired, which answered with its own text.
    # With --signal=KILL, timeout(1) kills its whole process group, itself too: nsenter passes the
    # signal on, and the supervisor sees the return code -9 (it took only 124 and 137 for a hang).
    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    @pytest.mark.parametrize("hang", ["killed by timeout", "no answer"])
    def test_a_realpath_that_does_not_answer_leaves_the_pool_mounted(self, mounted, monkeypatch, operation, hang):
        mounted.docker.add("syncthing", sources=["/srv/nfs/sync"])
        if hang == "killed by timeout":
            mounted.fail[("timeout", "--signal=KILL", "10", "realpath")] = (-signal.SIGKILL, "")
        else:
            real = host_module.run_on_host

            def hangs(cmd, **kwargs):
                if "realpath" in cmd:
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
                return real(cmd, **kwargs)

            monkeypatch.setattr(host_module, "run_on_host", hangs)

        with pytest.raises(RuntimeError, match=re.escape(
            "Pool 'data' was left mounted: realpath did not resolve the folders that containers mount "
            "within 10 seconds: a network or FUSE mount of the host may not answer"
        )):
            operation("data")
        assert mounted.changes == []
        self.assert_still_mounted_and_persistent(mounted)

    def test_docker_volumes_that_bind_no_folder_of_the_pool_do_not_count(self, mounted):
        mounted.docker.add("app", mounts=[
            mounted.docker.add_volume("db"),
            mounted.docker.add_volume("other", options={"type": "none", "o": "bind", "device": "/mnt/pools/database"}),
        ])
        assert not pools.unmount_pool("data").mounted

    def test_docker_volume_that_cannot_be_inspected_leaves_the_pool_mounted(self, mounted, caplog):
        mounted.docker.add("app", mounts=[mounted.docker.add_volume("media")])
        mounted.docker.volumes.clear()  # inspect_volume: 404
        with pytest.raises(pools.DockerUnavailableError, match="Docker did not answer"):
            pools.unmount_pool("data")
        assert "Could not list the running containers or their volumes" in caplog.text
        assert mounted.changes == []
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

    def test_on_a_spacemit_k1_an_nvme_pool_is_mounted_again_without_discard(self, data_pool):
        # The mount after an unmount that left the filesystem in use takes the pool's own
        # options (ADR-0370).
        data_pool.files["/proc/device-tree/compatible"] = "spacemit,k1-x\0"
        _add_nvme_pool(data_pool)
        pools.mount_pool("fast")
        wait_for_app_starts()
        data_pool.changes.clear()
        data_pool.held.add(MEDIA_UUID)

        with pytest.raises(pools.PoolBusyError, match="mounted again"):
            pools.unmount_pool("fast")

        assert data_pool.changes == [
            ["umount", "/mnt/pools/fast"],
            ["mount", "-t", "btrfs", "-o", "noatime,nodiscard", f"UUID={MEDIA_UUID}", "/mnt/pools/fast"],
        ]

    def test_the_mount_again_keeps_trim_off_on_a_k1_whose_devices_cannot_be_read(self, data_pool, monkeypatch):
        # The mount again must not fail on reading the pool's devices: on a K1 a pool whose devices
        # cannot be read mounts without discard, as one with an NVMe drive; elsewhere the devices
        # are not read at all (ADR-0370).
        def unreadable():
            raise RuntimeError("btrfs filesystem show failed")

        monkeypatch.setattr(pools, "btrfs_filesystems", unreadable)
        assert pools._remount_is_trimmed(DATA_UUID) is True
        data_pool.files["/proc/device-tree/compatible"] = "spacemit,k1-x\0"
        assert pools._remount_is_trimmed(DATA_UUID) is False

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

    # When Docker cannot be asked, a Docker volume of the local driver that binds a folder of the
    # pool keeps the filesystem mounted on the host at Docker's folder for the volume, which the
    # check after the umount takes for the administrator's own mount ("also mounted elsewhere on
    # the host"): the pool was reported unmounted while the container kept writing to it. The
    # unmount and the removal are refused instead, before anything changes (ADR-0344).
    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_without_docker_the_pool_stays_mounted_and_nothing_changes(self, mounted, operation, caplog):
        mounted.add_mount("/var/lib/docker/volumes/paperless_media/_data",
                          "/dev/sda[/apps/paperless/media]", "btrfs", DATA_UUID)
        options = {"type": "none", "o": "bind", "device": "/mnt/pools/data/apps/paperless/media"}
        mounted.docker.add("paperless", mounts=[mounted.docker.add_volume("paperless_media", options=options)])
        mounted.docker.unavailable = True
        with caplog.at_level(logging.WARNING, logger="src.storage.pools"):
            with pytest.raises(pools.DockerUnavailableError, match=re.escape(
                "Pool 'data' was left mounted: Docker did not answer, so it is not known whether a "
                "running container keeps data on it. Try again when Docker is running (Error while "
                "fetching server API version)."
            )):
                operation("data")
        assert "Could not list the running containers" in caplog.text
        assert mounted.changes == []
        self.assert_still_mounted_and_persistent(mounted)
        assert "/mnt/pools/data" in mounted.dirs

    def test_without_docker_a_pool_that_is_not_mounted_is_still_removed(self, data_pool):
        # Nothing is mounted, so no container can use the pool: Docker is not asked.
        data_pool.docker.unavailable = True
        pools.delete_pool("data")
        assert "/mnt/pools/data" not in data_pool.dirs

    # -- services keep copies of the pool's mount in mount namespaces of their own --

    # On Armbian, systemd runs services with sandboxing (systemd-resolved, -timesyncd,
    # -networkd, -logind, -udevd, rsyslogd) in mount namespaces of their own. Each keeps
    # a copy of the pool's mount (root "/" at /mnt/pools/data) in a peer group of its
    # own, which the host's umount does not reach: the filesystem stayed mounted, and
    # every unmount was refused as still in use and mounted again (ADR-0288).
    SERVICES = (612, 640, 655)
    UNMOUNTED_IN_SERVICES = [["umount", "--namespace", str(pid), "/mnt/pools/data"] for pid in SERVICES]

    @pytest.fixture
    def services(self, mounted):
        for pid in reversed(self.SERVICES):
            mounted.copy_into_namespace(pid)
        mounted.namespaces[900] = [dict(mount) for mount in WEBUI_MOUNTS]  # nothing of the pool
        return mounted

    @staticmethod
    def namespaces_holding(host, uuid=DATA_UUID):
        return sorted(pid for pid, table in host.namespaces.items() if any(m["uuid"] == uuid for m in table))

    def test_unmount_unmounts_the_copies_that_services_keep(self, services, caplog):
        info = pools.unmount_pool("data")

        # In the supervisor's log, which shows warnings and above.
        assert [(record.levelno, record.getMessage()) for record in caplog.records
                if record.name == "src.storage.pools" and "namespaces" in record.getMessage()] == [
            (logging.WARNING, "Unmounted /mnt/pools/data in the mount namespaces of processes 612, 640, 655, "
                              "which kept copies of its mount"),
        ]
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            *self.UNMOUNTED_IN_SERVICES,
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
            ["chattr", "+i", "/mnt/pools/data"],
        ]
        assert self.namespaces_holding(services) == []
        assert services.namespaces[900] == WEBUI_MOUNTS
        assert services.fstab == BASE_FSTAB
        assert not info.mounted and not info.persistent

    def test_delete_unmounts_the_copies_that_services_keep(self, services):
        pools.delete_pool("data")

        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            *self.UNMOUNTED_IN_SERVICES,
            ["write", "/etc/fstab"],
            ["systemctl", "daemon-reload"],
            ["chattr", "-i", "/mnt/pools/data"],
            ["rmdir", "/mnt/pools/data"],
        ]
        assert self.namespaces_holding(services) == []
        assert not any(c[0] in ("mkfs.btrfs", "wipefs") for c in services.calls)
        [listed] = pools.list_pools()
        assert (listed.name, listed.health, listed.uuid) == ("data", "unmounted", DATA_UUID)

    def test_stacked_copies_are_all_unmounted(self, services):
        services.copy_into_namespace(640)  # the copy of a pool that was mounted twice
        pools.unmount_pool("data")
        assert services.changes.count(["umount", "--namespace", "640", "/mnt/pools/data"]) == 2
        assert self.namespaces_holding(services) == []

    def test_copies_of_other_filesystems_are_left_alone(self, services):
        services.add_disk("sdb")
        services.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
        services.mount_pool("media")
        services.copy_into_namespace(640, "/mnt/pools/media")
        pools.unmount_pool("data")
        assert self.namespaces_holding(services, MEDIA_UUID) == [640]
        assert ["umount", "--namespace", "640", "/mnt/pools/media"] not in services.calls

    def test_a_copy_that_went_with_the_unmount_of_its_peer_is_no_failure(self, services, caplog):
        # 640's copy came from 612's namespace (a process of the service made a namespace
        # of its own): both are one peer group, and the unmount in 612 takes 640's too.
        services.namespace_peers.append({612, 640})
        assert not pools.unmount_pool("data").mounted
        assert ["umount", "--namespace", "640", "/mnt/pools/data"] in services.changes
        assert self.namespaces_holding(services) == []
        assert "Could not unmount" not in caplog.text

    def test_a_copy_that_went_with_its_peer_is_not_blamed_for_a_refusal(self, services):
        services.namespace_peers.append({612, 640})
        services.held.add(DATA_UUID)  # something no mount table shows keeps the pool mounted
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "still in use outside the host (for example by a container's bind mount)"
        )):
            pools.unmount_pool("data")

    # systemd hides a path from a sandboxed service by mounting over it (InaccessiblePaths=,
    # TemporaryFileSystem=). An umount by the path removes the topmost mount, which would be
    # that cover and not the copy below it: nothing is unmounted in such a namespace.
    @pytest.mark.parametrize("cover_first", [False, True], ids=["cover-on-top", "copy-on-top"])
    def test_a_namespace_with_another_filesystem_at_the_mountpoint_is_left_alone(self, services, cover_first):
        cover = {**WEBUI_MOUNTS[0], "target": "/mnt/pools/data", "source": "tmpfs", "fstype": "tmpfs",
                 "maj:min": "0:27"}
        if cover_first:
            services.namespaces[640].insert(0, cover)
        else:
            services.namespaces[640].append(cover)
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "Pool 'data' is still in use outside the host (process 640 has another filesystem mounted at "
            "/mnt/pools/data together with its copy), so it was mounted again."
        )):
            pools.unmount_pool("data")
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        assert cover in services.namespaces[640]
        self.assert_still_mounted_and_persistent(services)

    def test_copies_without_a_root_in_the_mount_table_count_as_copies(self, services):
        # A findmnt that leaves FSROOT out leaves it out on the host and in the namespaces alike.
        for table in [services.mounts, *services.namespaces.values()]:
            for mount in table:
                mount.pop("fsroot", None)
        assert not pools.unmount_pool("data").mounted
        assert self.namespaces_holding(services) == []

    def test_a_process_that_exited_before_its_mount_table_was_read_is_left_out(self, services, caplog):
        services.exited.add(700)
        assert not pools.unmount_pool("data").mounted
        assert "Could not read the mount table of process 700" in caplog.text

    # A container that Docker did not report (another runtime, or a bind mount made
    # by hand) has the pool, or a folder of it, mounted in its namespace: its users
    # keep writing there, so the disk must not be removed.
    @pytest.mark.parametrize(
        ("folder", "at"),
        [("/downloads", "/downloads"), ("/", "/data"), ("/apps/x", "/mnt/pools/data")],
        ids=["a-folder", "the-whole-pool-elsewhere", "a-folder-at-the-pools-mountpoint"],
    )
    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_refuses_while_a_namespace_has_the_pool_mounted_elsewhere(self, services, operation, folder, at):
        services.bind_into_namespace(1200, folder, at)
        with pytest.raises(
            pools.PoolBusyError,
            match=re.escape(
                f"Pool 'data' is still in use outside the host (process 1200 has it mounted at {at}), so "
                "it was mounted again. Stop the apps and containers that use the pool and try again."
            ),
        ):
            operation("data")
        # The copies of the services are not unmounted either while a user is left.
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        assert self.namespaces_holding(services) == [*self.SERVICES, 1200]
        self.assert_still_mounted_and_persistent(services)

    # A container that binds a parent folder of the pool (-v /mnt/pools:/mnt/pools, or the whole
    # /mnt, which the Containers page can make) with Docker's default propagation, rprivate, has
    # the pool's mount at the pool's mountpoint with the host mount's root, like a service's copy;
    # Docker reports a Source outside the pool, which _containers_using does not count. Its apps
    # use the pool there: unmounting it would leave them writing into an empty folder, or into the
    # container's own layer on the system disk. Only namespaces whose root is the host's root
    # filesystem, as systemd makes them for services, have their copies unmounted.
    @pytest.mark.parametrize("source", ["/mnt/pools", "/mnt"])
    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_refuses_while_a_container_has_the_pool_at_its_mountpoint(self, services, operation, source):
        services.docker.add("syncthing", mounts=[_bind(source, source)])
        services.container_namespace(1300)
        services.copy_into_namespace(1300)
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "Pool 'data' is still in use outside the host (process 1300 has it mounted at /mnt/pools/data), "
            "so it was mounted again. Stop the apps and containers that use the pool and try again."
        )):
            operation("data")
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        assert self.namespaces_holding(services) == [*self.SERVICES, 1300]
        self.assert_still_mounted_and_persistent(services)

    def test_without_docker_a_container_that_binds_the_pools_mountpoint_is_refused(self, services):
        # -v /mnt/pools/data:/mnt/pools/data: Docker would name the container up front, but cannot
        # be asked, so nothing is unmounted, neither on the host nor in a namespace (ADR-0344).
        services.docker.add("app", sources=["/mnt/pools/data"])
        services.docker.unavailable = True
        services.container_namespace(1400)
        services.copy_into_namespace(1400)
        with pytest.raises(pools.DockerUnavailableError, match="Docker did not answer"):
            pools.unmount_pool("data")
        assert services.changes == []
        assert self.namespaces_holding(services) == [*self.SERVICES, 1400]
        self.assert_still_mounted_and_persistent(services)

    # The root that tells a service's namespace from a container's is the host's root filesystem
    # (MAJ:MIN) and the folder of it (FSROOT) that the host's "/" shows. A container whose root is
    # a folder of the host's root filesystem (Docker's vfs driver, systemd-nspawn), and a process
    # that a chroot into a folder keeps from seeing any mount at "/", have roots of their own.
    @pytest.mark.parametrize("folder", ["/var/lib/machines/backup", None],
                             ids=["a-folder-of-the-hosts-root-filesystem", "no-mount-at-its-root"])
    def test_a_namespace_with_a_root_of_its_own_is_refused(self, services, folder):
        host_root = services._host_mount("/")
        services.namespaces[1500] = [] if folder is None else [
            {**host_root, "source": f"{host_root['source']}[{folder}]", "fsroot": folder}]
        services.copy_into_namespace(1500)
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "(process 1500 has it mounted at /mnt/pools/data), so it was mounted again"
        )):
            pools.unmount_pool("data")
        assert not [c for c in services.changes if c[:2] == ["umount", "--namespace"]]
        self.assert_still_mounted_and_persistent(services)

    @pytest.mark.parametrize("operation", [pools.unmount_pool, pools.delete_pool])
    def test_refuses_when_a_copy_cannot_be_unmounted(self, services, operation):
        # A service has a file of the pool open, for example rsyslogd a log file.
        services.fail[("umount", "--namespace", "640")] = (32, "umount: /mnt/pools/data: target is busy.")
        with pytest.raises(
            pools.PoolBusyError,
            match=re.escape(
                "Pool 'data' is still in use outside the host (process 640 keeps a copy of its mount, and "
                "unmounting it there failed: umount: /mnt/pools/data: target is busy), so it was mounted "
                "again. Stop the apps and containers that use the pool and try again."
            ),
        ):
            operation("data")
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            ["umount", "--namespace", "612", "/mnt/pools/data"],
            ["umount", "--namespace", "655", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        assert self.namespaces_holding(services) == [640]
        self.assert_still_mounted_and_persistent(services)

    def test_refuses_when_unmounting_a_copy_does_not_finish(self, services, monkeypatch):
        real_run = host_module.run_on_host

        def hanging_umount(cmd, **kwargs):
            if cmd[:3] == ["umount", "--namespace", "640"]:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", hanging_umount)
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "(process 640 keeps a copy of its mount, and unmounting it there failed: umount did not "
            f"finish within {pools.UMOUNT_TIMEOUT} seconds)"
        )):
            pools.unmount_pool("data")
        self.assert_still_mounted_and_persistent(services)

    def test_mounts_again_when_the_filesystem_stays_in_use_after_the_copies_are_unmounted(self, services):
        services.held.add(DATA_UUID)  # in use where no mount table shows it
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "Pool 'data' is still in use outside the host (for example by a container's bind mount), so it "
            "was mounted again."
        )):
            pools.unmount_pool("data")
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            *self.UNMOUNTED_IN_SERVICES,
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]
        self.assert_still_mounted_and_persistent(services)

    def test_mount_namespaces_that_cannot_be_listed_leave_the_pool_mounted(self, services, caplog):
        services.fail[("lsns",)] = (1, "lsns: unknown column: NS")
        with pytest.raises(pools.PoolBusyError, match="still in use outside the host .*mounted again"):
            pools.unmount_pool("data")
        assert "Could not list the host's mount namespaces: lsns failed: lsns: unknown column: NS" in caplog.text
        assert services.changes == [
            ["umount", "/mnt/pools/data"],
            ["mount", "-t", "btrfs", "-o", "noatime", f"UUID={DATA_UUID}", "/mnt/pools/data"],
        ]

    def test_the_reason_is_named_when_mounting_again_fails(self, services):
        services.bind_into_namespace(1200, "/downloads", "/downloads")
        services.fail[("mount",)] = (32, "mount: /mnt/pools/data: wrong fs type")
        with pytest.raises(pools.PoolBusyError, match=re.escape(
            "/mnt/pools/data was unmounted on the host, but the filesystem is still in use elsewhere "
            "(process 1200 has it mounted at /downloads), and mounting it again failed: "
        )):
            pools.unmount_pool("data")
        assert services.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    def test_a_filesystem_also_mounted_elsewhere_on_the_host_keeps_the_copies(self, services):
        services.mount_pool("data", target="/srv/data")  # the administrator's own mount
        assert not pools.unmount_pool("data").mounted
        assert services.changes[0] == ["umount", "/mnt/pools/data"]
        assert not [c for c in services.calls if c[0] == "lsns"]


class TestMountNamespaces:
    """disks.mount_namespace_pids (lsns) and disks.namespace_mounts (findmnt --task) on the host."""

    @staticmethod
    def answer(monkeypatch, stdout, returncode=0, stderr=""):
        calls = []

        def run(cmd, **kwargs):
            calls.append(list(cmd))
            return HostResult(returncode, stdout, stderr)

        monkeypatch.setattr(host_module, "run_on_host", run)
        return calls

    def test_one_process_of_each_namespace_other_than_the_hosts_in_pid_order(self, monkeypatch):
        calls = self.answer(monkeypatch, json.dumps({"namespaces": [
            {"ns": 4026531841, "pid": 1}, {"ns": 4026532310, "pid": 655}, {"ns": 4026532200, "pid": 612},
        ]}))
        assert disks_module.mount_namespace_pids() == [612, 655]
        assert calls == [["lsns", "--json", "--type", "mnt", "--output", "NS,PID"]]

    def test_numbers_that_older_lsns_prints_as_strings(self, monkeypatch):
        self.answer(monkeypatch, '{"namespaces": [{"ns": "4026531841", "pid": "1"}, {"ns": "4026532200", "pid": "612"},'
                                 ' {"ns": "4026532201", "pid": null}]}')
        assert disks_module.mount_namespace_pids() == [612]

    @pytest.mark.parametrize(("stdout", "returncode", "stderr", "problem"), [
        ("", 1, "lsns: unknown column: NS", "lsns failed: lsns: unknown column: NS"),
        ("not json", 0, "", "lsns returned invalid JSON"),
        ("[1, 2]", 0, "", "lsns returned invalid JSON"),
    ])
    def test_failures_raise(self, monkeypatch, stdout, returncode, stderr, problem):
        self.answer(monkeypatch, stdout, returncode, stderr)
        with pytest.raises(RuntimeError, match=re.escape(problem)):
            disks_module.mount_namespace_pids()

    def test_the_mount_table_of_a_process(self, monkeypatch):
        calls = self.answer(monkeypatch, json.dumps({"filesystems": [
            {"target": "/downloads", "source": "/dev/sda[/downloads]", "fstype": "btrfs", "uuid": DATA_UUID,
             "maj:min": "0:44", "fsroot": "/downloads", "vfs-options": "rw,noatime", "fs-options": "rw"},
        ]}))
        [mount] = disks_module.namespace_mounts(1200)
        assert (mount.target, mount.source, mount.fsroot, mount.majmin) == ("/downloads", "/dev/sda", "/downloads", "0:44")
        assert calls == [["findmnt", "-J", "-l", "--task", "1200", "-o", disks_module.FINDMNT_COLUMNS]]

    def test_a_mount_table_that_cannot_be_read_raises(self, monkeypatch):
        self.answer(monkeypatch, "", 1, "findmnt: can't read /proc/700/mountinfo: No such file or directory")
        with pytest.raises(RuntimeError, match="findmnt failed: findmnt: can't read /proc/700/mountinfo"):
            disks_module.namespace_mounts(700)

    def test_lsns_is_a_host_tool(self):
        assert host_module.HOST_TOOLS["lsns"] == "util-linux"


def test_the_e2e_fakes_answer_with_the_servers_texts(data_pool):
    """webui/tests/storage-fakes.ts refuses an unmount or a removal of a pool in use with these
    texts word for word (ADR-0189): running containers that Docker reports, a mount namespace
    that has the pool mounted elsewhere after the host's umount (ADR-0288), and, with 503, a
    Docker that cannot be asked (ADR-0344)."""
    fakes = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "storage-fakes.ts").read_text(encoding="utf-8")
    pools.mount_pool("data")

    data_pool.docker.add("transmission", sources=["/mnt/pools/data/downloads"])
    with pytest.raises(pools.PoolBusyError) as used:
        pools.unmount_pool("data")
    assert str(used.value) == "Pool 'data' is used by running containers (transmission). Stop them and try again."
    template = str(used.value).replace("'data'", "'${pool}'").replace("transmission", "${containers.join(', ')}")
    assert f"`{template}`" in fakes

    data_pool.docker.containers_.clear()  # a container that Docker does not report
    data_pool.bind_into_namespace(1200, "/downloads", "/downloads")
    with pytest.raises(pools.PoolBusyError) as held:
        pools.unmount_pool("data")
    reason = "process 1200 has it mounted at /downloads"
    assert reason in str(held.value)
    template = str(held.value).replace("'data'", "'${pool}'").replace(reason, "${reason}")
    assert f"`{template}`" in fakes
    assert "`process ${pid} has it mounted at ${at}`" in fakes

    data_pool.docker.unavailable = True
    for operation in (pools.unmount_pool, pools.delete_pool):
        with pytest.raises(pools.DockerUnavailableError) as unasked:
            operation("data")
        reason = "Error while fetching server API version"  # what the Docker client said
        assert reason in str(unasked.value)
        template = str(unasked.value).replace("'data'", "'${pool}'").replace(reason, "${reason}")
        assert f"`{template}`" in fakes


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
        # The SMB startup reconcile would import the run's smb.conf and run host commands.
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
        assert ["findmnt", "-J", "-l", "-o", "TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN,FSROOT,VFS-OPTIONS,FS-OPTIONS"] \
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
    def test_a_pool_that_the_off_device_copy_uses_answers_409(self, api, data_pool, monkeypatch, method, url):
        """While a copy or a restore of the off-device copy reads or writes a pool, UNOS neither
        unmounts nor forgets it (ADR-0337): restic would go on writing onto the mount point."""
        from datetime import datetime

        offsite = importlib.import_module("src.offsite.service")
        pools.mount_pool("data")
        started = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)
        monkeypatch.setattr(offsite, "_activity", offsite.Activity("copy", started, pools={"data"}))

        response = getattr(api, method)(url)

        assert (response.status_code, response.json()) == (409, {
            "detail": "Pool 'data' is used by the off-device copy: the copy started at 2026-11-11 03:00:00 UTC "
                      "is still running. Wait for it to end and try again."})
        assert data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

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

    # The texts the Unmount and Remove Pool dialogs show (webui/tests/storage.spec.ts).
    @pytest.mark.parametrize(
        ("method", "url"),
        [("post", "/api/v1/storage/pools/data/unmount"), ("delete", "/api/v1/storage/pools/data")],
    )
    def test_a_pool_that_running_apps_use_answers_which_ones(self, api, data_pool, method, url):
        pools.mount_pool("data")
        data_pool.docker.add("transmission", sources=["/mnt/pools/data/apps/transmission"])
        data_pool.docker.add("jellyfin", labels={"unos.app": "jellyfin", "unos.app.pool": "data"})

        response = getattr(api, method)(url)

        assert (response.status_code, response.json()) == (409, {
            "detail": "Pool 'data' is used by running containers (jellyfin, transmission). "
                      "Stop them and try again."})
        assert data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

    @pytest.mark.parametrize(
        ("method", "url"),
        [("post", "/api/v1/storage/pools/data/unmount"), ("delete", "/api/v1/storage/pools/data")],
    )
    def test_a_pool_that_host_processes_use_answers_409(self, api, data_pool, method, url):
        pools.mount_pool("data")
        data_pool.busy.add("/mnt/pools/data")

        response = getattr(api, method)(url)

        assert (response.status_code, response.json()) == (409, {
            "detail": "/mnt/pools/data is busy (umount: /mnt/pools/data: target is busy.). Stop the "
                      "shares, apps and containers that use the pool and try again."})
        assert data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)
        assert "/mnt/pools/data" in data_pool.dirs

    def test_pool_still_in_use_after_unmounting_is_409(self, api, data_pool):
        pools.mount_pool("data")
        data_pool.held.add(DATA_UUID)
        response = api.post("/api/v1/storage/pools/data/unmount")
        assert response.status_code == 409
        assert "mounted again" in response.json()["detail"]
        assert api.get("/api/v1/storage/pools/data").json()["mounted"] is True

    @pytest.mark.parametrize(
        ("method", "url", "answer"),
        [("post", "/api/v1/storage/pools/data/unmount", None),
         ("delete", "/api/v1/storage/pools/data", {"status": "deleted"})],
    )
    def test_a_pool_whose_mount_services_keep_copies_of_is_unmounted_and_smb_conf_applied(
        self, api, data_pool, method, url, answer
    ):
        """ADR-0288: the copies in the services' mount namespaces are unmounted, the request
        succeeds, and smb.conf is applied after it as after any unmount (ADR-0248)."""
        pools.mount_pool("data")
        for pid in (612, 640):
            data_pool.copy_into_namespace(pid)
        before = data_pool.smb_applies

        response = getattr(api, method)(url)

        assert response.status_code == 200, response.text
        if answer is None:
            assert (response.json()["mounted"], response.json()["persistent"]) == (False, False)
        else:
            assert response.json() == answer
        assert data_pool.smb_applies == before + 1
        assert data_pool.fstab == BASE_FSTAB
        assert TestUnmountAndDelete.namespaces_holding(data_pool) == []
        assert api.get("/api/v1/storage/pools/data").json()["mounted"] is False

    def test_pool_that_another_mount_namespace_binds_is_409(self, api, data_pool):
        pools.mount_pool("data")
        data_pool.copy_into_namespace(612)
        data_pool.bind_into_namespace(1200, "/downloads", "/downloads")
        before = data_pool.smb_applies
        response = api.post("/api/v1/storage/pools/data/unmount")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Pool 'data' is still in use outside the host (process 1200 has it mounted at /downloads), so "
            "it was mounted again. Stop the apps and containers that use the pool and try again."
        )
        assert data_pool.smb_applies == before + 1
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

    def test_unmount_and_remove_without_docker_answer_503_and_change_nothing(self, api, data_pool):
        # 503, as for a volume delete without Docker: it is not known whether a container uses
        # the pool (ADR-0344).
        pools.mount_pool("data")
        wait_for_app_starts()
        data_pool.changes.clear()
        data_pool.docker.unavailable = True
        for method, url in (
            ("post", "/api/v1/storage/pools/data/unmount"),
            ("delete", "/api/v1/storage/pools/data"),
        ):
            response = getattr(api, method)(url)
            assert response.status_code == 503, response.text
            assert response.json()["detail"].startswith(
                "Pool 'data' was left mounted: Docker did not answer, so it is not known whether a "
                "running container keeps data on it. Try again when Docker is running"
            )
        assert data_pool.changes == []
        assert data_pool.mounted("/mnt/pools/data")
        assert data_pool.fstab == BASE_FSTAB + marker("data") + entry("data", DATA_UUID)

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
        # A folder under the pools base that holds files but is not a pool.
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

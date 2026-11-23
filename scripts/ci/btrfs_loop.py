#!/usr/bin/env python3
"""Storage through the API of the real supervisor on real btrfs on loop devices, in CI (ADR-0464).

  btrfs_loop.py serve --state DIR --port PORT --disk /dev/loopA --disk /dev/loopB
  btrfs_loop.py check --url URL --disk /dev/loopA --disk /dev/loopB --other /dev/loopC [--supervisor PID]

scripts/ci/btrfs-loop.sh runs both as root on a GitHub-hosted runner, the job of
.github/workflows/btrfs.yml: it makes the loop devices, on sparse files in DIR, and refuses any other
machine.

serve runs the supervisor of this checkout (uvicorn on 127.0.0.1:PORT) as root on the runner, with its
host commands on the runner itself, through nsenter into the namespaces of PID 1 as in its container
(supervisor/src/host.py, ADR-0020): mkfs.btrfs, mount and btrfs, its pools below /mnt/pools and their
lines in /etc/fstab, the Docker socket of the runner, as on a NAS. Its data directory, smb.conf, NFS
exports, served certificate, updater backups and TMPDIR are in DIR (ADR-0419), and its background tasks
are off (UNOS_DISABLE_BACKGROUND_TASKS=1: no mounts at the start, no scheduler, no monitors). The
supervisor never offers a loop device for a pool: lsblk's type loop is no disk, and loop is one of
HIDDEN_DISK_PREFIXES (supervisor/src/storage/disks.py, ADR-0035). serve replaces two functions of that
module, as the stand-in replaces functions and sets no constant (ADR-0419), so that the --disk devices
are whole disks and the only ones the supervisor offers: _lsblk_tree, whose answer names them type
disk, and is_hidden_disk, which hides every other device, the other loop devices and the runner's own
disks. It refuses a --disk that is not /dev/loop<N> or whose backing file is not in DIR.

Both refuse to run but on a GitHub-hosted runner (GITHUB_ACTIONS=true, RUNNER_ENVIRONMENT=github-hosted),
and serve only as root on Linux.

check waits for the supervisor (and stops at once when the process PID has ended), signs in as its first administrator with a password made here and never
printed, and runs its steps with Check of scripts/dev/scenario.py (ADR-0304), without the dev device's
lock and SD measures: a PASS or FAIL line for each step with its seconds, the first FAIL ends the steps,
the clean-ups run newest first, also after a FAIL or an interruption, and the end compares the pools
mounted or in fstab, the mounts below /mnt/pools and their lines in /etc/fstab with the baseline. The
steps: the two loop devices listed as disks without data, alone (not the third, not the runner's own
disks); a runner without pools, mounts below /mnt/pools or fstab lines for them, else the check stops
before it changes anything; Create Pool refused for the third as no disk, which formats nothing; a raid1 pool of the two, which btrfs filesystem show and df, findmnt and findmnt --fstab show
mounted with its fstab line; a volume, a top-level subvolume in btrfs subvolume list; a file and 64 MiB
of random data written into it on the runner; a snapshot, which btrfs lists read-only; the file changed,
and a restore, a writable subvolume restore_<date>_<time> in the volume with the file as at the
snapshot; a scrub that ends finished without errors, whose data bytes btrfs scrub status -R counts on
both disks; Delete of the copy, the
snapshot and the volume, gone from btrfs's list; Forget, which unmounts the pool and drops its fstab
line while its disks keep the filesystem. Exit status: 0 when every step passed, 1 when one failed, 2
for a usage error or a refusal, 130 after an interruption.

check needs the standard library only; serve the supervisor's packages.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SUPERVISOR = REPO / "supervisor"
if str(REPO / "scripts" / "dev") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts" / "dev"))
import scenario  # noqa: E402  scripts/dev/scenario.py: Check, the steps' frame, and the scrub's read

relay = scenario.relay
LOOP = re.compile(r"/dev/(loop\d+)")
# Where the supervisor mounts its pools: the default of UNOS_POOLS_BASE_PATH, which serve keeps.
POOLS = "/mnt/pools"
ADMIN = "unosci"
# The file that the snapshot and the restore keep, and the random data that the scrub reads on both disks.
FILE = "unoscheck.txt"
DATA = b"unos btrfs check\n"
DATA2 = b"unos btrfs check, changed\n"
BALLAST_MIB = 64
REQUEST_TIMEOUT = 300
START_WAIT = 120
SCRUB_WAIT = 300


def fail(text: str) -> SystemExit:
    """A refusal: TEXT on standard error, and exit status 2."""
    print(f"btrfs_loop.py: {text}", file=sys.stderr)
    return SystemExit(2)


# --- serve -----------------------------------------------------------------------------------


def loop_names(paths: list[str]) -> frozenset[str]:
    """The kernel's names of the loop devices PATHS (/dev/loop<N>); anything else is refused."""
    names = []
    for path in paths:
        found = LOOP.fullmatch(path)
        if found is None:
            raise fail(f"{path} is not a loop device /dev/loop<N>")
        names.append(found[1])
    if not names or len(set(names)) != len(names):
        raise fail(f"the loop devices must be named once each: {paths}")
    return frozenset(names)


def check_backing(names: frozenset[str], state: Path, sys_block: Path = Path("/sys/block")) -> None:
    """Refuse a loop device whose backing file is not right in STATE, the job's own folder."""
    folder = os.path.realpath(state)
    for name in sorted(names):
        try:
            backing = (sys_block / name / "loop" / "backing_file").read_text().strip()
        except OSError as e:
            raise fail(f"/dev/{name} has no backing file: {e}") from None
        if os.path.dirname(os.path.realpath(backing)) != folder:
            raise fail(f"the backing file of /dev/{name}, {backing}, is not in {folder}")


def as_disks(tree: list[dict[str, Any]], names: frozenset[str]) -> list[dict[str, Any]]:
    """lsblk's tree with the loop devices NAMES as whole disks (type disk); every other node as it is."""
    return [dict(node, type="disk") if node.get("type") == "loop" and node.get("name") in names else node
            for node in tree]


def configure(disks: Any, names: frozenset[str]) -> None:
    """Make the loop devices NAMES the only disks that the supervisor's module DISKS offers: whole disks
    of type disk, and every other device hidden, as a boot or virtual device is."""
    lsblk_tree = disks._lsblk_tree

    def job_lsblk_tree() -> list[dict[str, Any]]:
        return as_disks(lsblk_tree(), names)

    def job_is_hidden_disk(name: str) -> bool:
        return name not in names

    disks._lsblk_tree = job_lsblk_tree
    disks.is_hidden_disk = job_is_hidden_disk


def supervisor_environment(state: Path) -> dict[str, str]:
    """The settings that put the supervisor's own files in STATE. The host paths (pools, fstab, /mnt),
    the Docker socket and the host commands keep their defaults, those of a NAS."""
    data = state / "data"
    return {
        "UNOS_DATA_DIR": str(data),
        # Empty: generated into data/jwt_secret, as in production.
        "UNOS_JWT_SECRET": "",
        # The runner has no Samba and no NFS server: the files go to the data folder.
        "UNOS_SMB_CONF_PATH": str(data / "smb.conf"),
        "UNOS_NFS_EXPORTS_PATH": str(data / "exports"),
        "UNOS_SERVED_CERTIFICATE_DIR": str(data / "served-certificate"),
        "UNOS_UPDATE_BACKUP_DIR": str(data / "update-backups"),
        "UNOS_INSTALL_DIR": str(state / "install"),
        "UNOS_DISABLE_BACKGROUND_TASKS": "1",
        "TMPDIR": str(state / "tmp"),
    }


def serve(args: argparse.Namespace) -> int:
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise fail("serve runs only as root on Linux, through scripts/ci/btrfs-loop.sh")
    names = loop_names(args.disk)
    state = args.state.resolve()
    check_backing(names, state)
    for name in list(os.environ):
        if name.startswith(("UNOS_", "DOCKER_")):
            del os.environ[name]
    os.environ.update(supervisor_environment(state))
    for folder in (state / "data", state / "tmp"):
        folder.mkdir(mode=0o700, exist_ok=True)
    # As the image's WORKDIR, /app, holds src/.
    os.chdir(SUPERVISOR)
    sys.path.insert(0, str(SUPERVISOR))
    supervisor = importlib.import_module("src.main")
    configure(importlib.import_module("src.storage.disks"), names)

    import uvicorn
    from src.logging_setup import logging_config

    # The supervisor's JSON lines (ADR-0398) from INFO up: the pools, volumes and snapshots it makes.
    uvicorn.run(supervisor.app, host="127.0.0.1", port=args.port, log_config=logging_config(logging.INFO),
                timeout_graceful_shutdown=3)
    return 0


# --- check -----------------------------------------------------------------------------------


class Api:
    """The supervisor's API at URL, as scenario.Check calls it: request() answers like api.py's relay,
    and the values of secrets are redacted wherever the check prints."""

    def __init__(self, url: str) -> None:
        if not re.fullmatch(r"http://127\.0\.0\.1:\d+/?", url):
            raise fail(f"check asks only the supervisor on this runner, http://127.0.0.1:<port>, not {url}")
        self.base = url.rstrip("/") + "/api/v1"
        self.token: str | None = None
        self.secrets: list[str] = []
        # 127.0.0.1, never through a proxy of the environment.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, json_body: Any = None, params: dict | None = None) -> relay.Answer:
        query = "?" + urllib.parse.urlencode(params, doseq=True) if params else ""
        data = None if json_body is None else json.dumps(json_body).encode()
        request = urllib.request.Request(self.base + path + query, data=data, method=method)  # noqa: S310 - http://127.0.0.1
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self.opener.open(request, timeout=REQUEST_TIMEOUT) as answer:
                return relay.Answer(answer.status, answer.read().decode(errors="replace"))
        except urllib.error.HTTPError as e:
            return relay.Answer(e.code, e.read().decode(errors="replace"))
        except (urllib.error.URLError, OSError) as e:
            raise relay.ApiError(f"{method} {path}: {getattr(e, 'reason', e)}") from None


def filesystem_show(text: str) -> dict[str, Any]:
    """The label, UUID and device paths that btrfs filesystem show prints for one filesystem."""
    head = re.search(r"^Label: (?:'(.*)'|none)\s+uuid: (\S+)", text, re.M)
    return {"label": head and head[1], "uuid": head and head[2],
            "devices": re.findall(r"^\s+devid\s+\d+\s.*\spath (\S+)$", text, re.M)}


def profiles(text: str) -> dict[str, str]:
    """The profile of each kind of chunk that btrfs filesystem df prints, without the global reserve."""
    return {kind: profile for kind, profile in re.findall(r"^(\w+), (\w+):", text, re.M) if kind != "GlobalReserve"}


def scrub_counts(text: str) -> dict[str, int]:
    """The counts that btrfs scrub status -R prints, name -> value."""
    return {name: int(value) for name, value in re.findall(r"^\s+(\w+): (\d+)$", text, re.M)}


def subvolumes(text: str) -> dict[str, int]:
    """Path -> the ID of its parent, as btrfs subvolume list prints them (5: the filesystem's top level)."""
    return {path: int(top) for top, path in re.findall(r"^ID \d+ .*?\btop level (\d+)(?: .*?)? path (.+)$", text,
                                                       re.M)}


class Runner:
    """What the runner itself shows, read as root: what each step checks after the API answered."""

    @staticmethod
    def sectors() -> None:
        return None  # no dev device here: the steps' seconds take the place of its writes

    @staticmethod
    def run(*argv: str, ok: tuple[int, ...] = (0,)) -> str:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        if result.returncode not in ok:
            raise scenario.Failed(f"{' '.join(argv)} exited with {result.returncode}: "
                                  f"{scenario.tail(result.stderr or result.stdout)}")
        return result.stdout

    def filesystem(self, mountpoint: str) -> dict[str, Any]:
        return filesystem_show(self.run("btrfs", "filesystem", "show", mountpoint))

    def profiles(self, mountpoint: str) -> dict[str, str]:
        return profiles(self.run("btrfs", "filesystem", "df", mountpoint))

    def subvolumes(self, mountpoint: str, snapshots: bool = False) -> dict[str, int]:
        return subvolumes(self.run("btrfs", "subvolume", "list", *(["-s"] if snapshots else []), mountpoint))

    def read_only(self, path: str) -> bool:
        answer = self.run("btrfs", "property", "get", "-ts", path, "ro").strip()
        if answer not in ("ro=true", "ro=false"):
            raise scenario.Failed(f"btrfs property get answered {answer!r} for {path}")
        return answer == "ro=true"

    def _findmnt(self, *args: str) -> dict[str, Any] | None:
        out = self.run("findmnt", "--json", *args, "-o", "TARGET,SOURCE,FSTYPE,UUID,OPTIONS", ok=(0, 1))
        found = json.loads(out)["filesystems"] if out.strip() else []
        return found[0] if found else None

    def mount(self, mountpoint: str) -> dict[str, Any] | None:
        """What the runner has mounted at MOUNTPOINT, or None."""
        return self._findmnt("--mountpoint", mountpoint)

    def fstab_line(self, mountpoint: str) -> dict[str, Any] | None:
        """The line of /etc/fstab that mounts MOUNTPOINT, or None."""
        return self._findmnt("--fstab", "--mountpoint", mountpoint)

    def mounts_below(self, base: str) -> list[str]:
        targets = self.run("findmnt", "-rn", "-o", "TARGET").split()
        return sorted(t for t in targets if t.startswith(base + "/"))

    def fstab_below(self, base: str) -> list[str]:
        """The mountpoints below BASE that /etc/fstab names."""
        targets = self.run("findmnt", "--fstab", "-rn", "-o", "TARGET", ok=(0, 1)).split()
        return sorted(t for t in targets if t.startswith(base + "/"))

    def fstype(self, device: str) -> str:
        """The filesystem that blkid finds on DEVICE itself, "" for none."""
        return self.run("blkid", "-p", "-s", "TYPE", "-o", "value", device, ok=(0, 2)).strip()

    def scrub_status(self, mountpoint: str) -> str:
        return self.run("btrfs", "scrub", "status", mountpoint)

    def scrub_counts(self, mountpoint: str) -> dict[str, int]:
        """The raw counts of the last scrub, summed over the disks (btrfs scrub status -R)."""
        return scrub_counts(self.run("btrfs", "scrub", "status", "-R", mountpoint))

    @staticmethod
    def write(path: str, data: bytes) -> None:
        Path(path).write_bytes(data)

    @staticmethod
    def write_random(path: str, mib: int) -> None:
        with open(path, "wb") as f:
            for _ in range(mib):
                f.write(os.urandom(1 << 20))
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def read(path: str) -> bytes:
        return Path(path).read_bytes()


class CiCheck(scenario.Check):
    """scenario.Check with the seconds of each step where the dev device's check prints its SD MiB."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.since = time.monotonic()

    def sd(self) -> str:
        now = time.monotonic()
        spent, self.since = now - self.since, now
        return f"{spent:.1f} s"


def alive(pid: int) -> bool:
    """Whether the process PID still runs (or has ended and was not reaped yet)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_start(api: Api, supervisor: int | None = None) -> None:
    """Until GET /health answers 200; a supervisor SUPERVISOR (its PID) that has ended fails at once."""
    deadline = time.monotonic() + START_WAIT
    while True:
        try:
            if api.request("GET", "/health").status_code == 200:
                return
        except relay.ApiError:
            pass
        if supervisor is not None and not alive(supervisor):
            raise scenario.Failed("the supervisor ended before it answered: its log, printed after the steps, says why")
        if time.monotonic() > deadline:
            raise scenario.Failed(f"GET /api/v1/health did not answer 200 within {START_WAIT} s: the supervisor's "
                                  "log, printed after the steps, says why")
        time.sleep(1)


def scrub_until_it_ends(c: scenario.Check, pool: str) -> dict:
    """The pool's scrub once it has ended: btrfs reports a short scrub as idle until it recorded it
    (ADR-0313), and this pool has had no other scrub."""
    deadline = time.monotonic() + SCRUB_WAIT
    while True:
        status = scenario.scrub_of(c, pool)
        if status.get("status") not in ("running", "idle"):
            return status
        c.expect(time.monotonic() < deadline, f"the scrub of {pool} has not ended after {SCRUB_WAIT} s")
        time.sleep(1)


def storage_check(c: scenario.Check, disks: list[str], other: str, password: str,
                  supervisor: int | None = None) -> None:
    api = c.api
    name = volume = snap = c.run
    mountpoint = f"{POOLS}/{name}"
    vpath = f"{mountpoint}/{volume}"
    spath = f"{vpath}/.snapshots/{snap}"
    made_copies: list[str] = []

    def pool_mounted() -> bool:
        return any(p.get("name") == name and p.get("mounted") for p in c.get("/storage/pools"))

    def cancel_scrub() -> None:
        """A scrub that still runs, cancelled; one that never started stays idle, which is no wait."""
        if pool_mounted():
            c.call("DELETE", f"/storage/pools/{name}/scrub")
            scenario.scrub_until_it_ends(c, name)

    def remove_volume() -> None:
        """The volume with the restored copies and snapshots in it, which its Delete refuses, while the
        pool is mounted: those that the lists name, and the copies whose restore answered."""
        if not pool_mounted():
            return
        listed = c.get("/snapshots/copies", {"subvolume": vpath}, expect=(200, 404))
        if isinstance(listed, list):
            for copy in sorted({x["name"] for x in listed} | set(made_copies)):
                c.call("DELETE", f"/snapshots/copies/{copy}", params={"subvolume": vpath}, expect=(200, 404))
            for snapshot in c.get("/snapshots/", {"subvolume": vpath}):
                c.call("DELETE", f"/snapshots/{snapshot['name']}", params={"subvolume": vpath}, expect=(200, 404))
        c.delete_volume(name, volume)

    def volumes() -> list[str]:
        return [v["name"] for v in c.get(f"/storage/pools/{name}/volumes")]

    with c.step("the supervisor answers, and its first administrator signs in"):
        wait_for_start(api, supervisor)
        c.post("/auth/setup", {"username": ADMIN, "password": password})
        token = c.post("/auth/login", {"username": ADMIN, "password": password})["access_token"]
        api.secrets.append(token)
        api.token = token

    with c.step(f"Storage lists {disks[0]} and {disks[1]} alone, as disks without data: not {other} nor the "
                "runner's own disks"):
        listed = {d["path"]: d for d in c.get("/storage/disks")}
        c.expect(set(listed) == set(disks), f"it lists {sorted(listed)}")
        for disk in disks:
            c.expect(not listed[disk]["has_data"] and not listed[disk]["in_use"] and not listed[disk]["is_system"],
                     f"{disk}: {listed[disk]}")

    # A machine with pools (a NAS) is refused here, before the check changes anything on it.
    with c.step(f"the runner has no pool, no mount below {POOLS} and no line of /etc/fstab for one"):
        c.baseline(lambda: {
            "the pools mounted or in fstab": sorted(p["name"] for p in c.get("/storage/pools")
                                                    if p.get("mounted") or p.get("persistent")),
            f"the mounts below {POOLS}": c.device.mounts_below(POOLS),
            f"the lines of /etc/fstab below {POOLS}": c.device.fstab_below(POOLS),
        })
        found = {key: value for key, value in (c.before or {}).items() if value}
        c.expect(not found, f"this is no fresh runner: {found}")

    # 404: the pool was never made, after a FAIL of the refusal or of Create Pool.
    c.on_cleanup(f"forget the pool {name}", lambda: c.call("DELETE", f"/storage/pools/{name}", expect=(200, 404)))
    with c.step(f"Create Pool refuses {other}, a loop device that is no disk of the job, and formats nothing") as s:
        refused = c.post("/storage/pools", {"name": name, "devices": [disks[0], other], "raid_level": "raid1",
                                            "confirm_name": name}, expect=400)
        c.expect(refused.get("detail") in (f"{other} is not a disk or partition on this system",
                                           f"{other} is a boot or virtual device and cannot be used for a pool"),
                 f"it is refused for another reason: {refused}")
        found = {device: c.device.fstype(device) for device in (disks[0], other)}
        c.expect(found == {disks[0]: "", other: ""}, f"blkid finds {found}")
        s.text += f": {refused.get('detail')!r}"

    with c.step(f"Create Pool makes the raid1 pool {name} of {disks[0]} and {disks[1]}, which btrfs shows on both "
                f"with RAID1 chunks, mounted at {mountpoint} with its line in /etc/fstab"):
        pool = c.post("/storage/pools", {"name": name, "devices": disks, "raid_level": "raid1", "confirm_name": name})
        c.expect((pool.get("mounted"), pool.get("persistent"), pool.get("mountpoint"), pool.get("raid_level"),
                  pool.get("health"), sorted(pool.get("devices") or []))
                 == (True, True, mountpoint, "raid1", "healthy", sorted(disks)), f"the answer is {pool}")
        shown = c.device.filesystem(mountpoint)
        c.expect({**shown, "devices": sorted(shown["devices"])} == {"label": name, "uuid": pool["uuid"],
                                                                     "devices": sorted(disks)},
                 f"btrfs filesystem show: {shown}")
        chunks = c.device.profiles(mountpoint)
        c.expect(chunks == {"Data": "RAID1", "Metadata": "RAID1", "System": "RAID1"}, f"btrfs filesystem df: {chunks}")
        mounted = c.device.mount(mountpoint)
        c.expect(mounted is not None and mounted["fstype"] == "btrfs" and mounted["uuid"] == pool["uuid"]
                 and mounted["source"] in disks, f"findmnt: {mounted}")
        line = c.device.fstab_line(mountpoint)
        c.expect(line is not None and line["source"] == f"UUID={pool['uuid']}" and line["fstype"] == "btrfs",
                 f"findmnt --fstab: {line}")

    c.on_cleanup(f"delete the volume {volume} with its restored copies and snapshots", remove_volume)
    with c.step(f"a volume is made at {vpath} and listed, and btrfs lists it as a subvolume at the top level"):
        made = c.post(f"/storage/pools/{name}/volumes", {"name": volume, "quota": None})
        c.expect(made.get("path") == vpath, f"it is at {made.get('path')}")
        c.expect(volume in volumes(), "it is not listed")
        listed_by_btrfs = c.device.subvolumes(mountpoint)
        c.expect(listed_by_btrfs.get(volume) == 5, f"btrfs subvolume list: {listed_by_btrfs}")

    with c.step(f"the runner writes {FILE} and {BALLAST_MIB} MiB of random data into the volume"):
        c.device.write(f"{vpath}/{FILE}", DATA)
        c.device.write_random(f"{vpath}/ballast", BALLAST_MIB)

    with c.step(f"a snapshot {snap} of the volume is made and listed, and btrfs lists it as a read-only snapshot"):
        made = c.post("/snapshots/", {"subvolume": vpath, "name": snap})
        c.expect(made.get("path") == spath, f"it is at {made.get('path')}")
        c.expect(spath in [x["path"] for x in c.get("/snapshots/", {"subvolume": vpath})], "it is not listed")
        listed_by_btrfs = c.device.subvolumes(mountpoint, snapshots=True)
        c.expect(f"{volume}/.snapshots/{snap}" in listed_by_btrfs, f"btrfs subvolume list -s: {listed_by_btrfs}")
        c.expect(c.device.read_only(spath), "btrfs shows it writable")

    with c.step(f"with {FILE} changed, Restore makes a writable subvolume restore_<date>_<time> in the volume "
                f"that holds {FILE} as it was at the snapshot") as s:
        c.device.write(f"{vpath}/{FILE}", DATA2)
        target = c.post("/snapshots/restore", {"snapshot_path": spath})["target"]
        copy = target.rsplit("/", 1)[1]
        made_copies.append(copy)
        s.text += f": {target}"
        c.expect(target == f"{vpath}/{copy}" and re.fullmatch(r"restore_\d{8}_\d{6}", copy),
                 "it is not restore_<date>_<time> right in the volume")
        c.expect(target in [x["path"] for x in c.get("/snapshots/copies", {"subvolume": vpath})],
                 "it is not listed with the volume's restored copies")
        listed_by_btrfs = c.device.subvolumes(mountpoint)
        c.expect(f"{volume}/{copy}" in listed_by_btrfs, f"btrfs subvolume list: {listed_by_btrfs}")
        c.expect(not c.device.read_only(target), "btrfs shows it read-only")
        c.expect(c.device.read(f"{target}/{FILE}") == DATA, f"its {FILE} is not the one of the snapshot")
        c.expect(c.device.read(f"{vpath}/{FILE}") == DATA2, f"the volume's {FILE} changed")

    c.on_cleanup(f"cancel a scrub of {name} that still runs", cancel_scrub)
    with c.step(f"Start Scrub of {name} starts a scrub that ends finished without errors, as btrfs scrub status "
                "says on the runner") as s:
        started = c.post(f"/storage/pools/{name}/scrub")
        c.expect(started.get("status") in ("running", "finished"), f"Start Scrub answered {started}")
        ended = scrub_until_it_ends(c, name)
        c.expect((ended.get("status"), ended.get("errors")) == ("finished", 0), f"it ended {ended}")
        status = c.device.scrub_status(mountpoint)
        c.expect(re.search(r"^Status:\s+finished$", status, re.M) and re.search(r"no errors found", status),
                 f"btrfs scrub status: {status}")
        # The data it read and checked, both copies of the volume's random data among it, without an error.
        counts = c.device.scrub_counts(mountpoint)
        c.expect(counts.get("data_bytes_scrubbed", 0) >= 2 * BALLAST_MIB << 20,
                 f"btrfs scrub status -R: {counts.get('data_bytes_scrubbed')} data bytes scrubbed, less than both "
                 f"copies of the volume's {BALLAST_MIB} MiB")
        errors = {key: value for key, value in counts.items() if key.endswith("_errors") and value}
        c.expect(not errors, f"btrfs scrub status -R: {errors}")
        s.text += f": {counts['data_bytes_scrubbed']} data bytes in {ended.get('duration')} s"

    with c.step("Delete removes the restored copy, the snapshot and the volume, which btrfs no longer lists"):
        c.call("DELETE", f"/snapshots/copies/{copy}", params={"subvolume": vpath})
        c.call("DELETE", f"/snapshots/{snap}", params={"subvolume": vpath})
        c.call("DELETE", f"/storage/pools/{name}/volumes/{volume}")
        c.expect(volume not in volumes(), "the volume is still listed")
        left = sorted(p for p in c.device.subvolumes(mountpoint) if p == volume or p.startswith(volume + "/"))
        c.expect(not left, f"btrfs still lists {left}")

    with c.step(f"Forget unmounts {name} and drops its line from /etc/fstab, and its disks keep the filesystem"):
        c.call("DELETE", f"/storage/pools/{name}")
        c.expect(c.device.mount(mountpoint) is None, f"findmnt: {c.device.mount(mountpoint)}")
        c.expect(c.device.fstab_line(mountpoint) is None, f"findmnt --fstab: {c.device.fstab_line(mountpoint)}")
        listed = next((p for p in c.get("/storage/pools") if p.get("name") == name), None)
        c.expect(listed is not None and not listed["mounted"] and not listed["persistent"],
                 f"the pools list it as {listed}")
        found = {disk: c.device.fstype(disk) for disk in disks}
        c.expect(found == dict.fromkeys(disks, "btrfs"), f"blkid finds {found}")


def check(args: argparse.Namespace) -> int:
    disks = list(args.disk)
    loop_names([*disks, args.other])  # three different loop devices
    if len(disks) != 2:
        raise fail("check takes two --disk devices, for a raid1 pool")
    api = Api(args.url)
    password = secrets.token_urlsafe(24)
    api.secrets.append(password)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, scenario.interrupt)
    c = CiCheck("btrfs", api, Runner(), signals=True)
    return c.execute(lambda c: storage_check(c, disks, args.other, password, args.supervisor))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="btrfs_loop.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    serving = commands.add_parser("serve", help="run the supervisor with the loop devices as its disks")
    serving.add_argument("--state", type=Path, required=True)
    serving.add_argument("--port", type=int, required=True)
    serving.add_argument("--disk", action="append", required=True)
    checking = commands.add_parser("check", help="run the steps against the supervisor at --url")
    checking.add_argument("--url", required=True)
    checking.add_argument("--disk", action="append", required=True)
    checking.add_argument("--other", required=True)
    checking.add_argument("--supervisor", type=int, help="the PID of serve: the check stops when it has ended")
    args = parser.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise fail("runs only on a GitHub-hosted runner (GITHUB_ACTIONS=true, RUNNER_ENVIRONMENT=github-hosted), "
                   "through scripts/ci/btrfs-loop.sh")
    return serve(args) if args.command == "serve" else check(args)


if __name__ == "__main__":
    sys.exit(main())

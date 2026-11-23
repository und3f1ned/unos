"""The reads that the web UI's pages wait for, against the fakes (architecture.fast-page-reads).

Each test counts the work of one request: the Docker requests of the container list, the host
commands of the share paths, and how many host commands a request waits for one after another
(``Overlap.depth``), where the commands that do not depend on each other run at once.
"""

import copy
import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from types import SimpleNamespace

import docker
import pytest
import requests
from fastapi.testclient import TestClient

from src import host as host_module
from src.audit import router as audit_router
from src.auth.utils import verify_token
from src.containers import docker as docker_module
from src.main import app
from src.storage import pools

from .test_pool_persistence import MEDIA_UUID
from .test_smb_integration import API, cycle, pool_cycle  # noqa: F401  (fixtures)
from .test_smb_interfaces import IP, SMBD_RESET, SS, SmbdHost
from .test_volumes import DATA_UUID, api, host  # noqa: F401  (fixtures)


# --- Docker ----------------------------------------------------------------------------------------


@dataclass
class FakeContainer:
    """A container as Docker keeps it: what its list and its inspect answer are made of."""

    id: str  # 64 hex digits
    name: str
    image_ref: str  # the reference it was created from (Config.Image)
    image_id: str  # the image it runs (sha256:...)
    state: str
    created: int  # Unix seconds, as the list gives it
    created_text: str  # RFC 3339 with nanoseconds, as the inspect gives it
    ports: dict[str, list[dict] | None]  # NetworkSettings.Ports, its keys sorted as Docker prints them
    labels: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)  # names of legacy links: /<other>/<alias>


class FakeDockerApi(requests.adapters.BaseAdapter):
    """Docker's HTTP API behind a real docker-py client: GET /containers/json (all; the filter id
    as Docker's index of IDs looks it up: an ID or a prefix that names one container, anything
    else none), /containers/{id}/json, /images/json, /images/{id}/json and POST
    /containers/{id}/stop. Each request is recorded in ``requests`` as (method, path without the
    API version)."""

    def __init__(self, containers: list[FakeContainer], images: dict[str, list[str]]) -> None:
        super().__init__()
        self.containers = containers
        self.images = images  # image ID -> RepoTags
        self.requests: list[tuple[str, str]] = []
        self.queries: list[dict[str, list[str]]] = []

    def summary(self, c: FakeContainer) -> dict:
        # Docker shows the reference while it still names the container's image, else the image's ID.
        image = c.image_ref if c.image_ref in self.images.get(c.image_id, []) else c.image_id
        ports = []
        for key, bindings in reversed(list(c.ports.items())):  # a Go map: in no particular order
            port, protocol = key.split("/")
            if not bindings:
                ports.append({"PrivatePort": int(port), "Type": protocol})
            for binding in bindings or []:
                ports.append({"IP": binding["HostIp"], "PrivatePort": int(port),
                              "PublicPort": int(binding["HostPort"]), "Type": protocol})
        return {
            "Id": c.id, "Names": sorted([f"/{c.name}", *c.aliases]), "Image": image, "ImageID": c.image_id,
            "Command": "/init", "Created": c.created, "Ports": ports, "Labels": c.labels, "State": c.state,
            "Status": "Up 2 hours" if c.state == "running" else "Exited (0) 3 days ago",
            "HostConfig": {"NetworkMode": "bridge"}, "Mounts": [],
        }

    def inspect(self, c: FakeContainer) -> dict:
        return {
            "Id": c.id, "Name": f"/{c.name}", "Created": c.created_text, "Image": c.image_id,
            "Config": {"Image": c.image_ref, "Labels": c.labels},
            "State": {"Status": c.state, "Running": c.state == "running"},
            "NetworkSettings": {"Ports": c.ports},
        }

    def _find(self, key: str) -> FakeContainer | None:
        for match in (lambda c: c.id == key, lambda c: c.name == key, lambda c: c.id.startswith(key)):
            found = [c for c in self.containers if match(c)]
            if found:
                return found[0]
        return None

    def send(self, request, **kwargs):
        url = urllib.parse.urlsplit(request.url)
        path = re.sub(r"^/v[0-9.]+", "", urllib.parse.unquote(url.path))
        query = urllib.parse.parse_qs(url.query)
        self.requests.append((request.method, path))
        self.queries.append(query)
        status, body = 404, {"message": f"page not found: {path}"}
        if path == "/containers/json":
            listed = [c for c in self.containers if query.get("all") == ["1"] or c.state == "running"]
            ids = json.loads(query.get("filters", ["{}"])[0]).get("id")
            if ids is not None:
                named = {c.id for c in self.containers for prefix in ids
                         if [other.id for other in self.containers if other.id.startswith(prefix)] == [c.id]}
                listed = [c for c in listed if c.id in named]
            status, body = 200, [self.summary(c) for c in listed]
        elif path == "/images/json":
            # With the containerd image store an image pulled by its digest holds that digest form
            # (name@sha256:...) among its RepoTags.
            status, body = 200, [{"Id": image_id, "RepoTags": tags or None, "RepoDigests": [], "Size": 1}
                                 for image_id, tags in self.images.items()]
        elif request.method == "POST" and (match := re.fullmatch(r"/containers/([^/]+)/stop", path)):
            found = self._find(match[1])
            if found is not None:
                found.state = "exited"
                status, body = 204, None
        elif match := re.fullmatch(r"/containers/([^/]+)/json", path):
            found = self._find(match[1])
            status, body = (200, self.inspect(found)) if found else (404, {"message": f"No such container: {match[1]}"})
        elif match := re.fullmatch(r"/images/([^/]+)/json", path):
            image_id = next((i for i in self.images if i.removeprefix("sha256:").startswith(match[1])), None)
            if image_id is not None:
                status, body = 200, {"Id": image_id, "RepoTags": self.images[image_id], "Size": 1}
        response = requests.Response()
        response.status_code = status
        response._content = b"" if body is None else json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
        response.url = request.url
        response.request = request
        return response

    def close(self) -> None:
        pass


def _hex(seed: str) -> str:
    return (seed.encode().hex() * 64)[:64]


JELLYFIN_IMAGE = f"sha256:{_hex('jellyfin-image')}"
OLD_TRANSMISSION_IMAGE = f"sha256:{_hex('old-transmission')}"
NEW_TRANSMISSION_IMAGE = f"sha256:{_hex('new-transmission')}"
SUPERVISOR_IMAGE = f"sha256:{_hex('supervisor')}"
IMMICH_IMAGE = f"sha256:{_hex('immich-image')}"
TRANSMISSION_IMAGE = f"sha256:{_hex('transmission-image')}"
# The app catalog creates an app's containers from their images' digests (name@sha256:...).
IMMICH_DIGEST = f"ghcr.io/immich-app/immich-server@{IMMICH_IMAGE}"
TRANSMISSION_DIGEST = f"ghcr.io/linuxserver/transmission@{TRANSMISSION_IMAGE}"

CONTAINERS = [
    FakeContainer(
        id=_hex("jellyfin"), name="jellyfin", image_ref="jellyfin/jellyfin:10.10.3", image_id=JELLYFIN_IMAGE,
        state="running", created=1763575781, created_text="2025-11-19T18:09:41.436906743Z",
        ports={
            "1900/udp": None,
            "7359/udp": None,
            "8096/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8096"}, {"HostIp": "::", "HostPort": "8096"}],
        },
        labels={"unos.app": "jellyfin"},
        aliases=["/aaa-proxy/media"],
    ),
    # An update kept the earlier version, whose tag now names the new image.
    FakeContainer(
        id=_hex("transmission-previous"), name="transmission.previous",
        image_ref="lscr.io/linuxserver/transmission:latest", image_id=OLD_TRANSMISSION_IMAGE,
        state="exited", created=1762498392, created_text="2025-11-07T06:53:12.755042928Z", ports={},
    ),
    FakeContainer(
        id=_hex("leaving"), name="leaving", image_ref=JELLYFIN_IMAGE, image_id=JELLYFIN_IMAGE,
        state="removing", created=1762498392, created_text="2025-11-07T06:53:12.000000001Z", ports={},
    ),
    # Created from a digest; its image also has a tag (as Immich's on the dev device).
    FakeContainer(
        id=_hex("immich"), name="immich", image_ref=IMMICH_DIGEST, image_id=IMMICH_IMAGE,
        state="running", created=1763384739, created_text="2025-11-17T13:05:39.526599643Z", ports={},
        labels={"unos.app": "immich"},
    ),
    # Created from a digest; its image has only that digest form (as Transmission's on the dev device).
    FakeContainer(
        id=_hex("transmission"), name="transmission", image_ref=TRANSMISSION_DIGEST, image_id=TRANSMISSION_IMAGE,
        state="running", created=1763575781, created_text="2025-11-19T18:09:41.436906743Z", ports={},
        labels={"unos.app": "transmission"},
    ),
    FakeContainer(
        id=_hex("unos-supervisor"), name="unos-supervisor", image_ref="deploy-supervisor:latest",
        image_id=SUPERVISOR_IMAGE, state="running", created=1762498392, created_text="2025-11-07T06:53:12Z",
        ports={},
    ),
]
IMAGES = {
    # Its first tag is not the one the container was created from.
    JELLYFIN_IMAGE: ["jellyfin/jellyfin:latest", "jellyfin/jellyfin:10.10.3"],
    IMMICH_IMAGE: [IMMICH_DIGEST, "ghcr.io/immich-app/immich-server:v3.4.1"],
    TRANSMISSION_IMAGE: [TRANSMISSION_DIGEST],
    OLD_TRANSMISSION_IMAGE: [],
    NEW_TRANSMISSION_IMAGE: ["lscr.io/linuxserver/transmission:latest"],
    SUPERVISOR_IMAGE: ["deploy-supervisor:latest"],
}

JELLYFIN = {
    # Its image's first tag, not the one it was created from.
    "id": _hex("jellyfin")[:12], "name": "jellyfin", "image": "jellyfin/jellyfin:latest",
    "status": "running", "state": "running", "created": "2025-11-19T18:09:41Z",
    # Sorted as Docker prints the inspect's port map, each port's bindings in their order.
    "ports": [
        {"host_port": None, "container_port": 1900, "protocol": "udp"},
        {"host_port": None, "container_port": 7359, "protocol": "udp"},
        {"host_port": 8096, "container_port": 8096, "protocol": "tcp"},
        {"host_port": 8096, "container_port": 8096, "protocol": "tcp"},
    ],
    "labels": {"unos.app": "jellyfin"},
}
PREVIOUS = {
    "id": _hex("transmission-previous")[:12], "name": "transmission.previous",
    "image": OLD_TRANSMISSION_IMAGE[:19], "status": "exited", "state": "exited",
    "created": "2025-11-07T06:53:12Z", "ports": [], "labels": {},
}
LEAVING = {  # created from its image's ID: the image's first tag
    "id": _hex("leaving")[:12], "name": "leaving", "image": "jellyfin/jellyfin:latest", "status": "removing",
    "state": "created", "created": "2025-11-07T06:53:12Z", "ports": [], "labels": {},
}
IMMICH = {  # the tag of its image, not the digest it was created from
    "id": _hex("immich")[:12], "name": "immich", "image": "ghcr.io/immich-app/immich-server:v3.4.1",
    "status": "running", "state": "running", "created": "2025-11-17T13:05:39Z", "ports": [],
    "labels": {"unos.app": "immich"},
}
TRANSMISSION = {  # its image has no tag but the digest form: the reference it was created from
    "id": _hex("transmission")[:12], "name": "transmission", "image": TRANSMISSION_DIGEST,
    "status": "running", "state": "running", "created": "2025-11-19T18:09:41Z", "ports": [],
    "labels": {"unos.app": "transmission"},
}


@pytest.fixture
def docker_api(monkeypatch, tmp_path):
    """A real docker-py client whose requests reach FakeDockerApi; the routes' get_client gives it."""
    fake = FakeDockerApi(copy.deepcopy(CONTAINERS), IMAGES)
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    client = docker.DockerClient(base_url="unix:///nonexistent/docker.sock", version="1.47", timeout=5)
    client.api.mount("http+docker://", fake)
    monkeypatch.setattr(docker_module, "get_client", lambda: client)
    previous = app.dependency_overrides.get(verify_token)
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True,
                                                                     is_active=True)
    try:
        yield SimpleNamespace(fake=fake, client=TestClient(app))
    finally:
        if previous is None:
            app.dependency_overrides.pop(verify_token, None)
        else:
            app.dependency_overrides[verify_token] = previous


class TestContainerList:
    def test_the_list_is_two_docker_requests(self, docker_api):
        """Docker's list of containers and its list of images: not an inspect of each container and
        of its image (12 requests here; on the dev device an image's inspect took about 40 ms,
        2026-11-23)."""
        response = docker_api.client.get("/api/v1/containers?all=true")

        assert response.status_code == 200, response.text
        assert docker_api.fake.requests == [("GET", "/containers/json"), ("GET", "/images/json")]
        assert docker_api.fake.queries[0]["all"] == ["1"]

    def test_the_list_answers_what_docker_lists(self, docker_api):
        response = docker_api.client.get("/api/v1/containers?all=true")

        assert response.json() == [JELLYFIN, PREVIOUS, LEAVING, IMMICH, TRANSMISSION]

    def test_a_container_created_from_a_digest_shows_its_images_tag_or_else_the_digest(self, docker_api):
        """The catalog creates the apps' containers from digests: Immich's image also has a tag,
        which it shows; Transmission's has only its digest form, which it keeps."""
        listed = {c["name"]: c["image"] for c in docker_api.client.get("/api/v1/containers?all=true").json()}

        assert (listed["immich"], listed["transmission"]) == (
            "ghcr.io/immich-app/immich-server:v3.4.1", TRANSMISSION_DIGEST)
        assert docker_api.client.get("/api/v1/containers/immich").json() == IMMICH
        assert docker_api.client.get("/api/v1/containers/transmission").json() == TRANSMISSION

    def test_only_the_running_ones(self, docker_api):
        response = docker_api.client.get("/api/v1/containers?all=false")

        assert response.json() == [JELLYFIN, IMMICH, TRANSMISSION]
        assert docker_api.fake.queries[0]["all"] == ["0"]

    @pytest.mark.parametrize("key", ["jellyfin", _hex("jellyfin")[:12], _hex("jellyfin")])
    def test_one_container_answers_as_the_list_does_without_its_images_inspect(self, docker_api, key):
        response = docker_api.client.get(f"/api/v1/containers/{key}")

        assert response.status_code == 200, response.text
        assert response.json() == JELLYFIN
        assert docker_api.fake.requests == [
            ("GET", f"/containers/{key}/json"), ("GET", "/containers/json"), ("GET", "/images/json")]
        assert json.loads(docker_api.fake.queries[1]["filters"][0]) == {"id": [_hex("jellyfin")]}

    def test_a_stop_answers_the_container_as_the_list_does(self, docker_api):
        response = docker_api.client.post("/api/v1/containers/jellyfin/stop")

        assert response.status_code == 200, response.text
        assert response.json() == {**JELLYFIN, "status": "exited", "state": "exited"}
        assert response.json() == docker_api.client.get("/api/v1/containers?all=true").json()[0]

    def test_an_unknown_container_is_404(self, docker_api):
        response = docker_api.client.get("/api/v1/containers/nope")

        assert response.status_code == 404


# --- host commands at once -------------------------------------------------------------------------


class Overlap:
    """In front of a fake host's run: each command takes DELAY seconds, and when each started and
    ended is recorded, so that ``depth`` tells how many commands a request waited for one after
    another."""

    DELAY = 0.5

    def __init__(self, run) -> None:
        self.inner = run
        self.spans: list[tuple[float, float, list[str]]] = []
        self.lock = threading.Lock()

    def run(self, cmd, **kwargs):
        start = time.monotonic()
        time.sleep(self.DELAY)
        try:
            return self.inner(cmd, **kwargs)
        finally:
            with self.lock:
                self.spans.append((start, time.monotonic(), list(cmd)))

    def depth(self) -> int:
        """The longest chain of commands in which each started after the one before it ended."""
        spans = sorted(self.spans)
        longest: list[int] = []
        for start, _, _ in spans:
            longest.append(1 + max((n for (_, end, _), n in zip(spans, longest) if end <= start), default=0))
        return max(longest, default=0)

    def commands(self) -> list[str]:
        return sorted(" ".join(cmd[:3]) for _, _, cmd in self.spans)


@pytest.fixture
def overlap(monkeypatch):
    def install(run=None) -> Overlap:
        recorder = Overlap(run or host_module.run_on_host)
        monkeypatch.setattr(host_module, "run_on_host", recorder.run)
        return recorder

    return install


def _get(c, path: str) -> dict | list:
    response = c.client.get(f"{API}{path}", headers=c.admin)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_smb_interfaces_read_the_host_at_once(pool_cycle, overlap):  # noqa: F811  (fixtures)
    """ip, ss and smbstatus: one command's time, not three (on the dev device 130 ms for the page)."""
    c = pool_cycle
    recorder = overlap(SmbdHost(SimpleNamespace(run=host_module.run_on_host), c.conf).run)

    _get(c, "/shares/smb-interfaces")

    assert recorder.commands() == sorted([" ".join(IP[:3]), " ".join(SS[:3]), "smbstatus --json"])
    assert recorder.depth() == 1
    assert SMBD_RESET not in [cmd for _, _, cmd in recorder.spans]


def test_the_smb_status_reads_the_host_in_two_rounds(pool_cycle, overlap):  # noqa: F811  (fixtures)
    """The Samba tools' check, the gid's group and the fstab, then the group, the accounts and
    Samba's passdb: two commands' time instead of six."""
    c = pool_cycle
    recorder = overlap()

    status = _get(c, "/shares/smb-status")

    assert status["group_ok"] is True and status["samba_tools_missing"] == []
    assert recorder.commands() == sorted([
        "/bin/sh -c for c do command -v -- \"$c\" >/dev/null 2>&1 || printf \"%s\\n\" \"$c\"; done",
        "getent group unos-smb", "getent passwd", "pdbedit -L", "getent group 70000", "cat -- /etc/fstab",
    ])
    assert recorder.depth() == 2


def test_the_pool_list_reads_the_host_in_two_rounds(pool_cycle, overlap):  # noqa: F811  (fixtures)
    """btrfs's list, the mount table and the fstab, then each mounted pool's use and error
    counters, all pools at once: two commands' time instead of three and two per pool."""
    c = pool_cycle
    c.pool_host.add_disk("sdb", model="WDC WD40EFRX", serial="WD-2")
    c.pool_host.add_btrfs("media", MEDIA_UUID, ["/dev/sdb"])
    c.pool_host.dirs.add(pools.pool_mountpoint("media"))
    c.pool_host.mount_pool("media", target=pools.pool_mountpoint("media"))
    recorder = overlap()

    listed = _get(c, "/storage/pools")

    assert sorted((p["name"], p["mounted"], p["health"]) for p in listed) == [
        ("data", True, "healthy"), ("media", True, "healthy")]
    assert recorder.commands() == sorted(
        ["btrfs filesystem show", "findmnt -J -l", "cat -- /etc/fstab"]
        + ["btrfs filesystem usage", "btrfs device stats"] * 2
    )
    assert recorder.depth() == 2


def test_the_disk_list_reads_the_host_in_three_rounds(pool_cycle, overlap):  # noqa: F811  (fixtures)
    """lsblk, the mount table, the swaps, the kernel's command line, the idle timeouts' rule and
    hdparm's check; then every disk's power at once; then wipefs of a disk that lsblk shows
    nothing on, after its own power read: three commands' time instead of nine."""
    c = pool_cycle
    c.pool_host.add_disk("sdb", model="WDC WD40EFRX", serial="WD-2")
    recorder = overlap()

    listed = _get(c, "/storage/disks")

    assert [(d["name"], d["power_state"], d["has_data"]) for d in listed] == [
        ("sda", "active", True), ("sdb", "active", False)]
    assert recorder.commands() == sorted([
        "lsblk -J -b", "findmnt -J -l", "swapon --show=NAME --noheadings", "cat -- /proc/cmdline",
        "cat -- /etc/udev/rules.d/90-unos-disk-spindown.rules",
        "/bin/sh -c for c do command -v -- \"$c\" >/dev/null 2>&1 || printf \"%s\\n\" \"$c\"; done",
        "timeout --kill-after=5 15", "timeout --kill-after=5 15", "wipefs --no-act --json",
    ])
    [wipefs] = [span for span in recorder.spans if span[2][0] == "wipefs"]
    [power_read] = [span for span in recorder.spans if span[2][-1] == "/dev/sdb" and "hdparm" in span[2]]
    assert power_read[1] <= wipefs[0]
    assert recorder.depth() == 3


# --- the share paths -------------------------------------------------------------------------------


def test_the_share_paths_read_the_host_once_and_scan_no_disk(host, api):  # noqa: F811  (fixtures)
    """The fstab once, the mounted filesystems as the kernel knows them (no scan that wakes a
    sleeping disk, ADR-0378), the mount table once, and each mounted pool's subvolumes: not two
    scans of every disk, two mount tables, two fstabs and each pool's use and error counters."""
    host.subvols.add(DATA_UUID, "documents")
    host.asleep.add("/dev/sdb")  # the disk of the pool media, which is not mounted
    host.calls.clear()

    response = api.get("/api/v1/shares/paths")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"path": "/mnt/pools/data", "name": "data", "type": "pool", "managed": False},
        {"path": "/mnt/pools/data/documents", "name": "data/documents", "type": "volume", "managed": False},
    ]
    assert host.woken == []
    assert sorted(" ".join(cmd[:4]) for cmd in host.calls) == sorted([
        "cat -- /etc/fstab",
        "btrfs filesystem show --mounted",
        "findmnt -J -l -o",
        "btrfs inspect-internal rootid /mnt/pools/data",
        "btrfs subvolume list /mnt/pools/data",
        "/bin/sh -c " + pools._VOLUME_PATHS_SCRIPT + " unos-volume-paths",
        "btrfs inspect-internal subvolid-resolve 256",  # the script's, which the fake records too
    ])


def test_the_share_paths_mark_the_managed_pools(host, api):  # noqa: F811  (fixtures)
    host.files["/etc/fstab"] += f"{pools.fstab_marker('data')}\n{pools.fstab_entry('data', DATA_UUID)}\n"

    response = api.get("/api/v1/shares/paths")

    assert response.json() == [{"path": "/mnt/pools/data", "name": "data", "type": "pool", "managed": True}]


def test_the_share_paths_without_a_mount_table_list_nothing(host, api):  # noqa: F811  (fixtures)
    host.fail[("findmnt",)] = (1, "findmnt: cannot read /proc/self/mountinfo")

    response = api.get("/api/v1/shares/paths")

    assert (response.status_code, response.json()) == (200, [])


def test_the_share_paths_without_the_fstab_list_the_pools_as_not_managed(host, api):  # noqa: F811
    host.files["/etc/fstab"] += f"{pools.fstab_marker('data')}\n{pools.fstab_entry('data', DATA_UUID)}\n"
    host.fail[("cat", "--", "/etc/fstab")] = (1, "cat: /etc/fstab: Permission denied")

    response = api.get("/api/v1/shares/paths")

    assert response.json() == [{"path": "/mnt/pools/data", "name": "data", "type": "pool", "managed": False}]


def test_a_pool_whose_volumes_cannot_be_read_is_listed_without_them(host, api):  # noqa: F811
    host.subvols.add(DATA_UUID, "documents")
    host.fail[("btrfs", "subvolume", "list")] = (1, "ERROR: can't perform the search: Permission denied")

    response = api.get("/api/v1/shares/paths")

    assert response.json() == [{"path": "/mnt/pools/data", "name": "data", "type": "pool", "managed": False}]


def test_the_share_paths_without_btrfs_list_nothing(host, api):  # noqa: F811  (fixtures)
    host.fail[("btrfs", "filesystem", "show")] = (1, "ERROR: cannot open /dev/btrfs-control")

    response = api.get("/api/v1/shares/paths")

    assert (response.status_code, response.json()) == (200, [])

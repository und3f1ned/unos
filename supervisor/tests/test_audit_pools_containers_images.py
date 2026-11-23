"""Mounting and unmounting pools, creating containers and pulling and removing images are in the
audit log (security.audit-admin-actions, ADR-0402).

Each writes one entry of its action, by the administrator, with the client address and browser and
the resource, after the change was made and for a real change only (ADR-0320): a refused or failed
request, a mount of a pool that is mounted and kept in the fstab, an unmount of one that is neither,
and a pull that left the image as it was write none. An unmount that left the pool unmounted but
failed after it writes its action as failed. The environment of a container never reaches an entry.
Two starts or two stops of one container at the same moment write one entry between them. An entry
that cannot be written is in the supervisor's log, and the change answers (ADR-0251).
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from src.containers import docker as docker_module

from . import test_audit_storage_and_apps as storage
from .test_audit_storage_and_apps import CONTAINERS, POOLS, entries, entry, lost, running
from .test_containers import DIGEST, REMOVE_IMAGE, _container, _DockerImage
from .test_pool_persistence import BASE_FSTAB, DATA_UUID
from .test_pool_persistence import entry as fstab_entry
from .test_pool_persistence import marker
from .test_sign_in_full_disk import the_audit_log_is_full


# The fixtures of test_audit_storage_and_apps.py: the administrator, the audit log of the test, the
# mocked Docker and the host with the pools data (mounted) and media (not).
admin = storage.admin
audit_file = storage.audit_file
docker = storage.docker
host = storage.host


class TestMountAndUnmount:
    def test_a_mount_is_in_the_audit_log(self, admin, audit_file, host):
        response = admin.post(f"{POOLS}/media/mount")

        assert response.status_code == 200, response.text
        assert host.mounted("/mnt/pools/media")
        assert entries(audit_file) == [entry("pool_mounted", "pool", "media", "Mounted the pool media")]

    def test_a_mount_of_a_mounted_pool_that_the_fstab_keeps_writes_nothing(self, admin, audit_file, host):
        host.files["/etc/fstab"] = BASE_FSTAB + marker("data") + fstab_entry("data", DATA_UUID)

        response = admin.post(f"{POOLS}/data/mount")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_a_mount_of_a_mounted_pool_that_added_its_fstab_entry_is_in_the_audit_log(self, admin, audit_file, host):
        host.files["/etc/fstab"] = BASE_FSTAB

        response = admin.post(f"{POOLS}/data/mount")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry("pool_mounted", "pool", "data", "Mounted the pool data")]

    def test_a_pool_that_is_not_there_writes_nothing(self, admin, audit_file, host):
        mounted = admin.post(f"{POOLS}/nothing/mount")
        unmounted = admin.post(f"{POOLS}/nothing/unmount")

        assert (mounted.status_code, unmounted.status_code) == (404, 404)
        assert entries(audit_file) == []

    def test_a_mount_that_failed_writes_nothing(self, admin, audit_file, host):
        host.fail[("mount",)] = (32, "mount: /mnt/pools/media: wrong fs type")

        response = admin.post(f"{POOLS}/media/mount")

        assert response.status_code >= 400
        assert entries(audit_file) == []

    def test_an_unmount_is_in_the_audit_log(self, admin, audit_file, host):
        response = admin.post(f"{POOLS}/data/unmount")

        assert response.status_code == 200, response.text
        assert not host.mounted("/mnt/pools/data")
        assert entries(audit_file) == [
            entry("pool_unmounted", "pool", "data", "Unmounted the pool data; its data was kept", "warning")
        ]

    def test_an_unmount_of_a_pool_that_is_not_mounted_writes_nothing(self, admin, audit_file, host):
        response = admin.post(f"{POOLS}/media/unmount")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_an_unmount_that_dropped_the_fstab_entry_of_an_unmounted_pool_is_in_the_audit_log(
        self, admin, audit_file, host
    ):
        host.files["/etc/fstab"] = BASE_FSTAB + marker("media") + fstab_entry("media", "1" * 8 + "-0000-4000-8000-" + "1" * 12)

        response = admin.post(f"{POOLS}/media/unmount")

        assert response.status_code == 200, response.text
        assert [e["action"] for e in entries(audit_file)] == ["pool_unmounted"]

    def test_an_unmount_that_left_the_pool_unmounted_and_failed_is_in_the_audit_log_as_failed(
        self, admin, audit_file, host
    ):
        host.held.add(DATA_UUID)  # still in use elsewhere after the umount
        host.fail[("mount",)] = (32, "mount: /mnt/pools/data: wrong fs type")

        response = admin.post(f"{POOLS}/data/unmount")

        assert response.status_code == 409
        assert not host.mounted("/mnt/pools/data")
        [written] = entries(audit_file)
        assert (written["action"], written["success"], written["severity"]) == ("pool_unmounted", False, "warning")
        assert written["details"].startswith("Unmounted the pool data; then: ")

    def test_the_pool_changes_answer_when_their_entry_cannot_be_written(self, admin, host, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        mounted = admin.post(f"{POOLS}/media/mount")
        unmounted = admin.post(f"{POOLS}/data/unmount")

        assert (mounted.status_code, unmounted.status_code) == (200, 200)
        assert [found["action"] for found in lost(caplog)] == ["pool_mounted", "pool_unmounted"]


@pytest.fixture
def runs(docker):
    """`containers.run`, which creates a container, makes one as `containers.create` does, and the
    short ID that docker.py then asks for finds it."""
    made: list = []
    get = docker.containers.get.side_effect

    def run(image, **kwargs):
        made.append(docker.containers.create.side_effect(image, **kwargs))
        return made[-1]

    docker.containers.run.side_effect = run
    docker.containers.get.side_effect = lambda name: made[-1] if name == "c0ffee" else get(name)
    return docker


def _image(image_id: str, tags: list[str]) -> SimpleNamespace:
    return SimpleNamespace(id=image_id, short_id=image_id[:19], tags=tags,
                           attrs={"Size": 1_000, "Created": "2026-01-01T00:00:00Z"})


class TestContainersCreated:
    def test_a_creation_is_in_the_audit_log_without_its_environment(self, admin, audit_file, runs):
        response = admin.post(CONTAINERS, json={"name": "web", "image": "example/web:latest",
                                                "environment": {"DB_PASSWORD": "hunter2"}})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [
            entry("container_created", "container", "web", "Created the container web from example/web:latest")
        ]
        assert "hunter2" not in audit_file.read_text()

    def test_a_refused_creation_writes_nothing(self, admin, audit_file, runs):
        reserved = admin.post(CONTAINERS, json={"name": "unos-supervisor", "image": "example/web:latest"})

        assert reserved.status_code == 500
        assert entries(audit_file) == []

    def test_a_creation_that_docker_refused_writes_nothing(self, admin, audit_file, runs):
        runs.containers.run.side_effect = NotFound("No such image: example/gone:latest")

        response = admin.post(CONTAINERS, json={"name": "web", "image": "example/gone:latest"})

        assert response.status_code >= 400
        assert entries(audit_file) == []

    def test_a_creation_whose_start_failed_leaves_a_container_and_a_failed_entry(self, admin, audit_file, runs):
        from docker.errors import APIError

        def create_then_fail(image, **kwargs):
            runs.containers.create.side_effect(image, **kwargs)  # Docker made it
            raise APIError("driver failed programming external connectivity: port is already allocated")

        runs.containers.run.side_effect = create_then_fail

        response = admin.post(CONTAINERS, json={"name": "web", "image": "example/web:latest"})

        assert response.status_code == 500
        [written] = entries(audit_file)
        assert (written["action"], written["resource_id"], written["success"]) == ("container_created", "web", False)
        assert written["details"].startswith("Created the container web from example/web:latest; then: ")
        assert "port is already allocated" in written["details"]

    def test_a_creation_answers_when_its_entry_cannot_be_written(self, admin, runs, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = admin.post(CONTAINERS, json={"name": "web", "image": "example/web:latest"})

        assert response.status_code == 200, response.text
        assert [found["action"] for found in lost(caplog)] == ["container_created"]


class TestImages:
    def test_a_pull_of_a_new_image_is_in_the_audit_log(self, admin, audit_file, docker):
        docker.images.list.return_value = []
        docker.images.pull.return_value = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])

        response = admin.post(f"{CONTAINERS}/images/pull", params={"image": "alpine:3.20"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry("image_pulled", "image", "alpine:3.20", "Pulled the image alpine:3.20")]

    def test_a_pull_that_moved_a_tag_is_in_the_audit_log(self, admin, audit_file, docker):
        docker.images.list.return_value = [_image("sha256:oldoldoldoldoldoldol", ["alpine:3.20"])]
        docker.images.pull.return_value = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])

        response = admin.post(f"{CONTAINERS}/images/pull", params={"image": "alpine:3.20"})

        assert response.status_code == 200, response.text
        assert [e["action"] for e in entries(audit_file)] == ["image_pulled"]

    def test_a_pull_that_left_the_image_as_it_was_writes_nothing(self, admin, audit_file, docker):
        same = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])
        docker.images.list.return_value = [same]
        docker.images.pull.return_value = same

        response = admin.post(f"{CONTAINERS}/images/pull", params={"image": "alpine:3.20"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_a_pull_that_failed_writes_nothing(self, admin, audit_file, docker):
        docker.images.list.return_value = []
        docker.images.pull.side_effect = NotFound("pull access denied")

        response = admin.post(f"{CONTAINERS}/images/pull", params={"image": "nobody/nothing"})

        assert response.status_code >= 400
        assert entries(audit_file) == []

    def test_a_removal_is_in_the_audit_log(self, admin, audit_file, docker):
        docker.images.get.return_value = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])

        response = admin.delete(f"{CONTAINERS}/images/sha256:aaaaaaaaaaaaaaaaaaaa")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [
            entry("image_removed", "image", "alpine:3.20", "Removed the image alpine:3.20", "warning")
        ]

    @pytest.mark.parametrize(
        "tags", [["mariadb:11.4", "mariadb" + DIGEST], ["mariadb:11.4", "mariadb:11", "mariadb" + DIGEST]],
        ids=["tag-and-its-digest", "two-tags-and-a-digest"],
    )
    def test_a_removal_with_the_containerd_store_writes_one_entry(self, admin, audit_file, docker, tags):
        # Docker's containerd image store lists the image's digest among its tags, and removing a
        # tag removes the digest with it: the removal is still one removal of the image.
        image = _DockerImage(docker, tags, store="containerd")

        response = admin.delete(REMOVE_IMAGE)

        assert response.status_code == 200, response.text
        assert not image.exists
        assert entries(audit_file) == [
            entry("image_removed", "image", "mariadb:11.4", "Removed the image mariadb:11.4", "warning")
        ]

    @pytest.mark.parametrize("store", ["classic", "containerd"])
    def test_an_image_that_is_not_there_to_remove_writes_nothing(self, admin, audit_file, docker, store):
        image = _DockerImage(docker, ["mariadb:11.4", "mariadb" + DIGEST], store=store)
        unknown = admin.delete(f"{CONTAINERS}/images/sha256:0123456789ab")
        read = image.get

        def read_then_gone(reference):  # another removes it after UNOS read it
            found = read(reference)
            image.tags, image.exists = [], False
            return found

        docker.images.get.side_effect = read_then_gone
        gone = admin.delete(REMOVE_IMAGE)

        assert (unknown.status_code, gone.status_code) == (404, 404)
        assert entries(audit_file) == []

    def test_a_refused_removal_writes_nothing(self, admin, audit_file, docker):
        docker.images.get.return_value = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])
        running(docker.add(_container("web", {}, [])))
        in_use = admin.delete(f"{CONTAINERS}/images/sha256:aaaaaaaaaaaaaaaaaaaa")
        docker.images.get.side_effect = NotFound("no such image")
        missing = admin.delete(f"{CONTAINERS}/images/sha256:nothing")

        assert (in_use.status_code, missing.status_code) == (409, 404)
        assert entries(audit_file) == []

    def test_the_image_changes_answer_when_their_entry_cannot_be_written(self, admin, docker, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)
        docker.images.list.return_value = []
        docker.images.pull.return_value = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])
        image = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine:3.20"])

        def get(reference):
            if reference == "alpine:3.20":  # not there before the pull
                raise NotFound("no such image")
            return image

        docker.images.get.side_effect = get

        pulled = admin.post(f"{CONTAINERS}/images/pull", params={"image": "alpine:3.20"})
        removed = admin.delete(f"{CONTAINERS}/images/sha256:aaaaaaaaaaaaaaaaaaaa")

        assert (pulled.status_code, removed.status_code) == (200, 200)
        assert [found["action"] for found in lost(caplog)] == ["image_pulled", "image_removed"]


    def test_a_pull_by_digest_that_left_the_image_as_it_was_writes_nothing(self, admin, audit_file, docker):
        same = _image("sha256:aaaaaaaaaaaaaaaaaaaa", ["alpine@sha256:1234"])
        docker.images.list.return_value = []  # the list does not know a reference by digest
        docker.images.get.return_value = same
        docker.images.pull.return_value = same

        response = admin.post(f"{CONTAINERS}/images/pull", params={"image": "alpine@sha256:1234"})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []


class TestTwoRequestsAtOnce:
    """The step that decides whether a start or a stop changed anything holds the container's state
    until it has acted: of two at the same moment only one changes it."""

    @pytest.mark.parametrize(("operation", "before", "after"), [
        ("start_container", "exited", "running"),
        ("stop_container", "running", "exited"),
    ])
    def test_only_one_of_two_changes_the_container(self, docker, operation, before, after):
        web = docker.add(_container("web", {}, []))
        web.status = before
        verb = operation.split("_")[0]

        def act(*args, **kwargs):
            time.sleep(0.2)  # the other request reads the status in this time
            web.status = after

        getattr(web, verb).side_effect = act
        changed: list[bool] = []
        barrier = threading.Barrier(2)

        def request():
            barrier.wait()
            changed.append(getattr(docker_module, operation)("web")[1])

        threads = [threading.Thread(target=request) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(changed) == [False, True]

    def test_another_container_is_not_held_up(self, docker):
        web = docker.add(_container("web", {}, []))
        web.status = "exited"
        db = docker.add(_container("db", {}, []))
        db.status = "exited"
        release = threading.Event()
        web.start.side_effect = lambda *a, **k: release.wait(5)
        slow = threading.Thread(target=docker_module.start_container, args=("web",))
        slow.start()
        time.sleep(0.1)

        started = docker_module.start_container("db")  # returns while web's start is still running

        assert started[1] is True
        release.set()
        slow.join()

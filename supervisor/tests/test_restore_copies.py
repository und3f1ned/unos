"""The copies that a snapshot's restore made, listed and deleted on the Snapshots page
(storage.restore-copies-deletable, ADR-0356).

Restore makes a writable snapshot ``restore_<date>_<time>`` (UTC) right in the pool or the volume
whose snapshot it restored (src/snapshots/router.py). GET /snapshots/copies lists those of one pool
or volume, and DELETE /snapshots/copies/{name} deletes one by its subvolume id, after the checks of
a volume's Delete (containers, SMB shares, NFS exports, the off-device copy), and only a subvolume
that is such a copy. The volume that held it can be deleted afterwards. The host is the FakeHost of
test_volumes, with the subvolumes of its mounted pool "data" (FakeSubvolumes).
"""

from __future__ import annotations

import importlib
import re
from datetime import datetime, timezone

import pytest

from src import host as host_module
from src.storage import pools

from . import test_audit_storage_and_apps, test_volumes
from .test_audit_storage_and_apps import entries, entry
from .test_pool_persistence import DATA_UUID
from .test_volumes import _btrfs_calls, add_export, add_share

# The fixtures of test_volumes: the FakeHost with pool "data" mounted at /mnt/pools/data and pool
# "media" not mounted, and the administrator's client.
host = test_volumes.host
api = test_volumes.api
# Those of test_audit_storage_and_apps: the audit log of the test, and the administrator's client
# from a known address and browser.
audit_file = test_audit_storage_and_apps.audit_file
admin = test_audit_storage_and_apps.admin

COPIES = "/api/v1/snapshots/copies"
VOLUMES = "/api/v1/storage/pools"
POOL = "/mnt/pools/data"
DOCUMENTS = f"{POOL}/documents"
COPY = "restore_20261112_093000"
OLDER = "restore_20261111_080000"


def add_copy(host, path: str, parent: int = test_volumes.TOP_LEVEL, *, of: int = 300) -> int:
    """A copy that a restore made at ``path`` (from the top level): a writable snapshot of the snapshot ``of``."""
    return host.subvols.add(DATA_UUID, path, parent=parent, snapshot_of=of)


def copy_info(name: str, volume: str | None = "documents") -> dict:
    """An entry of GET /snapshots/copies."""
    folder = f"{POOL}/{volume}" if volume else POOL
    stamp = datetime.strptime(name, "restore_%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    return {"name": name, "pool": "data", "volume": volume, "subvolume": folder, "path": f"{folder}/{name}",
            "created": stamp.isoformat()}


@pytest.fixture
def documents(host) -> int:
    """The volume "documents" of pool "data", with a read-only snapshot "before" of it; its id."""
    volume = host.subvols.add(DATA_UUID, "documents")
    host.dirs.add(DOCUMENTS)
    host.subvols.add(DATA_UUID, "documents/.snapshots/before", parent=volume, snapshot_of=volume, read_only=True)
    return volume


class TestList:
    def test_the_copies_of_a_volume_are_listed_newest_first_with_their_time(self, api, host, documents):
        add_copy(host, f"documents/{OLDER}", documents)
        add_copy(host, f"documents/{COPY}", documents)
        # Not copies of the volume: the snapshot, a subvolume made by hand under a copy's name (no
        # Parent UUID), a read-only snapshot under one, names that UNOS does not give, a copy deeper
        # in the volume and one of the pool.
        host.subvols.add(DATA_UUID, "documents/restore_20261112_093001", parent=documents)
        host.subvols.add(DATA_UUID, "documents/restore_20261112_093002", parent=documents, snapshot_of=documents,
                         read_only=True)
        for name in ("restore_x", "restore_20261399_000000", "restore_20261112_0930000", "Restore_20261112_093000"):
            add_copy(host, f"documents/{name}", documents)
        scans = host.subvols.add(DATA_UUID, "documents/scans", parent=documents)
        add_copy(host, f"documents/scans/{COPY}", scans)
        add_copy(host, "restore_20261110_070000")

        response = api.get(COPIES, params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text
        assert response.json() == [copy_info(COPY), copy_info(OLDER)]
        assert host.changes == []

    def test_the_copies_of_a_pool_are_the_ones_in_its_top_folder(self, api, host, documents):
        add_copy(host, f"documents/{COPY}", documents)
        add_copy(host, OLDER)

        response = api.get(COPIES, params={"subvolume": POOL})

        assert response.status_code == 200, response.text
        assert response.json() == [copy_info(OLDER, volume=None)]

    def test_no_copies(self, api, host, documents):
        response = api.get(COPIES, params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_a_name_that_only_starts_like_a_copys_is_not_listed(self, api, host, documents):
        # `btrfs subvolume list` prints a line break of a name as it is: the line of this subvolume
        # ends with the copy's name, and the shell's check of its path tells it apart.
        add_copy(host, f"documents/{COPY}\nx", documents)

        response = api.get(COPIES, params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_an_unknown_volume_is_not_found(self, api, host, documents):
        response = api.get(COPIES, params={"subvolume": f"{POOL}/photos"})

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Volume 'photos' not found in pool 'data'"

    @pytest.mark.parametrize("subvolume", [f"{DOCUMENTS}/scans", "/srv/data", "/mnt/pools", f"{DOCUMENTS}/../x"])
    def test_only_a_pool_or_a_volume_holds_copies(self, api, host, documents, subvolume):
        response = api.get(COPIES, params={"subvolume": subvolume})

        assert response.status_code == 400, response.text

    def test_a_pool_that_is_not_mounted(self, api, host):
        response = api.get(COPIES, params={"subvolume": "/mnt/pools/media"})

        assert response.status_code == 409, response.text
        assert "is not mounted at /mnt/pools/media" in response.json()["detail"]

    def test_a_failing_btrfs_is_reported(self, api, host, documents):
        add_copy(host, f"documents/{COPY}", documents)
        host.fail[("btrfs", "subvolume", "show")] = (1, "ERROR: Failed to get subvol info: Input/output error")

        response = api.get(COPIES, params={"subvolume": DOCUMENTS})

        assert response.status_code == 500, response.text
        assert "Input/output error" in response.json()["detail"]

    def test_the_names_are_those_that_restore_gives(self):
        snapshots = importlib.import_module("src.snapshots.router")

        assert pools.RESTORE_COPY_NAME.fullmatch(snapshots.RESTORE_PREFIX + snapshots.utc_stamp())


class TestDelete:
    def test_the_copy_is_deleted_by_its_subvolume_id(self, api, host, documents):
        copy = add_copy(host, f"documents/{COPY}", documents)

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted", "name": COPY}
        assert host.changes == [["btrfs", "subvolume", "delete", "--subvolid", str(copy), POOL]]
        assert host.subvols.paths(DATA_UUID) == ["documents", "documents/.snapshots/before"]

    def test_the_volume_that_held_the_copy_can_be_deleted_afterwards(self, api, host):
        volume = host.subvols.add(DATA_UUID, "documents")
        host.dirs.add(DOCUMENTS)
        add_copy(host, f"documents/{COPY}", volume)

        refused = api.delete(f"{VOLUMES}/data/volumes/documents")
        deleted = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})
        volume_deleted = api.delete(f"{VOLUMES}/data/volumes/documents")

        assert refused.status_code == 409, refused.text
        assert "contains snapshots or other subvolumes" in refused.json()["detail"]
        assert (deleted.status_code, volume_deleted.status_code) == (200, 200), (deleted.text, volume_deleted.text)
        assert host.subvols.paths(DATA_UUID) == []

    def test_a_copy_of_a_pools_snapshot(self, api, host):
        copy = add_copy(host, OLDER)

        response = api.delete(f"{COPIES}/{OLDER}", params={"subvolume": POOL})

        assert response.status_code == 200, response.text
        assert host.changes == [["btrfs", "subvolume", "delete", "--subvolid", str(copy), POOL]]

    @pytest.mark.parametrize("users, named", [
        ("container", "running containers (paperless)"),
        ("share", "SMB shares (scans)"),
        ("export", f"NFS exports ({DOCUMENTS}/{COPY}/scans)"),
    ])
    def test_a_copy_in_use_is_kept_and_its_users_are_named(self, api, host, documents, users, named):
        add_copy(host, f"documents/{COPY}", documents)
        if users == "container":
            host.docker.add("paperless", sources=[f"{DOCUMENTS}/{COPY}/consume"])
        elif users == "share":
            add_share(host, "scans", f"{DOCUMENTS}/{COPY}")
        else:
            add_export(host, f"{DOCUMENTS}/{COPY}/scans")

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Restored copy 'documents/{COPY}' is used by {named}. Stop or remove them and try again."
        )
        assert host.changes == [] and _btrfs_calls(host, "subvolume", "delete") == []

    def test_users_of_the_volume_around_it_do_not_count(self, api, host, documents):
        # They keep working without the copy.
        host.docker.add("backup", sources=[DOCUMENTS])
        add_share(host, "documents", DOCUMENTS)
        add_export(host, DOCUMENTS)
        add_copy(host, f"documents/{COPY}", documents)

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text

    def test_without_docker_nothing_is_deleted(self, api, host, documents):
        add_copy(host, f"documents/{COPY}", documents)
        host.docker.unavailable = True

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 503, response.text
        assert response.json()["detail"].startswith(
            f"Restored copy 'documents/{COPY}' was not deleted: Docker did not answer"
        )
        assert host.changes == []

    def test_the_off_device_copy_that_uses_it(self, api, host, documents, monkeypatch):
        offsite = importlib.import_module("src.offsite.service")
        started = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(offsite, "_activity", offsite.Activity("copy", started, paths={f"{DOCUMENTS}/{COPY}"}))
        add_copy(host, f"documents/{COPY}", documents)

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith(f"Restored copy 'documents/{COPY}' is used by the off-device copy")
        assert host.changes == []

    def test_the_check_and_the_delete_hold_the_locks(self, api, host, documents, monkeypatch):
        """The pools' lock, and that of the off-device copy's folders (ADR-0337), as for a volume."""
        offsite = importlib.import_module("src.offsite.service")
        add_copy(host, f"documents/{COPY}", documents)
        held = []
        real = host_module.run_on_host

        def run_on_host(argv, *args, **kwargs):
            if argv[:3] == ["btrfs", "subvolume", "delete"]:
                held.append((pools._lock._is_owned(), offsite.FOLDERS_LOCK.locked()))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", run_on_host)

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text
        assert held == [(True, True)]

    @pytest.mark.parametrize("made", ["by hand", "read-only"])
    def test_a_subvolume_that_no_restore_made_is_refused(self, api, host, documents, made):
        if made == "by hand":  # btrfs subvolume create: no Parent UUID
            host.subvols.add(DATA_UUID, f"documents/{COPY}", parent=documents)
        else:
            host.subvols.add(DATA_UUID, f"documents/{COPY}", parent=documents, snapshot_of=documents, read_only=True)

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == (
            f"'documents/{COPY}' of pool 'data' is not a copy that a restore made (a writable snapshot): "
            "it was not deleted."
        )
        assert host.changes == []

    @pytest.mark.parametrize("name", ["documents", "before", ".snapshots", "restore_x", "restore_20261399_000000"])
    def test_only_a_name_that_restore_gives_is_taken(self, api, host, documents, name):
        response = api.delete(f"{COPIES}/{name}", params={"subvolume": POOL})

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == f"'{name}' is not the name of a restored copy (restore_<date>_<time>)"
        assert host.changes == []

    def test_a_copy_that_is_not_there(self, api, host, documents):
        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == f"Restored copy 'documents/{COPY}' not found in pool 'data'"

    def test_a_folder_under_a_copys_name_is_no_copy(self, api, host, documents):
        host.dirs.add(f"{DOCUMENTS}/{COPY}")

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 404, response.text
        assert host.changes == []

    def test_a_forged_line_cannot_redirect_the_delete(self, api, host, documents):
        # A subvolume whose name holds a line break and a line of the list: it gives the copy's
        # name the id of the volume. The check of each id against the filesystem keeps it.
        add_copy(host, f"documents/{COPY}", documents)
        host.subvols.add(DATA_UUID, f"x\nID {documents} gen 12 top level {documents} path documents/{COPY}")

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 200, response.text
        [delete] = _btrfs_calls(host, "subvolume", "delete")
        assert delete[4] != str(documents) and "documents" in host.subvols.paths(DATA_UUID)

    def test_only_ids_of_other_subvolumes_are_a_conflict(self, api, host, documents):
        host.subvols.add(DATA_UUID, f"x\nID {documents} gen 12 top level {documents} path documents/{COPY}")

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith(f"Restored copy 'documents/{COPY}' was not deleted: the subvolume "
                                                     f"list of pool 'data' gives it the id {documents}")
        assert host.changes == []

    def test_a_copy_that_holds_a_subvolume_is_kept(self, api, host, documents):
        copy = add_copy(host, f"documents/{COPY}", documents)
        host.subvols.add(DATA_UUID, f"documents/{COPY}/nested", parent=copy)

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith(
            f"Restored copy 'documents/{COPY}' contains snapshots or other subvolumes"
        )

    def test_a_read_only_pool(self, api, host, documents):
        add_copy(host, f"documents/{COPY}", documents)
        host.mounts[:] = [m for m in host.mounts if m["target"] != POOL]
        host.mount_pool("data", vfs_options="ro,noatime", fs_options="ro")

        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert response.status_code == 409, response.text
        assert "mounted read-only" in response.json()["detail"]
        assert host.changes == []

    def test_an_unknown_volume_is_not_found(self, api, host, documents):
        response = api.delete(f"{COPIES}/{COPY}", params={"subvolume": f"{POOL}/photos"})

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Volume 'photos' not found in pool 'data'"

    def test_the_audit_log_gets_the_deletion(self, admin, audit_file, host, documents):
        """As a snapshot's Delete (ADR-0350): snapshot_deleted, a warning, after the change only."""
        add_copy(host, f"documents/{COPY}", documents)

        deleted = admin.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})
        missing = admin.delete(f"{COPIES}/{COPY}", params={"subvolume": DOCUMENTS})

        assert (deleted.status_code, missing.status_code) == (200, 404), (deleted.text, missing.text)
        assert entries(audit_file) == [
            entry("snapshot_deleted", "snapshot", f"{DOCUMENTS}/{COPY}",
                  f"Deleted the restored copy {COPY} in {DOCUMENTS}", "warning")
        ]


def test_the_restored_copies_are_named_by_their_time_in_utc():
    assert re.fullmatch(pools.RESTORE_COPY_NAME, COPY)

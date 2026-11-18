"""Importing a filesystem as a pool is in the audit log (security.audit-admin-actions, ADR-0402):
one `pool_imported` entry after the import, by the administrator, with the client address and
browser, the pool's name and the filesystem; a refused import writes none (ADR-0320)."""
from __future__ import annotations

import pytest

from . import test_audit_storage_and_apps as storage
from . import test_pool_import
from .test_audit_storage_and_apps import entries, entry, lost
from .test_pool_import import FOREIGN_UUID
from .test_sign_in_full_disk import the_audit_log_is_full

# The administrator and the audit log of test_audit_storage_and_apps.py, and the host of
# test_pool_import.py: the pool data and a filesystem without a label that can be imported.
admin = storage.admin
audit_file = storage.audit_file
host = test_pool_import.host

API = "/api/v1/storage"


def test_an_import_is_in_the_audit_log(admin, audit_file, host):
    response = admin.post(f"{API}/filesystems/{FOREIGN_UUID}/import", json={"name": "backup"})

    assert response.status_code == 200, response.text
    assert entries(audit_file) == [entry(
        "pool_imported", "pool", "backup", f"Imported the filesystem {FOREIGN_UUID} as the pool backup")]


@pytest.mark.parametrize(("uuid", "name", "status"), [
    ("7d4f2c1a-5678-4e9f-8a0b-ffffffffffff", "backup", 404),  # no such filesystem
    (FOREIGN_UUID, "data", 409),  # the name is a pool's already
])
def test_a_refused_import_writes_nothing(admin, audit_file, host, uuid, name, status):
    response = admin.post(f"{API}/filesystems/{uuid}/import", json={"name": name})

    assert response.status_code == status, response.text
    assert entries(audit_file) == []


def test_an_import_answers_when_its_entry_cannot_be_written(admin, host, monkeypatch, caplog):
    the_audit_log_is_full(monkeypatch)

    response = admin.post(f"{API}/filesystems/{FOREIGN_UUID}/import", json={"name": "backup"})

    assert response.status_code == 200, response.text
    assert [found["action"] for found in lost(caplog)] == ["pool_imported"]

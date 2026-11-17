"""Deleting several SMB shares at once: POST /shares/smb/bulk-delete (ADR-0389).

One request, one hold of smb_lock and one apply for all the shares it deletes; each share sent
gets its own result: deleted, not found (deleted or renamed elsewhere) or changed (its version
is no longer the one the page loaded). The folders are kept, as a single Delete keeps them.
"""

import sqlite3

from sqlalchemy.ext.asyncio import AsyncSession

import pytest

from src.host import HostResult
from src.shares import router as shares_router
from src.shares import service

from .test_share_api import ALICE, API, BOB, access, section
from .test_share_api import api as share_api  # noqa: F401  (a fixture: the share API on the real app)
from .test_sign_in_full_disk import ENTRY_LOST, the_audit_log_is_full

BULK = f"{API}/smb/bulk-delete"


@pytest.fixture
def api(request):
    """The share API of test_share_api.py: its client, paths, database and host."""
    return request.getfixturevalue("share_api")


def items(api, *shares):
    """The request body for (name, expected_version) pairs, each share in the folder of its name in
    the pool of the test, or for (name, expected_version, expected_path)."""
    return {"shares": [
        {"name": share[0], "expected_version": share[1],
         "expected_path": share[2] if len(share) > 2 else f"{api.pool}/{share[0].lower()}"}
        for share in shares
    ]}


def reloads(api) -> int:
    return sum(1 for cmd in api.host.calls if cmd[:3] == ["smbcontrol", "all", "reload-config"])


def testparms(api) -> int:
    return sum(1 for cmd in api.host.calls if cmd[0] == "testparm")


def make_shares(api, *names):
    for name in names:
        shares_router.limiter.reset()  # creating them is not under test
        response = api.create(name=name, folder=name, access=access((ALICE, "read")))
        assert response.status_code == 200, response.text
    shares_router.limiter.reset()
    api.host.calls.clear()


class TestBulkDelete:
    def test_the_shares_are_deleted_with_one_apply_and_their_folders_stay(self, api, monkeypatch):
        make_shares(api, "docs", "media", "photos")
        installs = []
        real_install = service.smb._install
        monkeypatch.setattr(service.smb, "_install", lambda tmp: (installs.append(tmp), real_install(tmp))[1])

        response = api.client.post(BULK, json=items(api, ("docs", 1), ("MEDIA", 1)))

        assert response.status_code == 200, response.text
        assert response.json() == {
            "results": [
                {"name": "docs", "status": "deleted", "detail": None},
                {"name": "MEDIA", "status": "deleted", "detail": None},
            ],
            "warnings": [],
        }
        assert api.rows("SELECT name FROM smb_shares") == [("photos",)]
        assert api.rows("SELECT DISTINCT share_id FROM smb_share_access") == api.rows("SELECT id FROM smb_shares")
        assert section(api, "docs") == "" and section(api, "media") == ""
        assert section(api, "photos") != ""
        # One staged file, one testparm, one install and one reload for both shares.
        assert (testparms(api), len(installs), reloads(api)) == (1, 1, 1)
        # The open connections to both are closed (ADR-0103).
        assert sorted(api.host.closed()) == ["docs", "media"]
        for folder in ("docs", "media", "photos"):
            assert (api.pool / folder).is_dir()

    def test_each_deleted_share_gets_its_own_audit_entry_as_a_single_delete_writes_it(self, api):
        make_shares(api, "docs", "media")
        entries_before = len(api.audit_entries())

        assert api.client.post(BULK, json=items(api, ("docs", 1), ("media", 1))).status_code == 200

        entries = api.audit_entries()[entries_before:]
        assert [(e["action"], e["severity"], e["username"], e["resource_type"], e["resource_id"], e["details"])
                for e in entries] == [
            ("share_deleted", "warning", "admin", "share", name,
             f"Deleted share {name}; its folder {api.pool}/{name} and the files in it were kept")
            for name in ("docs", "media")
        ]

    def test_a_share_gone_or_changed_meanwhile_is_reported_and_the_others_are_deleted(self, api):
        make_shares(api, "docs", "media", "photos")
        # Changed in another session after the page loaded it: version 2.
        assert api.patch("media", comment="Changed elsewhere").status_code == 200
        api.host.calls.clear()
        entries_before = len(api.audit_entries())

        response = api.client.post(BULK, json=items(api, ("docs", 1), ("media", 1), ("gone", 1), ("photos", 1)))

        assert response.status_code == 200, response.text
        assert response.json()["results"] == [
            {"name": "docs", "status": "deleted", "detail": None},
            {"name": "media", "status": "changed", "detail": "The share was changed meanwhile; reload"},
            {"name": "gone", "status": "not_found", "detail": "Share not found"},
            {"name": "photos", "status": "deleted", "detail": None},
        ]
        assert api.rows("SELECT name, version FROM smb_shares") == [("media", 2)]
        assert section(api, "media") != ""
        assert (testparms(api), reloads(api)) == (1, 1)
        assert [e["resource_id"] for e in api.audit_entries()[entries_before:]] == ["docs", "photos"]

    def test_a_share_made_again_under_the_name_in_another_folder_is_not_deleted(self, api):
        # Deleted and made again elsewhere after the page loaded it: version 1 again, another folder.
        make_shares(api, "docs")
        assert api.client.delete(f"{API}/smb/docs").status_code == 200
        assert api.create(name="docs", folder="other").status_code == 200
        api.host.calls.clear()

        response = api.client.post(BULK, json=items(api, ("docs", 1, f"{api.pool}/docs")))

        assert response.status_code == 200, response.text
        assert response.json()["results"] == [
            {"name": "docs", "status": "changed", "detail": "The share was changed meanwhile; reload"}]
        assert api.rows("SELECT name, path, version FROM smb_shares") == [("docs", f"{api.pool}/other", 1)]
        assert api.host.calls == []

    def test_a_busy_database_deletes_none_of_them(self, api, monkeypatch):
        make_shares(api, "docs", "media")
        before = api.conf.read_bytes()
        entries_before = len(api.audit_entries())
        real_commit = AsyncSession.commit
        attempts = []

        async def commit(self):
            if not attempts:
                attempts.append(1)
                raise sqlite3.OperationalError("database is locked")
            return await real_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", commit)
        response = api.client.post(BULK, json=items(api, ("docs", 1), ("media", 1)))

        assert (response.status_code, response.json()["detail"]) == (
            503, "Nothing was changed: the database was busy; try again")
        assert api.rows("SELECT name FROM smb_shares ORDER BY name") == [("docs",), ("media",)]
        assert api.conf.read_bytes() == before and api.temp_files() == []
        assert len(api.audit_entries()) == entries_before

    def test_nothing_to_delete_writes_nothing(self, api):
        make_shares(api, "docs")
        assert api.patch("docs", comment="Changed elsewhere").status_code == 200
        api.host.calls.clear()
        before = api.conf.read_bytes()
        entries_before = len(api.audit_entries())

        response = api.client.post(BULK, json=items(api, ("docs", 1), ("gone", 3)))

        assert response.status_code == 200, response.text
        assert [r["status"] for r in response.json()["results"]] == ["changed", "not_found"]
        assert api.host.calls == [] and api.conf.read_bytes() == before
        assert len(api.audit_entries()) == entries_before

    def test_a_rejected_apply_deletes_none_of_them(self, api):
        make_shares(api, "docs", "media")
        before = api.conf.read_bytes()
        entries_before = len(api.audit_entries())
        api.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")

        response = api.client.post(BULK, json=items(api, ("docs", 1), ("media", 1)))

        assert response.status_code == 500
        assert "testparm rejected the new smb.conf: Error loading services." in response.json()["detail"]
        assert api.rows("SELECT name FROM smb_shares ORDER BY name") == [("docs",), ("media",)]
        assert api.conf.read_bytes() == before and api.temp_files() == []
        assert len(api.audit_entries()) == entries_before

    def test_the_group_access_and_the_options_of_the_deleted_shares_go_with_them(self, api):
        make_shares(api, "docs", "media", "photos")
        ids = dict(api.rows("SELECT name, id FROM smb_shares"))
        api.sql("INSERT INTO user_groups (id, name, version) VALUES (1, 'family', 1)",
                *(f"INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES ({ids[n]}, 1, 'read')"
                  for n in ids),
                *(f"INSERT INTO smb_share_options (share_id, encryption_required, time_machine, previous_versions) "
                  f"VALUES ({ids[n]}, 1, 0, 0)" for n in ids))

        assert api.client.post(BULK, json=items(api, ("docs", 1), ("media", 1))).status_code == 200

        assert api.rows("SELECT share_id FROM smb_share_group_access") == [(ids["photos"],)]
        assert api.rows("SELECT share_id FROM smb_share_options") == [(ids["photos"],)]
        assert api.rows("SELECT share_id FROM smb_share_access") == [(ids["photos"],)]

    def test_an_audit_log_that_cannot_be_written_loses_no_entry_and_the_answer_says_so(
        self, api, monkeypatch, caplog
    ):
        # A full /data: every deleted share's entry goes to the supervisor's log, and the answer
        # still names each share's result (ADR-0389), with a warning that names the entries.
        make_shares(api, "docs", "media", "photos")
        the_audit_log_is_full(monkeypatch)

        response = api.client.post(BULK, json=items(api, ("docs", 1), ("media", 1)))

        assert response.status_code == 200, response.text
        body = response.json()
        assert [r["status"] for r in body["results"]] == ["deleted", "deleted"]
        assert body["warnings"] == [
            "The audit log could not record the deletion of docs, media (No space left on device); "
            "the supervisor's log has the entries"
        ]
        lost = [r.getMessage() for r in caplog.records if r.getMessage().startswith(ENTRY_LOST)]
        assert len(lost) == 2 and '"resource_id":"docs"' in lost[0] and '"resource_id":"media"' in lost[1]
        assert api.rows("SELECT name FROM smb_shares") == [("photos",)]

    def test_a_running_repair_of_each_deleted_share_stops(self, api, monkeypatch):
        make_shares(api, "docs", "media", "photos")
        ids = dict(api.rows("SELECT name, id FROM smb_shares"))
        stopped = []
        monkeypatch.setattr(service, "_stop_repair",
                            lambda share_id, by, *, deleted=False: stopped.append((share_id, by, deleted)))

        assert api.client.post(BULK, json=items(api, ("docs", 1), ("photos", 1))).status_code == 200

        assert stopped == [(ids["docs"], "the deletion of the share", True),
                           (ids["photos"], "the deletion of the share", True)]

    def test_a_rejected_apply_stops_no_repair(self, api, monkeypatch):
        make_shares(api, "docs")
        stopped = []
        monkeypatch.setattr(service, "_stop_repair", lambda *args, **kwargs: stopped.append(args))
        api.host.fail[("testparm",)] = HostResult(1, "", "Error loading services.")

        assert api.client.post(BULK, json=items(api, ("docs", 1))).status_code == 500

        assert stopped == []

    def test_more_shares_than_the_rate_limit_of_single_deletes_go_in_one_request(self, api):
        # DELETE /shares/smb/{name} allows 10 a minute: an 11th share would be refused with 429.
        names = [f"share{i:02d}" for i in range(12)]
        make_shares(api, *names)

        response = api.client.post(BULK, json=items(api, *((name, 1) for name in names)))

        assert response.status_code == 200, response.text
        assert {r["status"] for r in response.json()["results"]} == {"deleted"}
        assert api.rows("SELECT * FROM smb_shares") == []
        assert (testparms(api), reloads(api)) == (1, 1)

    def test_the_request_is_rate_limited_as_a_single_delete_is(self, api):
        answers = [api.client.post(BULK, json=items(api, ("gone", 1))).status_code for _ in range(11)]
        assert answers == [200] * 10 + [429]

    def test_malformed_requests_are_refused_before_anything_changes(self, api):
        make_shares(api, "docs")
        for body in (
            {"shares": []},
            items(api, ("docs", 1), ("DOCS", 1)),  # the same share twice
            items(api, *((f"s{i}", 1) for i in range(201))),
            items(api, ("x" * 65, 1)),  # longer than any share name
            {"shares": [{"name": "docs", "expected_path": f"{api.pool}/docs"}]},  # without its version
            {"shares": [{"name": "docs", "expected_version": 1}]},  # without its folder
            {"shares": [{"name": "docs", "expected_version": 1, "expected_path": f"{api.pool}/docs", "force": True}]},
            {"names": ["docs"]},
        ):
            shares_router.limiter.reset()
            response = api.client.post(BULK, json=body)
            assert response.status_code == 422, (body, response.text)
        assert api.rows("SELECT name FROM smb_shares") == [("docs",)]
        assert api.host.calls == []

    def test_access_of_other_shares_is_kept(self, api):
        make_shares(api, "docs")
        assert api.create(name="media", folder="media", access=access((BOB, "write"))).status_code == 200

        assert api.client.post(BULK, json=items(api, ("docs", 1))).status_code == 200

        assert api.rows("SELECT username, level FROM smb_share_access") == [("bob", "write")]
        assert "   write list = bob" in section(api, "media").splitlines()

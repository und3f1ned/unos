"""Groups of users and their access to SMB shares (ADR-0383, feature shares.group-access).

A group is a row of UNOS's database with its members; a share grants a group read or read-write
access like a user. smb.conf names no group: the members whose SMB account is active are written
into the share's lists when it is rendered, so a new member gets the group's access and a member
who leaves loses it (with the share's open connections closed, ADR-0103). A user who is also
given access directly gets the higher of the two levels.

The share API's fixture of test_share_api.py: alice, bob and carol have active SMB accounts, dave
has none (off), erin needs a password, admin is an administrator and Bob.Smith has a name SMB
cannot use. The cycle of test_smb_integration.py runs the real accounts code against the
FakeSambaHost.
"""

import sqlite3

import pytest

from src.shares import service

from .test_share_api import ADMIN, ALICE, API, BOB, CAROL, DAVE, ERIN, LEGACY, NAMES, access, section
from .test_share_api import api as share_api  # noqa: F401  (a fixture: the share API on the real app)
from .test_smb_integration import API as ROOT, TEST_PW, patch_share, startup
from .test_smb_integration import cycle as smb_cycle  # noqa: F401  (a fixture: the accounts code and the FakeSambaHost)
from .test_smb_integration import section as conf_section


@pytest.fixture
def api(request):
    """The share API of test_share_api.py: its client, paths, database and host."""
    return request.getfixturevalue("share_api")


@pytest.fixture
def cycle(request):
    """The SMB cycle of test_smb_integration.py: the real accounts code against the FakeSambaHost."""
    return request.getfixturevalue("smb_cycle")


GROUPS = "/api/v1/groups"
USERS = "/api/v1/auth/users"
UNCHECKED = "unchecked"  # verify_token is overridden by the fixture: no token is read
BEARER = {"Authorization": f"Bearer {UNCHECKED}"}


def members(*ids):
    return [{"user_id": user_id, "username": NAMES[user_id]} for user_id in ids]


def make_group(api, name="family", *ids):
    return api.client.post(GROUPS, json={"name": name, "members": members(*ids)})


def group_id(api, name) -> int:
    return next(group["id"] for group in api.client.get(GROUPS).json() if group["name"] == name)


def grant(api, *entries):
    """Group access entries as the web UI sends them: each group's id with its name."""
    return [{"group_id": group_id(api, group), "group": group, "level": level} for group, level in entries]


def patch_group(api, name, **fields):
    return api.client.patch(f"{GROUPS}/{group_id(api, name)}", json={"name": name, **fields})


def delete_group(api, name):
    return api.client.delete(f"{GROUPS}/{group_id(api, name)}", params={"name": name})


def lists(api, share="docs") -> tuple[str, str]:
    """The valid users and write list lines of the share's section."""
    lines = section(api, share).split("\n")
    valid = next((line.split(" = ", 1)[1] if " = " in line else "" for line in lines
                  if line.startswith("   valid users =")), None)
    write = next(line.split("=", 1)[1].strip() for line in lines if line.startswith("   write list ="))
    return valid, write


def member(user_id, state="active"):
    return {"user_id": user_id, "username": NAMES[user_id], "smb_state": state}


# --- the group API ------------------------------------------------------------------------------


class TestGroups:
    def test_a_group_is_made_with_its_members_listed_and_audited(self, api):
        response = make_group(api, "family", BOB, ALICE, DAVE)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "id": 1, "name": "family", "version": 1,
            "members": [member(ALICE), member(BOB), member(DAVE, "off")],
            "shares": [], "warnings": [],
        }
        assert api.rows("SELECT id, name, version FROM user_groups") == [(1, "family", 1)]
        assert api.rows("SELECT group_id, user_id, username FROM user_group_members ORDER BY user_id") == [
            (1, ALICE, "alice"), (1, BOB, "bob"), (1, DAVE, "dave")]
        assert [g["name"] for g in api.client.get(GROUPS).json()] == ["family"]
        assert api.client.get(f"{GROUPS}/{group_id(api, 'family')}").json()["members"][0] == member(ALICE)
        (entry,) = api.audit_entries()
        assert (entry["action"], entry["severity"], entry["username"], entry["resource_type"],
                entry["resource_id"], entry["details"]) == (
            "group_created", "info", "admin", "group", "family", "Created group family; members: alice, bob, dave")

    def test_a_new_group_writes_no_smb_conf(self, api):
        assert make_group(api, "family", ALICE).status_code == 200
        assert not api.conf.exists()
        assert [call for call in api.host.calls if call[0] == "testparm"] == []

    def test_users_who_need_a_password_or_are_off_can_be_members(self, api):
        response = make_group(api, "family", DAVE, ERIN)
        assert response.status_code == 200, response.text
        assert response.json()["members"] == [member(DAVE, "off"), member(ERIN, "password_needed")]

    @pytest.mark.parametrize("name", ["Family", "a b", "", "-x", "1st", "x" * 33, "fam.ily", "group\n"])
    def test_names_follow_the_rule_of_usernames(self, api, name):
        response = make_group(api, name)
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Group names have 1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter")

    @pytest.mark.parametrize("name", ["staff", "users", "alice", "s-1-1-0", "a"])
    def test_names_that_only_unos_uses_need_not_be_free_on_the_host(self, api, name):
        """No group of the host is made: a host group of the same name, or a user's, is no clash."""
        assert make_group(api, name, ALICE).status_code == 200

    def test_a_name_that_another_group_has_in_any_case_is_refused(self, api):
        api.sql("INSERT INTO user_groups (id, name, version) VALUES (5, 'Family', 1)")  # a restored row
        response = make_group(api, "family")
        assert (response.status_code, response.json()["detail"]) == (409, "Group family already exists")

    def test_members_are_checked_against_the_users_as_stored(self, api):
        unknown = api.client.post(GROUPS, json={"name": "g", "members": [{"user_id": 42, "username": "ghost"}]})
        assert (unknown.status_code, unknown.json()["detail"]) == (400, "User 42 does not exist")
        api.sql("UPDATE users SET username = 'mallory' WHERE id = 2")
        stale = make_group(api, "g", BOB)
        assert (stale.status_code, stale.json()["detail"]) == (409, "User 2 is now 'mallory'; reload the page")
        admin = make_group(api, "g", ADMIN)
        assert (admin.status_code, admin.json()["detail"]) == (400, "Administrators cannot use SMB")
        legacy = make_group(api, "g", LEGACY)
        assert legacy.status_code == 400
        assert legacy.json()["detail"].startswith("The username 'Bob.Smith' cannot be used for SMB.")
        twice = api.client.post(GROUPS, json={"name": "g", "members": members(ALICE, ALICE)})
        assert twice.status_code == 422 and "Each user can appear only once in the group" in twice.text
        assert api.rows("SELECT * FROM user_groups") == []

    def test_an_unknown_group_is_404(self, api):
        for response in (api.client.get(f"{GROUPS}/9"), api.client.patch(f"{GROUPS}/9", json={"name": "x"}),
                         api.client.delete(f"{GROUPS}/9", params={"name": "x"})):
            assert (response.status_code, response.json()["detail"]) == (404, "Group not found")

    def test_a_request_for_a_group_that_was_renamed_meanwhile_is_refused(self, api):
        """A request names the group by id and the name it saw (as for users, ADR-0133)."""
        assert make_group(api, "family", ALICE).status_code == 200
        assert patch_group(api, "family", new_name="parents").status_code == 200
        for response in (api.client.patch(f"{GROUPS}/1", json={"name": "family", "members": []}),
                         api.client.delete(f"{GROUPS}/1", params={"name": "family"})):
            assert (response.status_code, response.json()["detail"]) == (409, "Group 1 is now 'parents'; reload the page")
        assert api.rows("SELECT group_id, user_id FROM user_group_members") == [(1, ALICE)]

    def test_the_id_of_a_deleted_group_is_never_handed_out_again(self, api):
        """A request for a deleted group never reaches a new one of the same name (AUTOINCREMENT)."""
        assert make_group(api, "family", ALICE).status_code == 200
        assert delete_group(api, "family").status_code == 200
        again = make_group(api, "family", BOB)
        assert again.json()["id"] == 2
        response = api.client.patch(f"{GROUPS}/1", json={"name": "family", "members": members(CAROL)})
        assert (response.status_code, response.json()["detail"]) == (404, "Group not found")
        assert api.rows("SELECT group_id, user_id FROM user_group_members") == [(2, BOB)]

    def test_a_rename_keeps_the_members_and_the_access(self, api):
        assert make_group(api, "family", ALICE).status_code == 200
        assert api.create(group_access=grant(api, ("family", "write"))).status_code == 200
        conf = api.conf.read_bytes()

        api.host.calls.clear()

        response = patch_group(api, "family", new_name="kids", expected_version=1)

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["name"], body["version"], body["members"], body["shares"]) == (
            "kids", 2, [member(ALICE)], [{"share": "docs", "level": "write"}])
        assert api.conf.read_bytes() == conf  # smb.conf names no group: it is not even prepared
        assert api.host.calls == []
        assert api.get("docs").json()["group_access"][0]["group"] == "kids"
        assert api.audit_entries()[-1]["details"] == "Changed group family: name family→kids"
        taken = make_group(api, "other")
        assert taken.status_code == 200
        clash = patch_group(api, "other", new_name="kids")
        assert (clash.status_code, clash.json()["detail"]) == (409, "Group kids already exists")
        bad = patch_group(api, "other", new_name="Kids!")
        assert bad.status_code == 400

    def test_a_stale_version_is_refused_and_nothing_changes(self, api):
        assert make_group(api, "family", ALICE).status_code == 200
        assert patch_group(api, "family", members=members(ALICE, BOB)).status_code == 200
        response = patch_group(api, "family", expected_version=1, members=[])
        assert (response.status_code, response.json()["detail"]) == (409, "The group was changed meanwhile; reload")
        assert len(api.rows("SELECT * FROM user_group_members")) == 2

    def test_an_unchanged_group_writes_and_audits_nothing(self, api):
        assert make_group(api, "family", ALICE).status_code == 200
        response = patch_group(api, "family", new_name="family", members=members(ALICE))
        assert response.status_code == 200 and response.json()["version"] == 1
        assert len(api.audit_entries()) == 1

    @pytest.mark.parametrize("field", ["name", "new_name", "members", "expected_version"])
    def test_null_fields_are_refused(self, api, field):
        assert make_group(api).status_code == 200
        body = {"name": "family", field: None}
        assert api.client.patch(f"{GROUPS}/{group_id(api, 'family')}", json=body).status_code == 422
        assert api.client.patch(f"{GROUPS}/1", json={"members": []}).status_code == 422  # without the name


# --- a group's access to a share -----------------------------------------------------------------


class TestGroupAccess:
    @pytest.fixture
    def family(self, api):
        response = make_group(api, "family", ALICE, DAVE)
        assert response.status_code == 200, response.text
        return response.json()

    def test_read_write_access_renders_the_active_members(self, api, family):
        response = api.create(group_access=grant(api, ("family", "write")))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["access"] == [] and body["available"] is True
        assert body["group_access"] == [{"group_id": 1, "group": "family", "level": "write", "members": [
            {"username": "alice", "smb_state": "active", "effective": True},
            {"username": "dave", "smb_state": "off", "effective": False},
        ]}]
        assert lists(api) == ("alice", "alice")
        assert api.rows("SELECT share_id, group_id, level FROM smb_share_group_access") == [(1, 1, "write")]
        assert api.client.get(f"{GROUPS}/{group_id(api, 'family')}").json()["shares"] == [{"share": "docs", "level": "write"}]
        assert api.audit_entries()[-1]["details"] == (
            f"Created share docs at {api.pool}/docs; group access: family=write")

    def test_read_access(self, api, family):
        assert api.create(group_access=grant(api, ("family", "read"))).status_code == 200
        assert lists(api) == ("alice", "")

    def test_a_group_without_active_members_gives_no_access(self, api):
        assert make_group(api, "family", DAVE, ERIN).status_code == 200
        response = api.create(group_access=grant(api, ("family", "write")))
        assert response.json()["available"] is False
        assert lists(api) == ("root", "")

    def test_a_new_member_gets_the_group_s_access_and_nothing_is_closed(self, api, family):
        assert api.create(group_access=grant(api, ("family", "read"))).status_code == 200
        api.host.calls.clear()

        response = patch_group(api, "family", members=members(ALICE, BOB, DAVE))

        assert response.status_code == 200, response.text
        assert response.json()["version"] == 2
        assert lists(api) == ("alice, bob", "")
        assert api.host.closed() == []
        assert ["smbcontrol", "all", "reload-config"] in api.host.calls
        assert api.audit_entries()[-1]["details"] == "Changed group family: members: +bob"
        assert api.get("docs").json()["version"] == 1  # the share itself did not change

    def test_a_member_who_leaves_loses_the_access_and_the_share_s_connections_close(self, api, family):
        assert make_group(api, "kids", CAROL).status_code == 200
        assert api.create(group_access=grant(api, ("family", "write"))).status_code == 200
        assert api.create("media", "media", group_access=grant(api, ("kids", "read"))).status_code == 200
        api.host.calls.clear()

        response = patch_group(api, "family", members=members(DAVE))

        assert response.status_code == 200, response.text
        assert lists(api) == ("root", "")
        assert api.host.closed() == ["docs"]  # only the shares of this group
        assert api.audit_entries()[-1]["details"] == "Changed group family: members: -alice"

    @pytest.mark.parametrize("direct, through_group, rendered", [
        ("read", "write", ("alice", "alice")),
        ("write", "read", ("alice", "alice")),
        ("read", "read", ("alice", "")),
    ])
    def test_the_higher_of_a_user_s_own_level_and_the_group_s_counts(self, api, family, direct, through_group,
                                                                    rendered):
        response = api.create(access=access((ALICE, direct)), group_access=grant(api, ("family", through_group)))
        assert response.status_code == 200, response.text
        assert lists(api) == rendered
        assert response.json()["access"][0]["level"] == direct  # each grant is listed as given

    def test_removing_a_user_s_own_grant_leaves_the_group_s_and_closes_the_share(self, api, family):
        assert api.create(access=access((ALICE, "write")), group_access=grant(api, ("family", "read"))).status_code == 200
        api.host.calls.clear()
        response = api.patch("docs", access=[])
        assert response.status_code == 200, response.text
        assert lists(api) == ("alice", "")
        assert api.host.closed() == ["docs"]  # alice lost write

    def test_group_access_is_patched_and_audited(self, api, family):
        assert make_group(api, "kids", BOB).status_code == 200
        assert api.create(group_access=grant(api, ("family", "write"), ("kids", "read"))).status_code == 200
        api.host.calls.clear()

        response = api.patch("docs", expected_version=1, group_access=grant(api, ("family", "read")))

        assert response.status_code == 200, response.text
        assert [(g["group"], g["level"]) for g in response.json()["group_access"]] == [("family", "read")]
        assert response.json()["version"] == 2
        assert lists(api) == ("alice", "")
        assert api.host.closed() == ["docs"]
        assert api.audit_entries()[-1]["details"] == "Changed share docs: group access: family write→read, -kids"

    def test_a_group_is_named_by_its_id_and_the_name_the_page_saw(self, api, family):
        """As users (ADR-0133): an id whose group now has another name is refused, so that a page loaded
        before a rename and a new group of the old name never gives that new group the access."""
        assert patch_group(api, "family", new_name="parents").status_code == 200
        assert make_group(api, "family", BOB).status_code == 200
        stale = api.create(group_access=[{"group_id": 1, "group": "family", "level": "write"}])
        assert (stale.status_code, stale.json()["detail"]) == (409, "Group 1 is now 'parents'; reload the page")
        unknown = api.create(group_access=[{"group_id": 9, "group": "nope", "level": "read"}])
        assert (unknown.status_code, unknown.json()["detail"]) == (400, "Group 9 does not exist")
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_duplicate_and_invalid_entries_are_refused(self, api, family):
        twice = api.create(group_access=grant(api, ("family", "read"), ("family", "write")))
        assert twice.status_code == 422 and "Each group can appear only once in the group access list" in twice.text
        bad = api.create(group_access=[{"group_id": 1, "group": "family", "level": "owner"}])
        assert bad.status_code == 422
        without_name = api.create(group_access=[{"group_id": 1, "level": "read"}])
        assert without_name.status_code == 422
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_a_share_that_is_not_served_gets_no_group_access_but_can_lose_it(self, api, family):
        assert api.create(group_access=grant(api, ("family", "read"))).status_code == 200
        api.sql(f"UPDATE smb_shares SET path = '{api.pool}' WHERE id = 1")  # the pool root: not served
        refused = api.patch("docs", group_access=grant(api, ("family", "write")))
        assert (refused.status_code, refused.json()["detail"]) == (400, service.POOL_ROOT_PROBLEM)
        refused = api.patch("docs", comment="still granted")
        assert refused.status_code == 400  # the stored group access grants: a valid folder is needed
        removed = api.patch("docs", group_access=[])
        assert removed.status_code == 200, removed.text
        assert api.rows("SELECT * FROM smb_share_group_access") == []

    def test_deleting_a_group_removes_its_access_and_closes_its_shares(self, api, family):
        assert api.create(access=access((BOB, "read")), group_access=grant(api, ("family", "write"))).status_code == 200
        api.host.calls.clear()

        response = delete_group(api, "family")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted", "warnings": []}
        assert lists(api) == ("bob", "")
        assert api.host.closed() == ["docs"]
        assert api.rows("SELECT * FROM user_groups") == []
        assert api.rows("SELECT * FROM user_group_members") == []
        assert api.rows("SELECT * FROM smb_share_group_access") == []
        info = api.get("docs").json()
        assert (info["group_access"], info["version"]) == ([], 2)
        entry = api.audit_entries()[-1]
        assert (entry["action"], entry["severity"], entry["resource_id"], entry["details"]) == (
            "group_deleted", "warning", "family", "Deleted group family; its access was removed from: docs (write)")

    def test_a_rename_makes_a_share_dialog_loaded_before_it_stale(self, api, family):
        """A share dialog keys a group's level by its id and name: one loaded before a rename would show
        the renamed group at No access and send the list without it. The rename raises the versions of
        the group's shares, as a user's rename does (ADR-0368), so such a save is refused."""
        assert make_group(api, "kids", BOB).status_code == 200
        created = api.create(group_access=grant(api, ("family", "write")))
        assert created.status_code == 200, created.text
        version = created.json()["version"]

        assert patch_group(api, "family", new_name="parents").status_code == 200

        assert api.get("docs").json()["version"] == version + 1
        stale = api.patch("docs", expected_version=version, group_access=grant(api, ("kids", "read")))
        assert (stale.status_code, stale.json()["detail"]) == (409, service.VERSION_CONFLICT)
        assert lists(api) == ("alice", "alice")
        assert [g["group"] for g in api.get("docs").json()["group_access"]] == ["parents"]

    def test_deleting_a_share_deletes_its_group_access(self, api, family):
        assert api.create(group_access=grant(api, ("family", "write"))).status_code == 200
        assert api.client.delete(f"{API}/smb/docs").status_code == 200
        assert api.rows("SELECT * FROM smb_share_group_access") == []
        assert api.client.get(f"{GROUPS}/{group_id(api, 'family')}").json()["shares"] == []

    def test_rows_left_for_a_reused_group_id_give_a_new_group_nothing(self, api):
        """SQLite reuses the highest rowid: rows that a restored database left for id 1 belong to no
        group, and the new group with that id starts without them (as a new user does, ADR-0132)."""
        assert api.create(access=access((BOB, "read"))).status_code == 200
        api.sql("INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (1, 1, 'write')",
                f"INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, {CAROL}, 'carol')")
        assert lists(api) == ("bob", "")  # rows of no group grant nothing

        response = make_group(api, "family", ALICE)

        assert response.status_code == 200, response.text
        assert response.json()["members"] == [member(ALICE)] and response.json()["shares"] == []
        assert api.rows("SELECT * FROM smb_share_group_access") == []
        assert api.rows("SELECT group_id, user_id FROM user_group_members") == [(1, ALICE)]

    def test_stale_member_rows_are_not_rendered_or_listed(self, api, family):
        """A member row whose user now has another name, or is an administrator, gives nothing (ADR-0137)."""
        assert api.create(group_access=grant(api, ("family", "write"))).status_code == 200
        api.sql(f"INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, {BOB}, 'robert')",
                f"INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, {ADMIN}, 'admin')")
        assert api.patch("docs", comment="again").status_code == 200
        assert lists(api) == ("alice", "alice")
        assert [m["username"] for m in api.client.get(f"{GROUPS}/{group_id(api, 'family')}").json()["members"]] == ["alice", "dave"]
        assert [m["username"] for m in api.get("docs").json()["group_access"][0]["members"]] == ["alice", "dave"]

    def test_two_rows_of_one_group_on_a_share_give_the_lower_level(self, api, family):
        """ADR-0144: a restored table without its UNIQUE constraint; as for a user's two rows."""
        assert api.create().status_code == 200
        api.sql("DROP TABLE smb_share_group_access",
                "CREATE TABLE smb_share_group_access (id INTEGER PRIMARY KEY, share_id INTEGER NOT NULL, "
                "group_id INTEGER NOT NULL, level VARCHAR(8) NOT NULL)",
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (1, 1, 'write')",
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (1, 1, 'read')")
        assert api.patch("docs", comment="again").status_code == 200
        assert lists(api) == ("alice", "")
        assert [(g["group"], g["level"]) for g in api.get("docs").json()["group_access"]] == [("family", "read")]


# --- users and their groups ----------------------------------------------------------------------


class TestUsersInGroups:
    """The user flows of src/auth/router.py with the real share code (users without SMB rows run no
    account command on the host)."""

    @pytest.fixture
    def shared(self, api):
        assert make_group(api, "family", ALICE, DAVE).status_code == 200
        assert api.create(group_access=grant(api, ("family", "read"))).status_code == 200
        api.host.calls.clear()

    def test_a_deleted_user_leaves_every_group(self, api, shared):
        response = api.client.delete(f"{USERS}/{DAVE}", params={"username": "dave"})
        assert response.status_code == 200, response.text
        assert api.rows("SELECT user_id FROM user_group_members") == [(ALICE,)]

    def test_an_administrator_is_in_no_group(self, api, shared):
        response = api.client.put(f"{USERS}/{DAVE}", json={"username": "dave", "is_admin": True}, headers=BEARER)
        assert response.status_code == 200, response.text
        assert api.rows("SELECT user_id FROM user_group_members") == [(ALICE,)]
        # Demoted again, the user is in no group: a promotion revokes all of SMB (ADR-0117).
        response = api.client.put(f"{USERS}/{DAVE}", json={"username": "dave", "is_admin": False}, headers=BEARER)
        assert response.status_code == 200
        assert api.rows("SELECT user_id FROM user_group_members") == [(ALICE,)]

    def test_a_renamed_user_stays_a_member_under_the_new_name(self, api, shared):
        response = api.client.put(f"{USERS}/{DAVE}", json={"username": "dave", "new_username": "david"},
                                  headers=BEARER)
        assert response.status_code == 200, response.text
        assert api.rows("SELECT user_id, username FROM user_group_members ORDER BY user_id") == [
            (ALICE, "alice"), (DAVE, "david")]
        assert [m["username"] for m in api.client.get(f"{GROUPS}/{group_id(api, 'family')}").json()["members"]] == ["alice", "david"]

    def test_a_new_user_inherits_no_membership_of_its_id(self, api, shared):
        """A restored database may have left member rows for the id the next user gets (ADR-0132)."""
        api.sql("INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, 8, 'frank')")
        response = api.client.post(USERS, json={"username": "frank", "password": "Correct-Horse-9"})
        assert response.status_code == 200, response.text
        assert response.json()["id"] == 8
        assert api.rows("SELECT user_id FROM user_group_members ORDER BY user_id") == [(ALICE,), (DAVE,)]

    def test_the_account_page_shows_the_level_that_smb_conf_gives(self, api):
        """Two rows of one grant (a restored table without its UNIQUE constraint) give the lower level,
        as smb.conf does (ADR-0144); the higher level counts across grants."""
        from src.auth.models import User
        from src.auth.utils import verify_token
        from src.main import app

        assert make_group(api, "family", ALICE).status_code == 200
        for name in ("both", "docs", "media"):
            assert api.create(name, name).status_code == 200
        api.sql("DROP TABLE smb_share_group_access",
                "CREATE TABLE smb_share_group_access (id INTEGER PRIMARY KEY, share_id INTEGER NOT NULL, "
                "group_id INTEGER NOT NULL, level VARCHAR(8) NOT NULL)",
                "DROP TABLE smb_share_access",
                "CREATE TABLE smb_share_access (id INTEGER PRIMARY KEY, share_id INTEGER NOT NULL, user_id INTEGER "
                "NOT NULL, username VARCHAR(64) NOT NULL, level VARCHAR(8) NOT NULL, created_at DATETIME NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP)",
                # both: alice reads by her own level and writes through family.
                f"INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (1, {ALICE}, 'alice', 'read')",
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (1, 1, 'write')",
                # docs: two rows of family's grant.
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (2, 1, 'write')",
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (2, 1, 'read')",
                # media: two rows of alice's own grant.
                f"INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (3, {ALICE}, 'alice', 'write')",
                f"INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (3, {ALICE}, 'alice', 'read')")
        assert api.patch("both", comment="rendered again").status_code == 200
        assert [lists(api, name) for name in ("both", "docs", "media")] == [
            ("alice", "alice"), ("alice", ""), ("alice", "")]
        alice = User(id=ALICE, username="alice", hashed_password="x", is_admin=False, totp_enabled=False)
        app.dependency_overrides[verify_token] = lambda: alice  # the fixture removes it

        shares = api.client.get("/api/v1/auth/me/smb").json()["shares"]

        assert [(s["name"], s["level"]) for s in shares] == [("both", "write"), ("docs", "read"), ("media", "read")]

    def test_the_account_page_lists_the_shares_of_the_user_s_groups(self, api, shared):
        from src.auth.models import User
        from src.auth.utils import verify_token
        from src.main import app

        assert api.create("media", "media", access=access((ALICE, "read")),
                          group_access=grant(api, ("family", "write"))).status_code == 200
        assert api.create("other", "other", access=access((BOB, "read"))).status_code == 200
        alice = User(id=ALICE, username="alice", hashed_password="x", is_admin=False, totp_enabled=False)
        app.dependency_overrides[verify_token] = lambda: alice  # the fixture removes it

        shares = api.client.get("/api/v1/auth/me/smb").json()["shares"]

        assert [(s["name"], s["level"], s["usable"]) for s in shares] == [
            ("docs", "read", True), ("media", "write", True)]


# --- the startup reconcile and restores ------------------------------------------------------------


class TestReconcileAndRestore:
    async def test_the_reconcile_purges_stale_member_and_group_access_rows(self, api):
        assert make_group(api, "family", ALICE).status_code == 200
        assert api.create(group_access=grant(api, ("family", "read"))).status_code == 200
        api.sql(f"INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, {BOB}, 'robert')",
                f"INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, {ADMIN}, 'admin')",
                "INSERT INTO user_group_members (group_id, user_id, username) VALUES (1, 99, 'ghost')",
                f"INSERT INTO user_group_members (group_id, user_id, username) VALUES (7, {CAROL}, 'carol')",
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (1, 7, 'write')",
                "INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (9, 1, 'write')")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(f"sqlite+aiosqlite:///{api.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await service.purge_orphans(db) == 6
                await db.commit()
        finally:
            await engine.dispose()
        assert api.rows("SELECT group_id, user_id FROM user_group_members") == [(1, ALICE)]
        assert api.rows("SELECT share_id, group_id FROM smb_share_group_access") == [(1, 1)]
        assert api.rows("SELECT version FROM smb_shares") == [(2,)]  # docs lost a row

    async def test_after_a_restore_a_share_that_is_not_served_loses_its_group_access(self, api):
        """Post-restore step 2 (_remove_access_of_unserved) for a share with group access only."""
        assert make_group(api, "family", ALICE).status_code == 200
        assert api.create(group_access=grant(api, ("family", "write"))).status_code == 200
        api.sql(f"UPDATE smb_shares SET path = '{api.pool}' WHERE id = 1")  # the pool root: not served
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(f"sqlite+aiosqlite:///{api.db_path}", poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await service._remove_access_of_unserved(db) == ["docs"]
                await db.commit()
        finally:
            await engine.dispose()
        assert api.rows("SELECT * FROM smb_share_group_access") == []
        assert api.rows("SELECT version, import_notes FROM smb_shares") == [
            (2, f'["Not served: {service.POOL_ROOT_PROBLEM}; its access was removed at the restore"]')]

    def test_a_restore_keeps_group_access_only_for_served_shares(self, tmp_path, monkeypatch):
        from sqlalchemy import create_engine

        from src.database import Base
        from src.security import validators
        from src.shares import smb
        from src.storage import pools

        base = tmp_path / "pools"
        (base / "data").mkdir(parents=True)
        for settings in {id(smb.settings): smb.settings, id(validators.settings): validators.settings}.values():
            monkeypatch.setattr(settings, "pools_base_path", str(base))
        monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO user_groups (id, name, version) VALUES (1, 'family', 1)")
        conn.executemany("INSERT INTO smb_shares (id, name, path, version) VALUES (?, ?, ?, 1)",
                         [(1, "docs", f"{base}/data/docs"), (2, "top", f"{base}/data")])
        conn.executemany("INSERT INTO smb_share_group_access (share_id, group_id, level) VALUES (?, 1, 'read')",
                         [(1,), (2,), (3,)])

        service.prepare_restored_shares(conn, authoritative=True)

        assert conn.execute("SELECT share_id FROM smb_share_group_access").fetchall() == [(1,)]
        assert conn.execute("SELECT version FROM smb_shares WHERE id = 2").fetchone() == (2,)
        service.prepare_restored_shares(conn, authoritative=False)
        assert conn.execute("SELECT * FROM smb_share_group_access").fetchall() == []
        assert conn.execute("SELECT name FROM user_groups").fetchall() == [("family",)]  # groups stay
        conn.close()

    def test_a_backup_without_the_group_tables_is_restored_and_one_of_another_layout_is_refused(self, tmp_path):
        from sqlalchemy import create_engine

        from src.backup.router import BackupValidationError
        from src.database import Base

        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        for table in ("user_groups", "user_group_members", "smb_share_group_access"):
            conn.execute(f"DROP TABLE {table}")
        conn.commit()
        service.check_smb_tables(db)  # a backup from before groups: no group
        conn.execute("CREATE TABLE user_group_members (id INTEGER PRIMARY KEY, group_id INTEGER)")
        conn.commit()
        with pytest.raises(BackupValidationError, match="user_group_members.user_id"):
            service.check_smb_tables(db)

    @pytest.mark.parametrize("name", ["f" * 70, "Family Room", sqlite3.Binary(b"\xff")])
    def test_a_backup_with_a_group_name_that_unos_refuses_is_refused(self, tmp_path, name):
        """Such a group could not be named in a request (the API takes the rule's names), so its access
        could be neither changed nor deleted: the restore refuses the backup (ADR-0144)."""
        from sqlalchemy import create_engine

        from src.backup.router import BackupValidationError
        from src.database import Base

        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO user_groups (id, name, version) VALUES (1, 'family', 1)")
        conn.execute("INSERT INTO user_groups (id, name, version) VALUES (2, ?, 1)", (name,))
        conn.commit()
        conn.close()
        with pytest.raises(BackupValidationError, match="has a group whose name UNOS does not accept"):
            service.check_smb_tables(db)

    def test_a_backup_that_gives_one_name_to_two_groups_is_refused(self, tmp_path):
        """UNOS keeps group names unique whatever their case; a backup's table need not (ADR-0144)."""
        from sqlalchemy import create_engine

        from src.backup.router import BackupValidationError
        from src.database import Base

        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE user_groups")
        conn.execute("CREATE TABLE user_groups (id INTEGER PRIMARY KEY, name VARCHAR(32) NOT NULL, version INTEGER)")
        conn.executemany("INSERT INTO user_groups (name, version) VALUES (?, 1)", [("family",), ("Family",)])
        conn.commit()
        conn.close()
        with pytest.raises(BackupValidationError, match="gives the name Family to 2 groups"):
            service.check_smb_tables(db)


# --- the whole cycle with the real accounts code ---------------------------------------------------


def test_a_member_s_access_follows_the_group_on_the_host(cycle):
    """With the FakeSambaHost: an active user in a group with write access is in the share's lists, a
    session of the user on the share ends when the user leaves the group, and the user's deletion
    takes the user out of the group and the lists."""
    c, world, fake = cycle, cycle.world, cycle.fake
    test_id = world.ids["test"]
    admin = world.headers("admin")
    startup()
    patch_share(c, {"path": f"{c.base}/data/shared"})
    assert c.client.post(f"{ROOT}/auth/users/{test_id}/smb/enable", json={"username": "test"},
                         headers=admin).status_code == 200
    enabled = c.client.post(f"{ROOT}/auth/me/smb/enable", json={"current_password": TEST_PW},
                            headers=world.headers("test"))
    assert enabled.status_code == 200, enabled.text
    test_member = [{"user_id": test_id, "username": "test"}]

    made = c.client.post(f"{ROOT}/groups", json={"name": "family", "members": test_member}, headers=admin)
    assert made.status_code == 200, made.text
    family = made.json()["id"]
    patch_share(c, {"group_access": [{"group_id": family, "group": "family", "level": "write"}]})
    data = conf_section(c.conf.read_text(), "DATA")
    assert "   valid users = test" in data and "   write list = test" in data

    fake.add_session("test", ["IPC$", "DATA"])
    mark = len(fake.calls)
    left = c.client.request("PATCH", f"{ROOT}/groups/{family}", json={"name": "family", "members": []}, headers=admin)
    assert left.status_code == 200, left.text
    assert ["smbcontrol", "smbd", "close-share", "DATA"] in [argv for argv, _ in fake.calls[mark:]]
    assert "   valid users = root" in conf_section(c.conf.read_text(), "DATA")

    back = c.client.request("PATCH", f"{ROOT}/groups/{family}", json={"name": "family", "members": test_member}, headers=admin)
    assert back.status_code == 200, back.text
    assert "   write list = test" in conf_section(c.conf.read_text(), "DATA")

    deleted = c.client.delete(f"{ROOT}/auth/users/{test_id}", params={"username": "test"}, headers=admin)
    assert deleted.status_code == 200, deleted.text
    assert "   valid users = root" in conf_section(c.conf.read_text(), "DATA")
    assert c.client.get(f"{ROOT}/groups/{family}", headers=admin).json()["members"] == []


def test_the_e2e_fakes_answer_with_the_servers_texts():
    """webui/tests/shares-fakes.ts answers the group API with these texts word for word (ADR-0189)."""
    from pathlib import Path

    from src.shares import groups

    fakes = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "shares-fakes.ts").read_text(encoding="utf-8")
    assert f'"{groups.GROUP_NAME_RULE}"' in fakes
    assert f"'{groups.GROUP_VERSION_CONFLICT}'" in fakes
    assert f"'{groups.GROUP_NOT_FOUND}'" in fakes
    assert "`Group ${name} already exists`" in fakes and "`Group ${id} does not exist`" in fakes
    assert "`Group ${id} is now '${name}'; reload the page`" in fakes


def test_a_member_made_an_administrator_leaves_the_group_and_the_share_s_lists(cycle):
    """With the FakeSambaHost: the promotion of an active member revokes SMB (ADR-0117) and takes the
    user out of the group, so smb.conf no longer names them."""
    c, world = cycle, cycle.world
    test_id = world.ids["test"]
    admin = world.headers("admin")
    startup()
    patch_share(c, {"path": f"{c.base}/data/shared"})
    assert c.client.post(f"{ROOT}/auth/users/{test_id}/smb/enable", json={"username": "test"},
                         headers=admin).status_code == 200
    assert c.client.post(f"{ROOT}/auth/me/smb/enable", json={"current_password": TEST_PW},
                         headers=world.headers("test")).status_code == 200
    made = c.client.post(f"{ROOT}/groups", json={"name": "family", "members": [{"user_id": test_id, "username": "test"}]},
                         headers=admin)
    assert made.status_code == 200, made.text
    patch_share(c, {"group_access": [{"group_id": made.json()["id"], "group": "family", "level": "read"}]})
    assert "   valid users = test" in conf_section(c.conf.read_text(), "DATA")

    promoted = c.client.put(f"{ROOT}/auth/users/{test_id}", json={"username": "test", "is_admin": True}, headers=admin)

    assert promoted.status_code == 200, promoted.text
    assert "   valid users = root" in conf_section(c.conf.read_text(), "DATA")
    assert c.client.get(f"{ROOT}/groups/{made.json()['id']}", headers=admin).json()["members"] == []

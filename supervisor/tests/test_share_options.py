"""The protocol options of a share (ADR-0377): encryption required, Time Machine and Previous
Versions, each off by default.

The rendering (pure), then the share API with the fixture of test_share_api.py: the options
are stored in smb_share_options, a row of their own keyed by the share, deleted with it, and
validated when stored and when rendered. The host's vfs modules are checked with one host
command (``sh -c`` of ``smb.VFS_CHECK``), which ``modules()`` answers.
"""

import os

import pytest

from src.host import HostResult
from src.shares import service, smb, smbconf
from src.shares.service import ShareRender

from .test_share_api import ALICE, API, access, section
from .test_share_api import api as share_api  # noqa: F401  (a fixture: the share API on the real app)

PATH = "/mnt/pools/data/shared"
FRUIT = ("catia", "fruit", "streams_xattr")
SNAPSHOTS = ("shadow_copy2", "btrfs")
# The restart of avahi-daemon waits for its job, under the host's timeout (ADR-0258), after a reset
# of the unit's failed state and start limit.
AVAHI_RESET = ["systemctl", "reset-failed", "avahi-daemon"]
AVAHI = ["timeout", "60", "systemctl", "restart", "avahi-daemon"]


def share(name="shared", path=PATH, *, readers=("alice",), writers=(), guest_ok=False, **options):
    return ShareRender(name=name, path=path, comment="", browseable=True, guest_ok=guest_ok,
                       readers=tuple(readers), writers=tuple(writers), **options)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def option_lines(rendered: str) -> list[str]:
    """The lines of a section between ``available`` and the fixed lines."""
    found = lines(rendered)
    return found[found.index(next(x for x in found if x.startswith("available = "))) + 1:found.index(
        "veto files = /.snapshots/.unos-trash/")]


# --- the rendering --------------------------------------------------------------------------------


class TestRendering:
    def test_every_section_states_encryption_and_its_vfs_objects_also_with_every_option_off(self):
        """A line that disappears keeps its old value in a section in use (ADR-0088): turning an
        option off must change a line, never only remove one."""
        assert option_lines(smbconf.render_share(share())) == ["smb encrypt = default", "vfs objects ="]

    def test_encryption_required(self):
        assert option_lines(smbconf.render_share(share(encryption_required=True))) == [
            "smb encrypt = required", "vfs objects ="]

    def test_time_machine_loads_fruit_and_marks_the_share(self):
        text = smbconf.render_config([share(time_machine=True)])
        assert option_lines(text.split("\n\n")[2]) == [
            "smb encrypt = default", "vfs objects = catia fruit streams_xattr", "fruit:time machine = yes"]

    def test_while_a_time_machine_share_exists_every_share_loads_fruit_and_global_has_its_settings(self):
        """macOS negotiates Apple's SMB2 extensions (AAPL) on its first tree connect: a Mac that
        opens a share without fruit first cannot use the Time Machine share in that session."""
        text = smbconf.render_config([share("plain", "/mnt/pools/data/plain"), share(time_machine=True)])
        glob, plain, tm = text.split("\n\n")[1:]
        assert option_lines(plain) == ["smb encrypt = default", "vfs objects = catia fruit streams_xattr"]
        assert "fruit:time machine = yes" in lines(tm)
        assert lines(glob)[-2:] == ["fruit:aapl = yes", "fruit:nfs_aces = no"]

    def test_without_a_time_machine_share_nothing_loads_fruit(self):
        text = smbconf.render_config([share("plain", "/mnt/pools/data/plain"), share(encryption_required=True)])
        assert "fruit" not in text
        assert lines(text).count("vfs objects =") == 2

    def test_previous_versions_list_the_snapshots_of_the_pool_or_volume(self):
        rendered = smbconf.render_share(share(snapshot_root="/mnt/pools/data"))
        assert option_lines(rendered) == [
            "smb encrypt = default",
            "vfs objects = shadow_copy2 btrfs",
            "shadow:mountpoint = /mnt/pools/data",
            "shadow:snapdir = .snapshots",
            r"shadow:snapprefix = ^\(snap\|auto\)$",
            "shadow:delimiter = _",
            "shadow:format = _%Y%m%d_%H%M%S",
        ]

    def test_the_options_compose_one_vfs_objects_line(self):
        text = smbconf.render_config([share(encryption_required=True, time_machine=True,
                                            snapshot_root="/mnt/pools/data")])
        found = lines(text.split("\n\n")[2])
        assert [x for x in found if x.startswith("vfs objects")] == [
            "vfs objects = catia fruit streams_xattr shadow_copy2 btrfs"]
        assert "smb encrypt = required" in found and "fruit:time machine = yes" in found
        assert found[-6:] == [f"{k} = {v}" for k, v in smbconf._FIXED_SHARE_LINES]

    def test_an_unchanged_state_renders_byte_identical_text(self):
        shares = [share("b", "/mnt/pools/data/b", time_machine=True),
                  share("a", "/mnt/pools/data/a", encryption_required=True, snapshot_root="/mnt/pools/data")]
        assert smbconf.render_config(shares).encode() == smbconf.render_config(list(reversed(shares))).encode()

    def test_an_unsafe_snapshot_root_is_never_written(self):
        with pytest.raises(smb.SambaConfigError):
            smbconf.render_share(share(snapshot_root="/mnt/pools/data\n[x]"))

    def test_the_global_lines_of_time_machine_are_unos_own(self):
        parsed = smbconf.parse_smb_conf(smbconf.render_config([share(time_machine=True)]))
        assert smbconf.global_restrictions(parsed) == []
        assert all(smbconf.is_unos_global_line(o) for o in parsed.global_options)

    def test_turning_encryption_on_closes_the_open_connections_and_off_does_not(self):
        plain = smbconf.render_config([share()])
        required = smbconf.render_config([share(encryption_required=True)])
        assert smbconf.shrunk_shares(plain, required) == ["shared"]
        assert smbconf.shrunk_shares(required, plain) == []

    def test_turning_previous_versions_off_closes_the_open_connections_and_on_does_not(self):
        """smbd loads a tree connect's vfs modules, and shadow_copy2 reads its settings, at the tree
        connect: after a reload without them an open connection still lists and opens the snapshots."""
        listed = smbconf.render_config([share(snapshot_root="/mnt/pools/data")])
        plain = smbconf.render_config([share()])
        assert smbconf.shrunk_shares(listed, plain) == ["shared"]
        assert smbconf.shrunk_shares(plain, listed) == []
        assert smbconf.shrunk_shares(listed, listed) == []
        # Other snapshots (another pool or volume) are a shrink too.
        other = smbconf.render_config([share(snapshot_root="/mnt/pools/data/media")])
        assert smbconf.shrunk_shares(listed, other) == ["shared"]
        # Time Machine on or off keeps the snapshots' modules and lines.
        both = smbconf.render_config([share(time_machine=True, snapshot_root="/mnt/pools/data")])
        assert smbconf.shrunk_shares(listed, both) == [] and smbconf.shrunk_shares(both, listed) == []
        # A file written by hand: shadow_copy2 loaded from [global] counts as well.
        by_hand = "[global]\n vfs objects = shadow_copy2\n[x]\n path = /x\n valid users = alice\n"
        assert smbconf.shrunk_shares(by_hand, by_hand.replace("shadow_copy2", "recycle")) == ["x"]

    def test_time_machine_shares_of_a_file(self):
        text = smbconf.render_config([share("a", "/mnt/pools/data/a", time_machine=True),
                                      share("b", "/mnt/pools/data/b")])
        assert smbconf.time_machine_shares(text) == {"a"}
        assert smbconf.time_machine_shares("") == set()

    def test_a_time_machine_share_that_is_not_available_is_not_announced(self):
        """smbd registers _adisk._tcp only for the shares it serves (lp_snum_ok: available = yes)."""
        text = smbconf.render_config([share("a", "/mnt/pools/data/a", time_machine=True),
                                      share("b", "/mnt/pools/data/b", readers=(), time_machine=True)])
        assert "available = no" in text.split("\n\n")[3]
        assert smbconf.time_machine_shares(text) == {"a"}


# --- the share API ----------------------------------------------------------------------------------


@pytest.fixture
def api(request):
    """The share API of test_share_api.py: its client, paths, database and host."""
    return request.getfixturevalue("share_api")



def modules(api, missing=()):
    """The host's answer to the check of the vfs modules: ``missing`` are not installed."""
    api.host.fail[("sh", "-c")] = HostResult(0, "".join(f"{name}\n" for name in missing), "")


def options_rows(api):
    return api.rows("SELECT share_id, encryption_required, time_machine, previous_versions FROM smb_share_options")


def flags(body):
    return (body["encryption_required"], body["time_machine"], body["previous_versions"])


class TestStored:
    def test_every_option_is_off_by_default_and_needs_no_row(self, api):
        response = api.create(access=access((ALICE, "write")))
        assert response.status_code == 200, response.text
        assert flags(response.json()) == (False, False, False)
        assert options_rows(api) == []
        assert option_lines(section(api, "docs")) == ["smb encrypt = default", "vfs objects ="]
        assert not any(cmd[:2] == ["sh", "-c"] for cmd in api.host.calls)  # no module check

    def test_a_share_created_with_every_option(self, api):
        modules(api)
        response = api.create(access=access((ALICE, "write")), encryption_required=True, time_machine=True,
                              previous_versions=True)
        assert response.status_code == 200, response.text
        assert flags(response.json()) == (True, True, True)
        assert options_rows(api) == [(1, 1, 1, 1)]
        found = lines(section(api, "docs"))
        assert "smb encrypt = required" in found and "fruit:time machine = yes" in found
        assert "vfs objects = catia fruit streams_xattr shadow_copy2 btrfs" in found
        assert f"shadow:mountpoint = {api.pool}" in found
        assert response.json()["not_rendered"] == {}
        check = [cmd for cmd in api.host.calls if cmd[:2] == ["sh", "-c"]]
        assert check and check[0][2] == smb.VFS_CHECK and check[0][3] == "sh"
        assert set(check[0][4:]) == set(FRUIT + SNAPSHOTS)
        (entry,) = api.audit_entries()
        assert entry["details"] == (f"Created share docs at {api.pool}/docs; access: alice=write; "
                                    "encryption required, Time Machine, Previous Versions")

    def test_patch_turns_options_on_and_off_and_the_row_goes_with_the_last(self, api):
        modules(api)
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        on = api.patch("docs", expected_version=1, time_machine=True)
        assert on.status_code == 200, on.text
        assert (flags(on.json()), on.json()["version"]) == ((False, True, False), 2)
        assert options_rows(api) == [(1, 0, 1, 0)]
        same = api.patch("docs", time_machine=True)
        assert same.json()["version"] == 2  # nothing changed: no write
        off = api.patch("docs", time_machine=False, encryption_required=True)
        assert (flags(off.json()), off.json()["version"]) == ((True, False, False), 3)
        assert api.patch("docs", encryption_required=False).status_code == 200
        assert options_rows(api) == []
        assert option_lines(section(api, "docs")) == ["smb encrypt = default", "vfs objects ="]
        details = [e["details"] for e in api.audit_entries()][1:]
        assert details == ["Changed share docs: Time Machine on",
                           "Changed share docs: encryption required on; Time Machine off",
                           "Changed share docs: encryption required off"]

    def test_null_is_refused(self, api):
        assert api.create().status_code == 200
        assert api.patch("docs", time_machine=None).status_code == 422

    def test_delete_removes_the_options_with_the_share(self, api):
        modules(api)
        assert api.create(time_machine=True).status_code == 200
        assert api.client.delete(f"{API}/smb/docs").status_code == 200
        assert options_rows(api) == [] and api.rows("SELECT * FROM smb_shares") == []

    def test_a_new_share_never_takes_the_options_of_a_deleted_one(self, api):
        """SQLite reuses the highest rowid: an orphan row would attach to the next share."""
        api.sql("INSERT INTO smb_share_options (share_id, encryption_required, time_machine, previous_versions) "
                "VALUES (1, 1, 1, 1)")
        response = api.create()
        assert response.status_code == 200, response.text
        assert flags(response.json()) == (False, False, False)
        assert options_rows(api) == []


class TestRefused:
    def test_a_guest_share_cannot_require_encryption(self, api):
        response = api.create(guest_ok=True, encryption_required=True)
        assert (response.status_code, response.json()["detail"]) == (400, service.GUEST_ENCRYPTION)
        assert api.rows("SELECT * FROM smb_shares") == []
        assert api.create(encryption_required=True).status_code == 200
        response = api.patch("docs", guest_ok=True)
        assert (response.status_code, response.json()["detail"]) == (400, service.GUEST_ENCRYPTION)

    @pytest.mark.parametrize("option, needed", [("time_machine", FRUIT), ("previous_versions", SNAPSHOTS)])
    def test_an_option_whose_vfs_modules_the_host_lacks(self, api, option, needed):
        modules(api, missing=needed[-1:])
        response = api.create(**{option: True})
        assert response.status_code == 400
        assert response.json()["detail"] == service.modules_problem(option, [needed[-1]])
        assert "samba-vfs-modules" in response.json()["detail"]
        assert api.rows("SELECT * FROM smb_shares") == []

    def test_a_module_check_that_cannot_run_changes_nothing(self, api):
        api.host.fail[("sh", "-c")] = HostResult(3, "", "smbd: not found")
        assert api.create().status_code == 200
        response = api.patch("docs", time_machine=True)
        assert response.status_code == 500
        assert response.json()["detail"].startswith("Samba's vfs modules could not be checked")
        assert options_rows(api) == []

    def test_previous_versions_where_no_snapshot_of_unos_holds_the_folder(self, api, monkeypatch):
        modules(api)
        nested = api.pool / "media" / "restore_20261112_100000"
        nested.mkdir(parents=True)
        inode = os.stat(nested).st_ino
        monkeypatch.setattr(service, "_crosses", lambda parent, child: "subvolume" if child.st_ino == inode else None)
        assert api.create("old", "media/restore_20261112_100000/photos").status_code == 200
        response = api.patch("old", previous_versions=True)
        assert response.status_code == 400
        assert response.json()["detail"] == service.no_snapshots_problem(f"{api.pool}/media/restore_20261112_100000")

    def test_moving_a_share_with_previous_versions_into_a_nested_subvolume_is_refused(self, api, monkeypatch):
        modules(api)
        assert api.create(access=access((ALICE, "write")), previous_versions=True).status_code == 200
        nested = api.pool / "media" / "restore_20261112_100000"
        nested.mkdir(parents=True)
        inode = os.stat(nested).st_ino
        monkeypatch.setattr(service, "_crosses", lambda parent, child: "subvolume" if child.st_ino == inode else None)
        response = api.patch("docs", path=f"{nested}/photos")
        assert (response.status_code, response.json()["detail"]) == (400, service.no_snapshots_problem(str(nested)))
        assert api.rows("SELECT path, version FROM smb_shares") == [(f"{api.pool}/docs", 1)]

    def test_a_share_in_a_volume_lists_the_volume_s_snapshots(self, api, monkeypatch):
        modules(api)
        volumes = [api.pool / "media", api.pool / "music"]
        for volume in volumes:
            volume.mkdir()
        inodes = {os.stat(volume).st_ino for volume in volumes}
        monkeypatch.setattr(service, "_crosses", lambda parent, child: "subvolume" if child.st_ino in inodes else None)
        for name, folder, volume in (("media", "media", volumes[0]), ("rock", "music/rock", volumes[1])):
            response = api.create(name, folder, previous_versions=True)
            assert response.status_code == 200, response.text
            assert f"shadow:mountpoint = {volume}" in lines(section(api, name))


class TestApplied:
    def test_a_change_of_the_time_machine_shares_restarts_avahi_daemon_after_the_reload(self, api):
        """smbd registers _adisk._tcp for its Time Machine shares when it starts and when
        avahi-daemon restarts, never at a reload."""
        modules(api)
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.calls.clear()
        assert api.patch("docs", time_machine=True).status_code == 200
        calls = [cmd for cmd in api.host.calls if cmd[0] in ("smbcontrol", "systemctl", "timeout")]
        assert calls == [["smbcontrol", "all", "reload-config"], AVAHI_RESET, AVAHI]
        api.host.calls.clear()
        assert api.patch("docs", comment="Backups").status_code == 200
        assert AVAHI not in api.host.calls
        assert api.patch("docs", time_machine=False).status_code == 200
        assert AVAHI in api.host.calls

    def test_a_time_machine_share_that_gets_access_is_announced(self, api):
        """Without access a share is not available, and smbd does not announce it: the access that
        makes it available changes the Time Machine shares that smbd announces."""
        modules(api)
        assert api.create(time_machine=True).status_code == 200
        api.host.calls.clear()
        assert api.patch("docs", access=access((ALICE, "write"))).status_code == 200
        assert AVAHI in api.host.calls
        api.host.calls.clear()
        assert api.patch("docs", access=[]).status_code == 200
        assert AVAHI in api.host.calls

    def test_a_failed_restart_of_avahi_daemon_is_a_warning_and_a_problem_until_it_works(self, api):
        modules(api)
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.fail[tuple(AVAHI)] = HostResult(5, "", "Unit avahi-daemon.service not found.")
        response = api.patch("docs", time_machine=True)
        assert response.status_code == 200
        warning = service.advertise_warning("Unit avahi-daemon.service not found.")
        assert response.json()["warnings"] == [warning]
        assert api.client.get(f"{API}/smb-status").json()["problems"] == [warning]
        del api.host.fail[tuple(AVAHI)]
        api.host.calls.clear()
        response = api.patch("docs", comment="again")
        assert (response.status_code, response.json()["warnings"]) == (200, [])
        assert [cmd for cmd in api.host.calls if cmd in (AVAHI_RESET, AVAHI)] == [AVAHI_RESET, AVAHI]
        assert api.client.get(f"{API}/smb-status").json()["problems"] == []

    def test_a_start_that_fails_after_the_restart_was_queued_is_a_warning(self, api):
        """systemctl waits for the restart's job (no --no-block): a start that fails, as one that
        systemd's start limit refuses after a few quick changes, is reported, not taken as done.
        The next apply resets the unit's failed state and start limit first (AVAHI_RESET)."""
        modules(api)
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        refused = ("Job for avahi-daemon.service failed because start of the service was attempted too often.")
        api.host.fail[tuple(AVAHI)] = HostResult(1, "", refused)
        response = api.patch("docs", time_machine=True)
        assert response.json()["warnings"] == [service.advertise_warning(refused)]
        assert service.last_apply().advertise_pending

    def test_a_restart_of_avahi_daemon_that_does_not_end_is_a_warning(self, api):
        modules(api)
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.fail[tuple(AVAHI)] = HostResult(124, "", "")
        response = api.patch("docs", time_machine=True)
        assert response.json()["warnings"] == [service.advertise_warning("systemctl did not end within 60 s")]

    def test_avahi_daemon_is_restarted_only_after_a_reload_that_reached_smbd(self, api):
        """smbd registers the Time Machine shares of the configuration it has loaded."""
        modules(api)
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.fail[("smbcontrol",)] = HostResult(1, "", "no smbd")
        api.host.fail[("systemctl", "reload")] = HostResult(1, "", "not active")
        api.host.calls.clear()
        response = api.patch("docs", time_machine=True)
        assert response.json()["warnings"][0].startswith("Samba did not reload smb.conf")
        assert AVAHI not in api.host.calls and AVAHI_RESET not in api.host.calls
        del api.host.fail[("smbcontrol",)], api.host.fail[("systemctl", "reload")]
        api.host.calls.clear()
        response = api.patch("docs", comment="again")
        assert (response.status_code, response.json()["warnings"]) == (200, [])
        assert AVAHI in api.host.calls

    def test_turning_encryption_on_closes_the_share_s_open_connections(self, api):
        assert api.create(access=access((ALICE, "write"))).status_code == 200
        api.host.calls.clear()
        assert api.patch("docs", encryption_required=True).status_code == 200
        assert api.host.closed() == ["docs"]
        api.host.calls.clear()
        assert api.patch("docs", encryption_required=False).status_code == 200
        assert api.host.closed() == []

    def test_turning_previous_versions_off_closes_the_share_s_open_connections(self, api):
        modules(api)
        assert api.create(access=access((ALICE, "write")), previous_versions=True).status_code == 200
        api.host.calls.clear()
        assert api.patch("docs", previous_versions=False).status_code == 200
        assert api.host.closed() == ["docs"]
        api.host.calls.clear()
        assert api.patch("docs", previous_versions=True).status_code == 200
        assert api.host.closed() == []

    def test_a_stored_option_whose_modules_are_gone_is_not_rendered_and_the_status_says_why(self, api):
        modules(api)
        assert api.create(access=access((ALICE, "write")), time_machine=True).status_code == 200
        modules(api, missing=["fruit"])
        assert api.patch("docs", comment="x").status_code == 200
        assert "fruit" not in api.conf.read_text()
        problems = api.client.get(f"{API}/smb-status").json()["problems"]
        assert f"Share docs: Time Machine is off: {service.modules_problem('time_machine', ['fruit'])}" in problems
        body = api.get("docs").json()
        assert flags(body) == (False, True, False)  # as stored
        assert body["not_rendered"] == {"time_machine": service.modules_problem("time_machine", ["fruit"])}

    def test_previous_versions_whose_modules_are_gone_are_not_rendered_and_the_status_says_why(self, api):
        modules(api)
        assert api.create(access=access((ALICE, "write")), previous_versions=True).status_code == 200
        modules(api, missing=["shadow_copy2"])
        assert api.patch("docs", comment="x").status_code == 200
        assert "shadow" not in api.conf.read_text()
        assert option_lines(section(api, "docs")) == ["smb encrypt = default", "vfs objects ="]
        reason = service.modules_problem("previous_versions", ["shadow_copy2"])
        problems = api.client.get(f"{API}/smb-status").json()["problems"]
        assert f"Share docs: Previous Versions are off: {reason}" in problems
        assert api.get("docs").json()["not_rendered"] == {"previous_versions": reason}

    def test_a_restored_guest_share_that_requires_encryption_admits_no_guest(self, api):
        """smbd refuses guests where encryption is required: a row with both (a restored database,
        ADR-0144) is rendered without guests, so that [global] keeps mandatory signing (ADR-0339)."""
        assert api.create(access=access((ALICE, "write")), guest_ok=True).status_code == 200
        api.sql("INSERT INTO smb_share_options (share_id, encryption_required, time_machine, previous_versions) "
                "VALUES (1, 1, 0, 0)")
        assert api.patch("docs", comment="x").status_code == 200
        assert smbconf.render_global(False) in api.conf.read_text()
        assert "guest ok = no" in lines(section(api, "docs"))
        status = api.client.get(f"{API}/smb-status").json()
        assert f"Share docs: guest access is off: {service.GUEST_ENCRYPTION}" in status["problems"]
        assert status["guest_shares"] == []
        body = api.get("docs").json()
        assert (body["guest_ok"], body["encryption_required"]) == (True, True)  # as stored
        assert body["not_rendered"] == {"guest_ok": service.GUEST_ENCRYPTION}

    def test_a_restored_folder_without_snapshots_is_rendered_without_previous_versions(self, api, monkeypatch):
        modules(api)
        assert api.create(access=access((ALICE, "write")), previous_versions=True).status_code == 200
        inode = os.stat(api.pool / "docs").st_ino
        monkeypatch.setattr(service, "_crosses", lambda parent, child: "mount" if child.st_ino == inode else None)
        assert api.patch("docs", comment="x").status_code == 200
        assert "shadow_copy2" not in api.conf.read_text()
        problems = api.client.get(f"{API}/smb-status").json()["problems"]
        assert (f"Share docs: Previous Versions are off: {service.no_snapshots_problem(str(api.pool / 'docs'))}"
                in problems)
        assert api.get("docs").json()["not_rendered"] == {
            "previous_versions": service.no_snapshots_problem(str(api.pool / "docs"))}


class TestRestoreAndImport:
    def test_the_restore_drops_the_options_of_shares_it_drops_and_orphans(self, tmp_path, monkeypatch):
        import sqlite3

        from sqlalchemy import create_engine

        from src.database import Base
        from src.storage import pools

        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO smb_shares (id, name, path) VALUES (1, 'a', '/mnt/pools/data/a')")
        conn.executemany("INSERT INTO smb_share_options (share_id, encryption_required, time_machine, "
                         "previous_versions) VALUES (?, 1, 0, 0)", [(1,), (7,)])
        monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
        service.prepare_restored_shares(conn, authoritative=True)
        assert conn.execute("SELECT share_id FROM smb_share_options").fetchall() == [(1,)]
        service.prepare_restored_shares(conn, authoritative=False)
        assert conn.execute("SELECT * FROM smb_share_options").fetchall() == []
        conn.close()

    def test_the_restore_turns_guest_access_off_where_encryption_is_required(self, tmp_path, monkeypatch):
        """smbd refuses guests where encryption is required: the restore drops guest access with a note,
        as the import does."""
        import json
        import sqlite3

        from sqlalchemy import create_engine

        from src.database import Base
        from src.storage import pools

        from src.security import validators

        base = tmp_path / "pools"
        (base / "data").mkdir(parents=True)
        for settings in {id(smb.settings): smb.settings, id(validators.settings): validators.settings}.values():
            monkeypatch.setattr(settings, "pools_base_path", str(base))
        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        conn.executemany("INSERT INTO smb_shares (id, name, path, guest_ok, version) VALUES (?, ?, ?, 1, ?)",
                         [(1, "a", f"{base}/data/a", 4), (2, "b", f"{base}/data/b", 1)])
        conn.executemany("INSERT INTO smb_share_options (share_id, encryption_required, time_machine, "
                         "previous_versions) VALUES (?, ?, ?, 0)", [(1, 1, 0), (2, 0, 1)])
        monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
        assert service.prepare_restored_shares(conn, authoritative=True) == ["b"]
        assert conn.execute("SELECT name, guest_ok, import_notes, version FROM smb_shares ORDER BY id").fetchall() == [
            ("a", 0, json.dumps([f"Guest access was removed at the restore: {service.GUEST_ENCRYPTION}"]), 5),
            ("b", 1, None, 1),
        ]
        conn.close()

    def test_a_backup_without_the_options_table_is_restored_and_one_of_another_layout_is_refused(self, tmp_path):
        import sqlite3

        from sqlalchemy import create_engine

        from src.backup.router import BackupValidationError
        from src.database import Base

        db = tmp_path / "restored.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE smb_share_options")
        conn.commit()
        service.check_smb_tables(db)  # an older backup: every option off
        conn.execute("CREATE TABLE smb_share_options (share_id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(BackupValidationError, match="smb_share_options.time_machine"):
            service.check_smb_tables(db)

    def test_the_import_of_unos_s_own_file_keeps_every_option(self, api):
        text = smbconf.render_config([share("docs", f"{api.pool}/docs", encryption_required=True,
                                            time_machine=True, snapshot_root=str(api.pool))])
        users = [service._ImportUser(id=ALICE, username="alice", is_admin=False)]
        plan = service._plan_import(text, users, {"data"}, restored=True, source_label="smb.conf", kept=None,
                                    taken=[])
        (docs,) = plan.shares
        assert (docs.encryption_required, docs.time_machine, docs.previous_versions) == (True, True, True)
        assert docs.notes == [] and docs.access == {ALICE: ("alice", "read")}

    def test_an_option_line_that_unos_does_not_render_is_noted_also_in_unos_s_own_file(self, api):
        """Only the option lines that UNOS renders are its own (smbconf.is_unos_option_line): another
        encryption, vfs module, fruit or shadow setting written by hand into its file is not kept,
        with a note."""
        text = smbconf.render_config([share("docs", f"{api.pool}/docs")]).replace(
            "   smb encrypt = default\n   vfs objects =\n",
            "   smb encrypt = desired\n   vfs objects = recycle\n   fruit:locking = netatalk\n"
            "   fruit:time machine = no\n   shadow:snapdir = /elsewhere\n",
        )
        users = [service._ImportUser(id=ALICE, username="alice", is_admin=False)]
        plan = service._plan_import(text, users, {"data"}, restored=True, source_label="smb.conf", kept=None,
                                    taken=[])
        (docs,) = plan.shares
        assert (docs.encryption_required, docs.time_machine, docs.previous_versions) == (False, False, False)
        assert docs.notes == [f"Option '{line}' was not kept" for line in (
            "smb encrypt = desired", "vfs objects = recycle", "fruit:locking = netatalk",
            "fruit:time machine = no", "shadow:snapdir = /elsewhere")]

    def test_unos_s_own_file_takes_the_options_only_from_the_vfs_objects_line_that_unos_renders(self, api):
        """A vfs objects line edited by hand is not kept: neither are the options whose modules it
        names, and their lines are noted as not kept too."""
        rendered = smbconf.render_config([share("docs", f"{api.pool}/docs", time_machine=True,
                                                snapshot_root=str(api.pool))])
        text = rendered.replace("vfs objects = catia fruit streams_xattr shadow_copy2 btrfs",
                                "vfs objects = recycle fruit shadow_copy2")
        users = [service._ImportUser(id=ALICE, username="alice", is_admin=False)]
        plan = service._plan_import(text, users, {"data"}, restored=True, source_label="smb.conf", kept=None,
                                    taken=[])
        (docs,) = plan.shares
        assert (docs.encryption_required, docs.time_machine, docs.previous_versions) == (False, False, False)
        dropped = [line.strip() for line in text.split("\n\n")[2].splitlines()
                   if line.strip().startswith(("vfs objects", "fruit:", "shadow:"))]
        assert len(dropped) == 7
        assert docs.notes == [f"Option '{line}' was not kept" for line in dropped]

    def test_a_guest_share_of_unos_s_own_file_that_requires_encryption_keeps_no_guests(self, api):
        text = smbconf.render_config([share("docs", f"{api.pool}/docs", guest_ok=True, encryption_required=True)])
        plan = service._plan_import(text, [], {"data"}, restored=False, source_label="smb.conf", kept=None, taken=[])
        (docs,) = plan.shares
        assert (docs.guest_ok, docs.encryption_required) == (False, True)
        assert docs.notes == [f"Guest access was removed: {service.GUEST_ENCRYPTION}"]

    def test_the_import_of_a_file_written_by_hand_keeps_required_encryption_and_time_machine(self, api):
        """A file that UNOS did not render: required encryption comes back as the option, which keeps
        that restriction, so the access stays (ADR-0086 removed it); Time Machine where fruit is
        loaded; snapshots of another layout are not kept, and the vfs objects line is noted."""
        text = (
            f"[docs]\n   path = {api.pool}/docs\n   valid users = alice\n"
            "   vfs objects = fruit streams_xattr shadow_copy2\n   fruit:time machine = yes\n"
            "   smb encrypt = required\n"
        )
        users = [service._ImportUser(id=ALICE, username="alice", is_admin=False)]
        plan = service._plan_import(text, users, {"data"}, restored=False, source_label="smb.conf", kept=None,
                                    taken=[])
        (docs,) = plan.shares
        assert (docs.encryption_required, docs.time_machine, docs.previous_versions) == (True, True, False)
        assert docs.access == {ALICE: ("alice", "read")}
        assert docs.notes == ["Option 'vfs objects = fruit streams_xattr shadow_copy2' was not kept"]

    def test_time_machine_of_a_file_written_by_hand_needs_fruit_loaded(self, api):
        """vfs objects of [global] before the section count; fruit:time machine without fruit served nothing."""
        text = (
            "[global]\n   vfs objects = catia fruit streams_xattr\n"
            f"[a]\n   path = {api.pool}/a\n   valid users = alice\n   fruit:time machine = yes\n"
            f"[b]\n   path = {api.pool}/b\n   valid users = alice\n   vfs objects = recycle\n"
            "   fruit:time machine = yes\n"
        )
        users = [service._ImportUser(id=ALICE, username="alice", is_admin=False)]
        plan = service._plan_import(text, users, {"data"}, restored=False, source_label="smb.conf", kept=None,
                                    taken=[])
        a, b = plan.shares
        assert (a.time_machine, a.notes) == (True, [])
        assert (b.time_machine, b.notes) == (False, ["Option 'vfs objects = recycle' was not kept",
                                                     "Option 'fruit:time machine = yes' was not kept"])


def stat_of(device: int, inode: int) -> os.stat_result:
    return os.stat_result((0o40755, inode, device, 2, 0, 0, 4096, 0, 0, 0))


class TestCrosses:
    """service._crosses, which the tests of snapshot_root through the share API replace: a folder
    on another device starts a file tree of its own, a btrfs subvolume when its inode is 256."""

    def test_a_folder_on_its_parent_s_device_starts_no_tree(self):
        assert service._crosses(stat_of(40, 300), stat_of(40, 256)) is None

    def test_a_btrfs_subvolume(self):
        assert service._crosses(stat_of(40, 300), stat_of(41, service.BTRFS_SUBVOLUME_INODE)) == "subvolume"

    def test_another_file_system(self):
        assert service._crosses(stat_of(40, 300), stat_of(41, 2)) == "mount"


def test_unos_s_snapshot_names_are_the_ones_previous_versions_list():
    """shadow_copy2 splits a snapshot's name at the first shadow:delimiter, matches the part before it
    with shadow:snapprefix (a POSIX basic regular expression) and reads the rest, delimiter included,
    with shadow:format: the names of the Snapshots page (snap_) and of the Scheduler (auto_)."""
    import re
    from datetime import datetime, timezone

    from src.snapshots.router import AUTO_SNAPSHOT_PREFIX, RESTORE_PREFIX, SNAPSHOT_PREFIX, utc_stamp

    settings = dict(smbconf._SNAPSHOT_LINES)
    prefix = re.compile(settings["shadow:snapprefix"].replace("\\(", "(").replace("\\)", ")").replace("\\|", "|"))
    before = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    for name in (SNAPSHOT_PREFIX + utc_stamp(), AUTO_SNAPSHOT_PREFIX + utc_stamp()):
        head, delimiter, rest = name.partition(settings["shadow:delimiter"])
        assert prefix.search(head), name
        taken = datetime.strptime(delimiter + rest, settings["shadow:format"])
        assert 0 <= (taken - before).total_seconds() < 5, name
    assert not prefix.search(RESTORE_PREFIX.rstrip("_"))  # writable copies are no snapshots


def test_the_status_problem_text_of_missing_modules_names_the_package():
    assert service.modules_problem("previous_versions", ["btrfs", "shadow_copy2"]) == (
        "Samba on this NAS lacks the vfs modules btrfs, shadow_copy2 that Previous Versions need: "
        "install the package samba-vfs-modules on the host"
    )


def test_the_check_of_the_vfs_modules_reads_smbd_s_modules_folder(monkeypatch):
    import src.host

    assert "smbd -b" in smb.VFS_CHECK and "/vfs/" in smb.VFS_CHECK
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return HostResult(0, "fruit\nother\n", "")

    monkeypatch.setattr(src.host, "run_on_host", run)
    assert smb.missing_vfs_modules(["catia", "fruit"]) == ["fruit"]
    assert calls == [["sh", "-c", smb.VFS_CHECK, "sh", "catia", "fruit"]]
    monkeypatch.setattr(src.host, "run_on_host", lambda cmd, **kwargs: HostResult(3, "", "smbd: not found"))
    with pytest.raises(smb.SambaConfigError, match="Samba's vfs modules could not be checked: smbd: not found"):
        smb.missing_vfs_modules(["fruit"])


def test_the_e2e_fakes_answer_with_the_servers_texts():
    """webui/tests/shares-fakes.ts answers with these texts word for word (ADR-0189)."""
    import re
    from pathlib import Path

    fakes = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "shares-fakes.ts").read_text(encoding="utf-8")
    assert f'"{service.GUEST_ENCRYPTION}"' in fakes
    assert f"'{service.modules_problem('time_machine', ['fruit'])}'" in fakes
    start = fakes.index("export const NO_SNAPSHOTS =")
    pieces = re.findall(r"'([^']*)'|\"([^\"]*)\"", fakes[start:fakes.index(";", start)])
    assert "".join(a or b for a, b in pieces) == service.no_snapshots_problem(
        "/mnt/pools/data/media/restore_20261112_100000")

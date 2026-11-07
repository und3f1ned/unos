"""The SMB account rules of a database restore (spec 4.3, P2), on sqlite3 files:
accounts.snapshot_for_restore() reads the live database before the restore, and
accounts.prepare_restored_accounts() proves or revokes every restored account
inside the restore's transaction on the extracted database, before it replaces
the live one."""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.auth.models import APIToken, User
from src.database import Base
from src.security.models import LoginAttempt
from src.shares import accounts, interfaces
from src.shares.models import SmbAccount, SmbState

from .test_smb_accounts import ALICE_PW, BOB_PW, World

HASH_A = "$2b$04$" + "a" * 53  # bcrypt-shaped placeholders; the proof compares the strings
HASH_B = "$2b$04$" + "b" * 53
SET_AT = datetime(2026, 11, 1, 9, 0)
IMPORTED = datetime(2026, 11, 5, 19, 41)


def make_db(path: Path, users=(), rows=(), next_uid: int | None = 70000, imported: bool = True) -> Path:
    """A UNOS database: (id, username, hash, admin) users and smb_accounts rows (dicts)."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for user_id, username, hashed, admin in users:
            session.add(User(id=user_id, username=username, hashed_password=hashed, is_admin=admin))
        session.flush()
        for row in rows:
            values = {"enabled": True, "password_state": "set", "password_set_at": SET_AT, **row}
            session.add(SmbAccount(**values))
        if next_uid is not None:
            session.add(SmbState(id=1, next_uid=next_uid, shares_imported_at=IMPORTED if imported else None,
                                 pending_import_source=None if imported else "live"))
        session.commit()
    engine.dispose()
    return path


def account(user_id, name, uid, **values) -> dict:
    return {"user_id": user_id, "linux_name": name, "uid": uid, **values}


def prepare(path: Path, snapshot: interfaces.RestoreSnapshot, authoritative: bool) -> list[str]:
    """What _restore_locked does: one transaction on the extracted file."""
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            messages = accounts.prepare_restored_accounts(conn, snapshot, authoritative)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return messages


def rows(path: Path) -> dict[int, tuple]:
    with closing(sqlite3.connect(path)) as conn:
        return {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT user_id, linux_name, uid, enabled, password_state, password_set_at FROM smb_accounts"
            )
        }


def next_uid(path: Path):
    with closing(sqlite3.connect(path)) as conn:
        found = conn.execute("SELECT next_uid FROM smb_state WHERE id = 1").fetchone()
        return None if found is None else found[0]


def raw(path: Path, *statements: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = 1")
        for sql in statements:
            conn.execute(sql)
        conn.commit()


@pytest.fixture
def live(tmp_path) -> Path:
    """The live database before the restore: alice active (70000), bob allowed, carol active (70002)."""
    return make_db(
        tmp_path / "live.db",
        users=[(1, "admin", HASH_B, True), (2, "alice", HASH_A, False), (3, "bob", HASH_B, False),
               (4, "carol", HASH_A, False)],
        rows=[account(2, "alice", 70000), account(3, "bob", None, password_state="needed", password_set_at=None),
              account(4, "carol", 70002)],
        next_uid=70003,
    )


class TestSnapshot:
    def test_snapshot_of_the_live_database(self, live):
        snapshot = accounts.snapshot_for_restore(live)

        assert snapshot.next_uid == 70003
        assert sorted((p.username, p.linux_name, p.uid, p.enabled, p.password_state, p.is_admin, p.active)
                      for p in snapshot.accounts) == [
            ("alice", "alice", 70000, True, "set", False, True),
            ("bob", "bob", None, True, "needed", False, False),
            ("carol", "carol", 70002, True, "set", False, True),
        ]
        assert {p.username: p.hashed_password for p in snapshot.accounts}["alice"] == HASH_A

    def test_a_database_without_smb_tables(self, tmp_path):
        path = tmp_path / "old.db"
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine, tables=[User.__table__, APIToken.__table__, LoginAttempt.__table__])
        with Session(engine) as session:
            session.add(User(username="alice", hashed_password=HASH_A, is_admin=False))
            session.commit()
        engine.dispose()

        assert accounts.snapshot_for_restore(path) == interfaces.RestoreSnapshot()

    def test_no_file(self, tmp_path):
        missing = tmp_path / "missing.db"

        assert accounts.snapshot_for_restore(missing) == interfaces.RestoreSnapshot()
        assert accounts.snapshot_for_restore(None) == interfaces.RestoreSnapshot()
        assert accounts.snapshot_for_restore(Path(":memory:")) == interfaces.RestoreSnapshot()
        assert not missing.exists()  # opened read-only: never created

    def test_values_of_the_wrong_type_are_skipped(self, live):
        raw(live,
            "UPDATE smb_accounts SET uid = 'abc' WHERE user_id = 2",
            "UPDATE smb_state SET next_uid = 'x' WHERE id = 1")

        snapshot = accounts.snapshot_for_restore(live)

        assert sorted(p.username for p in snapshot.accounts) == ["bob", "carol"]
        assert snapshot.next_uid == 70000

    def test_the_live_database_is_not_changed(self, live):
        before = live.read_bytes()
        accounts.snapshot_for_restore(live)
        assert live.read_bytes() == before


class TestProof:
    def test_equal_hash_username_linux_name_and_uid_stays_set(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db",
                           users=[(1, "admin", HASH_B, True), (2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70000)])

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        assert messages == []
        assert rows(restored)[2] == ("alice", 70000, 1, "set", "2026-11-01 09:00:00.000000")

    @pytest.mark.parametrize("users, row", [
        pytest.param([(2, "alice", HASH_B, False)], account(2, "alice", 70000), id="other-hash"),
        pytest.param([(2, "alice", HASH_A, False)], account(2, "alice", 70001), id="other-uid"),
        pytest.param([(2, "alicia", HASH_A, False)], account(2, "alicia", 70000), id="other-username"),
        pytest.param([(2, "alice", HASH_A, False)], account(2, "alicia", 70000), id="other-linux-name"),
        pytest.param([(2, "alice", HASH_A, True)], account(2, "alice", 70000), id="administrator"),
    ])
    def test_anything_else_needs_the_password_again(self, live, tmp_path, users, row):
        restored = make_db(tmp_path / "restored.db", users=users, rows=[row])

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        restored_row = rows(restored)[2]
        assert restored_row[3:] == ("needed", None)
        assert messages == [f"SMB must be turned on again for {users[0][1]}"]

    def test_an_account_that_was_not_active_before(self, live, tmp_path):
        # bob was only allowed in the live database: his restored 'set' row is not proven.
        restored = make_db(tmp_path / "restored.db", users=[(3, "bob", HASH_B, False)],
                           rows=[account(3, "bob", 70001)])

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        assert rows(restored)[3][3] == "needed"
        assert messages == ["SMB must be turned on again for bob"]

    def test_rows_that_need_a_password_already_give_no_message(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(3, "bob", HASH_B, False)],
                           rows=[account(3, "bob", None, password_state="needed", password_set_at=None)])

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=True) == []
        assert rows(restored)[3] == ("bob", None, 1, "needed", None)

    def test_an_empty_snapshot_proves_nothing(self, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70000)])

        assert prepare(restored, interfaces.RestoreSnapshot(), authoritative=True) == [
            "SMB must be turned on again for alice"
        ]
        assert rows(restored)[2][3] == "needed"

    def test_hostile_values(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db",
                           users=[(2, "alice", HASH_A, False), (3, "bob", HASH_B, False), (5, "eve", HASH_A, False)],
                           rows=[account(2, "alice", 70000), account(3, "bob", 70001), account(5, "eve", 70005)])
        raw(restored,
            "UPDATE smb_accounts SET uid = 'abc' WHERE user_id = 2",
            "UPDATE smb_accounts SET password_state = 'x' WHERE user_id = 3",
            "UPDATE smb_accounts SET password_set_at = 'garbage', created_at = 12, updated_at = 'later' "
            "WHERE user_id = 5",
            "UPDATE smb_accounts SET password_state = 'needed' WHERE user_id = 5")

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        assert messages == ["SMB must be turned on again for alice"]
        found = rows(restored)
        assert found[2][3] == "needed"   # a text uid proves nothing
        assert found[3][3] == "needed"   # an unknown password_state becomes 'needed'
        assert found[5][3:] == ("needed", None)
        # Every row can be read by the ORM afterwards (a bad DateTime would make each load fail).
        engine = create_engine(f"sqlite:///{restored}")
        with Session(engine) as session:
            assert len(session.scalars(select(SmbAccount)).all()) == 3
        engine.dispose()

    def test_a_row_of_a_user_that_does_not_exist(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70000), account(9, "ghost", 70009)])

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=True) == []
        assert rows(restored)[9][3] == "needed"
        assert rows(restored)[2][3] == "set"


class TestLiveUids:
    """Decision 16 (sec-api-3): a restored row never keeps a uid that a live account of
    another name holds (a backup of another UNOS installation, whose uids also start at
    70000, or a crafted archive): uid NULL, 'needed', a fresh uid at the next activation."""

    def test_the_verifiers_case(self, tmp_path):
        live = make_db(tmp_path / "live.db", users=[(1, "admin", HASH_B, True), (2, "alice", HASH_A, False)],
                       rows=[account(2, "alice", 70000)], next_uid=70001)
        restored = make_db(tmp_path / "restored.db",
                           users=[(1, "admin", HASH_B, True), (2, "alice", HASH_B, False), (3, "bob", HASH_B, False)],
                           rows=[account(2, "alice", 70001),
                                 account(3, "bob", 70000, password_state="needed", password_set_at=None)],
                           next_uid=70002)

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        found = rows(restored)
        assert found[3] == ("bob", None, 1, "needed", None)
        assert found[2] == ("alice", 70001, 1, "needed", None)  # no live account of another name has 70001
        assert messages == ["SMB must be turned on again for alice", "SMB must be turned on again for bob"]
        assert next_uid(restored) == 70002

    @pytest.mark.parametrize("users, row", [
        pytest.param([(5, "dave", HASH_A, False)], account(5, "dave", 70002), id="active-row-of-another-user"),
        pytest.param([(5, "carol", HASH_A, False)], account(5, "caroline", 70002), id="another-linux-name"),
        pytest.param([(5, "carl", HASH_A, False)], account(5, "carol", 70002), id="another-username"),
        pytest.param([(5, "dave", HASH_A, True)], account(5, "dave", 70002), id="administrator"),
    ])
    def test_a_uid_of_a_live_account_of_another_name(self, live, tmp_path, users, row):
        restored = make_db(tmp_path / "restored.db", users=users, rows=[row])

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        assert rows(restored)[5] == (row["linux_name"], None, 1, "needed", None)
        assert messages == [f"SMB must be turned on again for {users[0][1]}"]
        assert next_uid(restored) == 70003  # above carol's 70002: the fresh uid is never hers

    def test_the_same_account_keeps_its_uid(self, live, tmp_path):
        """carol under another user id (a backup taken before a delete and re-create) is still carol."""
        restored = make_db(tmp_path / "restored.db", users=[(9, "carol", HASH_A, False)],
                           rows=[account(9, "carol", 70002)])

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=True) == []
        assert rows(restored)[9] == ("carol", 70002, 1, "set", "2026-11-01 09:00:00.000000")

    def test_a_row_of_a_user_that_does_not_exist(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[], rows=[account(9, "ghost", 70000)])

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=True) == []
        assert rows(restored)[9] == ("ghost", None, 1, "needed", None)

    def test_uids_no_live_account_holds_are_kept(self, live, tmp_path):
        # bob's live row has no uid; 70001 belongs to nobody on this host.
        restored = make_db(tmp_path / "restored.db", users=[(8, "erin", HASH_B, False)],
                           rows=[account(8, "erin", 70001, password_state="needed", password_set_at=None)])

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=True) == []
        assert rows(restored)[8] == ("erin", 70001, 1, "needed", None)


class TestCarryOver:
    def test_accounts_are_carried_over_by_username(self, live, tmp_path):
        # An old backup: users only (ensure_smb_tables made empty SMB tables), not authoritative.
        restored = make_db(tmp_path / "restored.db",
                           users=[(1, "admin", HASH_B, True), (7, "alice", HASH_A, False), (8, "bob", HASH_B, False),
                                  (9, "carol", HASH_B, False)],
                           next_uid=70000, imported=False)

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=False)

        found = rows(restored)
        # alice: same hash -> 'set' with her uid (under her restored id).
        assert found[7] == ("alice", 70000, 1, "set", None)
        # bob was only allowed: carried over, still 'needed'.
        assert found[8] == ("bob", None, 1, "needed", None)
        # carol's restored password differs: 'needed', the uid stays hers.
        assert found[9] == ("carol", 70002, 1, "needed", None)
        assert messages == ["SMB must be turned on again for carol"]
        assert next_uid(restored) == 70003

    def test_no_restored_user_no_row(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(1, "admin", HASH_B, True)], imported=False)

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=False) == []
        assert rows(restored) == {}
        # The uids of the live accounts are never handed out again.
        assert next_uid(restored) == 70003

    def test_no_carry_over_into_an_authoritative_database(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(7, "alice", HASH_A, False)])

        assert prepare(restored, accounts.snapshot_for_restore(live), authoritative=True) == []
        assert rows(restored) == {}

    def test_live_accounts_keep_their_uids(self, live, tmp_path):
        """Decision 16 (sec-api-3): a restored row never takes the uid of a live account of
        another name, which owns its files on the pools. The live owner keeps it."""
        restored = make_db(tmp_path / "restored.db",
                           users=[(7, "alice", HASH_A, False), (8, "dave", HASH_B, False)],
                           rows=[account(8, "dave", 70000, password_state="needed", password_set_at=None)],
                           imported=False)

        messages = prepare(restored, accounts.snapshot_for_restore(live), authoritative=False)

        found = rows(restored)
        # alice is carried over with her uid (and her hash: proven); dave gets a fresh uid when he turns SMB on.
        assert found[7] == ("alice", 70000, 1, "set", None)
        assert found[8] == ("dave", None, 1, "needed", None)
        assert messages == ["SMB must be turned on again for dave"]
        assert next_uid(restored) == 70003

    def test_administrators_and_unusable_names_are_not_carried_over(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(7, "alice", HASH_A, True)], imported=False)

        prepare(restored, accounts.snapshot_for_restore(live), authoritative=False)

        assert rows(restored) == {}


class TestNextUid:
    @pytest.mark.parametrize("stored, expected", [
        ("70000", 70003),     # below the snapshot's value
        ("70050", 70050),     # restored value kept
        ("'x'", 70003),       # text replaced by the floor
        ("999999", 70003),    # out of range replaced by the floor
        ("80000", 80000),     # exhausted is kept
    ])
    def test_floor(self, live, tmp_path, stored, expected):
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70000)])
        raw(restored, f"UPDATE smb_state SET next_uid = {stored} WHERE id = 1")

        prepare(restored, accounts.snapshot_for_restore(live), authoritative=True)

        assert next_uid(restored) == expected

    def test_floor_from_the_restored_rows(self, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70040)], next_uid=70000)

        prepare(restored, interfaces.RestoreSnapshot(), authoritative=True)

        assert next_uid(restored) == 70041

    def test_a_missing_state_row_is_inserted(self, live, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)], next_uid=None)

        prepare(restored, accounts.snapshot_for_restore(live), authoritative=False)

        assert next_uid(restored) == 70003
        with closing(sqlite3.connect(restored)) as conn:
            assert conn.execute("SELECT shares_imported_at, pending_import_source FROM smb_state").fetchone() == (
                None, None)

    def test_a_snapshot_value_out_of_range_is_ignored(self, tmp_path):
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)], next_uid=70000)
        snapshot = interfaces.RestoreSnapshot(accounts=(), next_uid=999999)

        prepare(restored, snapshot, authoritative=True)

        assert next_uid(restored) == 70000

    @pytest.mark.parametrize("authoritative", [True, False])
    def test_the_live_next_uid_is_a_floor(self, tmp_path, authoritative):
        """tests-3: the live next_uid is the only record of the uids of accounts deleted after
        the backup (their rows and host accounts are gone). It decides even when it is above
        every uid that the restored rows and the snapshot list."""
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70000)], next_uid=70001, imported=authoritative)

        prepare(restored, interfaces.RestoreSnapshot(accounts=(), next_uid=70010), authoritative=authoritative)

        assert next_uid(restored) == 70010

    @pytest.mark.parametrize("authoritative", [True, False])
    def test_the_live_next_uid_read_by_the_snapshot(self, tmp_path, authoritative):
        # Users with the uids 70001 to 70009 were created and deleted after the backup.
        live = make_db(tmp_path / "live.db", users=[(2, "alice", HASH_A, False)],
                       rows=[account(2, "alice", 70000)], next_uid=70010)
        restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_A, False)],
                           rows=[account(2, "alice", 70000)], next_uid=70001, imported=authoritative)
        snapshot = accounts.snapshot_for_restore(live)
        assert snapshot.next_uid == 70010

        prepare(restored, snapshot, authoritative=authoritative)

        assert next_uid(restored) == 70010


def test_a_failure_rolls_back_everything(live, tmp_path, monkeypatch):
    restored = make_db(tmp_path / "restored.db", users=[(2, "alice", HASH_B, False)],
                       rows=[account(2, "alice", 70000)])
    before = rows(restored)
    snapshot = accounts.snapshot_for_restore(live)

    def broken(value):
        raise RuntimeError("disk full")

    # The proof has already set alice to 'needed' when the floor step fails.
    monkeypatch.setattr(accounts, "_valid_next_uid", broken)
    with pytest.raises(RuntimeError):
        prepare(restored, snapshot, authoritative=True)

    assert rows(restored) == before


# ---------------------------------------------------------------------------
# The whole path of decision 16: the preparation, the swap, the post-restore
# reconcile and an activation, on the FakeSambaHost
# ---------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()


def swap_in(world: World, restored: Path) -> None:
    """What _replace_database does: SQLite's backup API copies the prepared file over the live one."""
    with closing(sqlite3.connect(restored)) as source, closing(sqlite3.connect(world.path)) as target:
        source.backup(target)


def reconcile(world: World) -> interfaces.ReconcileResult:
    async def run():
        async with world.maker() as db:
            return await accounts.reconcile_accounts(db)

    return asyncio.run(run())


def useradd_uids(world: World) -> list[tuple[str, str]]:
    """(uid, name) of every useradd (argv: useradd -r -l -N -M -u <uid> ... <name>)."""
    return [(argv[6], argv[-1]) for argv in world.fake.commands("useradd")]


def archive_users(world: World, *names: str) -> list[tuple]:
    """The users of an archive: the live users' ids and hashes (so the proof can hold)."""
    return [(world.ids[name], name, world.user(name).hashed_password, name == "admin") for name in names]


class TestWholePath:
    def test_an_archive_of_another_installation(self, world, tmp_path):
        """bob had uid 70002 on the other NAS; here carol has it. carol is not in the archive, so
        the post-restore reconcile removes her host account; bob still gets a fresh uid, and alice,
        who is in the archive as she is here, keeps hers."""
        world.make_active("alice", 70000, ALICE_PW)
        world.add_user("carol", "Carol-pass-2026")
        world.make_active("carol", 70002, "Carol-pass-2026")
        snapshot = accounts.snapshot_for_restore(world.path)
        restored = make_db(
            tmp_path / "restored.db",
            users=archive_users(world, "admin", "alice", "bob"),
            rows=[account(world.ids["alice"], "alice", 70000),
                  account(world.ids["bob"], "bob", 70002, password_state="needed", password_set_at=None)],
            next_uid=70001,
        )

        assert prepare(restored, snapshot, authoritative=True) == ["SMB must be turned on again for bob"]
        swap_in(world, restored)
        result = reconcile(world)

        assert "Removed the stale host account carol (uid 70002)" in result.changes
        assert world.fake.passwd["alice"].uid == 70000 and "alice" in world.fake.passdb
        assert (world.row("alice").uid, world.row("alice").password_state) == (70000, "set")
        enabled = world.self_enable("bob", BOB_PW)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["account"]["uid"] == 70003
        assert useradd_uids(world) == [("70003", "bob")]

    def test_an_old_backup_keeps_the_live_uids(self, world, tmp_path):
        """The carry-over: alice is carried over with her uid (the live owner keeps it), the
        restored row of bob that had it gets none, and bob's activation gets a fresh one."""
        world.make_active("alice", 70000, ALICE_PW)
        snapshot = accounts.snapshot_for_restore(world.path)
        restored = make_db(
            tmp_path / "restored.db",
            users=archive_users(world, "admin", "alice", "bob"),
            rows=[account(world.ids["bob"], "bob", 70000, password_state="needed", password_set_at=None)],
            next_uid=70000, imported=False,
        )

        assert prepare(restored, snapshot, authoritative=False) == ["SMB must be turned on again for bob"]
        swap_in(world, restored)
        result = reconcile(world)

        assert result == interfaces.ReconcileResult()
        assert world.fake.commands("userdel") == [] and world.fake.commands("pdbedit", "-x") == []
        assert world.fake.passwd["alice"].uid == 70000 and "alice" in world.fake.passdb
        assert (world.row("alice").uid, world.row("alice").password_state) == (70000, "set")
        enabled = world.self_enable("bob", BOB_PW)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["account"]["uid"] == 70001
        assert useradd_uids(world) == [("70001", "bob")]

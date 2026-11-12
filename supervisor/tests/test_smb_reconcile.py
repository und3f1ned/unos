"""The account reconcile of the startup and post-restore paths (ADR-0138):
accounts.reconcile_accounts(db) makes the host match the smb_accounts rows, and
writes nothing when they already match. Host commands go to the FakeSambaHost;
service.delete_access_of_user is an AsyncMock."""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import threading
from contextlib import closing

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from src.host import HostResult
from src.shares import accounts, interfaces

from .test_smb_accounts import ALICE_PW, BOB_PW, World, hide_from_enumeration, recorded

USERADD = ["useradd", "-r", "-l", "-N", "-M"]


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def commits(monkeypatch):
    """Counts AsyncSession.commit calls."""
    real = AsyncSession.commit
    counter = {"n": 0}

    async def commit(self):
        counter["n"] += 1
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    return counter


def reconcile(world: World) -> interfaces.ReconcileResult:
    async def run():
        async with world.maker() as db:
            return await accounts.reconcile_accounts(db)

    return asyncio.run(run())


def raw(world: World, *statements: str) -> None:
    """Write like a restored database can: plain SQL, no CHECK constraints."""
    with closing(sqlite3.connect(world.path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = 1")
        for sql in statements:
            conn.execute(sql)
        conn.commit()


# The account policies that the reconcile keeps, in the order it reads and sets them: the
# password history (ADR-0278), then the lockout's duration and reset time before its threshold
# (ADR-0334).
POLICIES = {"password history": 3, "lockout duration": 5, "reset count minutes": 5, "bad lockout attempt": 5}
LOCKOUT = ("lockout duration", "reset count minutes", "bad lockout attempt")
# Samba's defaults (pdbedit -P): no lockout, whose times are 30 minutes each.
LOCKOUT_DEFAULTS = {"lockout duration": 30, "reset count minutes": 30, "bad lockout attempt": 0}
READ = {policy: ["pdbedit", "-P", policy] for policy in POLICIES}
SET = {policy: ["pdbedit", "-P", policy, "-C", str(value)] for policy, value in POLICIES.items()}
READS = [READ[policy] for policy in POLICIES]
SKIPPED_THRESHOLD = "Samba's bad lockout attempt was not set: the lockout needs its duration and reset time first"


def consistent(world: World) -> None:
    """alice active, bob deactivated (account kept, no passdb), carol allowed (never activated);
    Samba keeps the account policies that the reconcile sets (ADR-0278, ADR-0334)."""
    world.make_active("alice", 70000, ALICE_PW)
    world.deactivated("bob", 70001)
    world.add_user("carol", "Carol-pass-2026")
    world.make_allowed("carol")
    world.set_next_uid(70002)
    world.fake.add_account("nas", 1000, gid=1000, gecos="nas", home="/home/nas", shell="/bin/bash")
    world.fake.add_passdb("nas", uid=1000)
    world.fake.policies.update(POLICIES)


def _erin_with_daves_uid(world: World) -> None:
    """consistent(), plus erin's active row (restored or hand-edited) with uid 70005 and her
    passdb entry, while 70005 is the uid of the stale marked host account dave (no row)."""
    consistent(world)
    world.add_user("erin", "Erin-pass-2026")
    raw(world, "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, created_at) "
               f"VALUES ({world.ids['erin']}, 'erin', 70005, 1, 'set', CURRENT_TIMESTAMP)")
    world.fake.add_passdb("erin", "x", uid=70005)
    world.fake.add_account("dave", 70005)


def _stored(world: World, name: str) -> tuple | None:
    """(uid, password_state, password_set_at) of the row of ``name``, read with a connection of
    its own (what another process, or the next start, would see)."""
    with closing(sqlite3.connect(world.path)) as conn:
        return conn.execute(
            "SELECT uid, password_state, password_set_at FROM smb_accounts WHERE linux_name = ?", (name,)
        ).fetchone()


class _Stopped(Exception):
    """The supervisor stops right after a host command (power loss, container restart)."""


class TestConsistent:
    def test_nothing_is_written(self, world, commits):
        consistent(world)

        result = reconcile(world)

        assert result == interfaces.ReconcileResult(changes=[], problems=[])
        assert world.fake.mutating_calls() == []
        assert commits["n"] == 0
        # The read-only commands of a consistent start (ADR-0138), Samba's account policies among them
        # (ADR-0278, ADR-0334).
        assert [a[:2] for a in world.fake.argvs()] == [
            ["/bin/sh", "-c"], ["getent", "passwd"], ["getent", "group"], ["getent", "group"], ["pdbedit", "-L"],
        ] + [["pdbedit", "-P"]] * 4
        assert world.fake.commands("pdbedit", "-P") == READS

    def test_twice(self, world, commits):
        consistent(world)
        del world.fake.passwd["alice"]

        first = reconcile(world)
        second = reconcile(world)

        assert first.changes == ["Created the missing host account alice (uid 70000)"]
        assert second == interfaces.ReconcileResult()


READ_HISTORY = READ["password history"]
SET_HISTORY = SET["password history"]


class TestPasswordHistory:
    """shares.samba-runtime-writes, step 2 (ADR-0278): Samba keeps the hashes of an account's last
    passwords, and a wrong password that is one of the two before the current one writes nothing to
    passdb.tdb. The reconcile reads the policy at every start and sets it only when it differs."""

    def test_a_host_without_a_history_gets_one(self, world):
        consistent(world)
        world.fake.policies["password history"] = 0  # Samba's default

        result = reconcile(world)

        assert result == interfaces.ReconcileResult(changes=["Set Samba's password history to 3 (it was 0)"])
        assert world.fake.policies["password history"] == 3
        assert world.fake.commands("pdbedit", "-P") == [READ_HISTORY, SET_HISTORY] + READS[1:]
        assert world.fake.mutating_calls() == [SET_HISTORY]

    def test_another_value_is_set_back(self, world):
        consistent(world)
        world.fake.policies["password history"] = 24

        result = reconcile(world)

        assert result.changes == ["Set Samba's password history to 3 (it was 24)"]
        assert world.fake.policies["password history"] == 3

    def test_a_host_that_has_it_is_only_read(self, world, commits):
        consistent(world)

        first = reconcile(world)
        second = reconcile(world)

        assert first == second == interfaces.ReconcileResult()
        assert world.fake.commands("pdbedit", "-P") == READS + READS
        assert world.fake.mutating_calls() == []
        assert commits["n"] == 0

    def test_set_once_then_only_read(self, world):
        consistent(world)
        world.fake.policies["password history"] = 0

        reconcile(world)
        again = reconcile(world)

        assert again == interfaces.ReconcileResult()
        assert world.fake.commands("pdbedit", "-P") == [READ_HISTORY, SET_HISTORY] + READS[1:] + READS

    def test_a_failed_read_is_a_problem_and_the_rest_goes_on(self, world):
        consistent(world)
        world.fake.policies["password history"] = 0
        world.fake.fail[("pdbedit", "-P")] = (1, "Failed to open account policy database")
        del world.fake.passwd["alice"]

        result = reconcile(world)

        # Each policy is read on its own, so each is named; without its times the lockout's
        # threshold is not even read (ADR-0334).
        assert result.problems == [
            f"pdbedit could not read Samba's {policy}: Failed to open account policy database"
            for policy in POLICIES if policy != "bad lockout attempt"
        ] + [SKIPPED_THRESHOLD]
        assert result.changes == ["Created the missing host account alice (uid 70000)"]
        assert world.fake.commands("pdbedit", "-P") == READS[:3]
        assert world.fake.policies["password history"] == 0

    def test_a_failed_change_is_a_problem(self, world):
        consistent(world)
        world.fake.policies["password history"] = 0
        world.fake.fail[tuple(SET_HISTORY)] = (1, "valid account policy, but unable to set value!")

        result = reconcile(world)

        assert result.problems == [
            "pdbedit could not set Samba's password history: valid account policy, but unable to set value!"
        ]
        assert result.changes == []
        assert world.fake.policies["password history"] == 0

    @pytest.mark.parametrize("stdout", [
        "",
        'account policy "password history" description: Length of Password History Entries (0 = off)\n',
        'account policy "password history" value is: many\n',
        'account policy "min password length" value is: 3\n',
        'account policy "bad lockout attempt" value is: 3\n',
    ])
    def test_an_answer_without_the_value_is_a_problem_and_nothing_is_set(self, world, monkeypatch, stdout):
        consistent(world)
        real = world.fake.run

        def run(cmd, **kwargs):
            if list(cmd) == READ_HISTORY:
                world.fake.calls.append((list(cmd), kwargs.get("input")))
                return HostResult(0, stdout, "")
            return real(cmd, **kwargs)

        monkeypatch.setattr("src.host.run_on_host", run)

        result = reconcile(world)

        assert result.problems == ["pdbedit did not print Samba's password history"]
        assert world.fake.commands("pdbedit", "-P") == READS


class TestLockout:
    """shares.smb-login-protection, step 1 (ADR-0334): Samba locks an account for 5 minutes after 5
    failed sign-ins, and the count of failures starts again after 5 minutes without one. The
    reconcile reads the three policies at every start and sets each only when it differs, the
    threshold last, so that the lockout starts with its own duration and reset time."""

    def test_a_host_without_a_lockout_gets_one(self, world):
        consistent(world)
        world.fake.policies.update(LOCKOUT_DEFAULTS)

        result = reconcile(world)

        assert result == interfaces.ReconcileResult(changes=[
            "Set Samba's lockout duration to 5 (it was 30)",
            "Set Samba's reset count minutes to 5 (it was 30)",
            "Set Samba's bad lockout attempt to 5 (it was 0)",
        ])
        assert world.fake.policies == POLICIES
        assert world.fake.commands("pdbedit", "-P") == [
            READ_HISTORY,
            READ["lockout duration"], SET["lockout duration"],
            READ["reset count minutes"], SET["reset count minutes"],
            READ["bad lockout attempt"], SET["bad lockout attempt"],
        ]
        assert world.fake.mutating_calls() == [SET[policy] for policy in LOCKOUT]

    def test_a_fresh_host_gets_every_policy_at_its_first_start(self, world):
        consistent(world)
        world.fake.policies.update({"password history": 0, **LOCKOUT_DEFAULTS})  # the fake's defaults

        result = reconcile(world)

        assert result.changes == [
            "Set Samba's password history to 3 (it was 0)",
            "Set Samba's lockout duration to 5 (it was 30)",
            "Set Samba's reset count minutes to 5 (it was 30)",
            "Set Samba's bad lockout attempt to 5 (it was 0)",
        ]
        assert world.fake.policies == POLICIES
        assert reconcile(world) == interfaces.ReconcileResult()
        assert world.fake.mutating_calls() == list(SET.values())

    def test_other_values_are_set_back(self, world):
        consistent(world)
        # pdbedit prints a duration of -1 (locked until an administrator unlocks) as 4294967295.
        world.fake.policies.update({"lockout duration": 4294967295, "reset count minutes": 0, "bad lockout attempt": 3})

        result = reconcile(world)

        assert result.changes == [
            "Set Samba's lockout duration to 5 (it was 4294967295)",
            "Set Samba's reset count minutes to 5 (it was 0)",
            "Set Samba's bad lockout attempt to 5 (it was 3)",
        ]
        assert world.fake.policies == POLICIES

    def test_a_host_that_has_it_is_only_read(self, world, commits):
        consistent(world)

        first = reconcile(world)
        second = reconcile(world)

        assert first == second == interfaces.ReconcileResult()
        assert world.fake.commands("pdbedit", "-P") == READS + READS
        assert world.fake.mutating_calls() == []
        assert commits["n"] == 0

    def test_a_failed_change_is_a_problem_and_the_lockout_stays_off(self, world):
        """Without its own duration the lockout would lock on the host's terms (ADR-0334)."""
        consistent(world)
        world.fake.policies.update(LOCKOUT_DEFAULTS)
        world.fake.fail[tuple(SET["lockout duration"])] = (1, "valid account policy, but unable to set value!")

        result = reconcile(world)

        assert result.problems == [
            "pdbedit could not set Samba's lockout duration: valid account policy, but unable to set value!",
            SKIPPED_THRESHOLD,
        ]
        assert result.changes == ["Set Samba's reset count minutes to 5 (it was 30)"]
        assert world.fake.policies["lockout duration"] == 30
        assert world.fake.policies["bad lockout attempt"] == 0

    def test_a_failed_read_is_a_problem_and_the_lockout_stays_off(self, world):
        consistent(world)
        world.fake.policies.update(LOCKOUT_DEFAULTS)
        world.fake.fail[tuple(READ["reset count minutes"])] = (1, "No account policy by that name!")

        result = reconcile(world)

        assert result.problems == [
            "pdbedit could not read Samba's reset count minutes: No account policy by that name!",
            SKIPPED_THRESHOLD,
        ]
        assert result.changes == ["Set Samba's lockout duration to 5 (it was 30)"]
        assert world.fake.policies["reset count minutes"] == 30
        assert world.fake.policies["bad lockout attempt"] == 0

    @pytest.mark.parametrize("policy", LOCKOUT)
    def test_an_answer_without_the_value_is_a_problem_and_nothing_is_set(self, world, monkeypatch, policy):
        consistent(world)
        world.fake.policies[policy] = LOCKOUT_DEFAULTS[policy]
        real = world.fake.run

        def run(cmd, **kwargs):
            if list(cmd) == READ[policy]:
                world.fake.calls.append((list(cmd), kwargs.get("input")))
                return HostResult(0, f'account policy "{policy}" value is: many\n', "")
            return real(cmd, **kwargs)

        monkeypatch.setattr("src.host.run_on_host", run)

        result = reconcile(world)

        if policy == "bad lockout attempt":
            assert result.problems == [f"pdbedit did not print Samba's {policy}"]
            assert world.fake.commands("pdbedit", "-P") == READS
        else:  # a time the lockout needs: its threshold stays as it was, and is not read
            assert result.problems == [f"pdbedit did not print Samba's {policy}", SKIPPED_THRESHOLD]
            assert world.fake.commands("pdbedit", "-P") == READS[:3]
        assert world.fake.policies[policy] == LOCKOUT_DEFAULTS[policy]


class TestHostRepairs:
    def test_missing_linux_account_of_an_active_row(self, world):
        consistent(world)
        del world.fake.passwd["alice"]

        result = reconcile(world)

        assert world.fake.commands("useradd") == [USERADD + [
            "-u", "70000", "-g", "70000", "-d", "/nonexistent", "-s", "/usr/sbin/nologin", "-c", "UNOS SMB user",
            "alice"]]
        assert world.fake.passwd["alice"].uid == 70000
        assert ["sync", "/etc"] in world.fake.argvs()
        assert result.changes == ["Created the missing host account alice (uid 70000)"]
        assert world.row("alice").password_state == "set"

    def test_missing_passdb_entry(self, world):
        consistent(world)
        pid = world.fake.add_session("alice", ["media"])
        del world.fake.passdb["alice"]

        result = reconcile(world)

        row = world.row("alice")
        assert (row.password_state, row.password_set_at, row.enabled) == ("needed", None, True)
        assert result.changes == ["SMB password of alice is missing in Samba; alice must enter it again"]
        assert result.problems == []
        assert ["smbcontrol", str(pid), "shutdown"] in world.fake.argvs()  # turned from active to not active

    def test_passdb_entry_in_another_case_is_foreign(self, world):
        consistent(world)
        del world.fake.passdb["alice"]
        world.fake.add_passdb("Alice", "hand-made", uid=1001)

        result = reconcile(world)

        assert world.row("alice").password_state == "needed"
        assert result.changes == ["SMB password of alice is missing in Samba; alice must enter it again"]
        assert result.problems == ["A Samba account named Alice exists that UNOS did not create"]
        assert world.fake.passdb["alice"] == {"name": "Alice", "password": "hand-made", "uid": 1001}
        assert world.fake.commands("pdbedit", "-x") == []

    def test_passdb_entry_of_a_needed_row(self, world):
        consistent(world)
        world.fake.add_passdb("bob", BOB_PW)  # the host is ahead of the database (a power cut)
        pid = world.fake.add_session("bob", ["media"])

        result = reconcile(world)

        assert world.fake.mutating_calls() == [["pdbedit", "-x", "-u", "bob"], ["smbcontrol", str(pid), "shutdown"]]
        assert "bob" not in world.fake.passdb
        assert result.changes == ["Removed the Samba password of bob: its SMB account is not active"]

    def test_a_stale_marked_account(self, world, commits):
        consistent(world)
        world.fake.add_account("dave", 70007)
        world.fake.add_passdb("dave", "x")
        pid = world.fake.add_session("dave", ["media"])

        result = reconcile(world)

        assert world.fake.mutating_calls() == [
            ["pdbedit", "-x", "-u", "dave"], ["smbcontrol", str(pid), "shutdown"], ["userdel", "dave"],
            ["sync", "/etc"],
        ]
        assert "dave" not in world.fake.passwd
        assert result.changes == [
            "The next SMB user id is now 70008 (it was 70002)",
            "Removed the Samba password of the stale account dave",
            "Removed the stale host account dave (uid 70007)",
        ]
        assert world.next_uid() == 70008
        assert commits["n"] == 1

    def test_a_busy_stale_account_is_a_problem(self, world):
        consistent(world)
        world.fake.add_account("dave", 70007)
        world.fake.busy["dave"] = 1

        result = reconcile(world)

        assert result.problems == ["The stale host account dave is still in use; UNOS removes it at its next start"]
        assert world.fake.passwd["dave"].uid == 70007

    def test_a_stale_account_with_the_name_of_an_active_row(self, world):
        """passdb is keyed by the name: the stale account's entry was the only password Samba
        had for alice. Her row must not stay 'set': removing the stale account's entry (step 6
        of reconcile_accounts) removed her password too."""
        consistent(world)
        world.fake.passwd["alice"] = accounts.HostAccount("alice", 70005, 70000, "UNOS SMB user", "/nonexistent",
                                                          "/usr/sbin/nologin")
        pid = world.fake.add_session("alice", ["media"])

        result = reconcile(world)

        row = world.row("alice")
        assert (row.enabled, row.password_state, row.password_set_at, row.uid) == (True, "needed", None, 70000)
        assert "alice" not in world.fake.passdb
        assert "alice" not in world.fake.passwd  # her own account comes back at her next activation
        assert pid not in world.fake.session_pids()
        assert world.fake.commands("useradd") == []
        assert result.changes == [
            "The next SMB user id is now 70006 (it was 70002)",
            "Removed the Samba password of the stale account alice",
            "Removed the stale host account alice (uid 70005)",
            "SMB password of alice is missing in Samba; alice must enter it again",
        ]
        assert result.problems == []
        assert reconcile(world) == interfaces.ReconcileResult()  # consistent now

    def test_a_busy_stale_account_with_the_name_of_an_active_row(self, world):
        consistent(world)
        world.fake.passwd["alice"] = accounts.HostAccount("alice", 70005, 70000, "UNOS SMB user", "/nonexistent",
                                                          "/usr/sbin/nologin")
        world.fake.busy["alice"] = 1

        result = reconcile(world)

        assert world.row("alice").password_state == "needed"
        assert "alice" not in world.fake.passdb
        assert world.fake.passwd["alice"].uid == 70005  # removed at the next start
        assert "SMB password of alice is missing in Samba; alice must enter it again" in result.changes
        assert result.problems == ["The stale host account alice is still in use; UNOS removes it at its next start"]

    def test_a_row_with_the_uid_of_a_stale_account_of_another_name(self, world):
        """ADR-0112, defense in depth: erin's row (restored or hand-edited) has the uid of the
        stale marked account dave, whose files on the pools that uid owns. Once dave's account is
        gone, erin must not get a host account with it: her row loses the uid and needs SMB
        turned on again (a fresh uid then)."""
        _erin_with_daves_uid(world)
        pid = world.fake.add_session("erin", ["media"])

        result = reconcile(world)

        row = world.row("erin")
        assert (row.uid, row.enabled, row.password_state, row.password_set_at) == (None, True, "needed", None)
        assert world.fake.commands("useradd") == []
        assert "dave" not in world.fake.passwd and "erin" not in world.fake.passwd
        assert "erin" not in world.fake.passdb
        assert pid not in world.fake.session_pids()
        # The row loses the uid first (a database rule, committed before the host actions).
        assert result.changes == [
            "The SMB account erin no longer has uid 70005: it belonged to the host account dave; "
            "erin must turn SMB on again",
            "The next SMB user id is now 70006 (it was 70002)",
            "Removed the stale host account dave (uid 70005)",
            "Removed the Samba password of erin: its SMB account is not active",
        ]
        assert result.problems == []
        assert reconcile(world) == interfaces.ReconcileResult()  # consistent now
        assert world.next_uid() == 70006  # her next activation gets a fresh uid

    def test_the_row_loses_the_uid_before_the_stale_account_is_removed(self, world, commits):
        """ADR-0112: when dave's userdel runs, erin's row has already lost dave's
        uid in the database (committed with the other database rules, before step 6)."""
        _erin_with_daves_uid(world)
        reached, release = world.fake.pause_at("userdel", "dave")
        results: list[interfaces.ReconcileResult] = []
        thread = threading.Thread(target=lambda: results.append(reconcile(world)))
        thread.start()
        try:
            assert reached.wait(5)
            during = _stored(world, "erin")
            commits_before_userdel = commits["n"]
        finally:
            release.set()
            thread.join(10)

        assert during == (None, "needed", None)
        assert commits_before_userdel == 1
        [result] = results
        assert "Removed the stale host account dave (uid 70005)" in result.changes
        assert commits["n"] == 1  # step 6 changed no row: nothing is left for step 7

    @pytest.mark.parametrize("interruption", ["stop-after-userdel", "commits-fail-after-userdel"])
    def test_an_interrupted_run_never_gives_the_uid_to_the_row(self, world, monkeypatch, interruption):
        """ADR-0112: the supervisor stops right after dave's userdel (power loss,
        container restart), or every commit after it fails (database busy). Dave's account
        is gone, so no later run sees the conflict: erin's row must not keep his uid, else
        the next reconcile or her next activation creates erin with uid 70005, the owner of
        dave's files."""
        _erin_with_daves_uid(world)
        removed = threading.Event()
        real_userdel = world.fake._userdel

        def userdel(argv):
            result = real_userdel(argv)
            if argv == ["userdel", "dave"]:
                removed.set()
                if interruption == "stop-after-userdel":
                    raise _Stopped()
            return result

        real_commit = AsyncSession.commit

        async def commit(self):
            if interruption == "commits-fail-after-userdel" and removed.is_set():
                raise OperationalError("COMMIT", {}, Exception("database is locked"))
            return await real_commit(self)

        monkeypatch.setattr(world.fake, "_userdel", userdel)
        monkeypatch.setattr(AsyncSession, "commit", commit)
        try:
            reconcile(world)
        except (_Stopped, OperationalError):
            pass
        monkeypatch.setattr(world.fake, "_userdel", real_userdel)
        monkeypatch.setattr(AsyncSession, "commit", real_commit)

        assert removed.is_set() and "dave" not in world.fake.passwd
        assert _stored(world, "erin") == (None, "needed", None)
        reconcile(world)  # the next start
        enabled = world.self_enable("erin", "Erin-pass-2026")

        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["account"]["uid"] == 70006
        assert [argv[6] for argv in world.fake.commands("useradd")] == ["70006"]
        assert world.fake.passwd["erin"].uid == 70006

    def test_a_row_whose_own_account_has_the_uid_of_a_stale_account(self, world):
        """Two marked accounts with one uid: the stale dave and erin's own account, while erin's
        row has 70005 too. Her files cannot be told from dave's: the row loses the uid, and her
        account, stale then, is removed with dave's in the same run."""
        _erin_with_daves_uid(world)
        world.fake.add_account("erin", 70005)

        result = reconcile(world)

        assert _stored(world, "erin") == (None, "needed", None)
        assert "dave" not in world.fake.passwd and "erin" not in world.fake.passwd
        assert "erin" not in world.fake.passdb
        assert world.fake.commands("useradd") == []
        assert result.changes == [
            "The SMB account erin no longer has uid 70005: it belonged to the host account dave; "
            "erin must turn SMB on again",
            "The next SMB user id is now 70006 (it was 70002)",
            "Removed the stale host account dave (uid 70005)",
            "Removed the Samba password of the stale account erin",
            "Removed the stale host account erin (uid 70005)",
        ]
        assert result.problems == []
        assert reconcile(world) == interfaces.ReconcileResult()

    def test_a_foreign_account_with_the_uid_of_an_active_row(self, world):
        """Step 6 of reconcile_accounts: an active row without its host account, while a foreign account has its
        uid: 'needed' plus a problem, and the foreign account is left untouched. Rule 3c' is only
        about marked accounts (UNOS removes those): the row keeps its uid."""
        consistent(world)
        del world.fake.passwd["alice"]
        svc = world.fake.add_account("svc", 70000, gid=1000, gecos="Service account", home="/srv", shell="/bin/sh")

        result = reconcile(world)

        row = world.row("alice")
        assert (row.uid, row.password_state) == (70000, "needed")
        assert world.fake.passwd["svc"] == svc
        assert world.fake.commands("useradd") == [] and world.fake.commands("userdel") == []
        assert "alice" not in world.fake.passdb
        assert result.changes == [
            "Removed the Samba password of alice: its SMB account is not active",
            "SMB access of alice was turned off: its host account is not UNOS's",
        ]
        assert result.problems == [
            "The host account alice cannot be created: uid 70000 belongs to the host account svc, "
            "which UNOS did not create"
        ]

    def test_a_busy_stale_account_takes_the_uid_too(self, world):
        consistent(world)
        world.add_user("erin", "Erin-pass-2026")
        raw(world, "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, created_at) "
                   f"VALUES ({world.ids['erin']}, 'erin', 70005, 1, 'needed', CURRENT_TIMESTAMP)")
        world.fake.add_account("dave", 70005)
        world.fake.busy["dave"] = 1

        result = reconcile(world)

        assert world.row("erin").uid is None
        assert world.fake.passwd["dave"].uid == 70005
        assert result.problems == ["The stale host account dave is still in use; UNOS removes it at its next start"]

    def test_reconcile_rechecks_the_marker_right_before_userdel(self, world):
        """ADR-0192: the stale account dave is taken over on the host (usermod -c) while the
        reconcile ends its sessions; userdel must not run."""
        consistent(world)
        world.fake.add_account("dave", 70007)
        reached, release = world.fake.pause_at("smbstatus", "--json")
        results: list[interfaces.ReconcileResult] = []
        thread = threading.Thread(target=lambda: results.append(reconcile(world)))
        thread.start()
        try:
            assert reached.wait(5)
            world.fake.add_account("dave", 70007, gecos="Dave")
        finally:
            release.set()
            thread.join(10)

        [result] = results
        assert world.fake.commands("userdel") == []
        assert world.fake.passwd["dave"].gecos == "Dave"
        assert "Refused to remove the host account dave: UNOS did not create it" in result.problems

    def test_a_marked_account_with_another_uid_than_the_row(self, world):
        consistent(world)
        world.fake.passwd["bob"] = accounts.HostAccount("bob", 70005, 70000, "UNOS SMB user", "/nonexistent",
                                                        "/usr/sbin/nologin")

        reconcile(world)

        assert world.fake.commands("userdel") == [["userdel", "bob"]]
        assert "bob" not in world.fake.passwd  # bob (needed) gets an account again at his next activation

    def test_a_row_whose_linux_name_is_not_the_username(self, world):
        consistent(world)
        # A restored or hand-edited database gave alice's id to another user, "eve".
        raw(world, f"UPDATE users SET username = 'eve' WHERE id = {world.ids['alice']}")
        pid = world.fake.add_session("alice", ["media"])

        result = reconcile(world)

        assert world.row("alice") is None
        assert world.fake.mutating_calls() == [
            ["pdbedit", "-x", "-u", "alice"], ["smbcontrol", str(pid), "shutdown"], ["userdel", "alice"],
            ["sync", "/etc"],
        ]
        assert result.changes[0] == f"Removed the SMB account alice of user id {world.ids['alice']}: the user is now 'eve'"

    def test_a_row_whose_user_is_gone(self, world):
        consistent(world)
        raw(world, f"DELETE FROM users WHERE id = {world.ids['bob']}")

        result = reconcile(world)

        assert world.row("bob") is None
        assert world.fake.commands("userdel") == [["userdel", "bob"]]
        assert result.changes[0] == "Removed the SMB account bob: its user no longer exists"
        assert world.next_uid() == 70002  # never lowered

    def test_a_foreign_account_that_getent_does_not_list(self, world):
        """ADR-0192: a directory account alice (enumeration off, sssd's default) with a
        hand-made Samba entry, while the UNOS row of alice is not active. The enumeration does
        not show the account; it is read by name before pdbedit -x, and its entry stays."""
        consistent(world)
        world.add_user("dora", "Dora-pass-2026")
        world.make_allowed("dora")
        world.fake.add_account("dora", 1001, gid=1001, gecos="Dora", home="/home/dora", shell="/bin/bash")
        world.fake.add_passdb("dora", "hers")
        hide_from_enumeration(world.fake, "dora")

        result = reconcile(world)

        assert world.fake.commands("pdbedit", "-x") == []
        assert world.fake.passdb["dora"]["password"] == "hers"
        assert world.fake.mutating_calls() == []
        assert result.problems == []

    def test_a_foreign_account_whose_passwd_line_does_not_parse(self, world):
        """A ':' in the GECOS of a directory account: getent passwd prints 8 fields. The line names
        alice, so alice is foreign (fail closed), not missing."""
        consistent(world)
        world.fake.add_account("alice", 1001, gid=1001, gecos="Alice: directory user", home="/home/alice",
                               shell="/bin/bash")
        world.fake.add_passdb("alice", "hers", uid=1001)

        result = reconcile(world)

        assert world.row("alice").password_state == "needed"
        assert world.fake.commands("pdbedit", "-x") == []
        assert world.fake.commands("useradd") == []
        assert world.fake.passdb["alice"]["password"] == "hers"
        assert result.changes == ["SMB password of alice is missing in Samba; alice must enter it again"]
        assert result.problems == ["A host account named alice exists that UNOS did not create"]

    @pytest.mark.parametrize("account", [
        accounts.HostAccount("svc", 70009, 70000, "Service account", "/srv", "/bin/sh"),            # no marker
        accounts.HostAccount("old", 20001, 70000, "UNOS SMB user", "/nonexistent", "/usr/sbin/nologin"),  # uid outside
        accounts.HostAccount("grp", 70009, 100, "UNOS SMB user", "/nonexistent", "/usr/sbin/nologin"),    # gid not 70000
        accounts.HostAccount("app", 1000, 70000, "UNOS SMB user", "/nonexistent", "/usr/sbin/nologin"),   # uid 1000
    ])
    def test_foreign_accounts_are_never_touched(self, world, account):
        consistent(world)
        world.fake.passwd[account.name] = account
        world.fake.add_passdb(account.name, "x", uid=account.uid)

        result = reconcile(world)

        assert world.fake.mutating_calls() == []
        assert world.fake.passwd[account.name] == account
        assert account.name in {e["name"] for e in world.fake.passdb.values()}
        assert result.problems == []

    def test_hostile_rows_never_reach_the_host(self, world):
        consistent(world)
        world.add_user("root", "Root-pass-2026")
        world.add_user("-o", "Dash-pass-2026")
        world.add_user("textuid", "Text-pass-2026")
        world.add_user("zero", "Zero-pass-2026")
        raw(
            world,
            f"INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, created_at) "
            f"VALUES ({world.ids['root']}, 'root', 0, 1, 'set', CURRENT_TIMESTAMP)",
            f"INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, created_at) "
            f"VALUES ({world.ids['-o']}, '-o', 70010, 1, 'set', CURRENT_TIMESTAMP)",
            f"INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, created_at) "
            f"VALUES ({world.ids['textuid']}, 'textuid', 'abc', 1, 'set', CURRENT_TIMESTAMP)",
            f"INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, created_at) "
            f"VALUES ({world.ids['zero']}, 'zero', -1, 1, 'set', CURRENT_TIMESTAMP)",
        )
        world.fake.add_passdb("-o", "x", uid=70010)

        result = reconcile(world)

        for argv in world.fake.argvs():
            assert not {"root", "-o", "0", "-1", "abc", "textuid", "zero"} & set(argv[1:]), argv
        assert world.fake.commands("useradd") == []
        assert world.fake.commands("userdel") == []
        assert world.fake.commands("pdbedit", "-x") == []
        for name in ("root", "-o", "textuid", "zero"):
            assert world.row(name).password_state == "needed", name
        assert world.row("textuid").uid is None
        assert world.row("zero").uid is None
        assert any("textuid had no valid user id" in change for change in result.changes)

    def test_admin_rows_are_cleaned(self, world):
        consistent(world)
        world.add_share("media", ("bob", "read"))
        raw(world, f"UPDATE users SET is_admin = 1 WHERE id = {world.ids['bob']}")
        world.set_row("bob", password_state="set")
        world.fake.add_passdb("bob", "x")
        pid = world.fake.add_session("bob", ["media"])

        result = reconcile(world)

        row = world.row("bob")
        assert (row.enabled, row.password_state) == (False, "needed")
        world.delete_access.assert_awaited_once()
        assert world.delete_access.await_args.args[1] == world.ids["bob"]
        assert world.access_rows("bob") == []
        assert "bob" not in world.fake.passdb
        assert pid not in world.fake.session_pids()
        assert result.changes[0] == "SMB access of bob was turned off: administrators cannot use SMB"


    def test_a_row_that_is_not_allowed_has_no_samba_password(self, world):
        consistent(world)
        world.set_row("alice", enabled=False)  # 'set' without enabled: a restored or hand-edited row
        pid = world.fake.add_session("alice", ["media"])

        result = reconcile(world)

        row = world.row("alice")
        assert (row.enabled, row.password_state) == (False, "needed")
        assert "alice" not in world.fake.passdb
        assert pid not in world.fake.session_pids()
        assert world.fake.passwd["alice"].uid == 70000  # the account and its uid stay with alice
        assert result.changes == [
            "SMB access of alice is not allowed; its Samba password is removed",
            "Removed the Samba password of alice: its SMB account is not active",
        ]

    def test_changes_and_problems_go_to_their_lists(self, world):
        consistent(world)
        del world.fake.passdb["alice"]           # a change: alice needs her password again
        world.fake.add_account("dave", 70007)    # a stale account that is busy: a problem
        world.fake.busy["dave"] = 1
        world.fake.add_passdb("Carol", "x", uid=1001)  # foreign entries are not problems of the reconcile

        result = reconcile(world)

        assert result.changes == [
            "SMB password of alice is missing in Samba; alice must enter it again",
            "The next SMB user id is now 70008 (it was 70002)",
        ]
        assert result.problems == ["The stale host account dave is still in use; UNOS removes it at its next start"]


class TestNextUid:
    @pytest.mark.parametrize("stored, expected", [
        (70000, 70002), (70002, 70002), (70050, 70050), ("'x'", 70002), (999999, 70002), (80000, 80000),
    ])
    def test_floor(self, world, stored, expected):
        consistent(world)
        raw(world, f"UPDATE smb_state SET next_uid = {stored} WHERE id = 1")

        result = reconcile(world)

        with closing(sqlite3.connect(world.path)) as conn:
            assert conn.execute("SELECT next_uid FROM smb_state").fetchone()[0] == expected
        assert bool(result.changes) == (expected != (stored if isinstance(stored, int) else None))

    def test_floor_from_a_marked_host_account(self, world):
        consistent(world)
        world.fake.add_account("old", 70100)  # stale: removed, but its uid is never handed out again
        world.fake.busy["old"] = 1

        reconcile(world)

        assert world.next_uid() == 70101

    def test_a_stale_marked_account_is_recorded_before_it_is_removed(self, world, commits):
        """ADR-0353: its files on the pools keep its uid after the userdel, so the uid stays in smb_uids
        under its name (the first start of a new installation, whose uninstall kept the accounts:
        ADR-0109). Written once, in the commit before the host actions; a busy account too."""
        consistent(world)
        world.fake.add_account("dave", 70007)
        world.fake.add_account("old", 70100)
        world.fake.busy["old"] = 1
        reached, release = world.fake.pause_at("userdel", "dave")
        during: list[dict[int, str]] = []
        thread = threading.Thread(target=lambda: reconcile(world))
        thread.start()
        try:
            assert reached.wait(5)
            during.append(recorded(world.path))
        finally:
            release.set()
            thread.join(10)

        assert during == [{70000: "alice", 70001: "bob", 70007: "dave", 70100: "old"}]
        assert "dave" not in world.fake.passwd and world.fake.passwd["old"].uid == 70100
        assert commits["n"] == 1
        assert "Removed the stale host account old (uid 70100)" in reconcile(world).changes  # no longer busy
        assert commits["n"] == 1  # already recorded: nothing to write
        assert recorded(world.path) == {70000: "alice", 70001: "bob", 70007: "dave", 70100: "old"}

    def test_the_rows_are_recorded_once(self, world, commits):
        """ADR-0353: rows from before smb_uids existed (the dev device) have uids that no record
        names. The first start records each row's uid under its name, silently and in one commit;
        a later start finds them all and writes nothing (ADR-0082, ADR-0214)."""
        consistent(world)
        raw(world, "DELETE FROM smb_uids")

        first = reconcile(world)
        second = reconcile(world)

        assert first == second == interfaces.ReconcileResult()
        assert recorded(world.path) == {70000: "alice", 70001: "bob"}
        assert commits["n"] == 1
        assert world.fake.mutating_calls() == []

    def test_a_missing_state_row(self, world):
        world.make_active("alice", 70000, ALICE_PW)
        raw(world, "DELETE FROM smb_state")

        reconcile(world)

        assert world.next_uid() == 70001


class TestToolsMissing:
    def test_database_rules_only(self, world):
        consistent(world)
        world.fake.missing = {"smbpasswd", "pdbedit", "smbstatus"}
        world.add_user("erin", "Erin-pass-2026", admin=True)  # 3b: an administrator in 'set'
        world.set_row("erin", enabled=True, password_state="set", uid=70003)
        world.add_user("frank", "Frank-pass-2026")  # 3c: a uid outside the range
        world.set_row("frank", enabled=True, password_state="set")
        raw(world, f"DELETE FROM users WHERE id = {world.ids['bob']}",
            f"UPDATE smb_accounts SET uid = 20001 WHERE user_id = {world.ids['frank']}",
            "UPDATE smb_state SET next_uid = 'x' WHERE id = 1")

        result = reconcile(world)

        assert world.fake.argvs() == [world.fake.argvs()[0]]  # the tool check only
        assert world.fake.argvs()[0][:2] == ["/bin/sh", "-c"]
        assert world.row("bob") is None  # 3a
        erin = world.row("erin")
        assert (erin.enabled, erin.password_state) == (False, "needed")  # 3b
        world.delete_access.assert_awaited_once()
        assert world.delete_access.await_args.args[1] == world.ids["erin"]
        frank = world.row("frank")
        assert (frank.uid, frank.password_state) == (None, "needed")  # 3c
        assert world.row("alice").password_state == "set"  # 3d not run
        assert world.next_uid() == 70004  # from the row uids (erin's 70003)
        assert result.problems == ["SMB tools missing on the host: smbpasswd, pdbedit, smbstatus (account reconcile skipped)"]

    def test_the_tool_check_failing(self, world, commits):
        consistent(world)
        world.fake.fail[("/bin/sh",)] = (1, "nsenter: reassociate to namespace 'ns/mnt' failed")

        result = reconcile(world)

        assert world.row("alice").password_state == "set"
        assert world.fake.commands("pdbedit") == []
        assert commits["n"] == 0
        assert result.problems == ["The SMB tool check failed (account reconcile skipped)"]
        assert result.changes == []


class TestSessions:
    def test_rows_turned_from_active_to_not_active(self, world):
        consistent(world)
        raw(world, f"UPDATE users SET username = 'Alice' WHERE id = {world.ids['alice']}")
        raw(world, f"UPDATE smb_accounts SET linux_name = 'Alice' WHERE user_id = {world.ids['alice']}")
        world.add_user("frank", "Frank-pass-2026")
        world.make_active("frank", 70003, "Frank-pass-2026")
        raw(world, f"UPDATE smb_accounts SET uid = 20001 WHERE user_id = {world.ids['frank']}")
        alice = world.fake.add_session("alice", ["media"])
        frank = world.fake.add_session("frank", ["media"])
        other = world.fake.add_session("bob", ["media"])

        result = reconcile(world)

        # 3b: 'Alice' cannot be used for SMB; 3c: frank's uid is out of range. Both end.
        assert world.row("alice").password_state == "needed"
        assert world.row("frank").uid is None
        assert set(world.fake.session_pids()) == {other}
        assert alice and frank
        assert result.problems == []


class TestWriteLock:
    def test_no_write_lock_is_held_while_host_commands_run(self, world):
        """The startup reconcile runs while requests are served (ADR-0138): the database rules
        are committed before the host actions, so other writers do not wait for SQLite's
        lock (and fail after its busy timeout) while userdel or smbstatus run."""
        consistent(world)
        raw(world, f"DELETE FROM users WHERE id = {world.ids['bob']}")  # 3a deletes bob's row
        reached, release = world.fake.pause_at("smbstatus", "--json")  # the stale account bob: its sessions
        results: list[interfaces.ReconcileResult] = []
        thread = threading.Thread(target=lambda: results.append(reconcile(world)))
        thread.start()
        try:
            assert reached.wait(5)
            with closing(sqlite3.connect(world.path, timeout=0.3)) as conn:
                conn.execute("UPDATE users SET totp_enabled = 0 WHERE id = ?", (world.ids["alice"],))
                conn.commit()
        finally:
            release.set()
            thread.join(10)

        [result] = results
        assert result.changes == [
            "Removed the SMB account bob: its user no longer exists",
            "Removed the stale host account bob (uid 70001)",
        ]
        assert world.row("bob") is None


class TestRollback:
    def test_host_ahead_of_a_rolled_back_database(self, world, tmp_path):
        """The commit of an activation was lost to a power cut right after it (ADR-0306)."""
        world.make_allowed("alice")
        world.set_next_uid(70000)
        before = tmp_path / "before.db"
        shutil.copy(world.path, before)
        assert world.self_enable().status_code == 200
        pid = world.fake.add_session("alice", ["media"])
        shutil.copy(before, world.path)  # the database rolled back to before the activation

        result = reconcile(world)

        assert "alice" not in world.fake.passdb
        assert "alice" not in world.fake.passwd
        assert pid not in world.fake.session_pids()
        mutations = world.fake.mutating_calls()
        assert mutations[-4:] == [["pdbedit", "-x", "-u", "alice"], ["smbcontrol", str(pid), "shutdown"],
                                  ["userdel", "alice"], ["sync", "/etc"]]
        # Her uid is never handed out again.
        assert world.next_uid() == 70001
        assert "Removed the stale host account alice (uid 70000)" in result.changes

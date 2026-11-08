"""The audit log: append-only (ADR-0224; feature-list.json: security.append-only-audit-log), and
listed, filtered, searched, counted and exported through the API (ADR-0237; security.audit-log).

Entries are JSON lines appended to the log, written in a worker thread, read back without
the lines that are damaged, and the log rotates by size into a fixed number of old files.
GET /audit/logs, /logs/count and /logs/export take the same filters.
"""
from __future__ import annotations

import asyncio
import csv
import errno
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.audit import router as audit_router
from src.audit.schemas import AuditAction, AuditEntry
from src.auth import router as auth_router
from src.main import app

from .audit_helpers import audit_entries
from .test_smb_accounts import ADMIN_PW, API, RecordingClient, World
from .webui_catalogs import REPO, key_map, read_catalog


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def opens(monkeypatch):
    """(file name, mode) of every open() the audit module makes."""
    calls: list[tuple[str, str]] = []
    real_open = open

    def spy(file, mode="r", *args, **kwargs):
        calls.append((Path(file).name, mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(audit_router, "open", spy, raising=False)
    return calls


def log(details: str) -> None:
    asyncio.run(audit_router.log_action(AuditAction.OTHER, details=details))


def sign_in(world: World) -> None:
    auth_router.limiter.reset()  # the login rate limit is not under test here
    response = world.client.post(f"{API}/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert response.status_code == 200, response.text


def get(world: World, path: str, **params):
    response = world.client.get(f"{API}/audit{path}", headers=world.headers("admin"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def listed(world: World) -> list[dict]:
    return get(world, "/logs", limit=10_000)


def rotated(path: Path, number: int) -> Path:
    return path.with_name(f"{path.name}.{number}")


def files_oldest_first(path: Path, keep: int) -> list[Path]:
    return [rotated(path, n) for n in range(keep, 0, -1)] + [path]


def write_opens(calls: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(name, mode) for name, mode in calls if any(c in mode for c in "wax+")]


class TestAppendOnly:
    def test_a_hundred_sign_ins_append_a_hundred_lines_and_keep_the_bytes_before(self, world, opens):
        # Bytes the writer would not produce itself: an entry in another JSON layout and a
        # damaged line. A writer that re-serialized the log would change them.
        seeded = {"success": True, "action": "other", "timestamp": "2026-11-01T00:00:00",
                  "id": "audit_seeded", "details": "seeded"}
        world.audit_file.parent.mkdir(parents=True, exist_ok=True)
        world.audit_file.write_bytes(json.dumps(seeded, indent=None).encode() + b"\n{damaged\n")
        first = world.audit_file.read_bytes()
        inode = world.audit_file.stat().st_ino

        for _ in range(100):
            sign_in(world)

        data = world.audit_file.read_bytes()
        assert data.startswith(first)  # nothing written before was rewritten
        assert world.audit_file.stat().st_ino == inode
        assert data.count(b"\n") == 102
        assert write_opens(opens) == [(world.audit_file.name, "ab")] * 100  # appends only
        actions = [e["action"] for e in reversed(listed(world))]
        assert actions == ["other"] + ["login_success"] * 100

    def test_an_entry_after_a_cut_line_starts_on_a_line_of_its_own(self, world):
        log("before")
        with open(world.audit_file, "ab") as f:
            f.write(b'{"id": "cut by a power fail')  # no newline: the write never finished

        log("after")

        assert [e["details"] for e in listed(world)] == ["after", "before"]

    def test_no_entry_is_fsynced(self, world, monkeypatch):
        synced: list[int] = []
        monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
        if hasattr(os, "fdatasync"):
            monkeypatch.setattr(os, "fdatasync", lambda fd: synced.append(fd))

        for n in range(20):
            log(f"entry {n}")

        assert synced == []
        assert len(audit_entries(world.audit_file)) == 20


class TestWorkerThread:
    @staticmethod
    def on_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def test_an_entry_is_written_in_a_worker_thread_never_on_the_event_loop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        writers: list[tuple[str, bool]] = []
        real_append = audit_router._append_line

        def spy(line: str) -> None:
            writers.append((threading.current_thread().name, self.on_loop()))
            real_append(line)

        monkeypatch.setattr(audit_router, "_append_line", spy)

        async def main() -> str:
            await audit_router.log_action(AuditAction.OTHER, details="from the event loop")
            return threading.current_thread().name

        loop_thread = asyncio.run(main())

        assert len(writers) == 1
        writer_thread, on_loop = writers[0]
        assert not on_loop
        assert writer_thread != loop_thread
        assert [e["details"] for e in audit_entries(tmp_path / "audit.jsonl")] == ["from the event loop"]

    def test_the_endpoints_read_and_clear_the_log_in_a_worker_thread(self, world, monkeypatch):
        log("an entry")
        calls: list[tuple[str, bool]] = []
        real_load, real_clear = audit_router.load_audit_log, audit_router._clear

        def load_spy():
            calls.append(("load", self.on_loop()))
            return real_load()

        def clear_spy(before_date):
            calls.append(("clear", self.on_loop()))
            return real_clear(before_date)

        monkeypatch.setattr(audit_router, "load_audit_log", load_spy)
        monkeypatch.setattr(audit_router, "_clear", clear_spy)

        for path in ("/logs", "/logs/count", "/logs/export", "/logs/summary"):
            get(world, path)
        response = world.client.delete(f"{API}/audit/logs", headers=world.headers("admin"))
        assert response.status_code == 200, response.text

        assert [name for name, _ in calls] == ["load"] * 4 + ["clear"]
        assert not any(on_loop for _, on_loop in calls)


class TestDamagedLines:
    def test_a_damaged_line_is_skipped_and_the_others_are_listed(self, world):
        log("before")
        with open(world.audit_file, "ab") as f:
            f.write(b'{"id": "half an entry\n')
            f.write(b"\xff\xfe not text\n")
            f.write(b'{"id": "x", "timestamp": "2026-11-07T00:00:00", "action": "no_such_action"}\n')
            f.write(b"[1, 2, 3]\n")
        log("after")

        assert [e["details"] for e in listed(world)] == ["after", "before"]
        assert get(world, "/logs/count") == {"count": 2}


class TestRotation:
    LIMIT = 1500

    @pytest.fixture
    def full_log(self, world, monkeypatch, opens):
        """The production number of old files, a small size limit, and more entries than fit."""
        monkeypatch.setattr(audit_router, "MAX_FILE_BYTES", self.LIMIT)
        assert audit_router.KEEP_ROTATED == 4
        for n in range(80):
            log(f"entry {n:02d} " + "x" * 100)
        return world

    def test_the_log_rotates_at_its_size_limit_and_keeps_a_fixed_number_of_old_files(self, full_log, opens):
        files = files_oldest_first(full_log.audit_file, 4)

        assert all(f.exists() for f in files)
        assert not rotated(full_log.audit_file, 5).exists()
        for older, newer in zip(files, files[1:]):
            first_line_of_newer = newer.read_bytes().split(b"\n")[0] + b"\n"
            # rotated only because the next entry did not fit
            assert older.stat().st_size <= self.LIMIT < older.stat().st_size + len(first_line_of_newer)
        assert full_log.audit_file.stat().st_size <= self.LIMIT
        assert {mode for _, mode in write_opens(opens)} == {"ab"}  # rotations only rename

    def test_the_api_lists_counts_and_exports_the_entries_of_the_old_files_too(self, full_log):
        files = files_oldest_first(full_log.audit_file, 4)
        kept = sum(len(audit_entries(f)) for f in files)

        numbers = [int(e["details"].split()[1]) for e in listed(full_log)]

        assert len(audit_entries(full_log.audit_file)) < kept < 80  # old files read, oldest gone
        assert numbers == list(range(79, 79 - kept, -1))  # newest first, nothing missing between
        assert get(full_log, "/logs/count") == {"count": kept}
        assert get(full_log, "/logs/export")["count"] == kept
        assert get(full_log, "/logs/summary", days=36500)["total_entries"] == kept


class TestClear:
    @pytest.fixture
    def rotated_log(self, world, monkeypatch):
        monkeypatch.setattr(audit_router, "MAX_FILE_BYTES", 1024)
        for n in range(20):
            log(f"entry {n:02d} " + "x" * 100)
        assert rotated(world.audit_file, 2).exists()
        return world

    def test_clearing_removes_every_file_and_records_the_clearing(self, rotated_log):
        present = len(listed(rotated_log))

        response = rotated_log.client.delete(f"{API}/audit/logs", headers=rotated_log.headers("admin"))

        assert response.status_code == 200, response.text
        assert response.json()["deleted_count"] == present
        assert not any(rotated(rotated_log.audit_file, n).exists() for n in range(1, 6))
        assert [e["details"] for e in audit_entries(rotated_log.audit_file)] == [
            f"Cleared {present} audit entries"]

    def test_clearing_before_a_date_spans_the_old_files_and_rewrites_only_one(self, rotated_log):
        files = files_oldest_first(rotated_log.audit_file, 4)
        straddling = next(f for f in files if f.exists() and len(audit_entries(f)) > 1)
        cutoff = audit_entries(straddling)[1]["timestamp"]
        before = {f: (f.stat().st_ino, f.read_bytes()) for f in files if f.exists()}
        older = [e for e in reversed(listed(rotated_log)) if e["timestamp"] < cutoff]
        kept = [e["details"] for e in reversed(listed(rotated_log)) if e["timestamp"] >= cutoff]

        response = rotated_log.client.delete(f"{API}/audit/logs", headers=rotated_log.headers("admin"),
                                             params={"before_date": cutoff})

        assert response.status_code == 200, response.text
        assert response.json()["deleted_count"] == len(older)
        assert [e["details"] for e in reversed(listed(rotated_log))] == kept + [
            f"Cleared {len(older)} audit entries"]
        for f, (inode, data) in before.items():
            if f.exists() and files.index(f) > files.index(straddling) and f != rotated_log.audit_file:
                assert (f.stat().st_ino, f.read_bytes()) == (inode, data)  # newer files untouched
        assert all(f.stat().st_size <= 1024 for f in files if f.exists())
        assert not list(rotated_log.audit_file.parent.glob("*.new"))

    def test_clearing_that_removes_nothing_rewrites_nothing(self, rotated_log):
        files = [f for f in files_oldest_first(rotated_log.audit_file, 4) if f.exists()]
        before = {f: (f.stat().st_ino, f.read_bytes()) for f in files}

        response = rotated_log.client.delete(f"{API}/audit/logs", headers=rotated_log.headers("admin"),
                                             params={"before_date": "2000-01-01"})

        assert response.json()["deleted_count"] == 0
        for f in files:
            assert f.stat().st_ino == before[f][0]
            assert f.read_bytes().startswith(before[f][1])  # only the clearing entry is appended

    def test_a_clearing_that_fails_keeps_the_entries_and_says_so(self, rotated_log, monkeypatch):
        files = files_oldest_first(rotated_log.audit_file, 4)
        straddling = next(f for f in files if f.exists() and len(audit_entries(f)) > 1)
        cutoff = audit_entries(straddling)[1]["timestamp"]
        kept = [e["details"] for e in reversed(listed(rotated_log)) if e["timestamp"] >= cutoff]

        def no_space(src, dst):
            raise OSError(errno.ENOSPC, "No space left on device")

        with monkeypatch.context() as patch:
            patch.setattr(audit_router.os, "replace", no_space)
            response = rotated_log.client.delete(f"{API}/audit/logs", headers=rotated_log.headers("admin"),
                                                 params={"before_date": cutoff})

        assert response.status_code == 500
        details = [e.details for e in audit_router.load_audit_log()]
        # The file that failed to be rewritten stays as it was: every entry to be kept is still
        # there, in order (older ones of that file too), and the failure is recorded.
        assert [d for d in details if d in kept] == kept
        assert details[-1].startswith("Clearing audit entries failed")
        assert not list(rotated_log.audit_file.parent.glob("*.new"))


class TestLock:
    def test_concurrent_entries_are_all_kept_across_rotations(self, tmp_path, monkeypatch):
        monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        monkeypatch.setattr(audit_router, "MAX_FILE_BYTES", 600)
        monkeypatch.setattr(audit_router, "KEEP_ROTATED", 100)
        errors: list[BaseException] = []

        def writer(n: int) -> None:
            try:
                for i in range(25):
                    entry = {"id": f"t{n}-{i}", "timestamp": f"2026-11-07T00:00:{n:02d}.{i:06d}",
                             "action": "other", "details": "x" * 60}
                    audit_router._append_line(json.dumps(entry) + "\n")
            except BaseException as exc:  # pragma: no cover - the failure is the assertion
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(audit_router.load_audit_log()) == 200
        assert all(p.stat().st_size <= 600 for p in tmp_path.glob("audit.jsonl*"))

    def test_a_reader_never_sees_a_rotation_halfway(self, tmp_path, monkeypatch):
        monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        monkeypatch.setattr(audit_router, "MAX_FILE_BYTES", 400)
        line = json.dumps({"id": "x", "timestamp": "2026-11-07T00:00:00", "action": "other",
                           "details": "y" * 200}) + "\n"
        for _ in range(8):
            audit_router._append_line(line)  # several old files
        count = len(audit_router.load_audit_log())
        real_replace = os.replace

        def slow_replace(src, dst):
            time.sleep(0.05)  # widen the rotation's window
            real_replace(src, dst)

        monkeypatch.setattr(audit_router.os, "replace", slow_replace)
        seen: list[int] = []
        writer = threading.Thread(target=audit_router._append_line, args=(line,))
        writer.start()
        time.sleep(0.02)  # the writer is inside the rotation now
        seen.append(len(audit_router.load_audit_log()))
        writer.join()

        # One entry per file: the rotation drops the oldest and adds the new one, so the count
        # stays. A file read mid-shift would be missed or read twice; behind the lock it is not.
        assert seen == [count]
        assert len(audit_router.load_audit_log()) == count


# A process of its own that writes the log, as the emergency command line does beside the
# supervisor (ADR-0284): it appends an entry that does not fit, and so rotates the log, or it
# clears the log before a date. Before its first rename it stops: it creates the file PAUSED and
# goes on once the file GO exists.
OTHER_PROCESS = textwrap.dedent(
    """
    import os, sys, time
    import src.audit.router as audit

    log_file, limit, paused, go, operation, argument = sys.argv[1:]
    audit.AUDIT_FILE, audit.MAX_FILE_BYTES = log_file, int(limit)
    rename = os.replace

    def stopping_rename(source, target):
        if not os.path.exists(paused):
            open(paused, "x").close()
            deadline = time.monotonic() + 60
            while not os.path.exists(go) and time.monotonic() < deadline:
                time.sleep(0.01)
        rename(source, target)

    os.replace = stopping_rename
    if operation == "append":
        audit._append_line(argument)
    else:
        audit._clear(argument)
    """
)
SUPERVISOR = Path(__file__).resolve().parents[1]


def entry_line(name: str, second: int) -> str:
    """An entry of about 290 bytes, at 2026-11-07T00:00:<second>."""
    return json.dumps({"id": name, "timestamp": f"2026-11-07T00:00:{second:02d}", "action": "other",
                       "details": f"{name} " + "x" * 200}) + "\n"


class TestOtherProcesses:
    """The emergency command line writes the log from a process of its own while the supervisor
    runs (ADR-0284): appends, rotations, reads and clearing exclude each other across processes
    as they do across threads (ADR-0286)."""

    LIMIT = 1000  # three entries of entry_line fit in a file

    @pytest.fixture
    def log_file(self, tmp_path, monkeypatch) -> Path:
        """A log of nine entries in three full files: audit.jsonl.2, audit.jsonl.1 and the
        current one, seeded-0 to seeded-8 a second apart. The next entry rotates it."""
        path = tmp_path / "audit" / "audit.jsonl"
        monkeypatch.setattr(audit_router, "AUDIT_FILE", str(path))
        monkeypatch.setattr(audit_router, "MAX_FILE_BYTES", self.LIMIT)
        assert audit_router.KEEP_ROTATED == 4
        for n in range(9):
            audit_router._append_line(entry_line(f"seeded-{n}", n))
        assert [len(audit_entries(f)) for f in files_oldest_first(path, 4)] == [0, 0, 3, 3, 3]
        return path

    def while_another_process_stops(self, tmp_path, log_file, operation, argument, ours):
        """Run ``ours`` in a thread of this process while another process that is in the middle
        of `operation` (append or clear) stops before its first rename: whether ``ours`` ended
        before the other process went on, what it returned, and what the other process wrote
        to its error output."""
        paused, go = tmp_path / "paused", tmp_path / "go"
        other = subprocess.Popen(
            [sys.executable, "-c", OTHER_PROCESS, str(log_file), str(self.LIMIT), str(paused), str(go),
             operation, argument],
            cwd=SUPERVISOR, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        result: dict = {}
        try:
            deadline = time.monotonic() + 60
            while not paused.exists():
                if other.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"the other process did not stop at its first rename: {other.communicate()[1]}")
                time.sleep(0.01)

            def run() -> None:
                try:
                    result["value"] = ours()
                except BaseException as exc:  # pragma: no cover - the failure is the assertion
                    result["error"] = exc

            thread = threading.Thread(target=run)
            thread.start()
            thread.join(0.5)
            ended_meanwhile = not thread.is_alive()
        finally:
            go.touch()
        thread.join(60)
        errors = other.communicate(timeout=60)[1]
        assert not thread.is_alive()
        assert (other.returncode, result.get("error")) == (0, None), errors
        return ended_meanwhile, result.get("value")

    @pytest.mark.parametrize("ours", ["append", "read", "clear"])
    def test_an_append_a_read_or_a_clearing_waits_for_the_rotation_of_another_process(self, tmp_path, log_file, ours):
        work = {
            "append": lambda: audit_router._append_line(entry_line("ours", 30)),
            "read": lambda: [e.id for e in audit_router.load_audit_log()],
            "clear": lambda: audit_router._clear(None),
        }[ours]

        ended_meanwhile, value = self.while_another_process_stops(
            tmp_path, log_file, "append", entry_line("other", 20), work)

        assert not ended_meanwhile
        seeded = [f"seeded-{n}" for n in range(9)]
        listed = [e.id for e in audit_router.load_audit_log()]
        if ours == "append":
            # Every file moved one number up, none was lost, and this entry came after.
            assert listed == seeded + ["other", "ours"]
            assert [len(audit_entries(f)) for f in files_oldest_first(log_file, 4)] == [0, 3, 3, 3, 2]
        elif ours == "read":
            assert value == seeded + ["other"] == listed  # the whole rotation, not half of it
        else:
            assert (value, listed) == (10, [])

    def test_an_append_waits_for_another_process_that_clears_the_log(self, tmp_path, log_file):
        # The current file holds seeded-6 to seeded-8: clearing before seeded-7 rewrites it.
        before = audit_entries(log_file)[1]["timestamp"]

        ended_meanwhile, _ = self.while_another_process_stops(
            tmp_path, log_file, "clear", before, lambda: audit_router._append_line(entry_line("ours", 30)))

        assert not ended_meanwhile
        assert [e.id for e in audit_router.load_audit_log()] == ["seeded-7", "seeded-8", "ours"]


# ==================== The API (ADR-0237; feature-list.json: security.audit-log) ====================

CLIENT = "192.0.2.10"  # the address the test client connects from
LISTED_ROUTES = ["/logs", "/logs/count", "/logs/export"]  # the routes that take the filters
BOUND_REFUSED = "{name} must be a date (YYYY-MM-DD) or a date and time (YYYY-MM-DDTHH:MM:SS) without a time zone"

# Entries at the edges of 2026-11-06, in the order written. Their times are the server's local
# time without a zone, as log_action writes them: with microseconds, and without them only at a
# whole second (datetime.isoformat() leaves out a fraction of zero).
DAY = "2026-11-06"
SEEDED = [
    {"timestamp": "2026-11-05T23:59:59.999999", "action": "login_failed", "username": "mallory",
     "severity": "warning", "success": False, "ip_address": "198.51.100.4",
     "details": "Incorrect username or password"},
    {"timestamp": "2026-11-06T00:00:00", "action": "login_success", "username": "admin", "ip_address": CLIENT},
    {"timestamp": "2026-11-06T12:00:00.483912", "action": "user_created", "username": "admin",
     "ip_address": CLIENT, "resource_type": "user", "resource_id": "carol", "details": "Created new user: carol"},
    {"timestamp": "2026-11-06T23:59:59.999999", "action": "user_deleted", "username": "admin",
     "severity": "warning", "ip_address": CLIENT, "resource_type": "user", "resource_id": "carol",
     "details": "Deleted user: carol"},
    {"timestamp": "2026-11-07T00:00:00", "action": "login_success", "username": "alice",
     "ip_address": "203.0.113.7", "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Firefox/140.0"},
]
# The times of the entries of SEEDED, which name them in the tests.
FAILED, ADMIN_IN, CREATED, DELETED, ALICE_IN = (e["timestamp"] for e in SEEDED)


@pytest.fixture
def seeded(world):
    world.audit_file.parent.mkdir(parents=True, exist_ok=True)
    world.audit_file.write_text(
        "".join(json.dumps({"id": f"audit_{e['timestamp']}", **e}) + "\n" for e in SEEDED)
    )
    return world


def stamps(entries: list[dict]) -> list[str]:
    return [e["timestamp"] for e in entries]


def ask(world: World, path: str, **params):
    return world.client.get(f"{API}/audit{path}", headers=world.headers("admin"), params=params)


class TestSignInAndUserManagementAreListed:
    def test_the_entries_name_the_user_the_action_the_time_and_the_client_address(self, world):
        world.client = RecordingClient(TestClient(app, raise_server_exceptions=False, client=(CLIENT, 50000)))
        auth_router.limiter.reset()  # the login rate limit is not under test here
        before = datetime.now()

        signed_in = world.client.post(f"{API}/auth/login", json={"username": "admin", "password": ADMIN_PW})
        assert signed_in.status_code == 200, signed_in.text
        headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
        created = world.client.post(
            f"{API}/auth/users", json={"username": "carol", "password": "Carol-pass-2026"}, headers=headers
        )
        assert created.status_code == 200, created.text
        deleted = world.client.delete(
            f"{API}/auth/users/{created.json()['id']}", params={"username": "carol"}, headers=headers
        )
        assert deleted.status_code == 200, deleted.text
        after = datetime.now()

        response = world.client.get(f"{API}/audit/logs", headers=headers)

        assert response.status_code == 200, response.text
        entries = response.json()
        assert [(e["action"], e["username"], e["resource_id"], e["ip_address"]) for e in entries] == [
            ("user_deleted", "admin", "carol", CLIENT),
            ("user_created", "admin", "carol", CLIENT),
            ("login_success", "admin", None, CLIENT),
        ]
        moments = [datetime.fromisoformat(e["timestamp"]) for e in entries]
        assert moments == sorted(moments, reverse=True)  # newest first
        assert before <= moments[-1] and moments[0] <= after


class TestFilters:
    @pytest.mark.parametrize("params, expected", [
        pytest.param({"username": "admin"}, [DELETED, CREATED, ADMIN_IN], id="user"),
        pytest.param({"username": "Admin"}, [], id="user-by-the-exact-name"),
        pytest.param({"action": "login_success"}, [ALICE_IN, ADMIN_IN], id="action"),
        pytest.param({"severity": "warning"}, [DELETED, FAILED], id="severity"),
        # A date covers its whole day, as the first and as the last day.
        pytest.param({"start_date": DAY, "end_date": DAY}, [DELETED, CREATED, ADMIN_IN], id="one-day"),
        pytest.param({"start_date": DAY}, [ALICE_IN, DELETED, CREATED, ADMIN_IN], id="from-a-day"),
        pytest.param({"end_date": DAY}, [DELETED, CREATED, ADMIN_IN, FAILED], id="up-to-a-day"),
        # A date and time covers the whole hour, minute, second or fraction of a second it names,
        # as the first and as the last one: the page shows times to the second.
        pytest.param({"start_date": "2026-11-06T12", "end_date": "2026-11-06T12"}, [CREATED], id="one-hour"),
        pytest.param({"start_date": "2026-11-06T12:00", "end_date": "2026-11-06T12:00"}, [CREATED],
                     id="one-minute"),
        pytest.param({"start_date": "2026-11-06T12:00:00", "end_date": "2026-11-06T12:00:00"}, [CREATED],
                     id="one-second"),
        pytest.param({"start_date": "2026-11-06T12:00:00.4", "end_date": "2026-11-06T12:00:00.4"}, [CREATED],
                     id="one-tenth-of-a-second"),
        pytest.param({"start_date": CREATED, "end_date": CREATED}, [CREATED], id="one-microsecond"),
        pytest.param({"end_date": "2026-11-06T12:00:00.483911"}, [ADMIN_IN, FAILED], id="up-to-the-microsecond-before"),
        pytest.param({"end_date": "2026-11-06T23:59:59"}, [DELETED, CREATED, ADMIN_IN, FAILED],
                     id="up-to-the-last-second-of-a-day"),
        pytest.param({"start_date": "2026-11-06 12:00", "end_date": "2026-11-06 12:00"}, [CREATED],
                     id="a-space-between-the-date-and-the-time"),
        pytest.param({"start_date": "2026-11-06T00:00:00.000001", "end_date": "2026-11-06T23:59:59.999998"},
                     [CREATED], id="between-two-instants"),
        pytest.param({"start_date": "2026-11-06T12:00", "end_date": "2026-11-07"}, [ALICE_IN, DELETED, CREATED],
                     id="from-a-minute-up-to-a-day"),
        pytest.param({"start_date": "2026-11-06T12:00:01"}, [ALICE_IN, DELETED], id="from-the-next-second"),
        pytest.param({"start_date": "2026-11-07", "end_date": DAY}, [], id="an-empty-range"),
        pytest.param({"username": "admin", "action": "user_deleted", "start_date": DAY, "end_date": DAY},
                     [DELETED], id="together"),
    ])
    def test_filters_narrow_the_list_and_the_count_counts_it(self, seeded, params, expected):
        assert stamps(get(seeded, "/logs", **params)) == expected
        assert get(seeded, "/logs/count", **params) == {"count": len(expected)}

    @pytest.mark.parametrize("path", LISTED_ROUTES)
    @pytest.mark.parametrize("params", [
        pytest.param({"start_date": "yesterday"}, id="not-a-date"),
        pytest.param({"end_date": "2026-11-31"}, id="no-such-day"),
        pytest.param({"start_date": "2026-11-06T12:00:00+01:00"}, id="an-offset"),
        pytest.param({"end_date": "2026-11-06T12:00:00Z"}, id="utc"),
        pytest.param({"end_date": "2026-11-06T24:00"}, id="no-such-hour"),
        # Only the forms whose unit is plain: the extended ISO 8601 date, then the time to the
        # hour, minute, second or 1 to 6 digits of a fraction.
        pytest.param({"start_date": "20261106"}, id="the-basic-form"),
        pytest.param({"end_date": "2026-W45-5"}, id="a-week-date"),
        pytest.param({"end_date": "2026-11-06T12:00:00,5"}, id="a-decimal-comma"),
        pytest.param({"end_date": "2026-11-06T12:00:00.1234567"}, id="seven-digits-of-a-fraction"),
        pytest.param({"start_date": "2026-11-06T12:0"}, id="a-minute-of-one-digit"),
    ])
    def test_a_bound_that_is_not_a_date_or_a_time_without_a_zone_is_refused(self, seeded, path, params):
        response = ask(seeded, path, **params)

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == BOUND_REFUSED.format(name=next(iter(params)))

    def test_the_list_comes_a_page_at_a_time(self, seeded):
        assert stamps(get(seeded, "/logs", limit=2)) == [ALICE_IN, DELETED]
        assert stamps(get(seeded, "/logs", limit=2, offset=1)) == [DELETED, CREATED]
        assert stamps(get(seeded, "/logs", username="admin", limit=2, offset=2)) == [ADMIN_IN]

    @pytest.mark.parametrize("path, params", [
        pytest.param("/logs", {"limit": 0}, id="no-entry"),
        pytest.param("/logs", {"limit": -1}, id="a-negative-limit"),
        pytest.param("/logs", {"offset": -1}, id="a-negative-offset"),
        pytest.param("/logs/export", {"format": "xml"}, id="an-unknown-format"),
    ])
    def test_a_page_or_a_format_that_means_nothing_is_refused(self, seeded, path, params):
        assert ask(seeded, path, **params).status_code == 422

    @pytest.mark.parametrize("path", LISTED_ROUTES)
    def test_only_an_administrator_gets_an_answer_whatever_the_filters(self, seeded, path):
        params = {"start_date": "yesterday", "search": "carol"}

        assert seeded.client.get(f"{API}/audit{path}", params=params).status_code == 401
        assert seeded.client.get(
            f"{API}/audit{path}", params=params, headers=seeded.headers("alice")
        ).status_code == 403


class TestSearch:
    @pytest.mark.parametrize("search, expected", [
        pytest.param("CAROL", [DELETED, CREATED], id="resource-and-details-in-any-case"),
        pytest.param("  carol ", [DELETED, CREATED], id="without-its-surrounding-spaces"),
        pytest.param("mallory", [FAILED], id="user"),
        pytest.param("198.51.100", [FAILED], id="client-address"),
        pytest.param("user_deleted", [DELETED], id="action"),
        pytest.param("login failed", [FAILED], id="action-as-the-page-names-it"),
        pytest.param("User Carol", [DELETED, CREATED], id="resource-as-the-page-shows-it"),
        pytest.param("firefox", [ALICE_IN], id="browser"),
        pytest.param("no such text", [], id="nothing"),
    ])
    def test_search_finds_the_text_in_any_field_of_an_entry(self, seeded, search, expected):
        assert stamps(get(seeded, "/logs", search=search)) == expected
        assert get(seeded, "/logs/count", search=search) == {"count": len(expected)}

    def test_search_narrows_the_filtered_list(self, seeded):
        assert stamps(get(seeded, "/logs", search="carol", action="user_created")) == [CREATED]
        assert stamps(get(seeded, "/logs", search="login", username="admin")) == [ADMIN_IN]

    @pytest.mark.parametrize("path", LISTED_ROUTES)
    def test_a_search_of_more_than_256_characters_is_refused(self, seeded, path):
        assert ask(seeded, path, search="x" * 256).status_code == 200
        assert ask(seeded, path, search="x" * 257).status_code == 422


class TestExport:
    def test_the_csv_export_downloads_the_filtered_entries(self, seeded):
        response = ask(seeded, "/logs/export", format="csv", username="admin", start_date=DAY, end_date=DAY,
                       search="carol")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert re.fullmatch(r"attachment; filename=audit_log_\d{8}\.csv", response.headers["content-disposition"])
        header, *rows = csv.reader(io.StringIO(response.text))
        assert header == ["ID", "Timestamp", "Action", "Severity", "Username", "IP Address", "Resource Type",
                          "Resource ID", "Details", "Success"]
        assert [(row[1], row[2], row[4], row[5], row[7]) for row in rows] == [
            (DELETED, "user_deleted", "admin", CLIENT, "carol"),
            (CREATED, "user_created", "admin", CLIENT, "carol"),
        ]

    def test_a_failed_sign_in_puts_no_formula_into_the_csv_export(self, world):
        # Whoever reaches the login, signed in or not, chooses the user name of a failed sign-in.
        failed = world.client.post(f"{API}/auth/login", json={"username": "=1+1", "password": "Not-the-password-1"})
        assert failed.status_code == 401, failed.text

        response = ask(world, "/logs/export", format="csv", action="login_failed")

        assert response.status_code == 200, response.text
        header, *rows = csv.reader(io.StringIO(response.text, newline=""))
        assert [row[header.index("Username")] for row in rows] == ["'=1+1"]
        assert [e["username"] for e in get(world, "/logs/export", action="login_failed")["entries"]] == ["=1+1"]

    @pytest.mark.parametrize("recorded, exported", [
        # A spreadsheet reads a cell that starts with one of these as a formula (CSV injection).
        pytest.param("=1+1", "'=1+1", id="equals"),
        pytest.param("+1+1", "'+1+1", id="plus"),
        pytest.param("-1+1", "'-1+1", id="minus"),
        pytest.param("@SUM(1,1)", "'@SUM(1,1)", id="at"),
        pytest.param("\tcarol", "'\tcarol", id="tab"),
        pytest.param("\rcarol", "'\rcarol", id="carriage-return"),
        # A spreadsheet that splits a line at semicolons (the list separator of many locales) or
        # tabs starts a cell inside the value, and so does a line break.
        pytest.param("carol;=1+1", "carol;'=1+1", id="after-a-semicolon"),
        pytest.param("carol\t+1", "carol\t'+1", id="after-a-tab"),
        pytest.param("carol\n-1", "carol\n'-1", id="after-a-line-feed"),
        pytest.param("carol\r@SUM(1,1)", "carol\r'@SUM(1,1)", id="after-a-carriage-return"),
        pytest.param("\t=1+1", "'\t'=1+1", id="both"),
        # Text that a spreadsheet does not read as a formula stays as it is.
        pytest.param("carol", "carol", id="text"),
        pytest.param("a-b = c; d @ e,+f", "a-b = c; d @ e,+f", id="signs-inside-the-text"),
        pytest.param("'=1+1", "'=1+1", id="already-text"),
    ])
    def test_the_csv_export_has_no_cell_that_a_spreadsheet_reads_as_a_formula(self, world, recorded, exported):
        entry = {"id": recorded, "timestamp": CREATED, "action": "other", "username": recorded,
                 "ip_address": recorded, "resource_type": recorded, "resource_id": recorded, "details": recorded}
        world.audit_file.parent.mkdir(parents=True, exist_ok=True)
        world.audit_file.write_text(json.dumps(entry) + "\n")

        response = ask(world, "/logs/export", format="csv")

        assert response.status_code == 200, response.text
        header, *rows = csv.reader(io.StringIO(response.text, newline=""))
        assert rows == [[exported, CREATED, "other", "info", exported, exported, exported, exported, exported, "True"]]
        # The entry and the JSON export keep the value as recorded.
        assert get(world, "/logs/export")["entries"][0]["username"] == recorded

    def test_the_json_export_downloads_the_filtered_entries(self, seeded):
        response = ask(seeded, "/logs/export", action="login_success", end_date=DAY)

        assert response.status_code == 200, response.text
        assert re.fullmatch(r"attachment; filename=audit_log_\d{8}\.json", response.headers["content-disposition"])
        body = response.json()
        assert body["count"] == 1
        assert stamps(body["entries"]) == [ADMIN_IN]

    @pytest.mark.parametrize("params", [
        pytest.param({"username": "admin"}, id="user"),
        pytest.param({"action": "login_success"}, id="action"),
        pytest.param({"severity": "warning"}, id="severity"),
        pytest.param({"start_date": DAY, "end_date": DAY}, id="time"),
        pytest.param({"search": "carol"}, id="search"),
        pytest.param({"username": "admin", "action": "user_deleted", "start_date": DAY, "end_date": DAY,
                      "search": "carol"}, id="together"),
    ])
    def test_the_export_holds_the_entries_the_list_shows(self, seeded, params):
        shown = get(seeded, "/logs", limit=10_000, **params)
        exported = get(seeded, "/logs/export", **params)

        assert 0 < len(shown) < len(SEEDED)  # the filter narrowed the list
        assert exported["entries"] == shown
        assert exported["count"] == len(shown)


AUDIT_PAGE = REPO / "webui" / "src" / "routes" / "audit" / "+page.svelte"


def test_the_web_ui_names_every_action_by_words_the_search_finds():
    """The Audit Log page shows an action by its name in the audit catalog (ADR-0287, ADR-0288):
    one for every AuditAction, in Russian and in English, where the name is the action with spaces
    for its underscores and a capital (ADR-0237), words that the search of the list finds in the
    action's entries."""
    actions = key_map(AUDIT_PAGE, "ACTION_NAMES")
    assert sorted(actions) == sorted(action.value for action in AuditAction)

    catalog = read_catalog("audit")  # skips without node
    for action in AuditAction:
        english = catalog["en"][actions[action.value]]
        assert english == action.value.replace("_", " ").capitalize()
        entry = AuditEntry(id="audit_1", timestamp="2026-11-08T10:00:00", action=action)
        assert audit_router.AuditFilter(search=english.casefold()).matches(entry), english
        assert catalog["ru"][actions[action.value]]

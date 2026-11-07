"""The append-only audit log (ADR-0224; feature-list.json: security.append-only-audit-log).

Entries are JSON lines appended to the log, written in a worker thread, read back without
the lines that are damaged, and the log rotates by size into a fixed number of old files.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.audit import router as audit_router
from src.audit.schemas import AuditAction
from src.auth import router as auth_router

from .audit_helpers import audit_entries
from .test_smb_accounts import ADMIN_PW, API, World


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

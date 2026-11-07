"""Signing in while /data is full (ADR-0247; feature auth.sign-in-with-full-system-disk).

A sign-in writes twice to /data before it answers: a row of the login history (SQLite) and an
entry of the audit log (JSON lines, ADR-0224). The tests make /data full the way those writes
meet it: SQLite answers its own "database or disk is full" (PRAGMA max_page_count keeps the
database file at its pages on every connection, and login_attempts is filled to its last
page), the audit log's append fails with ENOSPC, and for a failure at the end of a commit,
AsyncSession.commit raises what SQLite raises then. The dev device's /data is never filled for
a check (ADR-0214).
"""
from __future__ import annotations

import asyncio
import errno
import json
import logging
import sqlite3

import pyotp
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.audit import router as audit_router
from src.audit.schemas import AuditAction
from src.auth import utils as auth_utils
from src.auth.models import User
from src.security.models import LoginAttempt

from .test_recovery_codes import generate
from .test_smb_accounts import ALICE_PW, API, SECRETS, World
from .test_totp_once import Clock

DB_FULL = "database or disk is full"
NO_SPACE = "No space left on device"
# How the supervisor's log reports a record it could not write: the record as JSON follows.
ENTRY_LOST = "Could not write this audit entry to the audit log: "
ATTEMPT_LOST = "Could not store this login attempt in the login history: "
FILLER = "filler"
SECRET = pyotp.random_base32()


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()
    # What the log reports in place of the lost records holds no password (ADR-0125).
    logged = "\n".join(r.getMessage() for when in ("setup", "call", "teardown") for r in caplog.get_records(when))
    for secret in SECRETS:
        assert secret not in logged, "a password in the log"


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
    return clock


def fill_the_database(world: World) -> None:
    """The database is full: a write that needs a new page gets SQLite's "database or disk is
    full", on every connection of the app and of the test, and login_attempts has no room left."""
    with world.engine.connect() as connection:
        pages = connection.exec_driver_sql("PRAGMA page_count").scalar_one()

    def at_its_size(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA max_page_count = {pages}")
        cursor.close()

    for engine in (world.engine, world.async_engine.sync_engine):
        event.listen(engine, "connect", at_its_size)
    world.engine.dispose()  # its pooled connections were opened without the limit
    for _ in range(10_000):
        try:
            with Session(world.engine) as session:
                session.add(LoginAttempt(username=FILLER, success=False))
                session.commit()
        except OperationalError as exc:
            assert str(exc.orig) == DB_FULL
            return
    pytest.fail("the database did not fill up")


def the_audit_log_is_full(monkeypatch) -> None:
    """The audit log's append, its only writer (ADR-0224), fails as on a full disk."""
    def no_space(line: str) -> None:
        raise OSError(errno.ENOSPC, NO_SPACE)

    monkeypatch.setattr(audit_router, "_append_line", no_space)


def fill_data(world: World, monkeypatch) -> None:
    fill_the_database(world)
    the_audit_log_is_full(monkeypatch)


def every_commit_fails(patch) -> None:
    """Every commit fails at its end, where SQLite writes the journal and the database file."""
    async def commit(self):
        raise OperationalError("COMMIT", {}, sqlite3.OperationalError(DB_FULL))

    patch.setattr(AsyncSession, "commit", commit)


def sign_in(world: World, name: str = "alice", password: str = ALICE_PW, *, headers=None, **second):
    """POST /auth/login; ``second`` is totp_code or recovery_code."""
    return world.client.post(f"{API}/auth/login", json={"username": name, "password": password, **second},
                             headers=headers or {})


def stored_attempts(world: World) -> list[tuple[str, bool]]:
    """The login history, without the rows that filled the database."""
    with Session(world.engine) as session:
        rows = session.scalars(select(LoginAttempt).where(LoginAttempt.username != FILLER).order_by(LoginAttempt.id))
        return [(row.username, row.success) for row in rows]


def stored_codes(world: World) -> str | None:
    """users.recovery_codes of alice, as the database holds it."""
    with Session(world.engine) as session:
        return session.get(User, world.ids["alice"]).recovery_codes


def reported(world: World, prefix: str) -> list[tuple[dict, str]]:
    """The records that the log reports in place of writing them: (the record, the whole
    message) of each error that starts with ``prefix``."""
    found = []
    for record in world.caplog.records:
        message = record.getMessage()
        if record.levelno >= logging.ERROR and message.startswith(prefix):
            found.append((json.JSONDecoder().raw_decode(message, len(prefix))[0], message))
    return found


class TestSignInWhileDataIsFull:
    def test_the_sign_in_answers_with_a_session_token_that_works(self, world, monkeypatch):
        fill_data(world, monkeypatch)

        response = sign_in(world)

        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        assert token and response.json()["requires_totp"] is False
        me = world.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert (me.status_code, me.json()["username"]) == (200, "alice")
        assert stored_attempts(world) == []
        assert world.audit() == []

    def test_the_records_it_could_not_write_are_reported_in_the_log(self, world, monkeypatch):
        fill_data(world, monkeypatch)

        sign_in(world, headers={"user-agent": "backup-script/1.0"})

        [(entry, message)] = reported(world, ENTRY_LOST)
        assert (entry["action"], entry["username"], entry["success"]) == ("login_success", "alice", True)
        assert (entry["ip_address"], entry["user_agent"]) == ("testclient", "backup-script/1.0")
        assert entry["timestamp"] and NO_SPACE in message
        [(attempt, message)] = reported(world, ATTEMPT_LOST)
        assert attempt == {
            "username": "alice", "ip_address": "testclient", "user_agent": "backup-script/1.0",
            "success": True, "failure_reason": None,
        }
        assert DB_FULL in message

    def test_a_wrong_password_answers_401_and_both_records_are_reported(self, world, monkeypatch):
        fill_data(world, monkeypatch)

        response = sign_in(world, password="Wrong-pass-2026")

        assert (response.status_code, response.json()["detail"]) == (401, "Incorrect username or password")
        [(entry, _)] = reported(world, ENTRY_LOST)
        assert (entry["action"], entry["username"], entry["success"]) == ("login_failed", "alice", False)
        [(attempt, _)] = reported(world, ATTEMPT_LOST)
        assert (attempt["username"], attempt["success"], attempt["failure_reason"]) == (
            "alice", False, "Incorrect username or password"
        )

    def test_a_two_factor_sign_in_answers_with_a_session_token(self, world, monkeypatch, clock):
        world.set_totp("alice", SECRET)
        fill_data(world, monkeypatch)

        assert sign_in(world).json() == {"access_token": "", "token_type": "bearer", "requires_totp": True}
        response = sign_in(world, totp_code=clock.code(SECRET))

        assert response.status_code == 200, response.text
        assert response.json()["access_token"]
        assert [attempt["success"] for attempt, _ in reported(world, ATTEMPT_LOST)] == [True]

    def test_a_wrong_two_factor_code_answers_401(self, world, monkeypatch, clock):
        world.set_totp("alice", SECRET)
        fill_data(world, monkeypatch)
        wrong = f"{(int(clock.code(SECRET)) + 1) % 1_000_000:06d}"

        response = sign_in(world, totp_code=wrong)

        assert (response.status_code, response.json()["detail"]) == (401, "Invalid 2FA code")
        [(attempt, _)] = reported(world, ATTEMPT_LOST)
        assert (attempt["success"], attempt["failure_reason"]) == (False, "Invalid 2FA code")

    def test_a_commit_that_fails_at_its_end_is_reported_too(self, world, monkeypatch):
        the_audit_log_is_full(monkeypatch)
        every_commit_fails(monkeypatch)

        response = sign_in(world)

        assert response.status_code == 200, response.text
        assert [attempt["username"] for attempt, _ in reported(world, ATTEMPT_LOST)] == ["alice"]
        assert len(reported(world, ENTRY_LOST)) == 1

    def test_a_recovery_code_whose_use_cannot_be_stored_does_not_sign_in(self, world, monkeypatch):
        """A recovery code signs in once: a sign-in whose use of the code cannot be stored would
        leave the code to sign in again (ADR-0238). While /data is full it fails, and the code
        stays unused."""
        world.set_totp("alice", SECRET)
        [code, *_] = generate(world)
        before = stored_codes(world)
        fill_data(world, monkeypatch)

        with monkeypatch.context() as patch:
            # The code's use rewrites its row in place, which the full file still takes; a full
            # disk refuses the journal of every write.
            every_commit_fails(patch)
            response = sign_in(world, recovery_code=code)

        assert response.status_code == 500
        assert "access_token" not in response.text
        assert stored_codes(world) == before


class TestOneWriteAlone:
    def test_a_full_database_keeps_the_audit_entry(self, world):
        fill_the_database(world)

        assert sign_in(world).status_code == 200

        assert [entry["action"] for entry in world.audit()] == ["login_success"]
        assert len(reported(world, ATTEMPT_LOST)) == 1
        assert reported(world, ENTRY_LOST) == []

    def test_a_full_audit_log_keeps_the_login_history_row(self, world, monkeypatch):
        the_audit_log_is_full(monkeypatch)

        assert sign_in(world).status_code == 200

        assert stored_attempts(world) == [("alice", True)]
        assert reported(world, ATTEMPT_LOST) == []
        assert len(reported(world, ENTRY_LOST)) == 1

    def test_with_room_nothing_is_reported(self, world):
        assert sign_in(world).status_code == 200
        assert sign_in(world, password="Wrong-pass-2026").status_code == 401

        assert stored_attempts(world) == [("alice", True), ("alice", False)]
        assert [entry["action"] for entry in world.audit()] == ["login_success", "login_failed"]
        assert reported(world, ATTEMPT_LOST) == reported(world, ENTRY_LOST) == []


class TestLogAction:
    def test_an_entry_it_cannot_write_is_logged_and_the_caller_gets_the_error(self, world, monkeypatch):
        """Every caller's lost entry is in the log; whether its request still answers is the
        caller's choice (a sign-in does)."""
        the_audit_log_is_full(monkeypatch)

        with pytest.raises(OSError):
            asyncio.run(audit_router.log_action(
                action=AuditAction.USER_CREATED, username="admin", resource_type="user",
                resource_id="carol", details="Created new user: carol",
            ))

        [(entry, message)] = reported(world, ENTRY_LOST)
        assert (entry["action"], entry["username"], entry["resource_id"]) == ("user_created", "admin", "carol")
        assert NO_SPACE in message

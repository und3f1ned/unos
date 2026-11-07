"""An API token's last use is committed at most once an hour (ADR-0248; feature
auth.api-token-last-used-throttle).

Every request made with a token used to set its last_used_at and commit it: a write to the
database on the system disk per request (ADR-0214). A request now commits it only when the
stored one is empty or an hour old. The time the server checks and stamps tokens at is the
tests' (auth.utils._token_time); commits are counted as SQLAlchemy reports them on the app's
engine.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.auth import utils as auth_utils
from src.auth.models import APIToken

from .test_smb_accounts import API, World

START = datetime(2026, 11, 7, 9, 0, 0)  # UTC without a zone, as token times are stored
DB_FULL = "database or disk is full"


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()


class Clock:
    def __init__(self) -> None:
        self.now = START


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(auth_utils, "_token_time", lambda: clock.now)
    return clock


@pytest.fixture
def commits(world) -> list[object]:
    """The commits of the app's database while the test runs."""
    seen: list[object] = []

    def on_commit(connection) -> None:
        seen.append(connection)

    engine = world.async_engine.sync_engine
    event.listen(engine, "commit", on_commit)
    yield seen
    event.remove(engine, "commit", on_commit)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def new_token(world: World, owner: str = "alice", name: str = "script") -> tuple[int, str]:
    response = world.client.post(f"{API}/auth/tokens", json={"name": name}, headers=world.headers(owner))
    assert response.status_code == 200, response.text
    return response.json()["id"], response.json()["token"]


def use(world: World, token: str):
    response = world.client.get(f"{API}/auth/me", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response


def stored_last_use(world: World, token_id: int) -> datetime | None:
    with Session(world.engine) as session:
        return session.get(APIToken, token_id).last_used_at


def listed_last_use(world: World, token_id: int, owner: str = "alice") -> datetime | None:
    """The last use that GET /auth/tokens shows (asked with a session token, which writes nothing)."""
    response = world.client.get(f"{API}/auth/tokens", headers=world.headers(owner))
    assert response.status_code == 200, response.text
    [token] = [t for t in response.json() if t["id"] == token_id]
    return None if token["last_used_at"] is None else datetime.fromisoformat(token["last_used_at"])


class TestLastUse:
    def test_100_requests_within_a_minute_commit_it_once(self, world, clock, commits):
        token_id, token = new_token(world)
        commits.clear()

        for request in range(100):
            clock.now = START + timedelta(seconds=request * 0.6)
            use(world, token)

        assert len(commits) == 1
        assert stored_last_use(world, token_id) == START

    def test_it_is_committed_again_once_the_stored_one_is_an_hour_old(self, world, clock, commits):
        token_id, token = new_token(world)
        use(world, token)
        commits.clear()

        clock.now = START + timedelta(minutes=59, seconds=59)
        use(world, token)
        assert (commits, stored_last_use(world, token_id)) == ([], START)

        clock.now = START + timedelta(hours=1)
        use(world, token)
        assert (len(commits), stored_last_use(world, token_id)) == (1, START + timedelta(hours=1))

    def test_the_token_list_shows_a_last_use_at_most_an_hour_old(self, world, clock):
        token_id, token = new_token(world)
        assert listed_last_use(world, token_id) is None

        for minutes in range(0, 3 * 60 + 1, 7):
            clock.now = START + timedelta(minutes=minutes)
            use(world, token)
            shown = listed_last_use(world, token_id)
            assert clock.now - timedelta(hours=1) <= shown <= clock.now, (minutes, shown)

    def test_each_token_has_its_own_last_use(self, world, clock, commits):
        first_id, first = new_token(world, name="first")
        second_id, second = new_token(world, name="second")
        use(world, first)
        clock.now = START + timedelta(minutes=30)
        commits.clear()

        use(world, second)
        use(world, first)

        assert len(commits) == 1
        assert (stored_last_use(world, first_id), stored_last_use(world, second_id)) == (
            START, START + timedelta(minutes=30)
        )

    def test_a_request_that_writes_does_not_take_a_skipped_last_use_along(self, world, clock):
        """A request that commits for its own sake, here one that creates a second token, would
        also write a last use that was set and left uncommitted."""
        token_id, token = new_token(world)
        use(world, token)
        clock.now = START + timedelta(minutes=10)

        created = world.client.post(f"{API}/auth/tokens", json={"name": "second"}, headers=bearer(token))

        assert created.status_code == 200, created.text
        assert stored_last_use(world, token_id) == START

    def test_session_tokens_commit_nothing(self, world, commits):
        for _ in range(5):
            assert world.client.get(f"{API}/auth/me", headers=world.headers("alice")).status_code == 200

        assert commits == []

    def test_a_last_use_that_cannot_be_stored_still_answers(self, world, clock, monkeypatch):
        """A full /data refuses the commit (ADR-0247): the request is answered, and the next one
        tries again."""
        token_id, token = new_token(world)

        async def disk_full(self):
            raise OperationalError("COMMIT", {}, sqlite3.OperationalError(DB_FULL))

        monkeypatch.setattr(AsyncSession, "commit", disk_full)
        for _ in range(2):
            assert use(world, token).json()["username"] == "alice"

        assert stored_last_use(world, token_id) is None
        warnings = [r.getMessage() for r in world.caplog.records
                    if r.levelno == logging.WARNING and "last use of API token" in r.getMessage()]
        assert len(warnings) == 2, warnings
        assert all(f"API token {token_id}:" in message and DB_FULL in message for message in warnings)

"""Repair permissions as a background job (ADR-0363): the start that answers at once, one repair at a
time, the stop, and the job that the events socket sends (repair_router.current_job).

The walk (repair.repair_tree) is a fake here, so these tests run everywhere; test_share_repair.py
walks real folders on Linux, and test_ws_auth.py shows the socket sending the job.
"""
import inspect
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.auth.utils import require_admin, verify_token
from src.database import Base, get_db
from src.security import validators
from src.shares import accounts, constants, repair, repair_router, service
from src.shares.models import SmbShare
from src.storage import pools
from .audit_helpers import audit_entries

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
REGULAR = SimpleNamespace(id=2, username="alice", is_admin=False)
URL = "/api/v1/shares/smb/{}/repair-permissions"
REAL_REPAIR_TREE = repair.repair_tree
# The fake walk ends by itself after this long, so that a start that waits for it fails instead of hanging.
WALK_FOR_AT_MOST = 5.0


def _result(state: str, *, visited: int = 0, changed: int = 0, message: str = "") -> repair.RepairResult:
    """A result as repair_tree answers it: share empty, finished_at None while it runs."""
    now = datetime.now(timezone.utc)
    return repair.RepairResult(
        share="", state=state, started_at=now, finished_at=None if state == "running" else now, visited=visited,
        changed=changed, skipped=dict.fromkeys(repair.SKIP_REASONS, 0), skipped_paths=[], errors=0,
        error_samples=[], message=message,
    )


class SlowWalk:
    """repair_tree replaced: it reports its progress once (5 visited, 2 changed), then walks until it is
    let go (``go``), done with 9 and 4, or stopped (``stop`` of the call), incomplete with 7 and 3."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.reported = threading.Event()
        self.go = threading.Event()

    def __call__(self, path, **kwargs):
        self.calls.append((path, kwargs))
        stop = kwargs.get("stop")
        kwargs["progress"](_result("running", visited=5, changed=2))
        self.reported.set()
        deadline = time.monotonic() + WALK_FOR_AT_MOST
        while not self.go.is_set() and not stop.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        if stop.is_set():
            return _result("incomplete", visited=7, changed=3, message=repair.STOPPED_MESSAGE)
        return _result("done", visited=9, changed=4, message="Permissions repaired.")


def _the_end(timeout: float = 10.0) -> repair.RepairResult:
    """The job once it is no longer running."""
    deadline = time.monotonic() + timeout
    while (job := repair_router.current_job()) is None or job.state == "running":
        assert time.monotonic() < deadline, f"the repair still runs: {job}"
        time.sleep(0.01)
    return job


def _reset() -> None:
    repair_router._last_results.clear()
    repair_router._job = None
    repair_router._running = None
    repair_router.limiter.reset()


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The repair router, as main.py includes it, on a temporary database with the shares Media and
    Docs; the checks before a walk pass, and the walk is a SlowWalk."""
    monkeypatch.setattr(validators.settings, "pools_base_path", str(tmp_path / "pools"))
    base = validators.pools_base()
    path = tmp_path / "repair.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([SmbShare(name="Media", path=str(base / "data" / "media")),
                         SmbShare(name="Docs", path=str(base / "data" / "docs"))])
        session.commit()
    engine.dispose()
    maker = async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool), expire_on_commit=False
    )

    async def override_get_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.state.limiter = repair_router.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(repair_router.router, prefix="/api/v1", dependencies=[Depends(require_admin)])
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: ADMIN

    async def share_problem(db, share_id):
        return None

    walk = SlowWalk()
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(audit))
    monkeypatch.setattr(service, "share_problem", share_problem)
    monkeypatch.setattr(service, "unsafe_parent_of", lambda path: None)
    monkeypatch.setattr(accounts, "gid_problem", lambda: None)
    monkeypatch.setattr(pools, "_mounted_pool_target", lambda name, *, write=False: str(base / name))
    monkeypatch.setattr(repair, "repair_tree", walk)
    _reset()
    yield SimpleNamespace(app=app, client=TestClient(app), walk=walk, audit=audit, media=str(base / "data" / "media"))
    walk.go.set()
    if repair_router._running is not None:
        _the_end()
    _reset()


class TestTheStart:
    def test_it_answers_at_once_while_the_walk_runs_on_and_the_end_is_kept(self, api):
        response = api.client.post(URL.format("media"))

        assert not api.walk.go.is_set()
        assert response.status_code == 202, response.text
        started = response.json()
        assert (started["share"], started["state"], started["finished_at"]) == ("Media", "running", None)
        assert (started["visited"], started["changed"], started["errors"], started["message"]) == (0, 0, 0, "")
        assert started["skipped"] == dict.fromkeys(repair.SKIP_REASONS, 0)
        # The walk's progress is the job now, under the share's name and the start's time.
        assert api.walk.reported.wait(WALK_FOR_AT_MOST)
        job = repair_router.current_job()
        assert (job.share, job.state, job.visited, job.changed, job.finished_at) == ("Media", "running", 5, 2, None)
        assert job.model_dump(mode="json")["started_at"] == started["started_at"]
        # Until it ends, the last run stays the last finished one: none yet.
        assert api.client.get(URL.format("Media")).status_code == 404

        api.walk.go.set()
        end = _the_end()

        assert (end.share, end.state, end.visited, end.changed, end.message) == (
            "Media", "done", 9, 4, "Permissions repaired.")
        assert end.finished_at is not None
        last = api.client.get(URL.format("MEDIA"))
        assert last.status_code == 200
        assert last.json() == end.model_dump(mode="json")
        assert last.json()["started_at"] == started["started_at"]
        assert list(repair_router._last_results) == ["media"]
        [entry] = audit_entries(api.audit)
        assert entry["action"] == "share_permissions_repaired"
        assert (entry["resource_type"], entry["resource_id"], entry["username"]) == ("share", "Media", "admin")
        assert entry["details"] == "Repaired permissions: 4 changed, 0 skipped, 0 errors (done)"
        assert entry["success"] is True

    def test_the_walk_runs_without_the_limit_of_a_request(self, api):
        assert api.client.post(URL.format("Media")).status_code == 202
        assert api.walk.reported.wait(WALK_FOR_AT_MOST)

        [(path, kwargs)] = api.walk.calls
        assert path == api.media
        # Only the walk's own bound: 24 hours, and no number of entries.
        assert sorted(kwargs) == ["gid", "progress", "stop"]
        assert kwargs["gid"] == constants.SMB_GROUP_GID
        limits = inspect.signature(REAL_REPAIR_TREE).parameters
        assert (limits["time_limit"].default, limits["max_entries"].default) == (24 * 3600, None)
        assert repair.INCOMPLETE_MESSAGE == (
            "Stopped after 24 hours; run it again to continue (already repaired entries are skipped quickly)")

    def test_one_repair_runs_at_a_time(self, api):
        assert api.client.post(URL.format("Media")).status_code == 202
        assert api.walk.reported.wait(WALK_FOR_AT_MOST)

        for name in ("Media", "Docs"):
            response = api.client.post(URL.format(name))
            assert (response.status_code, response.json()["detail"]) == (409, "A permissions repair is already running")
        assert len(api.walk.calls) == 1
        assert repair_router.current_job().share == "Media"

        api.walk.go.set()
        assert _the_end().share == "Media"
        # The slot is free again.
        assert api.client.post(URL.format("Docs")).status_code == 202
        assert repair_router.current_job().share == "Docs"

    def test_a_walk_that_raises_ends_as_failed_and_frees_the_slot(self, api, monkeypatch):
        def broken(path, **kwargs):
            raise RuntimeError("the walk broke")

        monkeypatch.setattr(repair, "repair_tree", broken)

        assert api.client.post(URL.format("Media")).status_code == 202
        end = _the_end()

        assert (end.share, end.state, end.visited, end.changed) == ("Media", "failed", 0, 0)
        assert end.message == "The repair stopped on an unexpected error: the walk broke"
        assert api.client.get(URL.format("Media")).json() == end.model_dump(mode="json")
        [entry] = audit_entries(api.audit)
        assert (entry["details"], entry["success"]) == (
            "Repaired permissions: 0 changed, 0 skipped, 0 errors (failed)", False)
        monkeypatch.setattr(repair, "repair_tree", api.walk)
        assert api.client.post(URL.format("Media")).status_code == 202

    def test_a_start_without_a_thread_is_500_and_leaves_nothing_running(self, api, monkeypatch):
        class Unstartable:
            def __init__(self, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        monkeypatch.setattr(repair_router, "threading", SimpleNamespace(Thread=Unstartable))

        response = api.client.post(URL.format("Media"))

        assert (response.status_code, response.json()["detail"]) == (
            500, "The repair could not be started: can't start new thread")
        assert (repair_router.current_job(), repair_router._running) == (None, None)
        monkeypatch.setattr(repair_router, "threading", threading)
        assert api.client.post(URL.format("Media")).status_code == 202


class TestTheStop:
    def test_a_running_repair_is_stopped_and_ends_incomplete(self, api):
        started = api.client.post(URL.format("Media")).json()
        assert api.walk.reported.wait(WALK_FOR_AT_MOST)

        response = api.client.delete(URL.format("media"))

        assert response.status_code == 202, response.text
        assert response.json()["state"] == "running"
        assert response.json()["started_at"] == started["started_at"]
        end = _the_end()
        assert api.walk.calls[0][1]["stop"].is_set()
        assert (end.state, end.visited, end.changed, end.message) == ("incomplete", 7, 3, repair.STOPPED_MESSAGE)
        assert repair.STOPPED_MESSAGE == (
            "Stopped by an administrator; run it again to continue (already repaired entries are skipped quickly)")
        assert api.client.get(URL.format("Media")).json()["state"] == "incomplete"
        [entry] = audit_entries(api.audit)
        assert entry["details"] == "Repaired permissions: 3 changed, 0 skipped, 0 errors (incomplete), stopped by admin"

    def test_only_a_running_repair_of_the_same_share_is_stopped(self, api):
        response = api.client.delete(URL.format("Media"))
        assert (response.status_code, response.json()["detail"]) == (409, "No permissions repair of this share is running")

        api.client.post(URL.format("Media"))
        assert api.walk.reported.wait(WALK_FOR_AT_MOST)
        response = api.client.delete(URL.format("Docs"))
        assert (response.status_code, response.json()["detail"]) == (409, "No permissions repair of this share is running")
        response = api.client.delete(URL.format("nosuch"))
        assert (response.status_code, response.json()["detail"]) == (404, "Share not found")
        assert not api.walk.calls[0][1]["stop"].is_set()

        api.walk.go.set()
        assert _the_end().state == "done"
        assert api.client.delete(URL.format("Media")).status_code == 409


class TestRoles:
    def test_regular_users_can_neither_start_nor_stop_a_repair(self, api):
        api.app.dependency_overrides[verify_token] = lambda: REGULAR

        assert api.client.post(URL.format("Media")).status_code == 403
        assert api.client.delete(URL.format("Media")).status_code == 403
        assert api.walk.calls == []
        assert repair_router.current_job() is None

    def test_the_stop_requires_an_administrator_without_the_router_dependency(self, api):
        api.client.post(URL.format("Media"))
        assert api.walk.reported.wait(WALK_FOR_AT_MOST)
        app = FastAPI()
        app.include_router(repair_router.router, prefix="/api/v1")
        app.dependency_overrides.update(api.app.dependency_overrides)
        app.dependency_overrides[verify_token] = lambda: REGULAR

        assert TestClient(app).delete(URL.format("Media")).status_code == 403
        assert not api.walk.calls[0][1]["stop"].is_set()

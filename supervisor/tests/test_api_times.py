"""Every time that the API answers carries its offset, and a stored time without one is UTC
(architecture.times-with-time-zones, ADR-0401).

* The source of the supervisor reads no clock without a zone (``datetime.now()``, ``utcnow``,
  ``fromtimestamp`` without ``tz``), and every ``datetime`` field of its models is ``UtcDatetime``.
* The walk calls every GET of the API as an administrator, in a process whose local time zone is
  not UTC, with stored data from before the change (naive times in the database and in the files of
  /data), and fails for a time without an offset anywhere in an answer.
* Stored times without an offset read back as UTC: the database's rows and the audit log's entries,
  whose hash chain still holds.
"""
from __future__ import annotations

import ast
import csv
import importlib
import importlib.util
import io
import json
import pkgutil
import re
import tarfile
import time
import typing
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import AfterValidator, BaseModel

import src
from src import times
from src.audit import router as audit_router
from src.audit.schemas import AuditAction
from src.health import problems as health_problems

from . import test_authz
from .audit_helpers import audit_entries
from . import test_audit_log
from .test_authz import _http_operations

authz_db = test_authz.authz_db
client = test_authz.client
admin_headers = test_authz.admin_headers
no_side_effects = test_authz.no_side_effects
world = test_audit_log.world

SRC = Path(times.__file__).resolve().parent
REPO = SRC.parents[1]
API = "/api/v1"

# A date and a time, as ISO 8601 or as SQLite and Python write it, with or without an offset; and
# the date of C's ctime() and of openssl, which carry no offset that ISO 8601 knows.
_ISO_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?P<offset>Z|[+-]\d{2}(?::?\d{2})?)?"
)
_CTIME = re.compile(r"(?:[A-Z][a-z]{2} )?[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4}(?: GMT)?")
# Keys whose number would be a point in time (seconds since the epoch): an answer gives a time as text.
_TIME_KEY = re.compile(r"(?:^|_)(?:time|timestamp|at|date|created|modified|since|until)$")


def times_without_offset(value: object, where: str = "") -> list[str]:
    """Where ``value``, a decoded JSON answer, holds a time without an offset, and that time."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{where}.{key}"
            if isinstance(item, (int, float)) and not isinstance(item, bool) and _TIME_KEY.search(str(key)):
                found.append(f"{here}: {item!r} (a number)")
            found.extend(times_without_offset(item, here))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found.extend(times_without_offset(item, f"{where}[{i}]"))
    elif isinstance(value, str):
        text = value.strip()
        iso = _ISO_TIME.fullmatch(text)
        if (iso and not iso["offset"]) or _CTIME.fullmatch(text):
            found.append(f"{where}: {value!r}")
    return found


class TestTheOffsetCheck:
    @pytest.mark.parametrize("text", [
        "2026-11-07T12:00:00Z", "2026-11-07T12:00:00+00:00", "2026-11-07T12:00:00.123456+00:00",
        "2026-11-08T03:00:00+01:00", "2026-11-07T12:00:00+0545", "the log of 2026-11-07 12:00:00", "12:00",
        "2026-11-07",
    ])
    def test_a_time_with_its_offset_or_no_time_passes(self, text):
        assert times_without_offset({"x": text}) == []

    @pytest.mark.parametrize("text", [
        "2026-11-07T12:00:00", "2026-11-07 12:00:00", "2026-11-07T12:00:00.123456", "2026-11-07T12:00",
        "Fri Nov  6 10:00:00 2026", "Nov  8 01:47:31 2027 GMT",
    ])
    def test_a_time_without_an_offset_fails(self, text):
        assert times_without_offset({"a": [{"b": text}]}) == [f".a[0].b: {text!r}"]

    def test_a_number_under_a_time_key_fails_and_a_duration_passes(self):
        assert times_without_offset({"boot_time": 1.5, "uptime_seconds": 3, "created_at": True}) == [
            ".boot_time: 1.5 (a number)"
        ]


# ==================== The source ====================

def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node


def _named(node: ast.expr, name: str) -> bool:
    """``node`` is ``name`` or ``<module>.name``, as datetime or datetime.datetime."""
    return (isinstance(node, ast.Name) and node.id == name) or (isinstance(node, ast.Attribute) and node.attr == name)


def _none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def clock_reads_without_a_zone(source: str) -> list[int]:
    """The lines of ``source`` that read a clock or convert a time without a zone."""
    lines = []
    for call in _calls(ast.parse(source)):
        name = call.func.attr
        zone = [k.value for k in call.keywords if k.arg == "tz"]
        if name in ("utcnow", "utcfromtimestamp", "today"):
            lines.append(call.lineno)
        elif name == "now" and _named(call.func.value, "datetime"):
            if not (call.args or zone) or any(_none(arg) for arg in [*call.args, *zone]):
                lines.append(call.lineno)
        elif name == "fromtimestamp" and len(call.args) < 2 and not zone:
            lines.append(call.lineno)
        elif name == "astimezone" and not call.args and not call.keywords:
            lines.append(call.lineno)  # into the process's local zone
        elif name in ("localtime", "ctime", "mktime", "strftime") and _named(call.func.value, "time"):
            lines.append(call.lineno)
    return lines


def test_the_clock_check_finds_each_form():
    source = (
        "datetime.now()\n"
        "datetime.utcnow()\n"
        "datetime.fromtimestamp(1)\n"
        "datetime.now(timezone.utc)\n"
        "datetime.fromtimestamp(1, timezone.utc)\n"
        "datetime.fromtimestamp(1, tz=timezone.utc)\n"
        "time.localtime()\n"
        "datetime.datetime.now()\n"
        "datetime.now(tz=None)\n"
        "moment.astimezone()\n"
        "moment.astimezone(timezone.utc)\n"
        "time.strftime('%H')\n"
        "moment.strftime('%H')\n"
    )
    assert clock_reads_without_a_zone(source) == [1, 2, 3, 7, 8, 9, 10, 12]


def test_the_supervisor_reads_no_clock_without_a_zone():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for line in clock_reads_without_a_zone(path.read_text()):
            found.append(f"{path.relative_to(SRC.parent)}:{line}")
    assert found == [], "use src.times (utc_now, db_now, from_epoch): " + ", ".join(found)


def _models() -> list[type[BaseModel]]:
    for info in pkgutil.walk_packages(src.__path__, "src."):
        importlib.import_module(info.name)
    seen, stack = set(), [BaseModel]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return [model for model in seen if model.__module__.startswith("src.")]


def _naive_datetimes(annotation: object, metadata: tuple = ()) -> bool:
    """Whether ``annotation`` holds a datetime that is not UtcDatetime."""
    utc = any(isinstance(m, AfterValidator) and m.func is times.as_utc for m in metadata)
    if annotation is datetime:
        return not utc
    if typing.get_origin(annotation) is typing.Annotated:
        base, *more = typing.get_args(annotation)
        return _naive_datetimes(base, tuple(more))
    return any(_naive_datetimes(arg) for arg in typing.get_args(annotation))


def test_every_datetime_field_of_a_model_is_answered_in_utc():
    fields = [
        f"{model.__module__}.{model.__qualname__}.{name}"
        for model in _models()
        for name, field in model.model_fields.items()
        if _naive_datetimes(field.annotation, tuple(field.metadata))
    ]
    assert sorted(fields) == [], "type them src.times.UtcDatetime: " + ", ".join(sorted(fields))


# ==================== The walk ====================

def _standin_data_globals() -> dict[str, tuple[str, ...]]:
    spec = importlib.util.spec_from_file_location(
        "standin_run_supervisor_times", REPO / "scripts" / "standin" / "run_supervisor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DATA_GLOBALS


@contextmanager
def local_zone(name: str):
    """The process's local time zone is ``name`` (as TZ sets it), and back."""
    import os

    before = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


# Old values: as SQLite's CURRENT_TIMESTAMP, datetime.utcnow() and the container's datetime.now()
# wrote them, without an offset, in UTC.
OLD = "2026-11-07 12:00:00"
OLD_ISO = "2026-11-07T12:00:00"
OLD_UTC = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
TASK_ID = "task_20261107120000_abcd1234"
SERIAL = "WD-OLD1"


def _old_rows(engine, admin_id: int, alice_id: int) -> None:
    """Rows of the database as the supervisor wrote them before ADR-0401."""
    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        raw.execute("UPDATE users SET created_at = ?, updated_at = ?", (OLD, OLD))
        raw.execute(
            "INSERT INTO api_tokens (name, token_hash, token_prefix, user_id, is_active, created_at, last_used_at,"
            " expires_at, permissions) VALUES ('old', 'h', 'unos_xx', ?, 1, ?, ?, ?, '*')",
            (admin_id, OLD, "2026-11-07 13:00:00.123456", "2036-11-07 12:00:00.000000"),
        )
        raw.execute(
            "INSERT INTO login_attempts (username, ip_address, user_agent, success, failure_reason, timestamp)"
            " VALUES ('admin', '192.0.2.1', 'x', 1, NULL, ?)", (OLD,))
        raw.execute(
            "INSERT INTO two_factor_requirement (id, grace_days, enabled_at, enabled_by) VALUES (1, 3650, ?, 'admin')",
            (OLD,))
        raw.execute(
            "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, password_set_at, created_at)"
            " VALUES (?, 'alice', 70001, 1, 'set', ?, ?)", (alice_id, "2026-11-07 12:30:00.250000", OLD))
        raw.execute("INSERT INTO dashboard_layouts (user_id, layout, updated_at) VALUES (?, '[]', ?)",
                    (admin_id, OLD))


def _old_files(data: Path) -> None:
    """Files of /data as the supervisor wrote them before ADR-0401, in its container (UTC)."""
    audit = Path(audit_router.AUDIT_FILE)
    audit.parent.mkdir(parents=True, exist_ok=True)
    old_entry = {"id": "audit_20261107120000000000", "timestamp": "2026-11-07T12:00:00.500000",
                 "action": "login_success", "severity": "info", "username": "admin", "success": True}
    audit.write_text(json.dumps(old_entry) + "\n")

    updater = importlib.import_module("src.updates.updater")
    Path(updater.UPDATE_HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(updater.UPDATE_HISTORY_FILE).write_text(json.dumps([{
        "id": 1, "from_version": "0.1.0", "to_version": "0.1.1", "status": "completed",
        "started_at": OLD_ISO, "completed_at": "2026-11-07T12:05:00.123456",
    }]))
    Path(updater.UPDATE_STATE_FILE).write_text(json.dumps({
        "status": "completed", "progress_percent": 100, "message": "", "error": None,
        "started_at": OLD_ISO, "completed_at": "2026-11-07T12:05:00", "can_rollback": False,
    }))

    scheduler = importlib.import_module("src.scheduler.router")
    # As the scheduler wrote them before its times took their offset (ADR-0264): naive, UTC.
    Path(scheduler.TASKS_FILE).write_text(json.dumps([{
        "id": TASK_ID, "name": "Nightly", "task_type": "snapshot",
        "frequency": "daily", "hour": 3, "minute": 0, "enabled": True, "pool_name": "tank",
        "last_run": "2026-11-07T03:00:00.104071", "last_status": "success",
        "schedule_since": OLD_ISO,
    }]))
    Path(scheduler.RUNS_FILE).write_text(json.dumps([{
        "id": "run_1", "task_id": TASK_ID, "task_name": "Nightly",
        "started_at": "2026-11-07T03:00:00.104071", "finished_at": "2026-11-07T03:00:05",
        "status": "success",
    }]))

    health = importlib.import_module("src.health.router")
    Path(health.RAID_STATUS_FILE).write_text(json.dumps({"tank": {
        "status": "healthy", "total_errors": 0, "forced_read_only": False, "last_checked": OLD_ISO}}))
    Path(health.SMART_HISTORY_FILE).write_text(json.dumps({SERIAL: [{
        "timestamp": OLD_ISO, "device": "/dev/sda", "serial": SERIAL, "attributes": {"5": 0}}]}))

    notifications = importlib.import_module("src.notifications.router")
    Path(notifications.LOGS_FILE).write_text(json.dumps([{
        "id": "log_1", "event": {"type": "info", "title": "t", "message": "m", "timestamp": OLD_ISO},
        "channel": "email", "channel_name": "Mail",
        "sent_at": "2026-11-07T12:00:00+00:00", "success": True,
    }]))

    backup = importlib.import_module("src.backup.router")
    backup_dir = Path(backup.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.dumps({"version": "1.0", "created": OLD_ISO}).encode()
    with tarfile.open(backup_dir / "unos_backup_20261107_120000.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))


@pytest.fixture
def stored_before(authz_db, tmp_path, monkeypatch):
    """The supervisor's /data in tmp_path, with a database and files from before ADR-0401."""
    data = tmp_path / "data"
    data.mkdir()
    for module_name, names in _standin_data_globals().items():
        module = importlib.import_module(module_name)
        for name in names:
            original = getattr(module, name)
            path = Path(str(original))  # the audit log is in a tmp_path already (conftest)
            new = data / (path.relative_to("/data") if path.is_relative_to("/data") else path.name)
            monkeypatch.setattr(module, name, new if isinstance(original, Path) else str(new))
    _old_rows(authz_db.engine, authz_db.admin_id, authz_db.alice_id)
    _old_files(data)
    # A problem that a check found (kept in RAM, ADR-0332).
    monkeypatch.setattr(health_problems, "_by_source", {"pools": {"pool_unmounted:tank": health_problems.Problem(
        key="pool_unmounted:tank", severity="error", kind="pool_unmounted", subject="tank",
        title="t", detail="d")}})

    # The schedules follow the host's zone: a task's next run is answered in it, with its offset (ADR-0266).
    monkeypatch.setattr("src.host.time_zone_name", lambda: "Europe/Berlin")
    # No request leaves this machine (GitHub's releases, notification channels).
    async def offline(self, request, **kwargs):
        raise httpx.ConnectError("no network in this test", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", offline)
    return data


# The GETs whose answers this walk does not read: files and streams, not JSON.
NOT_JSON = {
    f"{API}/backup/download/{{filename}}",
    f"{API}/files/download",
    f"{API}/health/prometheus",
}
# The values of path parameters that name what the seeded data holds.
PATH_VALUES = {
    f"{API}/scheduler/runs/{{task_id}}": f"{API}/scheduler/runs/{TASK_ID}",
    f"{API}/health/smart/history/{{serial}}": f"{API}/health/smart/history/{SERIAL}",
}
# Queries for GETs that need one.
QUERIES = {
    f"{API}/audit/logs/export": [{"format": "json"}, {"format": "csv"}],
    f"{API}/files/list": [{"path": "/"}],
}
# Answers that must hold times in the seeded data, so that the walk cannot pass by reaching none.
MUST_HOLD_TIMES = {
    f"{API}/audit/logs",
    f"{API}/audit/logs/export",
    f"{API}/auth/me",
    f"{API}/auth/tokens",
    f"{API}/auth/users",
    f"{API}/backup/list",
    f"{API}/health/full",
    f"{API}/health/raid-monitor/status",
    f"{API}/health/smart/history/{{serial}}",
    f"{API}/notifications/logs",
    f"{API}/scheduler/runs",
    f"{API}/scheduler/runs/{{task_id}}",
    f"{API}/scheduler/tasks",
    f"{API}/security/login-history",
    f"{API}/security/two-factor-requirement",
    f"{API}/updates/history",
    f"{API}/updates/progress",
}


# How many GETs answer 200 here at least (53 do): the others need the host, Docker or the network.
# system.resource-monitor (ADR-0399) removed six monitor routes that answered here and added one;
# ADR-0416 removed /system/network, /system/pcie and /logs/files, and the firewall's GET asks the host.
MIN_ANSWERED = 53


def _holds_a_time(value: object) -> bool:
    if isinstance(value, dict):
        return any(_holds_a_time(v) for v in value.values())
    if isinstance(value, list):
        return any(_holds_a_time(v) for v in value)
    return isinstance(value, str) and _ISO_TIME.fullmatch(value.strip()) is not None


def _csv_values(text: str) -> list[str]:
    return [cell for row in csv.reader(io.StringIO(text)) for cell in row]


class TestEveryAnswerCarriesItsOffset:
    def test_the_get_answers_of_an_administrator(self, client, admin_headers, stored_before):
        problems, holding, answered = [], set(), set()
        with local_zone("Asia/Kathmandu"):  # UTC+05:45: a naive local time differs from UTC
            # Something new of each kind of row and entry, written now.
            assert client.post(f"{API}/auth/tokens", json={"name": "new", "expires_in_days": 30},
                               headers=admin_headers).status_code == 200
            for method, path, _ in sorted(_http_operations()):
                url = PATH_VALUES.get(path, path)
                if method != "GET" or path in NOT_JSON or "{" in url:
                    continue
                for query in QUERIES.get(path, [{}]):
                    response = client.get(url, params=query, headers=admin_headers)
                    if response.status_code != 200:
                        if path in MUST_HOLD_TIMES:
                            problems.append(f"GET {path}: {response.status_code} {response.text[:200]}")
                        continue
                    answered.add(path)
                    if response.headers.get("content-type", "").startswith("text/csv"):
                        body: object = _csv_values(response.text)
                    else:
                        body = response.json()
                    problems.extend(f"GET {path} {query or ''}{p}" for p in times_without_offset(body))
                    if _holds_a_time(body):
                        holding.add(path)
        assert problems == [], "\n".join(problems)
        assert len(answered) >= MIN_ANSWERED, f"answered: {sorted(answered)}"
        assert MUST_HOLD_TIMES - holding == set(), f"answered: {sorted(answered)}"

    def test_the_stats_of_the_events_socket(self, monkeypatch):
        from src.websocket import events

        with local_zone("Asia/Kathmandu"):
            message = events.get_system_stats()
        assert message["type"] == "stats"
        assert times_without_offset(message) == []
        assert _holds_a_time(message)


# ==================== Stored times without an offset ====================

class TestStoredTimesReadBackAsUtc:
    """The database keeps naive UTC (SQLite's CURRENT_TIMESTAMP, db_now) and keeps the rows it has;
    the files of /data keep what the supervisor wrote before, in its container's clock, UTC."""

    def test_old_rows_and_entries_are_answered_with_their_clock_time_in_utc(self, client, admin_headers,
                                                                             stored_before):
        with local_zone("Asia/Kathmandu"):
            def get(path):
                response = client.get(f"{API}{path}", headers=admin_headers)
                assert response.status_code == 200, response.text
                return response.json()

            users = {user["username"]: user for user in get("/auth/users")}
            tokens = {token["name"]: token for token in get("/auth/tokens")}
            [attempt] = [a for a in get("/security/login-history")["attempts"] if a["ip_address"] == "192.0.2.1"]
            audit = {entry["id"]: entry for entry in get("/audit/logs")}
            [update] = get("/updates/history")
            [run] = get(f"/scheduler/runs/{TASK_ID}")
            [task] = get("/scheduler/tasks")
            raid = get("/health/raid-monitor/status")["last_status"]["tank"]
            smart = get(f"/health/smart/history/{SERIAL}")

        assert users["admin"]["created_at"] == "2026-11-07T12:00:00Z"
        assert users["alice"]["smb"]["password_set_at"] == "2026-11-07T12:30:00.250000Z"
        assert (tokens["old"]["created_at"], tokens["old"]["last_used_at"], tokens["old"]["expires_at"]) == (
            "2026-11-07T12:00:00Z", "2026-11-07T13:00:00.123456Z", "2036-11-07T12:00:00Z")
        assert attempt["timestamp"] == "2026-11-07T12:00:00Z"
        assert get_requirement(client, admin_headers)["enabled_at"] == "2026-11-07T12:00:00Z"
        assert audit["audit_20261107120000000000"]["timestamp"] == "2026-11-07T12:00:00.500000Z"
        assert (update["started_at"], update["completed_at"]) == ("2026-11-07T12:00:00Z", "2026-11-07T12:05:00.123456Z")
        # The scheduler's form, +00:00 (ADR-0266).
        assert (run["started_at"], run["finished_at"]) == ("2026-11-07T03:00:00.104071+00:00", "2026-11-07T03:00:05+00:00")
        assert (task["last_run"], task["schedule_since"]) == (
            "2026-11-07T03:00:00.104071+00:00", "2026-11-07T12:00:00+00:00")
        assert task["next_run"].endswith(("+01:00", "+02:00"))  # the host's zone, Europe/Berlin (ADR-0266)
        assert raid["last_checked"] == "2026-11-07T12:00:00Z"
        assert (smart["oldest"], smart["data"][0]["timestamp"]) == ("2026-11-07T12:00:00Z", "2026-11-07T12:00:00Z")

    def test_new_rows_are_stored_in_utc_whatever_the_process_zone(self, client, admin_headers, authz_db):
        with local_zone("Asia/Kathmandu"):
            response = client.post(f"{API}/auth/tokens", json={"name": "new", "expires_in_days": 30},
                                   headers=admin_headers)
            assert response.status_code == 200, response.text
            answered = response.json()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
        with authz_db.engine.connect() as conn:
            created, expires = conn.exec_driver_sql(
                "SELECT created_at, expires_at FROM api_tokens WHERE name = 'new'").one()
        # Naive text, as the column keeps it: UTC, not the process's clock 5:45 ahead.
        assert abs(datetime.fromisoformat(created) - now) < timedelta(minutes=2)
        assert abs(datetime.fromisoformat(expires) - (now + timedelta(days=30))) < timedelta(minutes=2)
        assert times.parse_time(answered["expires_at"]) == times.as_utc(datetime.fromisoformat(expires))


def get_requirement(client, headers) -> dict:
    response = client.get(f"{API}/security/two-factor-requirement", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestAuditLogAcrossTheChange:
    """The audit log's lines keep their text: the hash chain of the entries written before the change
    still holds, and their times without an offset are UTC (ADR-0328, ADR-0401)."""

    OLD = ("2026-11-07T11:59:59.250000", "2026-11-07T12:00:00", "2026-11-07T12:00:00.500000")

    @pytest.fixture
    def old_log(self, world):
        # Entries as the supervisor wrote them before, in its container (UTC): chained, without an offset.
        for n, stamp in enumerate(self.OLD):
            with audit_router._log_lock():
                last = audit_router._last_of_log()
                audit_router._write_chained(
                    {"id": f"audit_old_{n}", "timestamp": stamp, "action": "other", "severity": "info",
                     "username": "admin", "details": f"old {n}", "success": True},
                    last.hash if last else None)
        audit_router._heads.clear()  # a new supervisor, after the update
        return world

    def listed(self, world, **params) -> list[dict]:
        response = world.client.get(f"{API}/audit/logs", headers=world.headers("admin"),
                                    params={"limit": 1000, **params})
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_old_chain_holds_and_the_new_entries_continue_it(self, old_log):
        before = old_log.audit_file.read_bytes()
        with local_zone("Asia/Kathmandu"):
            asyncio_run(audit_router.log_action(AuditAction.OTHER, details="new"))
            now = datetime.now(timezone.utc)
            entries = self.listed(old_log)

        assert old_log.audit_file.read_bytes().startswith(before)  # nothing written before is rewritten
        lines = audit_entries(old_log.audit_file)
        assert [line["timestamp"] for line in lines[:3]] == list(self.OLD)
        new = lines[-1]
        assert new["timestamp"].endswith("Z")
        assert abs(times.parse_time(new["timestamp"]) - now) < timedelta(minutes=1)
        assert new["prev_hash"] == lines[2]["hash"]

        by_details = {e["details"]: e for e in entries}
        assert {details: e["chain"] for details, e in by_details.items() if details.startswith(("old", "new"))} == {
            "old 0": "ok", "old 1": "ok", "old 2": "ok", "new": "ok"}
        assert [by_details[f"old {n}"]["timestamp"] for n in range(3)] == [
            "2026-11-07T11:59:59.250000Z", "2026-11-07T12:00:00Z", "2026-11-07T12:00:00.500000Z"]
        # Newest first by their time, whatever form their lines hold.
        instants = [times.parse_time(e["timestamp"]) for e in entries]
        assert instants == sorted(instants, reverse=True)
        # The hash of each entry is over its line's text: a new line holds the answered time, and an
        # older one the answered time without its Z, so an export can be checked again (ADR-0328).
        exported = {e["id"]: e for e in old_log.client.get(
            f"{API}/audit/logs/export", headers=old_log.headers("admin")).json()["entries"]}
        for line in lines:
            entry = exported[line["id"]]
            recorded = entry["timestamp"] if line is new else entry["timestamp"].removesuffix("Z")
            fields = {key: value for key, value in entry.items() if key not in ("chain", "hash")}
            assert line["hash"] == audit_router.entry_hash(audit_router.AuditEntry.model_validate(
                {**{k: v for k, v in fields.items() if k in line}, "timestamp": recorded}))

    @pytest.mark.parametrize(("params", "expected"), [
        ({"start_date": "2026-11-07T12:00"}, ["old 2", "old 1"]),  # without an offset: UTC
        ({"start_date": "2026-11-07T17:45+05:45"}, ["old 2", "old 1"]),  # 12:00 UTC
        ({"start_date": "2026-11-07T17:45:00.4+05:45"}, ["old 2"]),
        ({"end_date": "2026-11-07T17:44:59+05:45"}, ["old 0"]),  # its whole second
        ({"end_date": "2026-11-07T12:00:00Z"}, ["old 2", "old 1", "old 0"]),  # 12:00:00.5 too
        ({"end_date": "2026-11-07T12:00:00.0Z"}, ["old 1", "old 0"]),
        ({"start_date": "2026-11-07T11:59:59.250Z", "end_date": "2026-11-07T11:59:59.250Z"}, ["old 0"]),
        # The browser's day in Kathmandu, as the page sends it: its first and its last millisecond.
        ({"start_date": "2026-11-06T18:15:00.000Z", "end_date": "2026-11-07T18:14:59.999Z"},
         ["old 2", "old 1", "old 0"]),
        ({"end_date": "2026-11-06T18:14:59.999Z"}, []),
    ])
    def test_the_bounds_compare_instants_and_take_an_offset(self, old_log, params, expected):
        listed = [e["details"] for e in self.listed(old_log, search="old", **params)]
        assert listed == expected
        response = old_log.client.get(f"{API}/audit/logs/count", headers=old_log.headers("admin"),
                                      params={"search": "old", **params})
        assert response.json() == {"count": len(expected)}

    @pytest.mark.parametrize("bound", ["2026-11-07+05:45", "2026-11-07T12:00+5:45", "2026-11-07T12:00:00+05",
                                       "2026-11-07T12:00 UTC", "2026-11-07T12:00:00+24:00"])
    def test_another_form_of_offset_is_refused(self, old_log, bound):
        response = old_log.client.get(f"{API}/audit/logs", headers=old_log.headers("admin"),
                                      params={"start_date": bound})
        assert response.status_code == 422
        assert "with an offset (Z or +HH:MM) or in UTC" in response.json()["detail"]

    def test_the_exports_answer_the_old_times_in_utc(self, old_log):
        csv_answer = old_log.client.get(f"{API}/audit/logs/export", headers=old_log.headers("admin"),
                                        params={"format": "csv", "search": "old"})
        rows = list(csv.DictReader(io.StringIO(csv_answer.text)))
        assert [row["Timestamp"] for row in rows] == [
            "2026-11-07T12:00:00.500000Z", "2026-11-07T12:00:00Z", "2026-11-07T11:59:59.250000Z"]
        exported = old_log.client.get(f"{API}/audit/logs/export", headers=old_log.headers("admin"),
                                      params={"format": "json", "search": "old"}).json()
        assert times_without_offset(exported) == []
        assert [e["timestamp"] for e in exported["entries"]] == [
            "2026-11-07T12:00:00.500000Z", "2026-11-07T12:00:00Z", "2026-11-07T11:59:59.250000Z"]

    def test_clearing_before_a_time_with_an_offset_keeps_the_rest_and_the_chain(self, old_log):
        response = old_log.client.delete(f"{API}/audit/logs", headers=old_log.headers("admin"),
                                         params={"before_date": "2026-11-07T17:45+05:45"})
        assert response.status_code == 200, response.text
        assert response.json()["deleted_count"] == 1
        entries = self.listed(old_log)
        assert [e["details"] for e in entries if e["details"].startswith("old")] == ["old 2", "old 1"]
        assert {e["chain"] for e in entries} == {"ok"}

    def test_a_clearing_bound_of_another_form_is_refused_and_removes_nothing(self, old_log):
        response = old_log.client.delete(f"{API}/audit/logs", headers=old_log.headers("admin"),
                                         params={"before_date": "yesterday"})
        assert response.status_code == 422
        assert len(audit_entries(old_log.audit_file)) == 3


def asyncio_run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


# ==================== Times that the supervisor reads from tools ====================

class TestTimesOfTools:
    def test_btrfs_prints_the_start_of_a_scrub_in_its_containers_clock_utc(self):
        from src.storage.pools import btrfs_time

        assert btrfs_time("Fri Nov  6 10:00:00 2026") == datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
        assert btrfs_time("Fri Nov 16 10:00:00 2026 ") == datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
        assert btrfs_time("Mon Feb 30 10:00:00 2026") is None  # no such day
        assert btrfs_time("2026-11-06 10:00:00") is None

    def test_a_scrub_is_sent_over_the_events_socket_with_its_offset(self, monkeypatch):
        from src.storage import scrubs
        from src.storage.pools import btrfs_time
        from src.storage.schemas import ScrubStatus

        monkeypatch.setattr(scrubs, "_scrubs", {})
        scrubs.note("tank", ScrubStatus(pool="tank", status="running", last_run=btrfs_time("Fri Nov  6 10:00:00 2026")))
        message = json.loads(json.dumps(scrubs.current()))
        assert message[0]["last_run"] == "2026-11-06T10:00:00Z"

    def test_openssl_prints_a_certificates_dates_in_gmt(self):
        from src.security.router import openssl_time

        assert openssl_time("Nov  8 01:47:31 2027 GMT\n") == datetime(2027, 11, 8, 1, 47, 31, tzinfo=timezone.utc)
        assert openssl_time("2027-11-08 01:47:31Z") is None

    def test_a_notification_event_takes_a_time_without_an_offset_as_utc(self):
        from src.notifications.router import _event_time
        from src.notifications.schemas import NotificationEvent

        event = NotificationEvent(type="info", title="t", message="m", timestamp="2026-11-07T12:00:00")
        assert _event_time(event) == "2026-11-07T12:00:00Z"
        with local_zone("Asia/Kathmandu"):
            now = times.parse_time(_event_time(NotificationEvent(type="info", title="t", message="m")))
        assert abs(now - datetime.now(timezone.utc)) < timedelta(minutes=1)


def test_a_utc_field_assigned_a_time_without_a_zone_is_answered_in_utc():
    from src.updates.schemas import UpdateProgress, UpdateStatus

    progress = UpdateProgress(status=UpdateStatus.IDLE)
    progress.completed_at = datetime(2026, 11, 7, 12, 0)  # no validation on assignment
    assert json.loads(progress.model_dump_json())["completed_at"] == "2026-11-07T12:00:00Z"


class TestTimesThatAreNone:
    """A value that is no time, or one outside the calendar in UTC, is refused or skipped, never a 500."""

    def test_an_event_time_outside_the_calendar_is_a_validation_error(self):
        from pydantic import ValidationError
        from src.notifications.schemas import NotificationEvent

        for stamp in ("0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-01:00", "yesterday"):
            with pytest.raises(ValidationError):
                NotificationEvent(type="info", title="t", message="m", timestamp=stamp)

    def test_a_history_entry_whose_event_time_is_no_time_keeps_its_place(self, monkeypatch, tmp_path):
        notifications = importlib.import_module("src.notifications.router")
        monkeypatch.setattr(notifications, "LOGS_FILE", str(tmp_path / "logs.json"))
        entry = {"id": "log_1", "event": {"type": "info", "title": "t", "message": "m", "timestamp": ""},
                 "channel": "email", "channel_name": "Mail", "sent_at": OLD_ISO, "success": True}
        Path(notifications.LOGS_FILE).write_text(json.dumps([entry]))
        [log] = notifications.load_logs()
        assert (log.event.timestamp, log.model_dump(mode="json")["sent_at"]) == (None, "2026-11-07T12:00:00+00:00")

    @pytest.mark.parametrize("stamp", ["0001-01-01T00:00:00+05:00", "not a time"])
    def test_an_audit_line_whose_time_is_none_is_skipped_as_damaged(self, world, stamp):
        world.audit_file.parent.mkdir(parents=True, exist_ok=True)
        world.audit_file.write_text(json.dumps(
            {"id": "audit_bad", "timestamp": stamp, "action": "other", "details": "bad"}) + "\n")
        asyncio_run(audit_router.log_action(AuditAction.OTHER, details="next"))
        response = world.client.get(f"{API}/audit/logs", headers=world.headers("admin"))
        assert response.status_code == 200, response.text
        assert [e["details"] for e in response.json()] == ["next"]
        response = world.client.get(f"{API}/audit/logs", headers=world.headers("admin"),
                                    params={"start_date": "0001-01-01T00:00+05:00"})
        assert response.status_code == 422

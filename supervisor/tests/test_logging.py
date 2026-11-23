"""The supervisor's log (ADR-0398): one JSON object per line, set up in one place before uvicorn
logs, INFO by default and changed by UNOS_LOG_LEVEL, nothing written while it idles, a traceback
for the exception a handler answers, no secret in any line and no print() in supervisor/src."""
from __future__ import annotations

import ast
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import logging_setup
from src.logging_setup import JsonFormatter, configure_logging, resolve_level

SUPERVISOR = Path(__file__).resolve().parents[1]
SRC = SUPERVISOR / "src"
API = "/api/v1"


@pytest.fixture
def restore_logging():
    """Puts the loggers back as the test session had them."""
    names = ["", "src", "uvicorn", "uvicorn.error", "uvicorn.access"]
    saved = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).level, logging.getLogger(n).propagate)
             for n in names}
    yield
    for n, (handlers, level, propagate) in saved.items():
        log = logging.getLogger(n)
        log.handlers[:] = handlers
        log.setLevel(level)
        log.propagate = propagate


def _record(msg="hello", level=logging.INFO, name="src.x", args=(), exc_info=None, **extra):
    record = logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)
    record.__dict__.update(extra)
    return record


class TestJsonFormatter:
    def test_a_line_is_one_json_object_with_time_level_logger_and_message(self):
        line = JsonFormatter().format(_record("Pool %s is mounted", args=("tank",), name="src.storage.pools"))

        entry = json.loads(line)
        assert "\n" not in line
        assert list(entry) == ["time", "level", "logger", "message"]
        assert (entry["level"], entry["logger"], entry["message"]) == (
            "INFO", "src.storage.pools", "Pool tank is mounted")
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", entry["time"])

    def test_a_multi_line_message_and_non_ascii_stay_one_line(self):
        line = JsonFormatter().format(_record("first\nsecond \u0434\u0438\u0441\u043a"))

        assert "\n" not in line
        assert json.loads(line)["message"] == "first\nsecond \u0434\u0438\u0441\u043a"

    def test_the_traceback_is_a_field_of_the_same_line(self):
        try:
            raise ValueError("boom")
        except ValueError:
            line = JsonFormatter().format(_record("Failed", logging.ERROR, exc_info=sys.exc_info()))

        entry = json.loads(line)
        assert "\n" not in line
        assert entry["exception"].startswith("Traceback (most recent call last)")
        assert entry["exception"].rstrip().endswith("ValueError: boom")

    def test_extra_fields_go_into_fields(self):
        entry = json.loads(JsonFormatter().format(_record("Mounted", pool="tank", size=3)))

        assert entry["fields"] == {"pool": "tank", "size": 3}

    def test_a_field_that_is_not_json_is_written_as_text(self):
        entry = json.loads(JsonFormatter().format(_record("x", path=Path("/mnt/a"))))

        assert entry["fields"] == {"path": "/mnt/a"}

    @pytest.mark.parametrize("text", [
        "Authorization: " + "Bearer " + "abc.def.ghi",
        "password=hunter2-xyz",
        "GET /api/v1/x?token=hunter2-xyz&a=1",
        '{"password": "hunter2-xyz", "a": 1}',
        "totp_code='hunter2-xyz'",
        "Cookie: __Host-unos-session=hunter2-xyz",
        "session_key: hunter2-xyz",
        "1 validation error for X\npassword\n  Value error, x [type=value_error, input_value='hunter2-xyz', input_type=str]",
        "(sqlite3.IntegrityError) x\n[SQL: UPDATE users SET totp_secret=?]\n[parameters: ('hunter2-xyz', 2)]\n(Background)",
    ])
    def test_a_secret_shaped_value_is_masked_in_the_message_the_traceback_and_the_fields(self, text):
        try:
            raise RuntimeError(text)
        except RuntimeError:
            line = JsonFormatter().format(_record(text, exc_info=sys.exc_info(), detail=text))

        assert "hunter2-xyz" not in line and "abc.def.ghi" not in line
        assert "[redacted]" in line


    def test_a_long_text_without_a_secret_is_masked_in_linear_time(self):
        text = "a" * 300_000 + " token " + "b" * 300_000

        started = time.monotonic()
        masked = logging_setup.mask_secrets(text)

        assert masked == text
        assert time.monotonic() - started < 2


class TestLevel:
    @pytest.mark.parametrize("value, expected", [
        (None, ("INFO", None)), ("", ("INFO", None)), ("debug", ("DEBUG", None)), (" Warning ", ("WARNING", None)),
        ("ERROR", ("ERROR", None)), ("CRITICAL", ("CRITICAL", None)), ("verbose", ("INFO", "verbose")),
        ("10", ("INFO", "10")), ("NOTSET", ("INFO", "NOTSET")),
    ])
    def test_resolve_level(self, value, expected):
        level, invalid = resolve_level(value)

        assert (logging.getLevelName(level), invalid) == expected

    def test_the_default_is_info(self, restore_logging, monkeypatch):
        monkeypatch.delenv("UNOS_LOG_LEVEL", raising=False)

        configure_logging()

        assert logging.getLogger("src").level == logging.INFO

    def test_an_administrator_sets_it_in_the_environment(self, restore_logging, monkeypatch):
        monkeypatch.setenv("UNOS_LOG_LEVEL", "warning")

        configure_logging()

        assert [logging.getLogger(n).level for n in ("src", "uvicorn", "uvicorn.error", "uvicorn.access")] == [
            logging.WARNING] * 4

    def test_a_library_logs_warnings_and_above_whatever_the_level(self, restore_logging, monkeypatch):
        """At DEBUG aiosqlite writes the bound values of every statement: password hashes, secrets."""
        monkeypatch.setenv("UNOS_LOG_LEVEL", "DEBUG")

        configure_logging()

        assert logging.getLogger("src.storage.pools").isEnabledFor(logging.DEBUG)
        assert logging.getLogger("uvicorn.error").isEnabledFor(logging.DEBUG)
        for library in ("aiosqlite", "sqlalchemy.engine", "asyncio", "docker", "urllib3", "httpx"):
            assert not logging.getLogger(library).isEnabledFor(logging.INFO), library
            assert logging.getLogger(library).isEnabledFor(logging.WARNING), library

    def test_a_value_that_is_no_level_keeps_info_and_says_so_once(self, restore_logging, monkeypatch, capsys):
        monkeypatch.setenv("UNOS_LOG_LEVEL", "loud")

        configure_logging()

        assert logging.getLogger("src").level == logging.INFO
        lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
        assert [(e["level"], "loud" in e["message"]) for e in lines] == [("WARNING", True)]


class TestSetup:
    def test_every_logger_writes_json_to_stderr_through_one_handler(self, restore_logging, monkeypatch, capsys):
        monkeypatch.delenv("UNOS_LOG_LEVEL", raising=False)

        configure_logging()
        configure_logging()  # again: no second handler, so no line twice
        logging.getLogger("src.anything").info("one")
        logging.getLogger("uvicorn.error").info("two")

        out = capsys.readouterr()
        assert out.out == ""
        lines = [json.loads(line) for line in out.err.splitlines()]
        assert [(e["logger"], e["message"]) for e in lines] == [("src.anything", "one"), ("uvicorn.error", "two")]
        assert len(logging.getLogger().handlers) == 1
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert logging.getLogger(name).handlers == [] and logging.getLogger(name).propagate

    def test_uvicorns_handlers_are_replaced_and_the_access_filter_stays(self, restore_logging, capsys):
        import uvicorn.config
        from logging.config import dictConfig

        dictConfig(uvicorn.config.LOGGING_CONFIG)  # what uvicorn does before it loads the app

        configure_logging()
        logging.getLogger("uvicorn.error").warning("late")

        assert json.loads(capsys.readouterr().err)["logger"] == "uvicorn.error"
        from src.main import _RefusedOrFailedRequestFilter
        assert any(isinstance(f, _RefusedOrFailedRequestFilter) for f in logging.getLogger("uvicorn.access").filters)

    def test_main_sets_it_up_before_anything_else_is_imported(self):
        tree = ast.parse((SRC / "main.py").read_text())
        calls = [i for i, node in enumerate(tree.body)
                 if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and getattr(node.value.func, "id", "") == "configure_logging"]
        imports = [i for i, node in enumerate(tree.body)
                   if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module != "logging_setup"]

        assert len(calls) == 1 and calls[0] < min(imports)

    def test_the_stand_in_hands_uvicorn_the_same_configuration(self):
        text = (SUPERVISOR.parent / "scripts" / "standin" / "run_supervisor.py").read_text()

        assert "log_config=logging_config(" in text


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("uvicorn")
    (tmp / "wrapped.py").write_text(textwrap.dedent(f"""
        import src.audit.router as audit
        audit.AUDIT_FILE = {str(tmp / "audit.jsonl")!r}
        from src.main import app
    """))
    sock = __import__("socket").socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    env = {**os.environ, "UNOS_DB_PATH": str(tmp / "unos.db"), "UNOS_DISABLE_BACKGROUND_TASKS": "1",
           "UNOS_JWT_SECRET": secrets.token_hex(32), "TMPDIR": str(tmp), "PYTHONPATH": f"{tmp}{os.pathsep}{SUPERVISOR}",
           "NO_PROXY": "127.0.0.1"}
    env.pop("UNOS_LOG_LEVEL", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "wrapped:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=SUPERVISOR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[str] = []
    threading.Thread(target=lambda: [lines.append(line.rstrip("\n")) for line in proc.stdout], daemon=True).start()
    deadline = time.monotonic() + 60
    while not any("Uvicorn running" in line for line in lines):
        assert proc.poll() is None, "\n".join(lines)
        assert time.monotonic() < deadline, "\n".join(lines)
        time.sleep(0.1)
    yield SimpleNamespace(port=port, lines=lines)
    proc.terminate()
    proc.wait(timeout=30)



class TestRealUvicorn:
    """The image's own command, `uvicorn src.main:app`, as a subprocess: every line it writes is
    JSON, uvicorn's included, and a quiet minute and a sign-in write what ADR-0398 counts."""

    @staticmethod
    def _call(server, method, path, body=None, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")

    def test_the_whole_life_of_the_server(self, server):
        password = "Real-uvicorn-pass-2026"
        time.sleep(0.5)
        startup = list(server.lines)
        mark = len(server.lines)

        # A quiet stretch: health checks and reads that answer below 400, and a sign-in
        for _ in range(20):
            assert self._call(server, "GET", f"{API}/health")[0] == 200
        assert self._call(server, "POST", f"{API}/auth/setup", {"username": "owner", "password": password})[0] == 200
        status, token = self._call(server, "POST", f"{API}/auth/login", {"username": "owner", "password": password})
        assert status == 200
        auth = {"Authorization": f"Bearer {token['access_token']}"}
        assert self._call(server, "GET", f"{API}/auth/me", headers=auth)[0] == 200
        time.sleep(1)
        assert server.lines[mark:] == [], "a quiet minute and a sign-in write no line"

        # A refused request writes the one access line (ADR-0338)
        assert self._call(server, "POST", f"{API}/auth/login", {"username": "owner", "password": "nope-nope-1"})[0] == 401
        time.sleep(1)
        refused = server.lines[mark:]
        assert len(refused) == 1
        entry = json.loads(refused[0])
        assert entry["logger"] == "uvicorn.access" and "401" in entry["message"]

        # Every line from the first on is one JSON object, uvicorn's own included
        entries = [json.loads(line) for line in server.lines]
        assert {"uvicorn.error"} <= {e["logger"] for e in entries}
        assert any("Started server process" in e["message"] for e in entries)
        assert all(set(e) >= {"time", "level", "logger", "message"} for e in entries)
        # No password or token in any line
        blob = "\n".join(server.lines)
        assert password not in blob and token["access_token"] not in blob
        assert len(startup) >= 3


class TestNoPrint:
    def test_supervisor_src_has_no_print_call(self):
        found = []
        for path in sorted(SRC.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                    found.append(f"{path.relative_to(SUPERVISOR)}:{node.lineno}")

        assert found == []


def _is_log_call(node: ast.AST) -> bool:
    """logger.exception(...), or any logger call that passes exc_info."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr == "exception":
        return True
    return node.func.attr in {"error", "warning", "critical", "info", "debug", "log"} and any(
        kw.arg == "exc_info" for kw in node.keywords)


def _literal_client_error(call: ast.Call) -> bool:
    """HTTPException(400, ...) or (status_code=409, ...): an expected error, which is not logged."""
    status = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "status_code"), None)
    return isinstance(status, ast.Constant) and isinstance(status.value, int) and status.value < 500


class TestHandlersLogWhatTheyAnswer:
    def test_a_handler_that_catches_everything_and_answers_logs_it_with_its_traceback(self):
        """Every `except Exception` (or bare, or BaseException) of supervisor/src whose body raises an
        HTTPException (or a helper's HTTP error) other than a literal 4xx calls logger.exception first."""
        missing = []
        for path in sorted(SRC.rglob("*.py")):
            for handler in (n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.ExceptHandler)):
                caught = [ast.unparse(e) for e in (handler.type.elts if isinstance(handler.type, ast.Tuple)
                                                   else [handler.type] if handler.type else [])]
                if handler.type is not None and not {"Exception", "BaseException"} & set(caught):
                    continue
                body = ast.Module(body=handler.body, type_ignores=[])
                answers = any(
                    isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
                    and ast.unparse(n.exc.func) in {"HTTPException", "_http_error"}
                    and not _literal_client_error(n.exc)
                    for n in ast.walk(body))
                if answers and not any(_is_log_call(n) for n in ast.walk(body)):
                    missing.append(f"{path.relative_to(SUPERVISOR)}:{handler.lineno}")

        assert missing == []

    def test_a_failing_docker_call_is_logged_once_with_its_traceback_and_answers_500(self, monkeypatch, caplog):
        from fastapi.testclient import TestClient
        from src.auth.utils import require_admin, verify_token
        from src.containers import router as containers
        from src.main import app

        monkeypatch.setattr(containers.docker_ops, "list_containers", AsyncMock(side_effect=OSError("socket gone")),
                            raising=False)
        monkeypatch.setattr(containers, "_docker", AsyncMock(side_effect=OSError("socket gone")))
        app.dependency_overrides[verify_token] = app.dependency_overrides[require_admin] = (
            lambda: {"username": "a", "is_admin": True})
        try:
            with caplog.at_level(logging.INFO):
                response = TestClient(app, raise_server_exceptions=False).get(f"{API}/containers")
        finally:
            app.dependency_overrides.pop(verify_token, None)
            app.dependency_overrides.pop(require_admin, None)

        assert response.status_code == 500
        records = [r for r in caplog.records if r.exc_info]
        assert len(records) == 1
        assert records[0].levelno == logging.ERROR and records[0].name == "src.containers.router"
        assert "socket gone" in JsonFormatter().format(records[0])


class TestIdleWritesNothing:
    def test_a_loop_logs_only_in_the_handler_of_an_error(self):
        """Every background loop of supervisor/src (an `async def` named `*_loop`, and the startup
        checks the lifespan starts) logs no INFO line and no warning of a pass that went well: ADR-0214
        keeps the system disk quiet while UNOS idles. What it logs when it fails is held back by
        RepeatLimit to one line per ten minutes, and a loop that is new must be listed here."""
        loops_that_log_failures = {
            "app_resources_loop", "scheduler_loop", "task_check_loop", "raid_monitor_loop", "smart_monitor_loop",
            "ups_monitor_loop", "check_host_tools", "first_upload_leftover_check", "power_schedule_loop",
            "automatic_install_loop",
        }
        # What a pass logs when it changed something or found a problem, each behind an `if`: the
        # shutdown by the UPS monitor, the items the trash expiry deleted and what it could not, the
        # hidden files that uploads left, the host programs that are missing.
        allowed = ("UPS auto-shutdown triggered", "Deleted %d item(s) for good", "The trash could not be cleared",
                   "Found %d hidden file(s)", "Some folders could not be checked", "The host is missing programs")
        outside, with_failure_lines, found = [], set(), set()
        for path in sorted(SRC.rglob("*.py")):
            for function in ast.walk(ast.parse(path.read_text())):
                if not (isinstance(function, ast.AsyncFunctionDef)
                        and (function.name.endswith("_loop") or function.name in loops_that_log_failures)):
                    continue
                found.add(function.name)
                in_handlers = {id(n) for h in ast.walk(function) if isinstance(h, ast.ExceptHandler)
                               for n in ast.walk(h)}
                for node in ast.walk(function):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr in {"info", "warning", "error", "exception", "critical"}):
                        continue
                    if id(node) in in_handlers:
                        with_failure_lines.add(function.name)
                    elif not (node.args and ast.unparse(node.args[0]).strip("'\"").startswith(allowed)):
                        outside.append(f"{path.name}:{node.lineno} in {function.name}")

        assert outside == []
        assert loops_that_log_failures <= found
        assert with_failure_lines <= loops_that_log_failures


class TestRepeatLimit:
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

    def _emit(self, flt, msg="UPS monitor error: gone", level=logging.WARNING, name="src.power.router", exc=None):
        record = _record(msg, level, name, exc_info=exc)
        return record, flt.filter(record)

    def test_the_same_failure_is_written_once_per_window_and_counts_what_it_held_back(self):
        clock = self.Clock()
        limit = logging_setup.RepeatLimit(window=600, clock=clock)

        first = self._emit(limit)
        held = [self._emit(limit)[1] for _ in range(20)]
        clock.now += 601
        later, written = self._emit(limit)

        assert first[1] is True and held == [False] * 20 and written is True
        assert later.suppressed_repeats == 20
        assert json.loads(JsonFormatter().format(later))["fields"] == {"suppressed_repeats": 20}

    def test_another_message_or_logger_is_a_new_failure(self):
        limit = logging_setup.RepeatLimit(clock=self.Clock())

        self._emit(limit)

        assert self._emit(limit, msg="UPS monitor error: other")[1]
        assert self._emit(limit, name="src.health.router")[1]
        assert self._emit(limit, level=logging.ERROR)[1]

    def test_changes_of_state_and_the_access_log_are_never_held_back(self):
        limit = logging_setup.RepeatLimit(clock=self.Clock())

        assert [self._emit(limit, "Created the host account a", logging.INFO)[1] for _ in range(3)] == [True] * 3
        assert [self._emit(limit, "x", logging.WARNING, "uvicorn.access")[1] for _ in range(3)] == [True] * 3

    def test_the_log_is_filtered_through_it(self, restore_logging, monkeypatch, capsys):
        monkeypatch.delenv("UNOS_LOG_LEVEL", raising=False)
        configure_logging()

        for _ in range(5):
            logging.getLogger("src.power.router").warning("UPS monitor error: gone")

        assert len(capsys.readouterr().err.splitlines()) == 1

    def test_the_table_of_failures_stays_small(self):
        limit = logging_setup.RepeatLimit(clock=self.Clock(), limit=8)

        for i in range(100):
            self._emit(limit, f"failure {i}")

        assert len(limit._seen) <= 8



class TestNoSecretInAnyLine:
    """Every secret a sign-in, a password change, the two-factor setup and an API token handle
    stays out of the log, at the most verbose level, in the JSON lines the supervisor writes."""

    def test_passwords_tokens_session_keys_and_codes_never_reach_a_line(
            self, tmp_path, monkeypatch, caplog, restore_logging):
        from .test_smb_accounts import ALICE_PW, API as SMB_API, NEW_PW, World
        from .test_totp_once import Clock
        from src.auth import utils as auth_utils

        clock = Clock()
        monkeypatch.setattr(auth_utils, "_totp_time", lambda: clock.now)
        world = World(tmp_path, monkeypatch, caplog)
        monkeypatch.setenv("UNOS_LOG_LEVEL", "DEBUG")
        configure_logging()  # the most verbose level an administrator can ask for
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        # What the code itself logs, before the formatter's masking could hide a secret.
        raw = logging.StreamHandler(raw_stream := io.StringIO())
        raw.setFormatter(logging.Formatter("%(message)s"))  # the message and the traceback, as they are
        root = logging.getLogger()
        root.addHandler(handler)
        root.addHandler(raw)
        seen: list[str] = []
        try:
            client = world.client
            headers = world.headers("alice")
            seen.append(client.post(f"{SMB_API}/auth/login", json={"username": "alice", "password": "Wrong-pass-2026"}).text)
            setup = client.post(f"{SMB_API}/auth/totp/setup", headers=headers).json()
            secret = setup["secret"]
            code = clock.code(secret)
            enabled = client.post(f"{SMB_API}/auth/totp/enable", json={"code": code}, headers=headers)
            assert enabled.status_code == 200, enabled.text
            clock.next_step()
            wrong = "000000"
            client.post(f"{SMB_API}/auth/login", json={"username": "alice", "password": ALICE_PW, "totp_code": wrong})
            session = client.post(f"{SMB_API}/auth/login",
                                  json={"username": "alice", "password": ALICE_PW, "totp_code": clock.code(secret)})
            assert session.status_code == 200, session.text
            access = session.json()["access_token"]
            bearer = {"Authorization": f"Bearer {access}"}
            created = client.post(f"{SMB_API}/auth/tokens", json={"name": "ci", "expires_in_days": 1}, headers=bearer)
            assert created.status_code == 200, created.text
            api_token = created.json()["token"]
            client.get(f"{SMB_API}/auth/me", headers={"Authorization": f"Bearer {api_token}"})
            client.get(f"{SMB_API}/auth/me", headers=bearer)
            generated = client.post(f"{SMB_API}/auth/recovery-codes/generate", headers=bearer)
            assert generated.status_code == 200, generated.text
            codes = generated.json()["codes"]
            used = client.post(f"{SMB_API}/auth/login", json={"username": "alice", "password": ALICE_PW,
                                                                "recovery_code": codes[0]})
            assert used.status_code == 200, used.text
            changed = client.post(f"{SMB_API}/auth/change-password", headers=bearer,
                                  json={"current_password": ALICE_PW, "new_password": NEW_PW})
            assert changed.status_code == 200, changed.text
            client.post(f"{SMB_API}/auth/logout", headers={"Authorization": f"Bearer {access}"})
            # The capture works: a canary at DEBUG arrives, masked, in the same format.
            logging.getLogger("src.canary").debug("password=%s", ALICE_PW)
        finally:
            root.removeHandler(handler)
            root.removeHandler(raw)
            world.close()

        logged = stream.getvalue() + raw_stream.getvalue()
        assert json.loads(stream.getvalue().splitlines()[-1])["message"] == "password=[redacted]"
        logged = logged.replace(f"password={ALICE_PW}", "")  # the canary's own raw text
        assert "aiosqlite" not in logged and "sqlalchemy" not in logged
        for entry in stream.getvalue().splitlines():
            json.loads(entry)
        for secret_value in (ALICE_PW, NEW_PW, "Wrong-pass-2026", secret, code, wrong, access, api_token, *codes):
            assert secret_value not in logged, "a secret in the log"

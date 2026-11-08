"""The emergency command line and the recovery runbook (ADR-0261, ADR-0262; feature
system.emergency-runbook-and-cli).

`python -m src.emergency reset-admin [NAME]` sets a new password for an administrator and turns
its two-factor authentication off, recorded in the audit log; root on the host runs it with
deploy/reset-admin.sh, which runs it in the supervisor container. Here the command runs against
the temporary database and audit log of test_smb_accounts.World: in this process (main), and in
a process of its own that a fake docker starts as `docker exec` does when reset-admin.sh runs.
Afterwards the API signs in with the new password and lists the audit entries. The password is
never on a command line, in the output or in the log.

docs/RECOVERY.md is checked against the code: its mounts never write to a pool, and the names it
gives are the code's. Whether its btrfs commands work needs real disks: the entry's device check.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import pty
import re
import secrets
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pyotp
import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src import emergency
from src.audit import router as audit_router
from src.auth import utils as auth_utils
from src.auth.models import User
from src.auth.schemas import MAX_PASSWORD_BYTES
from src.auth.utils import verify_password
from src.config import Settings, get_settings
from src.database import Base
from src.files.router import TRASH
from src.main import app
from src.shares.models import SmbShare
from src.snapshots.router import SNAPSHOT_DIR_NAME
from src.storage.pools import PROFILE_MAP

from .audit_helpers import audit_entries
from .test_smb_accounts import ADMIN_PW, ALICE_PW, API, World, _hash

REPO = Path(__file__).resolve().parents[2]
SUPERVISOR = REPO / "supervisor"
SCRIPT = REPO / "deploy" / "reset-admin.sh"
STANDIN = REPO / "scripts" / "standin"
RUNBOOK = REPO / "docs" / "RECOVERY.md"

NEW_PW = "Emergency-pass-2026"
CAROL_PW = "Carol-admin-2026"
SECRET = pyotp.random_base32()
NOTHING_CHANGED = "Nothing was changed."
# The command as reset-admin.sh runs it in the supervisor container.
CONTAINER_COMMAND = "python -m src.emergency reset-admin"


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    """World's database (admin, alice, bob) as the command's database; getpass fails unless a
    test answers it: without a terminal the command must not ask."""
    w = World(tmp_path, monkeypatch, caplog)
    monkeypatch.setattr(get_settings(), "db_path", str(w.path))
    monkeypatch.setattr(emergency, "hash_password", _hash)  # bcrypt at cost 4 (the tests do not need 12)

    def no_terminal(prompt="Password: ", stream=None):
        raise AssertionError(f"getpass asked {prompt!r} without a terminal")

    monkeypatch.setattr(emergency.getpass, "getpass", no_terminal)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def run(monkeypatch, capsys):
    """emergency.main(argv) with `stdin` as standard input: (exit status, stdout, stderr)."""

    def run(*argv: str, stdin: str | io.StringIO = f"{NEW_PW}\n") -> tuple[int, str, str]:
        monkeypatch.setattr(sys, "stdin", stdin if isinstance(stdin, io.StringIO) else io.StringIO(stdin))
        status = emergency.main(list(argv))
        out, err = capsys.readouterr()
        return status, out, err

    return run


class Terminal(io.StringIO):
    """A standard input that is a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def terminal(world, monkeypatch):
    """Standard input is a terminal (`terminal.stdin`), and getpass answers the entries of
    `terminal.entries` in turn: a text, a function that returns one, or an exception to raise.
    `terminal.prompts` keeps what it was asked."""
    state = SimpleNamespace(prompts=[], entries=[], stdin=Terminal())

    def answer(prompt="Password: ", stream=None):
        state.prompts.append(prompt)
        entry = state.entries.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        return entry() if callable(entry) else entry

    monkeypatch.setattr(emergency.getpass, "getpass", answer)
    return state


def set_recovery_codes(world: World, name: str, value: str | None) -> None:
    with Session(world.engine) as session:
        session.get(User, world.ids[name]).recovery_codes = value
        session.commit()


def change_user(world: World, name: str, **values) -> None:
    with Session(world.engine) as session:
        user = session.get(User, world.ids[name])
        for key, value in values.items():
            setattr(user, key, value)
        session.commit()


def entries_of_root(world: World) -> list[dict]:
    return [entry for entry in audit_entries(world.audit_file) if entry["username"] == "root"]


def users(world: World) -> dict[int, tuple]:
    """Every user row by id: (username, hash, administrator, two-factor state)."""
    with Session(world.engine) as session:
        return {u.id: (u.username, u.hashed_password, u.is_admin, u.totp_enabled) for u in session.query(User)}


def unchanged(world: World, name: str, password: str, *, totp: bool = False) -> None:
    """User `name` still signs in with `password` and has the two-factor state it had."""
    user = world.user(name)
    assert verify_password(password, user.hashed_password)
    assert not verify_password(NEW_PW, user.hashed_password)
    assert bool(user.totp_enabled) is totp


class TestReset:
    def test_sets_the_new_password_and_turns_two_factor_authentication_off(self, world, run):
        world.set_totp("admin", SECRET)
        set_recovery_codes(world, "admin", json.dumps(["a" * 64, None, "b" * 64]))

        status, out, err = run("reset-admin", "admin")

        assert (status, err) == (0, "")
        user = world.user("admin")
        assert verify_password(NEW_PW, user.hashed_password)
        assert not verify_password(ADMIN_PW, user.hashed_password)
        assert (user.totp_enabled, user.totp_secret, user.recovery_codes) == (False, None, None)
        assert (user.username, user.is_admin) == ("admin", True)
        assert "Administrator 'admin': the password is reset, and two-factor authentication is off." in out
        # The others are left alone.
        unchanged(world, "alice", ALICE_PW)

    def test_the_new_password_signs_in_without_a_code_and_the_old_one_no_longer_does(self, world, run):
        world.set_totp("admin", SECRET)

        assert run("reset-admin", "admin")[0] == 0

        signed_in = world.client.post(f"{API}/auth/login", json={"username": "admin", "password": NEW_PW})
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["requires_totp"] is False and signed_in.json()["access_token"]
        me = world.client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {signed_in.json()['access_token']}"})
        assert (me.json()["username"], me.json()["is_admin"], me.json()["totp_enabled"]) == ("admin", True, False)
        refused = world.client.post(f"{API}/auth/login", json={"username": "admin", "password": ADMIN_PW})
        assert refused.status_code == 401

    def test_the_audit_log_records_both_changes_by_root(self, world, run):
        world.set_totp("admin", SECRET)

        assert run("reset-admin", "admin")[0] == 0

        expected = {
            ("password_change", "Password reset on the host's command line"),
            ("totp_disabled", "Two-factor authentication turned off on the host's command line, with its recovery codes"),
        }
        written = entries_of_root(world)
        assert {(e["action"], e["details"]) for e in written} == expected
        assert len(written) == 2
        for entry in written:
            assert (entry["resource_type"], entry["resource_id"], entry["severity"], entry["success"]) == (
                "user", "admin", "warning", True)
            assert (entry["ip_address"], entry["user_agent"]) == (None, None)
        # The API lists them as it lists every entry, for an administrator.
        listed = world.client.get(f"{API}/audit/logs", params={"username": "root"}, headers=world.headers("admin"))
        assert listed.status_code == 200, listed.text
        assert {(e["action"], e["details"]) for e in listed.json()} == expected
        assert emergency.AUDIT_USER == "root"

    def test_without_two_factor_authentication_only_the_password_change_is_recorded(self, world, run):
        # A setup of two-factor authentication that was started but never turned on is dropped too.
        change_user(world, "admin", totp_secret=SECRET)

        status, out, err = run("reset-admin", "admin")

        assert (status, err) == (0, "")
        assert "Administrator 'admin': the password is reset (two-factor authentication was off)." in out
        assert [e["action"] for e in entries_of_root(world)] == ["password_change"]
        user = world.user("admin")
        assert verify_password(NEW_PW, user.hashed_password)
        assert (user.totp_enabled, user.totp_secret) == (False, None)

    def test_the_only_administrator_needs_no_name(self, world, run):
        status, out, _ = run("reset-admin")

        assert status == 0
        assert "Administrator 'admin'" in out
        assert verify_password(NEW_PW, world.user("admin").hashed_password)

    def test_with_several_administrators_one_must_be_named(self, world, run):
        world.add_user("carol", CAROL_PW, admin=True)

        status, out, err = run("reset-admin")

        assert (status, out) == (1, "")
        assert "UNOS has 2 administrators (admin, carol): name the one to reset." in err
        assert NOTHING_CHANGED in err
        unchanged(world, "admin", ADMIN_PW)
        unchanged(world, "carol", CAROL_PW)
        assert audit_entries(world.audit_file) == []

    def test_the_named_administrator_alone_is_reset(self, world, run):
        world.add_user("carol", CAROL_PW, admin=True, totp_secret=SECRET)
        world.set_totp("admin", SECRET)

        assert run("reset-admin", "carol")[0] == 0

        carol = world.user("carol")
        assert verify_password(NEW_PW, carol.hashed_password) and carol.totp_enabled is False
        unchanged(world, "admin", ADMIN_PW, totp=True)
        assert {e["resource_id"] for e in entries_of_root(world)} == {"carol"}

    @pytest.mark.parametrize("name", ["alice", "nobody", "Admin"])
    def test_a_name_that_is_not_an_administrator_changes_nothing(self, world, run, name):
        status, out, err = run("reset-admin", name)

        assert (status, out) == (1, "")
        assert f"'{name}' is not an administrator of UNOS (administrators: admin)." in err
        assert NOTHING_CHANGED in err
        unchanged(world, "alice", ALICE_PW)
        unchanged(world, "admin", ADMIN_PW)
        assert audit_entries(world.audit_file) == []

    def test_names_reach_the_terminal_without_control_characters(self, world, run, terminal):
        # A restored database is untrusted (ADR-0144): an administrator's name could carry escape
        # sequences that a terminal acts on.
        world.add_user("evil\x1b]0;owned\x07\u202e", CAROL_PW, admin=True)

        status, out, err = run("reset-admin", "\x1b[2Jnobody")

        assert status == 1
        assert "'?[2Jnobody' is not an administrator of UNOS (administrators: admin, evil?]0;owned??)." in err
        assert not re.search(r"[\x00-\x1f\x7f\u202e]", out + err.rstrip("\n"))

        terminal.entries = [NEW_PW, NEW_PW]
        status, out, _ = run("reset-admin", "evil\x1b]0;owned\x07\u202e", stdin=terminal.stdin)

        assert status == 0
        assert terminal.prompts[0] == "New password for administrator 'evil?]0;owned??': "
        assert "Administrator 'evil?]0;owned??': the password is reset" in out
        assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f\u202e]", out)
        # The audit log keeps the name as it is, as JSON (escaped).
        assert {e["resource_id"] for e in entries_of_root(world)} == {"evil\x1b]0;owned\x07\u202e"}

    def test_without_an_administrator_nothing_changes(self, world, run):
        change_user(world, "admin", is_admin=False)

        status, _, err = run("reset-admin")

        assert status == 1
        assert "UNOS has no administrator" in err and NOTHING_CHANGED in err
        unchanged(world, "admin", ADMIN_PW)

    def test_the_password_is_hashed_with_bcrypt_at_12_rounds(self, world, run, monkeypatch):
        monkeypatch.setattr(emergency, "hash_password", auth_utils.hash_password)

        assert run("reset-admin", "admin")[0] == 0

        stored = world.user("admin").hashed_password
        assert stored.startswith("$2b$12$") and verify_password(NEW_PW, stored)

    def test_sessions_signed_in_before_stay_valid_until_they_expire(self, world, run):
        # UNOS cannot end a session on the server yet (auth.session-revocation): docs/RECOVERY.md
        # says so, and how a new JWT secret ends them all.
        session = world.headers("admin")

        assert run("reset-admin", "admin")[0] == 0

        assert world.client.get(f"{API}/auth/me", headers=session).status_code == 200


class TestPassword:
    @pytest.mark.parametrize(
        "line, problem",
        [
            pytest.param("", "No new password", id="empty"),
            pytest.param("short77", "The new password must have at least 8 characters", id="7-characters"),
            pytest.param("x" * (MAX_PASSWORD_BYTES + 1), "The new password must not be longer than 72 bytes (UTF-8)",
                         id="73-bytes"),
            pytest.param("\u00fc" * 37, "The new password must not be longer than 72 bytes (UTF-8)",
                         id="74-bytes-in-37-characters"),
            pytest.param("tab\tinside-it", "The new password must not contain control characters", id="a-tab"),
            pytest.param("escape\x1b[0m-it", "The new password must not contain control characters", id="an-escape"),
        ],
    )
    def test_a_password_that_breaks_the_rules_changes_nothing(self, world, run, line, problem):
        world.set_totp("admin", SECRET)

        status, out, err = run("reset-admin", "admin", stdin=f"{line}\n")

        assert (status, out) == (1, "")
        assert problem in err and NOTHING_CHANGED in err
        unchanged(world, "admin", ADMIN_PW, totp=True)
        assert audit_entries(world.audit_file) == []

    def test_the_rules_are_those_of_bcrypt_and_of_the_web_ui(self):
        users_page = (REPO / "webui" / "src" / "routes" / "users" / "+page.svelte").read_text()
        setup_page = (REPO / "webui" / "src" / "routes" / "setup" / "+page.svelte").read_text()

        assert emergency.MIN_PASSWORD_CHARS == 8
        assert f"const MIN_PASSWORD_LENGTH = {emergency.MIN_PASSWORD_CHARS};" in users_page
        assert f'minlength="{emergency.MIN_PASSWORD_CHARS}"' in setup_page
        assert MAX_PASSWORD_BYTES == 72

    def test_a_password_of_8_characters_and_one_of_72_bytes_are_taken(self, world, run):
        for password in ("8-chars!", "\u00e9" * 36):  # the second: 36 characters, 72 bytes
            assert run("reset-admin", "admin", stdin=f"{password}\n")[0] == 0
            assert verify_password(password, world.user("admin").hashed_password)

    def test_an_empty_standard_input_changes_nothing(self, world, run):
        status, _, err = run("reset-admin", "admin", stdin="")

        assert status == 1
        assert "No new password: type it on a terminal, or give it as the first line of standard input." in err
        unchanged(world, "admin", ADMIN_PW)

    def test_only_the_first_line_is_read_without_its_line_break(self, world, run):
        assert run("reset-admin", "admin", stdin=f"{NEW_PW}\r\nsecond line\n")[0] == 0

        stored = world.user("admin").hashed_password
        assert verify_password(NEW_PW, stored)
        assert not verify_password(f"{NEW_PW}\r", stored) and not verify_password("second line", stored)

    def test_a_line_without_a_line_break_is_the_password(self, world, run):
        assert run("reset-admin", "admin", stdin=NEW_PW)[0] == 0

        assert verify_password(NEW_PW, world.user("admin").hashed_password)

    def test_on_a_terminal_the_password_is_asked_twice(self, world, run, terminal):
        terminal.entries = [NEW_PW, NEW_PW]

        status, out, _ = run("reset-admin", stdin=terminal.stdin)

        assert status == 0
        assert terminal.prompts == ["New password for administrator 'admin': ", "Repeat the new password: "]
        assert verify_password(NEW_PW, world.user("admin").hashed_password)
        assert "the password is reset" in out

    def test_on_a_terminal_two_different_entries_change_nothing(self, world, run, terminal):
        terminal.entries = [NEW_PW, NEW_PW + "x"]

        status, _, err = run("reset-admin", "admin", stdin=terminal.stdin)

        assert status == 1
        assert "The two entries differ." in err and NOTHING_CHANGED in err
        unchanged(world, "admin", ADMIN_PW)

    def test_on_a_terminal_the_rules_apply_too(self, world, run, terminal):
        terminal.entries = ["short", "short"]

        status, _, err = run("reset-admin", "admin", stdin=terminal.stdin)

        assert status == 1
        assert "at least 8 characters" in err
        unchanged(world, "admin", ADMIN_PW)

    @pytest.mark.parametrize("stop, status", [pytest.param(KeyboardInterrupt(), 130, id="ctrl-c"),
                                              pytest.param(EOFError(), 1, id="ctrl-d")])
    def test_an_interrupted_prompt_changes_nothing(self, world, run, terminal, stop, status):
        terminal.entries = [NEW_PW, stop]

        code, _, err = run("reset-admin", "admin", stdin=terminal.stdin)

        assert code == status
        assert NOTHING_CHANGED in err
        unchanged(world, "admin", ADMIN_PW)
        assert audit_entries(world.audit_file) == []

    def test_the_password_appears_nowhere(self, world, run, caplog):
        world.set_totp("admin", SECRET)

        _, out, err = run("reset-admin", "admin")

        written = world.audit_file.read_text()
        for text in (out, err, written, caplog.text):
            assert NEW_PW not in text
        assert NEW_PW not in world.user("admin").hashed_password


class TestSafety:
    @pytest.mark.parametrize("folder_exists", [True, False])
    def test_a_missing_database_is_not_created(self, world, run, monkeypatch, tmp_path, folder_exists):
        folder = tmp_path / "data"
        if folder_exists:
            folder.mkdir()
        missing = folder / "unos.db"
        monkeypatch.setattr(get_settings(), "db_path", str(missing))

        status, _, err = run("reset-admin", "admin")

        assert status == 1
        assert f"Could not read the UNOS database {missing}" in err and NOTHING_CHANGED in err
        assert not missing.exists()

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param(lambda w: w.set_totp("admin", SECRET), id="two-factor-turned-on"),
            pytest.param(lambda w: change_user(w, "admin", is_admin=False), id="demoted"),
            pytest.param(lambda w: w.replace_user("admin", "mallory", ALICE_PW), id="id-given-to-another-user"),
        ],
    )
    def test_an_administrator_changed_while_the_password_is_typed_is_not_reset(self, world, run, terminal, change):
        changed = {}

        def second_entry():
            change(world)
            changed.update(users(world))
            return NEW_PW

        terminal.entries = [NEW_PW, second_entry]

        status, out, err = run("reset-admin", "admin", stdin=terminal.stdin)

        assert (status, out) == (1, "")
        assert "changed while the new password was typed: run the command again." in err
        assert NOTHING_CHANGED in err
        assert users(world) == changed
        assert audit_entries(world.audit_file) == []

    def test_a_reset_that_the_audit_log_cannot_record_says_so(self, world, run, monkeypatch, tmp_path):
        world.set_totp("admin", SECRET)
        # A folder where the log should be: every append fails, as on a full /data.
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        monkeypatch.setattr(audit_router, "AUDIT_FILE", str(blocked))

        status, out, err = run("reset-admin", "admin")

        assert status == 1
        assert "the password is reset" in out
        assert "The reset is done, but the audit log could not record it" in err
        assert NEW_PW not in err
        user = world.user("admin")
        assert verify_password(NEW_PW, user.hashed_password) and user.totp_enabled is False

    def test_no_route_reaches_the_command(self):
        importers = []
        for path in sorted((SUPERVISOR / "src").rglob("*.py")):
            if path.name == "emergency.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [alias.name for alias in node.names]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    continue
                if any(name == "emergency" or name.endswith(".emergency") for name in names):
                    importers.append(str(path.relative_to(SUPERVISOR)))
        paths = [getattr(route, "path", "") for route in app.routes]

        assert importers == []
        assert [p for p in paths if "emergency" in p or "reset-admin" in p] == []

    @pytest.mark.parametrize("argv", [[], ["reset-admin", "admin", "extra"], ["reset-admin", "--password", NEW_PW]])
    def test_a_wrong_command_line_exits_2_before_anything_is_read(self, world, run, argv):
        with pytest.raises(SystemExit) as stopped:
            run(*argv, stdin=io.StringIO(f"{NEW_PW}\n"))

        assert stopped.value.code == 2
        unchanged(world, "admin", ADMIN_PW)

    def test_the_module_runs_with_python_m_and_takes_no_password_option(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.emergency", "reset-admin", "--help"],
            cwd=SUPERVISOR, capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        assert result.returncode == 0, result.stderr
        assert "reset-admin [-h] [username]" in " ".join(result.stdout.split())
        assert "--password" not in result.stdout


# --- deploy/reset-admin.sh ---------------------------------------------------------------------

# World forbids subprocess.run, so that no command reaches this machine as a host command
# (tests/samba_fake.py); these tests run bash and Python on purpose, with a fake docker.
REAL_RUN = subprocess.run

# Runs the command line of `docker exec` in the supervisor's place: `python -m src.emergency ...`
# in supervisor/ (the image's /app), against the test's audit log; runpy runs the module as -m does.
SHIM = textwrap.dedent(
    """
    import os, runpy
    import src.audit.router as audit
    audit.AUDIT_FILE = os.environ["TEST_AUDIT_FILE"]
    runpy.run_module("src.emergency", run_name="__main__", alter_sys=True)
    """
)

# docker as reset-admin.sh calls it: logs its arguments, then runs the container's command.
FAKE_DOCKER = textwrap.dedent(
    r"""
    #!/bin/bash
    printf '%s\n' "$*" >> "$STUB_DIR/calls"
    [ "$1" = exec ] || { echo "docker: unexpected command $1" >&2; exit 64; }
    shift
    while [ "${1#-}" != "$1" ]; do shift; done
    if [ "$1" != unos-supervisor ]; then echo "Error response from daemon: No such container: $1" >&2; exit 1; fi
    shift
    [ -e "$STUB_DIR/log-only" ] && exit 0
    if [ "$1 $2 $3" != "python -m src.emergency" ]; then echo "exec: $1: not found" >&2; exit 127; fi
    shift 3
    cd "$SUPERVISOR" && exec "$PYTHON" -c "$SHIM" "$@"
    """
).lstrip()


def _script_lib(tmp_path: Path) -> Path:
    """reset-admin.sh without its last line (`main "$@"`), to be sourced."""
    body, last = SCRIPT.read_text().rstrip("\n").rsplit("\n", 1)
    assert last == 'main "$@"'
    lib = tmp_path / "reset-admin-lib.sh"
    lib.write_text(body + "\n")
    return lib


@pytest.fixture
def host(world, tmp_path):
    """A host with the fake docker on PATH; run(args, stdin, as_root=True) runs reset-admin.sh."""
    bin_dir, stub_dir = tmp_path / "bin", tmp_path / "stub"
    bin_dir.mkdir()
    stub_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(stub_dir),
        "SUPERVISOR": str(SUPERVISOR),
        "PYTHON": sys.executable,
        "SHIM": SHIM,
        "TEST_AUDIT_FILE": str(world.audit_file),
        "UNOS_DB_PATH": str(world.path),
        "UNOS_JWT_SECRET": secrets.token_hex(32),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LIB": str(_script_lib(tmp_path)),
    }

    class Host:
        stubs = stub_dir

        @staticmethod
        def run(*args: str, stdin=None, input: str | None = None, as_root: bool = True):
            command = (
                ["bash", "-c", '. "$LIB"; check_root() { :; }; main "$@"', "reset-admin.sh", *args]
                if as_root else ["bash", str(SCRIPT), *args]
            )
            return REAL_RUN(command, env=env, input=input, stdin=stdin, capture_output=True, text=True, timeout=120)

        @staticmethod
        def calls() -> list[str]:
            calls = stub_dir / "calls"
            return calls.read_text().splitlines() if calls.exists() else []

    return Host()


class TestResetAdminScript:
    def test_runs_the_command_in_the_supervisor_container_with_the_password_on_standard_input(self, world, host):
        world.set_totp("admin", SECRET)

        result = host.run("admin", input=f"{NEW_PW}\n")

        assert result.returncode == 0, result.stderr
        assert host.calls() == [f"exec -i unos-supervisor {CONTAINER_COMMAND} admin"]
        assert "Administrator 'admin': the password is reset, and two-factor authentication is off." in result.stdout
        user = world.user("admin")
        assert verify_password(NEW_PW, user.hashed_password)
        assert (user.totp_enabled, user.totp_secret) == (False, None)
        assert {e["action"] for e in entries_of_root(world)} == {"password_change", "totp_disabled"}
        # The password went on standard input only.
        assert all(NEW_PW not in call for call in host.calls())
        assert NEW_PW not in result.stdout + result.stderr

    def test_the_command_finds_the_only_administrator(self, world, host):
        result = host.run(input=f"{NEW_PW}\n")

        assert result.returncode == 0, result.stderr
        assert host.calls() == [f"exec -i unos-supervisor {CONTAINER_COMMAND}"]
        assert verify_password(NEW_PW, world.user("admin").hashed_password)

    def test_a_refusal_of_the_command_is_the_scripts(self, world, host):
        result = host.run("alice", input=f"{NEW_PW}\n")

        assert result.returncode == 1
        assert "'alice' is not an administrator of UNOS (administrators: admin)." in result.stderr
        unchanged(world, "alice", ALICE_PW)

    def test_a_terminal_gets_a_terminal_in_the_container(self, host):
        (host.stubs / "log-only").touch()
        controller, terminal = pty.openpty()
        try:
            result = host.run("admin", stdin=terminal)
        finally:
            os.close(controller)
            os.close(terminal)

        assert result.returncode == 0, result.stderr
        assert host.calls() == [f"exec -it unos-supervisor {CONTAINER_COMMAND} admin"]

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="runs as root")
    def test_refuses_to_run_as_another_user_than_root(self, world, host):
        result = host.run("admin", input=f"{NEW_PW}\n", as_root=False)

        assert result.returncode == 1
        assert "This script must be run as root" in result.stderr
        assert host.calls() == []
        unchanged(world, "admin", ADMIN_PW)

    def test_the_container_and_the_module_are_those_of_the_image(self):
        compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
        dockerfile = (SUPERVISOR / "Dockerfile").read_text().splitlines()
        script = SCRIPT.read_text()

        assert compose["services"]["supervisor"]["container_name"] == "unos-supervisor"
        assert 'CONTAINER=unos-supervisor' in script.splitlines()
        assert script.count(f'"$CONTAINER" {CONTAINER_COMMAND} "$@"') == 2
        # python -m src.emergency resolves in /app, where the image keeps src, and writes no bytecode
        # to the system disk.
        assert "WORKDIR /app" in dockerfile and "COPY src/ ./src/" in dockerfile
        assert "ENV PYTHONDONTWRITEBYTECODE=1" in dockerfile
        assert (SUPERVISOR / "src" / "emergency.py").is_file()
        assert os.access(SCRIPT, os.X_OK)
        listed = subprocess.run(["git", "ls-files", "-s", "deploy/reset-admin.sh"], cwd=REPO, capture_output=True,
                                text=True, timeout=30)
        if listed.returncode == 0 and listed.stdout:
            assert listed.stdout.startswith("100755 "), "git must keep deploy/reset-admin.sh executable"


# --- scripts/standin/emergency.py: the command against the local stand-in's data ----------------

def _load_standin():
    spec = importlib.util.spec_from_file_location("standin_run_supervisor", STANDIN / "run_supervisor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStandInCommand:
    """webui/tests/emergency-reset.spec.ts resets an administrator of the stand-in with it."""

    @staticmethod
    def _stand_in(tmp_path: Path) -> Path:
        """A stand-in's state directory whose database has the administrator admin, with 2FA."""
        state = tmp_path / "state"
        _load_standin().prepare(state, fresh=True)
        engine = create_engine(f"sqlite:///{state / 'data' / 'unos.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(User(username="admin", hashed_password=_hash(ADMIN_PW), is_admin=True,
                             totp_secret=SECRET, totp_enabled=True))
            session.commit()
        engine.dispose()
        return state

    @staticmethod
    def _run(*args: str, input: str = f"{NEW_PW}\n"):
        # Without the caller's UNOS_* settings, as e2e.sh's Playwright has them; the script sets its own.
        env = {key: value for key, value in os.environ.items() if not key.startswith("UNOS_")}
        return REAL_RUN([sys.executable, str(STANDIN / "emergency.py"), *args], input=input, capture_output=True,
                        text=True, timeout=120, env={**env, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_resets_in_the_stand_ins_database_and_audit_log(self, tmp_path):
        state = self._stand_in(tmp_path)

        result = self._run("--state-dir", str(state), "reset-admin", "admin")

        assert result.returncode == 0, result.stderr
        assert "Administrator 'admin': the password is reset, and two-factor authentication is off." in result.stdout
        engine = create_engine(f"sqlite:///{state / 'data' / 'unos.db'}")
        with Session(engine) as session:
            admin = session.query(User).filter_by(username="admin").one()
            assert verify_password(NEW_PW, admin.hashed_password) and admin.totp_enabled is False
        engine.dispose()
        written = audit_entries(state / "data" / "audit" / "audit.jsonl")
        assert [(e["action"], e["username"]) for e in written] == [("password_change", "root"), ("totp_disabled", "root")]

    def test_refuses_a_folder_that_is_not_a_stand_in(self, tmp_path):
        result = self._run("--state-dir", str(tmp_path), "reset-admin", "admin")

        assert result.returncode != 0
        assert "is not the state directory of a stand-in" in result.stderr
        assert list(tmp_path.iterdir()) == []


# --- docs/RECOVERY.md --------------------------------------------------------------------------

def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """The lines inside the fenced code blocks of a Markdown text."""
    lines, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif fenced:
            lines.append(line.strip())
    return lines


MOUNT = re.compile(r"(?<![\w-])mount(?![\w-])")
# The options that keep a read-only mount from replaying the filesystem's log, which writes:
# btrfs's (rescue=nologreplay, before Linux 5.9 nologreplay) and ext4's (noload).
NO_LOG_REPLAY = {"rescue=nologreplay", "nologreplay", "noload"}
# What each btrfs profile can lose: its copies of every block less one (none for single and raid0).
MAY_BE_MISSING = {"single": "none", "raid0": "none", "raid1": "1", "raid10": "1", "raid1c3": "2", "raid1c4": "3"}


class TestRunbook:
    def test_is_linked_from_the_install_guide_the_user_guide_and_the_readme(self):
        install = (REPO / "docs" / "INSTALL.md").read_text()
        guide = (REPO / "docs" / "USER_GUIDE.md").read_text()
        readme = (REPO / "README.md").read_text()

        assert "](RECOVERY.md)" in install and "](RECOVERY.md" in guide
        assert "](docs/RECOVERY.md)" in readme
        troubleshooting = install.split("## Troubleshooting", 1)[1].split("\n## ", 1)[0]
        assert "](RECOVERY.md)" in troubleshooting

    def test_every_mount_is_read_only_without_log_replay(self):
        mounts = [line for line in _code_lines(_runbook()) if MOUNT.search(line)]

        assert mounts, "the runbook mounts nothing"
        for line in mounts:
            options = re.search(r"\s-o\s+(\S+)", line)
            assert options, f"a mount without -o ro: {line}"
            chosen = set(options[1].split(","))
            assert "ro" in chosen and chosen & NO_LOG_REPLAY, line
        assert any("degraded" in line for line in mounts), "no mount for a pool with a missing disk"
        assert any("rescue=nologreplay" in line for line in mounts), "no btrfs mount without the log replay"

    def test_no_command_writes_to_the_pool(self):
        writes = re.compile(
            r"mkfs|wipefs|--repair|btrfs\s+(?:rescue|scrub|balance|device\s+(?:add|remove|delete|replace))"
            r"|btrfs\s+(?:subvolume|property)\s+(?:delete|set|create)|(?:^|[\s,])rw(?:[\s,]|$)"
        )

        assert [line for line in _code_lines(_runbook()) if writes.search(line)] == []

    def test_lists_which_disks_each_profile_can_miss(self):
        rows = dict(re.findall(r"^\|[^|\n]*\(`(\w+)`\)\s*\|\s*([^|\n]+?)\s*\|", _runbook(), re.M))

        assert set(rows) == set(PROFILE_MAP)
        assert rows == {profile: MAY_BE_MISSING[profile] for profile in PROFILE_MAP}

    def test_names_the_places_that_the_code_uses(self):
        text = _runbook()
        compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())

        assert "btrfs device scan" in text and "btrfs filesystem show" in text and "Label" in text
        assert f"/{SNAPSHOT_DIR_NAME}" in text and "restore_<date>_<time>" in text
        assert f"`{TRASH}`" in text
        assert Settings.model_fields["pools_base_path"].default == "/mnt/pools" and "/mnt/pools/<pool>" in text
        assert "apps/<app>" in text
        assert f"`{SmbShare.__tablename__}`" in text and "/etc/samba/smb.conf" in text
        # The data volume: compose names it after the folder of the compose file, deploy/.
        assert "unos-data" in compose["volumes"] and "deploy_unos-data" in text
        assert "70000" in text and "2770" in text

    def test_gives_the_reset_command_of_the_script_and_its_rules(self):
        text = _runbook()
        install = (REPO / "deploy" / "install.sh").read_text()

        assert 'UNOS_INSTALL_DIR="${UNOS_INSTALL_DIR:-/opt/unos}"' in install
        assert "sudo /opt/unos/deploy/reset-admin.sh" in text
        assert f"sudo docker exec -it unos-supervisor {CONTAINER_COMMAND}" in text
        assert f"at least {emergency.MIN_PASSWORD_CHARS} characters" in text
        assert f"at most {MAX_PASSWORD_BYTES} bytes" in text
        for action in ("password_change", "totp_disabled"):
            assert f"`{action}`" in text
        assert f"`{emergency.AUDIT_USER}`" in text
        assert "auth.session-revocation" in text

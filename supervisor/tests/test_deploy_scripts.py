"""deploy/update.sh and install.sh, the compose files, the image build
contexts and the CI workflow.

update.sh runs in a sandbox: a local git "origin", a clone as the install
directory, and stub docker/curl commands on PATH that log their calls. git is
logged too, and flock is a stand-in that takes a real lock. Functions of
install.sh run with the same docker stub and with /etc moved. Nothing reaches
Docker, the network or the host.
"""

import contextlib
import fcntl
import http.server
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
# SUPERVISOR_START_TIMEOUT of update.sh and install.sh: as long as Docker can
# take to report the supervisor healthy or unhealthy (TestHealthchecks checks
# what the scripts pass against its healthcheck in docker-compose.yml).
SUPERVISOR_START = 680

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"
)

DOCKER_STUB = textwrap.dedent(
    r"""
    #!/bin/bash
    echo "docker $*" >> "$STUB_DIR/calls"
    # fd 9 is the update lock, which docker must not inherit.
    { true >&9; } 2>/dev/null && echo "docker $*" >> "$STUB_DIR/lock-inherited"
    has() { grep -qx "$1" "$STUB_DIR/scenario"; }
    # Ctrl+C or a closed SSH session at a given step (once): signal the whole
    # process group, as the terminal does.
    interrupt_at() {
      if has "interrupt-at-$1" && [ ! -e "$STUB_DIR/interrupted" ]; then
        touch "$STUB_DIR/interrupted"
        kill -"$(sed -n 's/^signal=//p' "$STUB_DIR/scenario")" -- -"$(cat "$STUB_DIR/pgid")"
        sleep 1
      fi
    }
    # Whether the update lock is held, tried as a second update.sh would.
    probe_lock() {
      if (exec 8>>"$LOCK" && flock -n 8); then echo free; else echo held; fi >> "$STUB_DIR/lock-state"
    }
    # The options of `compose up` as Compose parses them: one per argument, a
    # non-negative integer after --wait-timeout, which Compose before 2.17
    # (no-wait-timeout) does not know.
    check_up_options() {
      while [ $# -gt 0 ]; do
        case "$1" in
          -d | --no-build | --force-recreate) ;;
          --wait-timeout)
            if has no-wait-timeout; then echo "unknown flag: --wait-timeout" >&2; return 1; fi
            if ! [[ ${2-} =~ ^[0-9]+$ ]]; then echo "invalid argument \"${2-}\" for \"--wait-timeout\" flag" >&2; return 1; fi
            shift ;;
          *) echo "unknown flag: $1" >&2; return 1 ;;
        esac
        shift
      done
    }
    case "$1" in
      inspect)
        case "$3" in
          '{{.State.Running}}') echo true ;;
          '{{.Config.Image}}') echo "deploy-${4#unos-}" ;;
          '{{.Image}}') echo "sha256:running-${4#unos-}" ;;
          *) echo "UNOS_HOSTNAME=unos" ;;
        esac ;;
      exec)
        if [ "$2" = "-i" ] && has real-python; then UNOS_DB_PATH="$STUB_DIR/live.db" "$PYTHON" -; exit $?;
        elif [ "$2" = "-i" ]; then cat > /dev/null; interrupt_at backup; printf 'SQLite format 3'; else printf 'data' | gzip; fi ;;
      compose)
        # The docker CLI without the Compose plugin (no-compose).
        if has no-compose; then
          echo "docker: 'compose' is not a docker command." >&2; exit 1
        fi
        case "$2" in
          # Compose 5.0.2 unless the scenario has a line compose-version=<version>.
          version) v=$(sed -n 's/^compose-version=//p' "$STUB_DIR/scenario"); echo "${v:-5.0.2}" ;;
          build) interrupt_at build; has probe-lock && probe_lock; has build-fails && exit 1 ;;
          down) interrupt_at down ;;
          up) # Part of the help: --wait since Compose 2.1.1, --wait-timeout since 2.17.
              if [ "$3" = --help ]; then
                echo "  -d, --detach                   Detached mode: Run containers in the background"
                echo "      --no-build                 Don't build an image, even if it's policy"
                echo "      --wait                     Wait for services to be running|healthy. Implies detached mode."
                has no-wait-timeout ||
                  echo "      --wait-timeout int         Maximum duration in seconds to wait for the project to be running|healthy"
                exit 0
              fi
              check_up_options "${@:3}" || exit 1
              [[ " $* " == *" --force-recreate "* ]] && touch "$STUB_DIR/rolled-back"
              has probe-lock && probe_lock
              # A process that outlives the update, as a detached one does.
              if has leave-process; then sleep 60 > /dev/null 2>&1 & echo $! > "$STUB_DIR/leftover"; fi
              # up-fails: the new supervisor does not become healthy, the previous one does.
              if has up-fails && [ ! -e "$STUB_DIR/rolled-back" ]; then
                echo "dependency failed to start: container unos-supervisor is unhealthy" >&2; exit 1
              fi ;;
          port) [ "$4" = 80 ] && echo "0.0.0.0:80" || echo "0.0.0.0:443" ;;
        esac ;;
    esac
    exit 0
    """
).lstrip()

CURL_STUB = textwrap.dedent(
    """
    #!/bin/bash
    echo "curl $*" >> "$STUB_DIR/calls"
    if grep -qx unhealthy "$STUB_DIR/scenario" && [ ! -e "$STUB_DIR/rolled-back" ]; then exit 7; fi
    exit 0
    """
).lstrip()

# Logs the calls in a file of their own, then runs git.
GIT_STUB = textwrap.dedent(
    """
    #!/bin/bash
    echo "git $*" >> "$STUB_DIR/git-calls"
    { true >&9; } 2>/dev/null && echo "git $*" >> "$STUB_DIR/lock-inherited"
    exec "$REAL_GIT" "$@"
    """
).lstrip()

# util-linux flock as update.sh calls it, `flock -n <fd>`: a real flock(2) on
# the inherited descriptor, so the lock outlives this process like the real
# one's. Exit 1 if another descriptor holds it.
FLOCK_STUB = textwrap.dedent(
    """
    #!/bin/bash
    exec "$PYTHON" -c '
    import fcntl
    import sys

    nonblocking = "-n" in sys.argv[1:-1] or "--nonblock" in sys.argv[1:-1]
    try:
        fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    except BlockingIOError:
        sys.exit(1)
    ' "$@"
    """
).lstrip()


def _git(*args, cwd):
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True)


def _update_lib():
    """update.sh without its last line (`main "$@"; exit`), to be sourced."""
    return (REPO_ROOT / "deploy" / "update.sh").read_text().rstrip("\n").rsplit("\n", 1)[0] + "\n"


@pytest.fixture
def sandbox(tmp_path):
    """An install directory one commit behind its origin, plus stub commands."""
    _git("init", "-q", "--bare", str(tmp_path / "origin.git"), cwd=tmp_path)
    seed = tmp_path / "seed"
    _git("clone", "-q", str(tmp_path / "origin.git"), str(seed), cwd=tmp_path)
    (seed / "deploy").mkdir()
    (seed / "deploy" / "docker-compose.yml").write_text("services: {}\n")
    (seed / "VERSION").write_text("1\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-qm", "v1", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    install = tmp_path / "install"
    _git("clone", "-q", str(tmp_path / "origin.git"), str(install), cwd=tmp_path)
    (seed / "VERSION").write_text("2\n")
    _git("commit", "-qam", "v2", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB), ("git", GIT_STUB), ("flock", FLOCK_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "calls").write_text("")
    # main() of update.sh is run without the root check.
    (tmp_path / "update-lib.sh").write_text(_update_lib())
    return tmp_path


def _head(sandbox):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sandbox / "install", check=True, capture_output=True, text=True
    ).stdout.strip()


# Runs the command after it with the default action for SIGINT, as from a
# terminal: a shell cannot trap a signal that was ignored when it started,
# and a job that a script starts with `&` (pytest ... &) ignores SIGINT, so
# the Ctrl+C tests of update.sh failed there. Also for SIGPIPE and SIGXFSZ,
# which Python ignores and exec would pass on. Exec keeps the pid.
_WITH_DEFAULT_SIGNALS = [
    sys.executable,
    "-c",
    "import os, signal, sys\n"
    "for number in (signal.SIGINT, signal.SIGPIPE, signal.SIGXFSZ):\n"
    "    signal.signal(number, signal.SIG_DFL)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])",
]


def _run_update(sandbox, *scenario, overrides="", **extra_env):
    """main() of update.sh in the sandbox. `overrides`: shell code run after
    update.sh is sourced, such as a function that replaces one of its own."""
    (sandbox / "stub" / "scenario").write_text("".join(f"{s}\n" for s in scenario))
    env = {
        **os.environ,
        "PATH": f"{sandbox / 'bin'}:{os.environ['PATH']}",
        "STUB_DIR": str(sandbox / "stub"),
        "UNOS_INSTALL_DIR": str(sandbox / "install"),
        "UNOS_HEALTH_TIMEOUT": "0",
        "LIB": str(sandbox / "update-lib.sh"),
        "OVERRIDES": overrides,
        "BACKUPS": str(sandbox / "backups"),
        "LOCK": str(sandbox / "update.lock"),
        "PYTHON": sys.executable,
        "REAL_GIT": shutil.which("git"),
        **extra_env,
    }
    result = subprocess.run(
        [
            *_WITH_DEFAULT_SIGNALS,
            "bash",
            "-c",
            'echo $$ > "$STUB_DIR/pgid"; . "$LIB"; check_root() { :; }; eval "$OVERRIDES";'
            ' BACKUP_DIR="$BACKUPS"; LOCK_FILE="$LOCK"; main',
        ],
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        # Own process group: the stubs signal it like a terminal would.
        start_new_session=True,
    )
    calls = (sandbox / "stub" / "calls").read_text().splitlines()
    return result, calls


def _index(calls, prefix):
    return next(i for i, call in enumerate(calls) if call.startswith(prefix))


def _git_calls(sandbox):
    calls = sandbox / "stub" / "git-calls"
    return calls.read_text().splitlines() if calls.exists() else []


@contextlib.contextmanager
def _lock_held(path):
    """Hold the update lock, as a running update.sh does."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _lock_is_free(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        return True
    finally:
        os.close(fd)


def _path_without(command, sandbox):
    """$PATH without <command>: each directory that has one is replaced by a
    directory with links to its other programs."""
    links = sandbox / f"path-without-{command}"
    path = []
    for entry in os.environ["PATH"].split(os.pathsep):
        directory = Path(entry)
        if not (directory / command).exists():
            path.append(entry)
            continue
        links.mkdir(exist_ok=True)
        for program in directory.iterdir():
            if program.name != command and not os.path.lexists(links / program.name):
                (links / program.name).symlink_to(program)
        if str(links) not in path:
            path.append(str(links))
    return os.pathsep.join(path)


def _up_calls(calls):
    """The `docker compose up` calls that start containers (not `up --help`)."""
    return [call for call in calls if call.startswith("docker compose up ") and not call.endswith(" --help")]


def _compose_before_2_30(script, versions):
    """compose_before_2_30 of deploy/<script> for each version."""
    source = (REPO_ROOT / "deploy" / script).read_text().rstrip("\n").rsplit("\n", 1)[0]
    result = subprocess.run(
        [
            "bash",
            "-c",
            'eval "$LIB"; for v in "$@"; do compose_before_2_30 "$v" && echo yes || echo no; done',
            "-",
            *versions,
        ],
        env={**os.environ, "LIB": source},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [line == "yes" for line in result.stdout.splitlines()]


class TestUpdateScript:
    def test_local_changes_stop_the_update_before_anything_changes(self, sandbox):
        (sandbox / "install" / "VERSION").write_text("local edit\n")
        before = _head(sandbox)

        result, calls = _run_update(sandbox)

        assert result.returncode == 1
        assert "VERSION" in result.stdout
        assert calls == []
        assert _head(sandbox) == before
        assert (sandbox / "install" / "VERSION").read_text() == "local edit\n"

    def test_another_branch_stops_the_update(self, sandbox):
        _git("checkout", "-qb", "feature", cwd=sandbox / "install")

        result, calls = _run_update(sandbox)

        assert result.returncode == 1
        assert calls == []

    def test_builds_while_running_then_switches_and_prunes_after_health(self, sandbox):
        result, calls = _run_update(sandbox)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.index("docker image tag sha256:running-supervisor deploy-supervisor:previous") < _index(
            calls, "docker compose build"
        )
        assert _index(calls, "docker compose build") < _index(calls, "docker compose down")
        assert _index(calls, "docker compose down") < _index(calls, "docker compose up -d --no-build")
        assert _index(calls, "curl") < _index(calls, "docker image prune")
        backup = next((sandbox / "backups").iterdir())
        assert sorted(p.name for p in backup.iterdir()) == ["data.tar.gz", "unos.db"]
        assert (backup.stat().st_mode & 0o777) == 0o700
        assert (sandbox / "install" / "VERSION").read_text() == "2\n"

    def test_failed_build_leaves_the_running_version_alone(self, sandbox):
        before = _head(sandbox)

        result, calls = _run_update(sandbox, "build-fails")

        assert result.returncode == 1
        assert not any(call.startswith(("docker compose down", "docker compose up")) for call in calls)
        assert "docker image tag deploy-supervisor:previous deploy-supervisor" in calls
        assert _head(sandbox) == before

    def test_unhealthy_update_is_rolled_back(self, sandbox):
        before = _head(sandbox)

        result, calls = _run_update(sandbox, "unhealthy")

        assert result.returncode == 1
        retag = calls.index("docker image tag deploy-webui:previous deploy-webui")
        assert _index(calls, "docker compose up -d --no-build --force-recreate") > retag
        assert not any(call.startswith("docker image prune") for call in calls)
        assert _head(sandbox) == before
        assert "Rolled back" in result.stdout
        # UNOS_HEALTH_TIMEOUT is the time it gets to answer over HTTP and HTTPS.
        assert "UNOS did not answer within 0 seconds" in result.stdout
        assert "retry with: sudo UNOS_HEALTH_TIMEOUT=600" in result.stdout

    @pytest.mark.parametrize(
        "timeout, suggested",
        [("300", 600), ("301", 602), ("900", 1800), ("999999999", 999999999)],
    )
    def test_failed_health_check_suggests_a_longer_timeout(self, sandbox, timeout, suggested):
        # Twice the time it had, at least 600 seconds: after a run with 900,
        # the message suggested 600. At most 999999999, the largest value
        # update.sh accepts. Here the health check gives up at once instead
        # of after `timeout` seconds, and answers after the rollback.
        result, _ = _run_update(
            sandbox,
            "unhealthy",
            UNOS_HEALTH_TIMEOUT=timeout,
            overrides='wait_for_health() { [ -e "$STUB_DIR/rolled-back" ]; }',
        )

        assert result.returncode == 1
        assert "Rolled back" in result.stdout
        assert f"UNOS did not answer within {timeout} seconds" in result.stdout
        assert f"retry with: sudo UNOS_HEALTH_TIMEOUT={suggested} " in result.stdout

    def test_up_to_date_without_a_terminal_changes_nothing(self, sandbox):
        _git("pull", "-q", cwd=sandbox / "install")

        result, calls = _run_update(sandbox)

        assert result.returncode == 0
        assert "already up to date" in result.stdout
        assert calls == []

    def test_database_backup_is_a_consistent_copy(self, sandbox):
        live = sqlite3.connect(sandbox / "stub" / "live.db")
        live.execute("CREATE TABLE users (name TEXT)")
        live.execute("INSERT INTO users VALUES ('admin')")
        live.commit()

        result, _ = _run_update(sandbox, "real-python")
        live.close()

        assert result.returncode == 0, result.stdout + result.stderr
        backup = next((sandbox / "backups").iterdir()) / "unos.db"
        copy = sqlite3.connect(backup)
        assert copy.execute("SELECT name FROM users").fetchall() == [("admin",)]
        copy.close()

    def test_missing_database_fails_the_backup(self, sandbox):
        # mode=rw: no empty database is created at a wrong path. Without a
        # terminal the update then stops.
        result, calls = _run_update(sandbox, "real-python")

        assert result.returncode == 1
        assert "The backup failed" in result.stdout
        assert not (sandbox / "stub" / "live.db").exists()
        assert not (sandbox / "backups").exists() or list((sandbox / "backups").iterdir()) == []
        assert not any(call.startswith("docker image tag") for call in calls)

    # 2**63: bash wraps it to a negative number, and Compose 5 refuses a
    # negative --wait-timeout, for the update and then for the rollback.
    @pytest.mark.parametrize("timeout", ["5m", "1000000000", "9223372036854775808"])
    def test_health_timeout_must_be_a_number(self, sandbox, timeout):
        before = _head(sandbox)

        result, calls = _run_update(sandbox, UNOS_HEALTH_TIMEOUT=timeout)

        assert result.returncode == 1
        assert "UNOS_HEALTH_TIMEOUT" in result.stdout
        assert calls == []
        assert _head(sandbox) == before

    def test_health_timeout_is_read_in_base_10(self, sandbox):
        # Leading zeros neither make it octal (512) nor count towards the
        # limit. Longer than SUPERVISOR_START, so compose waits as long.
        result, calls = _run_update(sandbox, UNOS_HEALTH_TIMEOUT="0000000001000")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == ["docker compose up -d --no-build --wait-timeout 1000"]
        assert "Waiting for UNOS to answer (up to 1000 seconds)" in result.stdout

    @pytest.mark.parametrize("signal", ["INT", "HUP", "TERM"])
    def test_interrupted_build_is_undone(self, sandbox, signal):
        # Before: a closed SSH session left the checkout at the new version
        # while the old one kept running.
        before = _head(sandbox)

        result, calls = _run_update(sandbox, "interrupt-at-build", f"signal={signal}")

        assert result.returncode == 1
        assert _head(sandbox) == before
        assert "docker image tag deploy-supervisor:previous deploy-supervisor" in calls
        assert not any(call.startswith(("docker compose down", "docker compose up")) for call in calls)

    @pytest.mark.parametrize("signal", ["INT", "HUP"])
    def test_interrupted_restart_starts_the_previous_version(self, sandbox, signal):
        # Before: interrupted between `down` and `up`, UNOS stayed stopped.
        before = _head(sandbox)

        result, calls = _run_update(sandbox, "interrupt-at-down", f"signal={signal}")

        assert result.returncode == 1
        retag = calls.index("docker image tag deploy-webui:previous deploy-webui")
        assert _index(calls, "docker compose up -d --no-build --force-recreate") > retag
        assert not any(call.startswith("docker image prune") for call in calls)
        assert _head(sandbox) == before
        if signal == "HUP":
            log = (sandbox / "backups" / "update-interrupted.log").read_text()
            assert "Rolled back" in log

    def test_interrupted_backup_is_marked_partial(self, sandbox):
        result, calls = _run_update(sandbox, "interrupt-at-backup", "signal=INT")

        assert result.returncode == 1
        assert [p.name.endswith(".partial") for p in (sandbox / "backups").iterdir()] == [True]
        assert not any(call.startswith("docker image tag") for call in calls)

    def test_second_update_stops_before_it_runs_git_or_docker(self, sandbox):
        # Two SSH sessions, or cron and a manual run: the second one would
        # interleave its compose down/up with the first one's, and its rollback
        # (git reset --hard) would hit the first one's build.
        before = _head(sandbox)

        with _lock_held(sandbox / "update.lock"):
            result, calls = _run_update(sandbox)

        assert result.returncode == 1
        assert "Another update.sh is running" in result.stdout
        assert calls == []
        assert _git_calls(sandbox) == []
        assert _head(sandbox) == before

    def test_lock_is_held_until_the_update_ends_and_not_after(self, sandbox):
        result, calls = _run_update(sandbox, "probe-lock", "leave-process")
        leftover = sandbox / "stub" / "leftover"
        try:
            assert result.returncode == 0, result.stdout + result.stderr
            # A prefix, as elsewhere: options may follow (--wait-timeout).
            assert any(call.startswith("docker compose up -d --no-build") for call in calls)
            assert _git_calls(sandbox)
            # Held during the build and during the restart (compose down/up),
            # which a second update must not interleave with.
            assert (sandbox / "stub" / "lock-state").read_text() == "held\nheld\n"
            # Neither git nor docker got the lock's descriptor: the process
            # that `docker compose up -d` left behind still runs, and the lock
            # is free.
            assert not (sandbox / "stub" / "lock-inherited").exists()
            os.kill(int(leftover.read_text()), 0)
            assert _lock_is_free(sandbox / "update.lock")
        finally:
            if leftover.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(leftover.read_text()), signal.SIGKILL)

    def test_lock_is_held_during_the_rollback(self, sandbox):
        # A second update must not start its build while the rollback resets
        # the checkout (git reset --hard) and restarts the previous version.
        result, _ = _run_update(sandbox, "unhealthy", "probe-lock")

        assert result.returncode == 1
        assert "Rolled back" in result.stdout
        # The build, the restart, and the restart of the previous version.
        assert (sandbox / "stub" / "lock-state").read_text() == "held\nheld\nheld\n"

    def test_unusable_lock_file_stops_the_update(self, sandbox):
        result, calls = _run_update(sandbox, LOCK=str(sandbox / "missing" / "update.lock"))

        assert result.returncode == 1
        assert "Cannot open the lock file" in result.stdout
        assert calls == []
        assert _git_calls(sandbox) == []

    def test_lock_file_is_private_and_the_umask_is_restored(self, sandbox):
        # flock needs no write access: a lock file other accounts can open
        # would let them hold the lock and block every update.
        old_umask = os.umask(0o022)
        try:
            result, calls = _run_update(sandbox)
        finally:
            os.umask(old_umask)

        assert result.returncode == 0, result.stdout + result.stderr
        assert stat.S_IMODE((sandbox / "update.lock").stat().st_mode) == 0o600
        # The checkout written after the lock was taken has normal modes.
        assert (sandbox / "install" / "VERSION").read_text() == "2\n"
        assert stat.S_IMODE((sandbox / "install" / "VERSION").stat().st_mode) == 0o644

    def test_without_flock_the_update_warns_and_runs(self, sandbox):
        # macOS, minimal hosts without util-linux.
        (sandbox / "bin" / "flock").unlink()
        path = f"{sandbox / 'bin'}{os.pathsep}{_path_without('flock', sandbox)}"

        result, calls = _run_update(sandbox, PATH=path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "flock (util-linux) is not installed" in result.stdout
        assert any(call.startswith("docker compose up -d --no-build") for call in calls)
        assert (sandbox / "install" / "VERSION").read_text() == "2\n"

    @pytest.mark.parametrize("run_lock", [True, False])
    def test_lock_file_is_in_run_or_else_in_tmp(self, tmp_path, run_lock):
        run_lock_dir = tmp_path / "run"
        if run_lock:
            run_lock_dir.mkdir()
        lib = tmp_path / "update-lib.sh"
        source = _update_lib()
        assert source.count("LOCK_DIR=/run\n") == 1
        lib.write_text(source.replace("LOCK_DIR=/run\n", f"LOCK_DIR={run_lock_dir}\n"))

        result = subprocess.run(
            ["bash", "-c", '. "$LIB"; echo "$LOCK_FILE"'],
            env={**os.environ, "LIB": str(lib)},
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        expected = f"{run_lock_dir}/unos-update.lock" if run_lock else "/tmp/unos-update.lock"
        assert result.stdout.strip() == expected

    @pytest.mark.parametrize(
        "scenario, timeout, wait, options",
        [
            # The update.
            ((), "42", SUPERVISOR_START, ["--no-build"]),
            # Rollback after compose gave up on the new supervisor.
            (("up-fails",), "42", SUPERVISOR_START, ["--no-build", "--no-build --force-recreate"]),
            # Rollback after the health check over HTTP and HTTPS failed (with
            # 0, the sandbox value, it fails at once). --wait-timeout 0 would
            # mean no limit.
            (("unhealthy",), "0", SUPERVISOR_START, ["--no-build", "--no-build --force-recreate"]),
            # Rollback after an interrupted restart.
            (("interrupt-at-down", "signal=INT"), "42", SUPERVISOR_START, ["--no-build --force-recreate"]),
            # A health timeout longer than the supervisor's start.
            ((), "900", 900, ["--no-build"]),
            (("up-fails",), "900", 900, ["--no-build", "--no-build --force-recreate"]),
        ],
    )
    def test_every_compose_up_waits_for_the_supervisor_start_or_the_health_timeout(
        self, sandbox, scenario, timeout, wait, options
    ):
        # webui starts once the supervisor is healthy. Compose must not give
        # up before Docker has decided whether the supervisor is healthy:
        # a slow but healthy update was rolled back, and the rollback's up
        # then gave up on the previous version as well and left webui
        # stopped. Without --wait-timeout, compose waits without a limit for
        # a supervisor that keeps restarting (older Docker Engines).
        result, calls = _run_update(sandbox, *scenario, UNOS_HEALTH_TIMEOUT=timeout)

        ups = [f"docker compose up -d {option} --wait-timeout {wait}" for option in options]
        assert _up_calls(calls) == ups, result.stdout + result.stderr
        assert "cannot limit its wait" not in result.stdout
        # The health check over HTTP and HTTPS follows the last up: Compose
        # versions that start webui when the wait times out exit with 0.
        assert any(call.startswith("curl") for call in calls[calls.index(ups[-1]) :])
        assert f"Waiting for UNOS to answer (up to {int(timeout)} seconds)" in result.stdout

    def test_supervisor_that_does_not_become_healthy_is_rolled_back(self, sandbox):
        # compose up fails, with a message that depends on the Compose
        # version: "dependency failed to start: container unos-supervisor is
        # unhealthy" (or "... exited (1)"), or "timeout waiting for
        # dependencies".
        before = _head(sandbox)

        result, calls = _run_update(sandbox, "up-fails")

        assert result.returncode == 1
        assert "the supervisor stopped or did not become healthy in time" in result.stdout
        for message in ("dependency failed to start", "is unhealthy", "exited", "timeout waiting for dependencies"):
            assert f'"{message}"' in result.stdout, message
        # Compose waited as long as Docker took to decide: a longer health
        # timeout would not have helped.
        assert "UNOS_HEALTH_TIMEOUT" not in result.stdout
        assert "Rolled back" in result.stdout
        assert not any(call.startswith("docker image prune") for call in calls)
        assert _head(sandbox) == before

    @pytest.mark.parametrize("scenario", [(), ("up-fails",)])
    def test_compose_without_wait_timeout_gets_the_plain_command(self, sandbox, scenario):
        # Compose before 2.17 rejects the option (unknown flag): passing it
        # anyway would fail the update and then the rollback.
        result, calls = _run_update(
            sandbox, "no-wait-timeout", "compose-version=2.16.0", *scenario, UNOS_HEALTH_TIMEOUT="42"
        )

        options = ["--no-build", "--no-build --force-recreate"] if scenario else ["--no-build"]
        assert _up_calls(calls) == [f"docker compose up -d {option}" for option in options]
        assert "Docker Compose 2.16.0 cannot limit its wait" in result.stdout
        if scenario:
            assert result.returncode == 1
            assert "Rolled back" in result.stdout
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert (sandbox / "install" / "VERSION").read_text() == "2\n"

    def test_compose_before_2_30_gets_the_option_and_a_warning(self, sandbox):
        # 2.17 to 2.29 accept --wait-timeout but apply it to `up --wait` only.
        result, calls = _run_update(sandbox, "compose-version=2.26.1", UNOS_HEALTH_TIMEOUT="42")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == [f"docker compose up -d --no-build --wait-timeout {SUPERVISOR_START}"]
        assert "Docker Compose 2.26.1 cannot limit its wait" in result.stdout
        assert f"may wait longer than {SUPERVISOR_START} seconds" in result.stdout


def _install_lib(tmp_path, paths):
    """install.sh without its last line (`main "$@"`), with the system paths
    in `paths` moved into tmp_path. Returns the path of the copy."""
    script = (REPO_ROOT / "deploy" / "install.sh").read_text().rstrip("\n").rsplit("\n", 1)[0]
    for path in paths:
        assert path in script, path
        script = script.replace(path, str(tmp_path / path.strip("/").replace("/", "-")))
    lib = tmp_path / "install-lib.sh"
    lib.write_text(script + "\n")
    return lib


def _start_install(tmp_path, *scenario):
    """check_compose_wait, start_unos and create_systemd_service of install.sh
    with the docker stub of the update sandbox. Returns the result, the docker
    calls and the ExecStart lines of the unit (None without a unit)."""
    (tmp_path / "install" / "deploy").mkdir(parents=True)
    (tmp_path / "etc-systemd-system").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER_STUB)
    (bin_dir / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for name in ("docker", "systemctl"):
        (bin_dir / name).chmod(0o755)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "calls").write_text("")
    (stub_dir / "scenario").write_text("".join(f"{s}\n" for s in scenario))
    lib = _install_lib(tmp_path, ["/etc/systemd/system", "/etc/unos"])

    result = subprocess.run(
        ["bash", "-c", '. "$LIB"; check_compose_wait; start_unos; create_systemd_service'],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LIB": str(lib),
            "STUB_DIR": str(stub_dir),
            "UNOS_INSTALL_DIR": str(tmp_path / "install"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    unit = tmp_path / "etc-systemd-system" / "unos.service"
    exec_start = None
    if unit.exists():
        exec_start = [line for line in unit.read_text().splitlines() if line.startswith("ExecStart=")]
    return result, (stub_dir / "calls").read_text().splitlines(), exec_start


# What install.sh creates from setup_directories on (in the sandbox of
# _run_install_main).
_INSTALL_TARGETS = {"install", "data", "mnt-pools", "etc-unos", "etc-samba", "etc-systemd-system"}

# Logs the call; stubs of _run_install_main.
_LOGGER = '#!/bin/bash\necho "${0##*/} $*" >> "$STUB_DIR/calls"\n'


def _run_install_main(tmp_path, *scenario, docker_installed=True):
    """main() of install.sh as root on Debian 12 (aarch64), with stub
    commands that log their calls (docker as in the update sandbox) and the
    system paths moved into tmp_path. Without docker_installed, `apt-get
    install ... docker-ce ...` installs the docker stub. Returns the result
    and the calls."""
    bin_dir = tmp_path / "bin"
    docker_dir = tmp_path / "docker-bin"
    stub_dir = tmp_path / "stub"
    for directory in (bin_dir, docker_dir, stub_dir, tmp_path / "etc-apt" / "sources.list.d"):
        directory.mkdir(parents=True)
    (stub_dir / "calls").write_text("")
    (stub_dir / "scenario").write_text("".join(f"{s}\n" for s in scenario))
    (stub_dir / "docker").write_text(DOCKER_STUB)
    (stub_dir / "docker").chmod(0o755)
    if docker_installed:
        shutil.copy(stub_dir / "docker", docker_dir / "docker")
    (tmp_path / "etc-os-release").write_text('ID=debian\nVERSION_ID="12"\n')
    stubs = {
        "uname": "#!/bin/bash\necho aarch64\n",
        "apt-get": _LOGGER
        + 'if [ "$1" = install ] && [[ " $* " == *" docker-ce "* ]]; then cp "$STUB_DIR/docker" "$DOCKER_DIR/"; fi\n',
        # Writes the file after -o.
        "gpg": _LOGGER + 'while [ $# -gt 0 ]; do [ "$1" = -o ] && : > "$2"; shift; done\n',
        "dpkg": _LOGGER + "echo arm64\n",
        "lsb_release": _LOGGER + "echo bookworm\n",
        "curl": _LOGGER,
        "systemctl": _LOGGER,
        "git": _LOGGER + "exit 1\n",
    }
    for name, body in stubs.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    paths = ["/etc/os-release", "/etc/apt", "/mnt/pools", "/etc/unos", "/etc/samba", "/etc/systemd/system"]
    lib = _install_lib(tmp_path, paths)

    result = subprocess.run(
        ["bash", "-c", '. "$LIB"; check_root() { :; }; main'],
        env={
            **os.environ,
            "PATH": os.pathsep.join([str(bin_dir), str(docker_dir), _path_without("docker", tmp_path)]),
            "LIB": str(lib),
            "STUB_DIR": str(stub_dir),
            "DOCKER_DIR": str(docker_dir),
            "UNOS_INSTALL_DIR": str(tmp_path / "install"),
            "UNOS_DATA_DIR": str(tmp_path / "data"),
            "UNOS_REPO": str(tmp_path / "no-repository"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, (stub_dir / "calls").read_text().splitlines()


class TestInstallScript:
    def _configure_samba(self, tmp_path, smb_conf):
        etc = tmp_path / "samba"
        etc.mkdir()
        if smb_conf is not None:
            (etc / "smb.conf").write_text(smb_conf)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "systemctl").write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / "systemctl").chmod(0o755)
        # install.sh without its last line (`main "$@"`), with /etc/samba moved.
        script = (REPO_ROOT / "deploy" / "install.sh").read_text().rstrip("\n").rsplit("\n", 1)[0]
        (tmp_path / "install-lib.sh").write_text(script.replace("/etc/samba", str(etc)) + "\n")
        subprocess.run(
            ["bash", "-c", '. "$LIB"; configure_samba'],
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "LIB": str(tmp_path / "install-lib.sh")},
            check=True,
            capture_output=True,
            timeout=30,
        )
        return etc

    def test_rerun_keeps_the_shares_in_smb_conf(self, tmp_path):
        # The shares exist only in smb.conf; replacing it deleted all of them.
        managed = "[global]\n   server string = UNOS NAS\n\n[media]\n   path = /mnt/pools/data/media\n"

        etc = self._configure_samba(tmp_path, managed)

        assert (etc / "smb.conf").read_text() == managed

    def test_first_install_replaces_the_distribution_smb_conf(self, tmp_path):
        original = "[global]\n   server string = %h server (Samba, Ubuntu)\n"

        etc = self._configure_samba(tmp_path, original)

        assert "server string = UNOS NAS" in (etc / "smb.conf").read_text()
        assert (etc / "smb.conf.original").read_text() == original

    def test_first_install_writes_what_the_supervisor_renders_without_shares(self, tmp_path):
        # Until the supervisor first starts, the file of install.sh is in
        # effect: the same hardened [global] (no guest access, LAN addresses
        # only, signing required), byte for byte, so that the template cannot
        # drift from the supervisor's rendering.
        from src.shares import smbconf

        etc = self._configure_samba(tmp_path, None)

        assert (etc / "smb.conf").read_bytes() == smbconf.render_config([]).encode()

    def test_rerun_keeps_the_smb_conf_that_unos_generated(self, tmp_path):
        # The generated file starts with its header; the server string that
        # the rerun looks for is still in it.
        (tmp_path / "first").mkdir()
        generated = (self._configure_samba(tmp_path / "first", None) / "smb.conf").read_text()
        assert generated.startswith("# Generated by UNOS from its database.")
        managed = generated + "\n[media]\n   path = /mnt/pools/data/media\n"
        (tmp_path / "rerun").mkdir()

        etc = self._configure_samba(tmp_path / "rerun", managed)

        assert (etc / "smb.conf").read_text() == managed
        assert not (etc / "smb.conf.original").exists()

    def test_start_and_the_unit_wait_at_most_the_start_timeout(self, tmp_path):
        # webui starts once the supervisor is healthy. Without --wait-timeout,
        # compose waits for a supervisor that runs without answering until its
        # start_period has passed and its checks have failed, and longer for
        # one that keeps restarting without turning unhealthy (older Docker
        # Engines).
        result, calls, exec_start = _start_install(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == [f"docker compose up -d --wait-timeout {SUPERVISOR_START}"]
        assert exec_start == [f"ExecStart=/usr/bin/docker compose up -d --wait-timeout {SUPERVISOR_START}"]
        assert "WARN" not in result.stdout

    def test_compose_without_wait_timeout_gets_the_plain_command(self, tmp_path):
        # Compose before 2.17 rejects the option: the start, and the unit at
        # every boot, would fail.
        result, calls, exec_start = _start_install(tmp_path, "no-wait-timeout", "compose-version=2.16.0")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == ["docker compose up -d"]
        assert exec_start == ["ExecStart=/usr/bin/docker compose up -d"]
        assert "Docker Compose 2.16.0 has no `up --wait-timeout`" in result.stdout
        assert "run install.sh again" in result.stdout

    def test_compose_before_2_30_gets_the_option_and_a_warning(self, tmp_path):
        result, calls, exec_start = _start_install(tmp_path, "compose-version=2.26.1")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == [f"docker compose up -d --wait-timeout {SUPERVISOR_START}"]
        assert exec_start == [f"ExecStart=/usr/bin/docker compose up -d --wait-timeout {SUPERVISOR_START}"]
        assert "Docker Compose 2.26.1 cannot limit its wait" in result.stdout

    def test_failed_start_names_the_supervisor_health(self, tmp_path):
        result, calls, exec_start = _start_install(tmp_path, "up-fails")

        assert result.returncode == 1
        assert "the supervisor stopped or did not become healthy in time" in result.stdout
        # Each Compose version words it differently.
        for message in ("dependency failed to start", "is unhealthy", "exited", "timeout waiting for dependencies"):
            assert f'"{message}"' in result.stdout, message
        assert "docker logs unos-supervisor" in result.stdout
        assert exec_start is None

    def test_main_checks_compose_before_it_starts_unos(self):
        script = (REPO_ROOT / "deploy" / "install.sh").read_text()
        body = re.search(r"^main\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        steps = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]

        assert steps.index("check_compose_wait") < steps.index("start_unos") < steps.index("create_systemd_service")

    def test_missing_compose_plugin_stops_before_anything_changes(self, tmp_path):
        # The docker CLI without its Compose plugin (Debian's docker.io, say):
        # install.sh warned that Compose "(version unknown)" has no
        # --wait-timeout, installed packages, cloned UNOS and configured Samba,
        # and failed only at `docker compose build`.
        result, calls = _run_install_main(tmp_path, "no-compose")

        assert result.returncode == 1
        assert "Docker Compose is not installed: `docker compose version` failed. Nothing was changed." in result.stdout
        # The packages that install the plugin: Docker's, Ubuntu's, and
        # Debian 13's (/usr/libexec/docker/cli-plugins/docker-compose).
        message = " ".join(result.stdout.split())
        assert "the package docker-compose-plugin from Docker's repository" in message
        assert "docker-compose-v2 on Ubuntu, or docker-compose on Debian 13." in message
        assert calls == ["docker compose version"]
        assert "version unknown" not in result.stdout
        assert [path.name for path in tmp_path.iterdir() if path.name in _INSTALL_TARGETS] == []

    def test_compose_plugin_is_checked_once_docker_is_installed(self, tmp_path):
        # install_docker installs the plugin with Docker; without it, stop
        # before anything of UNOS is set up.
        result, calls = _run_install_main(tmp_path, "no-compose", docker_installed=False)

        assert result.returncode == 1
        assert "`docker compose version` failed. The system packages are installed; UNOS is not." in result.stdout
        docker_ce = next(
            i for i, call in enumerate(calls) if call.startswith("apt-get install") and " docker-ce " in call
        )
        assert calls[docker_ce + 1 :] == ["systemctl enable docker", "systemctl start docker", "docker compose version"]
        assert [path.name for path in tmp_path.iterdir() if path.name in _INSTALL_TARGETS] == []


def _uninstall_lib(tmp_path):
    """uninstall.sh without its last line (`main "$@"`). Returns the path of the copy."""
    script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text().rstrip("\n").rsplit("\n", 1)[0]
    lib = tmp_path / "uninstall-lib.sh"
    lib.write_text(script + "\n")
    return lib


# The host's accounts: alice and bob are the only SMB accounts UNOS created
# (GECOS "UNOS SMB user", a uid from 70000 to 79999, primary group 70000).
_PASSWD = textwrap.dedent(
    """\
    root:x:0:0:root:/root:/bin/bash
    nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
    nas:x:1000:1000:,,,:/home/nas:/bin/bash
    alice:x:70000:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    old:x:20001:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    below:x:69999:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    above:x:80000:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    padded:x:070001:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    othergroup:x:70002:1000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    handmade:x:70003:70000:Hand made:/home/handmade:/bin/bash
    similar:x:70004:70000:UNOS SMB user,,,:/nonexistent:/usr/sbin/nologin
    bob:x:79999:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
    """
)

# Samba's passdb as `pdbedit -L` lists it (name:uid:full name): the entries of
# alice and bob, and of accounts that UNOS did not create.
_PASSDB = textwrap.dedent(
    """\
    nas:1000:
    alice:70000:UNOS SMB user
    old:20001:UNOS SMB user
    padded:70001:UNOS SMB user
    handmade:70003:Hand made
    similar:70004:UNOS SMB user,,,
    bob:79999:UNOS SMB user
    """
)

# A command line listed in $STUB_DIR/fail fails.
_FAILS = 'if grep -qxF -- "${0##*/} $*" "$STUB_DIR/fail"; then echo "${0##*/}: failed" >&2; exit 1; fi\n'

# pdbedit as with tdbsam: -L lists the entries, and -x -u NAME removes the
# entry whose lower-cased name is NAME lower-cased, so `-x -u alice` also
# removes an entry stored as 'Alice'.
_PDBEDIT_STUB = (
    _LOGGER
    + _FAILS
    + textwrap.dedent(
        """\
        case "$1" in
          -L) cat "$STUB_DIR/passdb" ;;
          -x)
            key=$(printf '%s' "$3" | tr ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz)
            if ! awk -F: -v key="$key" 'tolower($1) == key { found = 1; next } { print } END { exit !found }' \\
                "$STUB_DIR/passdb" > "$STUB_DIR/passdb.new"; then
              rm -f "$STUB_DIR/passdb.new"
              echo "user $3 does not exist in the passdb" >&2
              exit 255
            fi
            mv "$STUB_DIR/passdb.new" "$STUB_DIR/passdb" ;;
        esac
        """
    )
)

# Commands that would remove or change a host account or group.
_ACCOUNT_COMMANDS = ("userdel", "usermod", "groupdel", "groupmod", "deluser", "delgroup")


def _marked(*names, first_uid=70000):
    """passwd lines of marked accounts (the UNOS marker) with these names."""
    return "".join(
        f"{name}:x:{first_uid + i}:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin\n" for i, name in enumerate(names)
    )


class TestUninstallScript:
    def _run(
        self, tmp_path, commands="remove_samba_passwords", *, passwd=_PASSWD, passdb=_PASSDB, fail=(), samba=(),
        stubs=None,
    ):
        """Functions of uninstall.sh (`commands`), under its `set -e`, with stub
        getent, pdbedit and systemctl that log their calls, and with SAMBA_DIR
        in tmp_path holding the files named in `samba`. The command lines in
        `fail` fail. Returns the result, the calls and passdb afterwards. Stubs
        of the commands that change accounts log their calls too, so a call
        would show. `stubs` adds stubs (name: script body)."""
        bin_dir = tmp_path / "bin"
        stub_dir = tmp_path / "stub"
        samba_dir = tmp_path / "samba"
        for directory in (bin_dir, stub_dir, samba_dir):
            directory.mkdir()
        (stub_dir / "calls").write_text("")
        (stub_dir / "passwd").write_text(passwd)
        (stub_dir / "passdb").write_text(passdb)
        (stub_dir / "fail").write_text("".join(f"{line}\n" for line in fail))
        for name in samba:
            (samba_dir / name).write_text(f"{name}\n")
        stubs = {
            "getent": _LOGGER + '[ "$*" = passwd ] && cat "$STUB_DIR/passwd"\n',
            "pdbedit": _PDBEDIT_STUB,
            "systemctl": _LOGGER + _FAILS,
            **{name: _LOGGER for name in _ACCOUNT_COMMANDS},
            **(stubs or {}),
        }
        for name, body in stubs.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)

        result = subprocess.run(
            # The script's `set -e` holds while the functions run.
            ["bash", "-c", '. "$LIB"; [[ $- == *e* ]] || exit 99; SAMBA_DIR="$SAMBA"; ' + commands],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "LIB": str(_uninstall_lib(tmp_path)),
                "STUB_DIR": str(stub_dir),
                "SAMBA": str(samba_dir),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        calls = (stub_dir / "calls").read_text().splitlines()
        assert not [call for call in calls if call.startswith(_ACCOUNT_COMMANDS)], calls
        return result, calls, (stub_dir / "passdb").read_text().splitlines()

    @staticmethod
    def _text(result):
        """stdout without colors, its lines joined."""
        return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout).split())

    def test_removes_only_the_samba_passwords_of_the_accounts_unos_created(self, tmp_path):
        # A marked account with uid 20001 (outside the range) is not UNOS's.
        # The accounts stay (no userdel, see _run): their uids must not be
        # given to new accounts by a new installation.
        result, calls, passdb = self._run(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [
            "getent passwd",
            "pdbedit -L",
            "pdbedit -x -u alice",
            "pdbedit -x -u bob",
            "systemctl try-restart smbd",
        ]
        assert passdb == [
            "nas:1000:",
            "old:20001:UNOS SMB user",
            "padded:70001:UNOS SMB user",
            "handmade:70003:Hand made",
            "similar:70004:UNOS SMB user,,,",
        ]
        assert "WARN" not in result.stdout
        assert "Removed the Samba password of alice" in result.stdout

    @pytest.mark.parametrize("stored", ["Alice", "ALICE", "aLiCe"])
    def test_an_entry_stored_in_another_case_is_kept(self, tmp_path, stored):
        # tdbsam finds entries by the lower-cased name: `pdbedit -x -u alice`
        # would remove the entry of the hand-made account Alice.
        passwd = f"{stored}:x:1001:1001:Hand made:/home/{stored}:/bin/bash\n" + _marked("alice", "bob")
        passdb = f"{stored}:1001:Hand made\nbob:70001:UNOS SMB user\n"

        result, calls, after = self._run(tmp_path, passwd=passwd, passdb=passdb)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == ["getent passwd", "pdbedit -L", "pdbedit -x -u bob", "systemctl try-restart smbd"]
        assert after == [f"{stored}:1001:Hand made"]
        assert f"Not removing the Samba account '{stored}': UNOS did not create it" in self._text(result)

    @pytest.mark.parametrize(
        "case, passwd, passdb, fail, expected",
        [
            # SMB was turned off for both: no Samba entry.
            ("no-entry", _PASSWD, "nas:1000:\n", (), ["getent passwd", "pdbedit -L"]),
            ("other-case-only", _PASSWD, "Alice:1001:\nBOB:1002:\n", (), ["getent passwd", "pdbedit -L"]),
            (
                "removal-failed",
                _PASSWD,
                _PASSDB,
                ("pdbedit -x -u alice", "pdbedit -x -u bob"),
                ["getent passwd", "pdbedit -L", "pdbedit -x -u alice", "pdbedit -x -u bob"],
            ),
            # No account of UNOS: pdbedit does not even run.
            ("no-unos-account", "root:x:0:0:root:/root:/bin/bash\n", _PASSDB, (), ["getent passwd"]),
        ],
    )
    def test_smbd_is_restarted_only_when_an_entry_was_removed(self, tmp_path, case, passwd, passdb, fail, expected):
        result, calls, _ = self._run(tmp_path, passwd=passwd, passdb=passdb, fail=fail)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == expected
        assert "Restarted Samba" not in result.stdout

    def test_smbd_is_restarted_once_after_the_last_removal(self, tmp_path):
        passwd = _marked("alice", "bob", "carol", "dave")
        passdb = "alice:70000:\nbob:70001:\ndave:70003:\n"

        result, calls, after = self._run(tmp_path, passwd=passwd, passdb=passdb)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [
            "getent passwd",
            "pdbedit -L",
            "pdbedit -x -u alice",
            "pdbedit -x -u bob",
            "pdbedit -x -u dave",
            "systemctl try-restart smbd",
        ]
        assert after == []
        assert "Restarted Samba (smbd): open SMB connections were ended" in result.stdout

    @pytest.mark.parametrize(
        "fail, expected_calls, warning, left",
        [
            # A pdbedit that fails for one account (`|| true` before): the next
            # one is still removed, and smbd restarted.
            (
                ("pdbedit -x -u alice",),
                ["getent passwd", "pdbedit -L", "pdbedit -x -u alice", "pdbedit -x -u bob", "systemctl try-restart smbd"],
                "Could not remove the Samba password of alice. Remove it with: pdbedit -x -u alice",
                ["alice"],
            ),
            # Without the list no entry can be checked: none is removed. The
            # warning gives no `pdbedit -x -u NAME` to run blindly, which would
            # also remove an entry stored in another case.
            (
                ("pdbedit -L",),
                ["getent passwd", "pdbedit -L"],
                "Could not list Samba's accounts (pdbedit -L), so no Samba password was removed "
                "(see the end of the uninstallation)",
                ["alice", "bob"],
            ),
            (
                ("systemctl try-restart smbd",),
                ["getent passwd", "pdbedit -L", "pdbedit -x -u alice", "pdbedit -x -u bob", "systemctl try-restart smbd"],
                "Could not restart Samba (smbd): open SMB connections end when the computers disconnect",
                [],
            ),
        ],
    )
    def test_a_failure_does_not_stop_the_uninstallation(self, tmp_path, fail, expected_calls, warning, left):
        # uninstall.sh runs with set -e: a failing command outside a condition
        # would end it before the last steps.
        result, calls, _ = self._run(
            tmp_path, "remove_samba_passwords; restore_samba_config; print_completion", fail=fail
        )
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == expected_calls
        assert warning in text
        assert "UNOS Uninstallation Complete!" in result.stdout
        # The last text, which is what users read, names the accounts whose
        # Samba password may be left, and how to remove only theirs.
        completion = result.stdout[result.stdout.index("UNOS Uninstallation Complete!") :]
        if left:
            block = completion[completion.index("Samba passwords:") : completion.index("SMB accounts:")]
            assert [line.strip() for line in block.splitlines() if line.startswith("  ")] == [
                *left,
                "systemctl restart smbd",
            ]
            assert (
                "Samba passwords: Samba may still have the passwords of these SMB accounts, which can then still "
                "sign in and open the shares that smb.conf gives them:" in text
            )
            assert (
                "Run pdbedit -L. For each of these names that it lists exactly so (an entry in other letter case "
                "is not UNOS's, and pdbedit -x would remove it too), run pdbedit -x -u NAME. Then run:" in text
            )
        else:
            assert "Samba passwords:" not in completion
        assert "Remove each one with" not in text

    def test_a_name_unos_never_gives_is_not_passed_to_pdbedit(self, tmp_path):
        # Also names that Samba reads as SIDs (constants.linux_name_problem).
        refused = ["-r", "--help", "Bob", "a.b", "x" * 33, "s-1-1-0", "s-1-22-2-70000"]
        accepted = ["s1", "s-a", "sam", "ok"]
        names = refused + accepted
        passdb = "".join(f"{name}:{70000 + i}:\n" for i, name in enumerate(names))

        result, calls, after = self._run(tmp_path, passwd=_marked(*names), passdb=passdb)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [
            "getent passwd",
            "pdbedit -L",
            *(f"pdbedit -x -u {name}" for name in accepted),
            "systemctl try-restart smbd",
        ]
        assert [line.split(":")[0] for line in after] == refused
        assert result.stdout.count("Not touching the account") == len(refused)

    def test_a_name_reserved_for_the_system_is_not_passed_to_pdbedit(self, tmp_path):
        # UNOS never gives these names (constants.linux_name_problem), so a
        # marked account with one was made by hand.
        from src.shares import constants

        names = sorted(constants.RESERVED_ACCOUNT_NAMES) + ["systemd-network", "systemd-timesync"]
        passwd = _marked(*names) + "alice:x:79999:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin\n"
        passdb = "".join(f"{name}:{70000 + i}:\n" for i, name in enumerate(names)) + "alice:79999:\n"

        result, calls, after = self._run(tmp_path, passwd=passwd, passdb=passdb)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == ["getent passwd", "pdbedit -L", "pdbedit -x -u alice", "systemctl try-restart smbd"]
        assert [line.split(":")[0] for line in after] == names
        assert result.stdout.count("Not touching the account") == len(names)
        assert "Not touching the account 'unos-smb'" in result.stdout

    def test_the_reserved_names_are_those_of_the_supervisor(self):
        from src.shares import constants

        script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text()
        block = re.search(r"^ +local -a reserved=\(\n(.*?)^ +\)$", script, re.M | re.S).group(1)

        assert set(block.split()) == constants.RESERVED_ACCOUNT_NAMES
        assert len(block.split()) == len(constants.RESERVED_ACCOUNT_NAMES)
        assert constants.RESERVED_NAME_PREFIXES == ("systemd-",)

    def test_the_script_refuses_exactly_the_names_the_supervisor_refuses(self, tmp_path):
        # The script's own checks (pattern, reserved list, systemd-, s- and a
        # digit) must give what constants.linux_name_problem gives, so that a
        # change of the supervisor's rules fails here until the script has it.
        from src.shares import constants

        names = [
            "alice", "a", "a" * 32, "a-b", "a_b", "x9", "s", "s1", "s-", "s-a", "s--1", "sx-1", "as-1", "sam",
            "-r", "--help", "Bob", "a.b", "a*", "1abc", "_x", "x" * 33, "é",
            "s-0", "s-1-1-0", "s-1-22-2-70000", "s-9abc", "S-1-1-0",
            *sorted(constants.RESERVED_ACCOUNT_NAMES),
            *(f"{prefix}x" for prefix in constants.RESERVED_NAME_PREFIXES),
        ]
        expected = {name for name in names if constants.linux_name_problem(name) is not None}
        # The script spells the SID rule as the glob s-[0123456789]*.
        assert constants.SID_LIKE_PREFIX.pattern == "s-[0-9]"
        passdb ="".join(f"{name}:{70000 + i}:\n" for i, name in enumerate(names))

        result, calls, _ = self._run(tmp_path, passwd=_marked(*names), passdb=passdb)

        assert result.returncode == 0, result.stdout + result.stderr
        refused = set(re.findall(r"Not touching the account '(.*?)': UNOS does not create", result.stdout))
        removed = {call.removeprefix("pdbedit -x -u ") for call in calls if call.startswith("pdbedit -x -u ")}
        assert refused == expected
        assert removed == set(names) - expected

    def test_main_removes_the_samba_passwords_once_unos_has_stopped_and_before_samba_is_restored(self):
        script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text()
        body = re.search(r"^main\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        steps = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]

        assert (
            steps.index("confirm_uninstall")
            < steps.index("stop_and_remove_containers")
            < steps.index("remove_systemd_service")
            < steps.index("remove_samba_passwords")
            < steps.index("restore_samba_config")
            < steps.index("print_completion")
        )
        assert "userdel" not in body

    def test_the_confirmation_says_what_is_removed_and_what_stays(self, tmp_path):
        result = subprocess.run(
            ["bash", "-c", '. "$LIB"; confirm_uninstall'],
            env={**os.environ, "LIB": str(_uninstall_lib(tmp_path))},
            input="no\n",
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert "Uninstallation cancelled" in result.stdout
        assert "  - The Samba passwords of the SMB accounts created by UNOS\n" in result.stdout
        assert "The SMB accounts themselves and the group unos-smb are kept" in self._text(result)

    def test_the_completion_names_the_kept_accounts_and_how_to_remove_them(self, tmp_path):
        result, _, _ = self._run(tmp_path, "remove_samba_passwords; print_completion")
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "  - The SMB accounts created by UNOS and the group unos-smb (see below)\n" in result.stdout
        assert (
            "the host accounts that UNOS created for SMB and the group unos-smb are kept. Files on the pools "
            "belong to them, and while these accounts exist, a new installation of UNOS gives its SMB accounts "
            "other user ids" in text
        )
        # Only UNOS's accounts, then the group (userdel first: it is their primary group).
        commands = [line.strip() for line in result.stdout.splitlines() if line.startswith("  userdel ")]
        assert commands == ["userdel alice", "userdel bob"]
        assert result.stdout.index("  userdel bob\n") < result.stdout.index("  groupdel unos-smb\n")
        # Every Samba password of these accounts was removed.
        assert "Samba passwords:" not in result.stdout

    def test_without_accounts_the_completion_names_the_group(self, tmp_path):
        result, _, _ = self._run(
            tmp_path, "remove_samba_passwords; print_completion", passwd="root:x:0:0:root:/root:/bin/bash\n"
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "The group unos-smb is kept: files on the pools belong to it." in self._text(result)
        assert "  groupdel unos-smb\n" in result.stdout
        assert "userdel" not in result.stdout

    @pytest.mark.parametrize(
        "samba, says, backup",
        [
            # The device: UNOS imported the old file, install.sh never ran.
            (("smb.conf", "smb.conf.unos-import-backup"), "stays", True),
            (("smb.conf",), "stays", False),
            # install.sh kept the file it replaced.
            (("smb.conf", "smb.conf.original"), "original", False),
            (("smb.conf", "smb.conf.original", "smb.conf.unos-import-backup"), "original", False),
        ],
    )
    def test_the_completion_says_what_happens_to_smb_conf(self, tmp_path, samba, says, backup):
        result, calls, _ = self._run(tmp_path, "restore_samba_config; print_completion", samba=samba)
        text = self._text(result)
        folder = tmp_path / "samba"

        assert result.returncode == 0, result.stdout + result.stderr
        if says == "original":
            assert calls == ["systemctl restart smbd nmbd"]
            assert (folder / "smb.conf").read_text() == "smb.conf.original\n"
            assert f"Samba configuration: {folder}/smb.conf is the file from before UNOS again" in text
            assert "still serves" not in text
        else:
            # Nothing restarts or changes it: the shares it lists stay served.
            assert calls == []
            assert (folder / "smb.conf").read_text() == "smb.conf\n"
            assert (
                f"Samba configuration: {folder}/smb.conf stays, and Samba still serves the shares it lists, "
                "guest shares to anyone on your network." in text
            )
            assert "systemctl restart smbd" in text
        # After an upgrade the copy is the file of the earlier release, not the
        # one from before UNOS (that is smb.conf.original).
        assert (
            f"The smb.conf from before this release of UNOS (UNOS imported its shares from it) is "
            f"{folder}/smb.conf.unos-import-backup. Check it before you copy it back: it may give every Samba "
            "account access to its shares." in text
        ) is backup
        assert ("unos-import-backup" in text) is backup
        assert "The file from before UNOS is" not in text

    def test_a_failed_restore_of_smb_conf_does_not_stop_the_uninstallation(self, tmp_path):
        """The copy of smb.conf.original fails (a read-only or full /etc). Under set -e it
        ended the uninstallation before its last text, which names the Samba passwords
        that may be left; that text now also says that smb.conf was not restored."""
        cp = _LOGGER + "echo \"cp: cannot create regular file '$2': Read-only file system\" >&2\nexit 1\n"
        result, calls, _ = self._run(
            tmp_path,
            "remove_samba_passwords; restore_samba_config; print_completion",
            fail=("pdbedit -x -u alice",),
            samba=("smb.conf", "smb.conf.original"),
            stubs={"cp": cp},
        )
        text = self._text(result)
        folder = tmp_path / "samba"

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"cp {folder}/smb.conf.original {folder}/smb.conf" in calls
        # Samba is not restarted with a file that may be incomplete.
        assert "systemctl restart smbd nmbd" not in calls
        assert (
            f"Could not restore {folder}/smb.conf from smb.conf.original; copy it back by hand "
            "(see the end of the uninstallation)" in text
        )
        assert "Original Samba configuration restored" not in text
        assert "UNOS Uninstallation Complete!" in result.stdout
        completion = result.stdout[result.stdout.index("UNOS Uninstallation Complete!") :]
        block = completion[completion.index("Samba passwords:") : completion.index("SMB accounts:")]
        assert [line.strip() for line in block.splitlines() if line.startswith("  ")] == [
            "alice",
            "systemctl restart smbd",
        ]
        assert (
            f"Samba configuration: {folder}/smb.conf could not be replaced by the file from before UNOS "
            f"({folder}/smb.conf.original): it may still be the file UNOS wrote, or be incomplete. Copy it "
            "back, then restart Samba:" in text
        )
        commands = [line.strip() for line in completion.splitlines() if line.startswith("  ")]
        assert commands[-2:] == [f"cp {folder}/smb.conf.original {folder}/smb.conf", "systemctl restart smbd nmbd"]
        assert "is the file from before UNOS again" not in text
        assert (folder / "smb.conf").read_text() == "smb.conf\n"


@pytest.mark.parametrize("script", ["update.sh", "install.sh"])
def test_compose_versions_before_2_30(script):
    # Compose 2.30 applies --wait-timeout to the wait for dependencies.
    versions = {
        "2.29.7": True,
        "2.16.0": True,
        "1.29.2": True,
        "v2.17.0-desktop.1": True,
        "2.30.0": False,
        "v2.40.3": False,
        "2.40.3+ds1-0ubuntu1~24.04.1": False,
        "5.0.2": False,
        "10.0.0": False,
        # Unknown.
        "": False,
        "dev": False,
    }

    assert _compose_before_2_30(script, list(versions)) == list(versions.values())


class TestTestStack:
    @pytest.fixture
    def compose(self):
        return yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.test.yml").read_text())

    def test_has_its_own_project(self, compose):
        # Otherwise it shares "deploy" with docker-compose.yml and replaces its images.
        assert compose["name"] == "unos-test"

    def test_serves_https_on_loopback(self, compose):
        webui = compose["services"]["webui"]
        assert set(webui["ports"]) == {"127.0.0.1:3080:80", "127.0.0.1:3443:443"}
        assert "UNOS_HOSTNAME=webui" in webui["environment"]
        assert "BASE_URL=https://webui" in compose["services"]["test-runner"]["environment"]

    def test_has_no_fixed_jwt_secret(self, compose):
        env = dict(item.split("=", 1) for item in compose["services"]["supervisor"]["environment"])
        assert env.get("UNOS_JWT_SECRET", "") == ""

    def test_runner_runs_the_restore_tests(self, compose):
        # tests/backup-restore.spec.ts restores only on a local BASE_URL or
        # with E2E_ALLOW_RESTORE=1, and https://webui is not local: the
        # restore tests were skipped on this throwaway stack.
        spec = (REPO_ROOT / "webui" / "tests" / "backup-restore.spec.ts").read_text()
        assert "process.env.E2E_ALLOW_RESTORE === '1'" in spec

        assert "E2E_ALLOW_RESTORE=1" in compose["services"]["test-runner"]["environment"]
        assert "E2E_ALLOW_RESTORE=1" in (REPO_ROOT / "docs" / "TESTING.md").read_text()


# The test-runner's view of the test stack: /api/v1/health fails as often as
# the file health-failures says, /api/v1/auth/setup answers with the status in
# setup-status.
RUNNER_CURL_STUB = textwrap.dedent(
    """
    #!/bin/bash
    echo "curl $*" >> "$STUB_DIR/calls"
    case "$*" in
      */api/v1/health*)
        left=$(cat "$STUB_DIR/health-failures")
        if [ "$left" -gt 0 ]; then echo $((left - 1)) > "$STUB_DIR/health-failures"; exit 7; fi ;;
      */api/v1/auth/setup*) cat "$STUB_DIR/setup-status" ;;
    esac
    exit 0
    """
).lstrip()


def _compose_string(value):
    """A string of the compose file as the container gets it: $$ is a $. A
    single $ would be replaced with a variable of the host's environment."""
    assert "$" not in value.replace("$$", ""), value
    return value.replace("$$", "$")


class TestTestRunner:
    """The test-runner service of the test profile (webui/Dockerfile.test)."""

    def test_image_has_the_playwright_version_of_package_lock(self):
        # The image carries the browsers of exactly one Playwright version.
        lock = json.loads((REPO_ROOT / "webui" / "package-lock.json").read_text())
        version = lock["packages"]["node_modules/@playwright/test"]["version"]
        dockerfile = (REPO_ROOT / "webui" / "Dockerfile.test").read_text()

        images = re.findall(
            r"^FROM\s+mcr\.microsoft\.com/playwright:v([^\s-]+)(?:-\w+)?\s*$", dockerfile, re.MULTILINE
        )

        assert images == [version]

    @pytest.fixture
    def run_runner(self, tmp_path):
        """Run the test-runner's entrypoint and command with stub curl/npx."""
        compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.test.yml").read_text())
        service = compose["services"]["test-runner"]
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stubs = (
            ("curl", RUNNER_CURL_STUB),
            ("npx", '#!/bin/bash\necho "npx $*" >> "$STUB_DIR/calls"\n'),
            ("sleep", "#!/bin/bash\n"),
        )
        for name, body in stubs:
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)

        def run(setup_status, health_failures=0):
            (tmp_path / "calls").write_text("")
            (tmp_path / "setup-status").write_text(setup_status)
            (tmp_path / "health-failures").write_text(f"{health_failures}\n")
            result = subprocess.run(
                [_compose_string(arg) for arg in [*service["entrypoint"], *service["command"]]],
                check=False,
                env={
                    **os.environ,
                    **dict(_compose_string(item).split("=", 1) for item in service["environment"]),
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "STUB_DIR": str(tmp_path),
                },
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result, (tmp_path / "calls").read_text().splitlines()

        return run

    @pytest.mark.parametrize("status", ["200", "400"])
    def test_creates_the_test_account_then_runs_the_tests(self, run_runner, status):
        # 200: a fresh stack. 400: the account exists already.
        result, calls = run_runner(status, health_failures=2)

        assert result.returncode == 0, result.stdout + result.stderr
        # Waits until Caddy answers, then creates the account once.
        assert [call for call in calls if "/api/v1/health" in call] == calls[:3]
        setup = calls[3]
        assert "-X POST" in setup
        assert setup.endswith("https://webui/api/v1/auth/setup")
        assert """-d {"username": "admin", "password": "admin123"}""" in setup
        assert calls[4:] == ["npx playwright test --reporter=list,html"]

    @pytest.mark.parametrize("status", ["500", "000"])
    def test_failed_setup_stops_before_the_tests(self, run_runner, status):
        # 000: no connection.
        result, calls = run_runner(status)

        assert result.returncode == 1
        assert f"Creating the test account failed (HTTP {status})" in result.stderr
        assert not any(call.startswith("npx") for call in calls)

    def test_gives_up_when_the_stack_does_not_answer(self, run_runner):
        result, calls = run_runner("200", health_failures=1000)

        assert result.returncode == 1
        assert "UNOS does not answer at https://webui" in result.stderr
        assert calls
        assert not any("/api/v1/auth/setup" in call or call.startswith("npx") for call in calls)


class TestTestCycle:
    """scripts/test-cycle.sh and the commands of docs/TESTING.md start the
    test stack, whose webui waits for the supervisor to be healthy."""

    def test_start_never_gives_up_before_docker_has_decided(self, tmp_path):
        # Without --wait-timeout, `up` waits without a limit for a supervisor
        # that keeps restarting without turning unhealthy.
        script = (REPO_ROOT / "scripts" / "test-cycle.sh").read_text()
        tail = '\n# Handle Ctrl+C\ntrap cleanup EXIT\n\n# Run main\nmain "$@"\n'
        assert script.endswith(tail)
        for directory in ("scripts", "deploy", "bin"):
            (tmp_path / directory).mkdir()
        # PROJECT_ROOT is the parent of the script's directory.
        (tmp_path / "scripts" / "test-cycle-lib.sh").write_text(script[: -len(tail)] + "\n")
        # The stack starts, and answers 200 to the health check and the setup.
        stubs = {
            "docker": 'echo "docker $*" >> "$STUB_DIR/calls"\n',
            "curl": 'echo "curl $*" >> "$STUB_DIR/calls"\necho 200\n',
        }
        for name, body in stubs.items():
            (tmp_path / "bin" / name).write_text("#!/bin/bash\n" + body)
            (tmp_path / "bin" / name).chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", '. "$LIB"; start_test_containers'],
            env={
                **os.environ,
                "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
                "LIB": str(tmp_path / "scripts" / "test-cycle-lib.sh"),
                "STUB_DIR": str(tmp_path),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        calls = (tmp_path / "calls").read_text().splitlines()

        assert result.returncode == 0, result.stdout + result.stderr
        assert "docker compose -f docker-compose.test.yml build --quiet" in calls
        assert _wait_timeouts(calls) == [_supervisor_decided_within("docker-compose.test.yml")]

    def test_testing_md_starts_the_stack_with_the_same_limit(self):
        testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()
        ups = re.findall(r"^docker compose -f docker-compose\.test\.yml up\b.*$", testing, re.M)

        assert len(ups) == 3
        assert _wait_timeouts(ups) == [_supervisor_decided_within("docker-compose.test.yml")] * 3


class TestInstallGuide:
    """docs/INSTALL.md: the systemd unit of a manual installation, and the
    command that adds --wait-timeout to the unit of an earlier install.sh."""

    def test_unit_starts_compose_as_install_sh_does(self, tmp_path):
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        _, _, exec_start = _start_install(tmp_path)

        assert re.findall(r"^ExecStart=.*$", guide, re.M) == exec_start

    def test_sed_command_gives_an_earlier_unit_the_limit_of_install_sh(self, tmp_path):
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        commands = re.findall(r"^ *sudo sed -i -E '([^']+)' /etc/systemd/system/unos\.service$", guide, re.M)
        _, _, exec_start = _start_install(tmp_path)
        assert len(commands) == 1
        assert len(exec_start) == 1

        # The unit of an earlier install.sh, one with another limit, and one
        # that has the limit already.
        for before in ("up -d", "up -d --wait-timeout 600", exec_start[0].split(" compose ", 1)[1]):
            unit = textwrap.dedent(
                f"""
                [Service]
                Type=oneshot
                ExecStart=/usr/bin/docker compose {before}
                ExecStop=/usr/bin/docker compose down
                TimeoutStartSec=0
                """
            )
            result = subprocess.run(
                ["sed", "-E", commands[0]], input=unit, capture_output=True, text=True, check=True, timeout=30
            )
            assert [line for line in result.stdout.splitlines() if line.startswith("ExecStart=")] == exec_start
            # Only ExecStart changed.
            assert result.stdout.replace(exec_start[0], "") == unit.replace(
                f"ExecStart=/usr/bin/docker compose {before}", ""
            )


def _dockerignore(context):
    lines = (REPO_ROOT / context / ".dockerignore").read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


class TestBuildContexts:
    def test_dockerignore_keeps_what_the_test_images_copy(self):
        for context in ("webui", "supervisor"):
            assert not any(p.strip("/*") == "tests" for p in _dockerignore(context)), context
        assert "COPY tests/" in (REPO_ROOT / "webui" / "Dockerfile.test").read_text()
        assert "COPY tests/mocks/" in (REPO_ROOT / "supervisor" / "Dockerfile.test").read_text()

    def test_dockerignore_drops_local_build_output(self):
        assert {"node_modules", "build", ".svelte-kit"} <= _dockerignore("webui")
        assert {"**/__pycache__", "**/*.pyc"} <= _dockerignore("supervisor")


COMPOSE_FILES = ("docker-compose.yml", "docker-compose.test.yml")
SUPERVISOR_HEALTH_URL = "http://127.0.0.1:8080/api/v1/health"


def _compose_file(name):
    return yaml.safe_load((REPO_ROOT / "deploy" / name).read_text())


def _seconds(duration):
    """A compose duration of hours, minutes and seconds ("1m30s") in seconds."""
    parts = re.findall(r"(\d+)([hms])", duration)
    assert parts and "".join(number + unit for number, unit in parts) == duration, duration
    return sum(int(number) * {"h": 3600, "m": 60, "s": 1}[unit] for number, unit in parts)


def _supervisor_decided_within(compose_file):
    """At least as long as Docker can take to report the supervisor of
    `compose_file` healthy or unhealthy (moby daemon/health.go). A failed
    check counts only if it started after start_period, so the one that runs
    then does not count, and the supervisor is unhealthy after retries more.
    Each check takes up to timeout once its process has started, and Docker
    waits interval after each one: start_period + (retries + 1) x (interval +
    timeout). start_period + retries x (interval + timeout) leaves out the
    check that runs when start_period ends: Compose could give up on a
    supervisor that answers in its last check up to timeout seconds before
    Docker reports it healthy."""
    check = _compose_file(compose_file)["services"]["supervisor"]["healthcheck"]
    return _seconds(check["start_period"]) + (check["retries"] + 1) * (
        _seconds(check["interval"]) + _seconds(check["timeout"])
    )


def _wait_timeouts(calls):
    """The --wait-timeout values of the `docker compose ... up` calls."""
    ups = [call for call in calls if re.match(r"docker compose (-f \S+ )?up ", call) and not call.endswith(" --help")]
    assert ups
    values = [re.search(r" --wait-timeout (\d+)(?: |$)", up) for up in ups]
    assert all(values), ups
    return [int(value.group(1)) for value in values]


@contextlib.contextmanager
def _http_server(status):
    """An HTTP server on a free port of 127.0.0.1 that answers every GET with
    `status`. Yields the port and the list of the requested paths. With status
    None nothing listens on the port: connections are refused."""
    paths = []
    if status is None:
        # Closed again: macOS lets connections to a bound socket that does
        # not listen time out instead of refusing them.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        yield port, paths
        return

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], paths
    finally:
        server.shutdown()
        server.server_close()


class TestHealthchecks:
    """docker ps shows whether the containers work, and compose starts webui
    only once the supervisor answers."""

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_has_no_obsolete_version_key(self, compose_file):
        # Compose ignores it and warns about it on every command.
        assert "version" not in _compose_file(compose_file)

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_webui_starts_once_the_supervisor_is_healthy(self, compose_file):
        services = _compose_file(compose_file)["services"]

        assert services["webui"]["depends_on"] == {"supervisor": {"condition": "service_healthy"}}
        assert services["supervisor"]["healthcheck"]["test"][0] == "CMD"

    @pytest.mark.parametrize("status, exit_code", [(200, 0), (503, 1), (None, 1)])
    def test_supervisor_is_healthy_only_while_the_api_answers(self, status, exit_code):
        check = _compose_file("docker-compose.yml")["services"]["supervisor"]["healthcheck"]
        # The image (python:3.11-slim) has neither curl nor wget.
        assert check["test"][:3] == ["CMD", "python", "-c"]
        code = check["test"][3]
        assert code.count(SUPERVISOR_HEALTH_URL) == 1
        # The request gives up before Docker kills the check.
        request_timeout = re.search(r"timeout=(\d+)", code)
        assert request_timeout, code
        assert int(request_timeout.group(1)) < _seconds(check["timeout"])

        env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
        with _http_server(None) as (proxy_port, _), _http_server(status) as (port, paths):
            # A proxy in the container's environment must not get the request.
            env["http_proxy"] = env["HTTP_PROXY"] = f"http://127.0.0.1:{proxy_port}"
            result = subprocess.run(
                [sys.executable, "-c", code.replace(SUPERVISOR_HEALTH_URL, f"http://127.0.0.1:{port}/api/v1/health")],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

        # Docker: 0 is healthy, 1 unhealthy.
        assert result.returncode == exit_code, result.stderr
        assert paths == ([] if status is None else ["/api/v1/health"])

    def test_supervisor_has_time_to_mount_the_pools(self):
        # At startup the supervisor mounts the pools the host has not mounted
        # before it answers, up to 2 minutes each. Failed checks count only
        # after start_period: at least as long as update.sh gives a new
        # version to answer by default.
        check = _compose_file("docker-compose.yml")["services"]["supervisor"]["healthcheck"]
        update_sh = (REPO_ROOT / "deploy" / "update.sh").read_text()
        default_timeout = re.search(r'^HEALTH_TIMEOUT="\$\{UNOS_HEALTH_TIMEOUT:-(\d+)\}"$', update_sh, re.M)
        assert default_timeout, "no default HEALTH_TIMEOUT in update.sh"

        assert _seconds(check["start_period"]) >= int(default_timeout.group(1))

    def test_update_never_gives_up_before_docker_has_decided(self, sandbox):
        # Compose ended the wait for a slow but healthy supervisor after
        # UNOS_HEALTH_TIMEOUT (300 seconds), the update was rolled back, and
        # the rollback's up gave up on the previous version the same way and
        # left webui stopped. Both ups, with the shortest health timeout.
        result, calls = _run_update(sandbox, "up-fails", UNOS_HEALTH_TIMEOUT="0")
        decided = _supervisor_decided_within("docker-compose.yml")

        assert len(_up_calls(calls)) == 2, result.stdout + result.stderr
        assert [timeout for timeout in _wait_timeouts(calls) if timeout < decided] == []

    def test_install_and_the_unit_never_give_up_before_docker_has_decided(self, tmp_path):
        # install.sh and the systemd unit (at boot, where pools are most
        # likely not mounted yet).
        result, calls, exec_start = _start_install(tmp_path)
        decided = _supervisor_decided_within("docker-compose.yml")

        assert result.returncode == 0, result.stdout + result.stderr
        timeouts = _wait_timeouts(calls) + _wait_timeouts([line.split("/usr/bin/", 1)[1] for line in exec_start])
        assert len(timeouts) == 2
        assert [timeout for timeout in timeouts if timeout < decided] == []

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_webui_check_reaches_the_api_through_caddy_over_plain_http(self, compose_file):
        check = _compose_file(compose_file)["services"]["webui"]["healthcheck"]
        assert check["test"] == ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/api/v1/health"]
        # busybox wget: the image is Caddy on Alpine.
        stages = re.findall(r"^FROM (\S+)", (REPO_ROOT / "webui" / "Dockerfile").read_text(), re.M)
        assert re.fullmatch(r"caddy:[\w.]+-alpine", stages[-1])
        # The http:// site answers the health route itself instead of
        # redirecting it to HTTPS.
        http_site = (REPO_ROOT / "webui" / "Caddyfile").read_text().split("\nhttp:// {\n", 1)[1]
        assert re.search(r"handle /api/v1/health \{\s+reverse_proxy supervisor:8080\s+\}", http_site)

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_docker_compose_accepts_the_file(self, compose_file):
        if shutil.which("docker") is None:
            pytest.skip("needs the docker CLI")
        # No daemon needed.
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--profile", "test", "config", "--quiet"],
            cwd=REPO_ROOT / "deploy",
            capture_output=True,
            text=True,
            timeout=120,
        )
        if "is not a docker command" in result.stderr:
            pytest.skip("needs the docker compose plugin")

        assert result.returncode == 0, result.stderr
        # Such as "the attribute `version` is obsolete".
        assert "obsolete" not in result.stderr, result.stderr


# The limit of the supervisor's tmpfs at /tmp in both compose files (size=2g):
# the largest legitimate use (_largest_tmp_use) with a margin, and about a
# quarter of the RAM of an 8 GB board for a runaway write.
SUPERVISOR_TMPFS = 2 * 1024**3
_TMPFS_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _supervisor_tmpfs(compose_file, path="/tmp"):
    """The options of the supervisor's tmpfs at `path` in `compose_file`, as
    Compose passes them to Docker (HostConfig.Tmpfs): "/tmp:noexec,size=2g"
    gives {"noexec": None, "size": "2g"}."""
    tmpfs = _compose_file(compose_file)["services"]["supervisor"].get("tmpfs", [])
    mounts = {}
    for entry in [tmpfs] if isinstance(tmpfs, str) else tmpfs:
        target, _, options = entry.partition(":")
        mounts[target] = {
            key: value if sep else None
            for key, sep, value in (option.partition("=") for option in options.split(",") if option)
        }
    assert path in mounts, f"the supervisor has no tmpfs at {path} in {compose_file}: {tmpfs!r}"
    return mounts[path]


def _tmpfs_bytes(size):
    """A tmpfs size= in bytes, as the kernel reads it: a number with an
    optional k, m or g (powers of 1024). Not a percentage of the RAM: the
    limit must not depend on the board."""
    match = re.fullmatch(r"(\d+)([kmg]?)", size, re.IGNORECASE)
    assert match, size
    return int(match.group(1)) * _TMPFS_UNITS[match.group(2).lower()]


def _largest_tmp_use():
    """The most legitimate work keeps in the supervisor's /tmp at once, from
    the limits in src/backup/router.py: a restore, and a backup made
    meanwhile. Uploads write nothing there: an uploaded backup is checked in
    one pass, as a stream (_inspect_archive).

    A restore (one at a time) holds the decompressed archive (_decompress: at
    most MAX_TAR_BYTES) and the files extracted from it (_extract), which are
    copied out of the archive, so no larger, and each within its own limit:
    the database MAX_DB_BYTES, any other file MAX_CONFIG_BYTES, at most
    MAX_MEMBERS entries. A backup made meanwhile (Create Backup or a
    scheduled backup task) collects its files there: a copy of the database
    and the small files (manifest, smb.conf, fstab, netplan), within the same
    limits, or it could not be restored. deploy/update.sh copies the database
    there the same way (backup_database_online)."""
    from src.backup.router import MAX_CONFIG_BYTES, MAX_DB_BYTES, MAX_MEMBERS, MAX_TAR_BYTES

    files = min(MAX_TAR_BYTES, MAX_DB_BYTES + MAX_MEMBERS * MAX_CONFIG_BYTES)
    restore = MAX_TAR_BYTES + files
    backup = files
    return restore + backup


class TestTemporaryFiles:
    """The supervisor's temporary files are in RAM: the container's writable
    layer is on the system disk, on many boards an SD card, which writes wear
    out."""

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_supervisor_has_a_tmpfs_at_tmp_with_the_chosen_limit(self, compose_file):
        options = _supervisor_tmpfs(compose_file)

        assert _tmpfs_bytes(options["size"]) == SUPERVISOR_TMPFS
        # Nothing runs from /tmp: Docker's defaults, stated in the file, and
        # none of them undone by a later option.
        assert {"noexec", "nosuid", "nodev"} <= options.keys()
        assert {"exec", "suid", "dev"}.isdisjoint(options)

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_btrfs_scrub_records_its_progress_in_ram(self, compose_file):
        # btrfs-progs rewrites /var/lib/btrfs/scrub.status.<fsid> every 5 s
        # while a scrub runs, a new file renamed over the old one each time:
        # in the writable layer, the system disk would get it for hours.
        options = _supervisor_tmpfs(compose_file, "/var/lib/btrfs")

        assert 0 < _tmpfs_bytes(options["size"]) <= 1024**2
        assert {"noexec", "nosuid", "nodev"} <= options.keys()
        assert {"exec", "suid", "dev"}.isdisjoint(options)

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_the_tmpfs_holds_the_largest_restore(self, compose_file):
        # Otherwise a restore of the largest backups that pass the checks of
        # src/backup/router.py, or a backup made while it runs, fails for lack
        # of room ("No space left on device"; the restore answers 507): raise
        # the tmpfs with those limits.
        size = _tmpfs_bytes(_supervisor_tmpfs(compose_file)["size"])

        assert _largest_tmp_use() <= size

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_sqlite_puts_its_temporary_files_on_the_tmpfs(self, compose_file):
        # SQLite takes $SQLITE_TMPDIR, then $TMPDIR, then /var/tmp before /tmp
        # (https://www.sqlite.org/tempfiles.html): without TMPDIR its
        # temporary files would go to /var/tmp, in the writable layer.
        environment = _compose_file(compose_file)["services"]["supervisor"]["environment"]
        env = dict(item.split("=", 1) for item in environment)

        assert env.get("TMPDIR") == "/tmp"
        assert "SQLITE_TMPDIR" not in env

    def test_install_guide_states_the_limit(self):
        size = _tmpfs_bytes(_supervisor_tmpfs("docker-compose.yml")["size"])
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        paragraphs = [p for p in re.split(r"\n\s*\n", guide) if "temporary files in RAM" in p]

        assert len(paragraphs) == 1, paragraphs
        assert f"at most {size / 1024**3:g} GiB" in " ".join(paragraphs[0].split())

    @staticmethod
    def _run_online_backup(tmp_path, tmpdir_writable):
        """update.sh's backup_database_online, its Python program run as there
        (python -, the container's environment): TMPDIR is the tmpfs, TEMP a
        stand-in for /var/tmp, which tempfile would take after it. Returns the
        result and the temporary directories the program tried to make (the
        audit event comes before the mkdir)."""
        text = (REPO_ROOT / "deploy" / "update.sh").read_text()
        program = re.search(r"\nbackup_database_online\(\) \{\n.*?<<'PY'\n(.*?)\nPY\n", text, re.S)
        assert program, "backup_database_online in update.sh"
        db = tmp_path / "data" / "unos.db"
        db.parent.mkdir()
        with contextlib.closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE users (name TEXT)")
            conn.execute("INSERT INTO users VALUES ('admin')")
        tmpdir, var_tmp = tmp_path / "tmp", tmp_path / "var-tmp"
        tmpdir.mkdir()
        var_tmp.mkdir()
        if not tmpdir_writable:
            # For a full tmpfs, which still takes a directory but no data (the
            # copy then fails with "database or disk is full"): tempfile's own
            # test write fails in both, so it would move on to TEMP.
            tmpdir.chmod(0o500)
            try:  # root, or a filesystem that ignores modes, writes there anyway
                (tmpdir / "probe").touch()
            except PermissionError:
                pass
            else:
                (tmpdir / "probe").unlink()
                tmpdir.chmod(0o700)
                pytest.skip("this user can write into a directory without write permission")
        hook = (
            "import sys\n"
            "sys.addaudithook(lambda event, args: event == 'tempfile.mkdtemp'"
            " and print('mkdtemp', args[0], file=sys.stderr))\n"
        )
        env = {key: value for key, value in os.environ.items() if key not in ("TMPDIR", "TEMP", "TMP")}
        env.update(UNOS_DB_PATH=str(db), TMPDIR=str(tmpdir), TEMP=str(var_tmp), PYTHONDONTWRITEBYTECODE="1")
        try:
            result = subprocess.run(
                [sys.executable, "-"], input=hook.encode() + program.group(1).encode(),
                capture_output=True, env=env, cwd=tmp_path, timeout=60,
            )
        finally:
            tmpdir.chmod(0o700)
        made = [line.split(" ", 1)[1] for line in result.stderr.decode().splitlines() if line.startswith("mkdtemp ")]
        return result, made, tmpdir, var_tmp

    def test_the_database_copy_of_update_sh_is_made_in_tmpdir(self, tmp_path):
        result, made, tmpdir, var_tmp = self._run_online_backup(tmp_path, tmpdir_writable=True)

        assert result.returncode == 0, result.stderr.decode()
        assert made and all(Path(path).parent == tmpdir for path in made)
        copy = tmp_path / "copy.db"
        copy.write_bytes(result.stdout)
        with contextlib.closing(sqlite3.connect(copy)) as conn:
            assert conn.execute("SELECT name FROM users").fetchall() == [("admin",)]
        assert os.listdir(tmpdir) == [] and os.listdir(var_tmp) == []

    def test_the_database_copy_of_update_sh_is_never_made_elsewhere(self, tmp_path):
        """A full /tmp makes the backup fail (update.sh then stops, or asks
        to update without one) rather than write the copy to /var/tmp, on
        the system disk."""
        result, made, tmpdir, var_tmp = self._run_online_backup(tmp_path, tmpdir_writable=False)

        assert result.returncode != 0
        assert b"PermissionError" in result.stderr
        assert made and all(Path(path).parent == tmpdir for path in made)  # tried there only
        assert result.stdout == b""
        assert os.listdir(tmpdir) == [] and os.listdir(var_tmp) == []


WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
# The command in the header of .github/lint-requirements.txt.
LINT_COMPILE = (
    "uv pip compile --universal --python-version 3.11 --generate-hashes"
    " .github/lint-requirements.in -o .github/lint-requirements.txt"
)
# npx installing a package that is not in package-lock.json.
NPX_INSTALL = re.compile(r"\bnpx\s+(-y|--yes|-p|--package)\b|\bnpx\s+[\w@./-]+@\d")
# A requirement as uv pip compile --generate-hashes writes it: the name (uv
# normalizes it; pip-compile keeps dots), extras (with --no-strip-extras), the
# version, the environment markers after " ; ", and the backslash before its
# hashes.
HASHED_REQUIREMENT = re.compile(r"([A-Za-z0-9][\w.-]*)(\[[\w.,\s-]*\])?==([^\s;]+)(?: ; (.+))? \\")
HASH_LINE = re.compile(r"\s+--hash=sha256:([0-9a-f]{64})(?: \\)?")


def _workflow_jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _commands(job):
    return [step["run"] for step in _workflow_jobs()[job]["steps"] if "run" in step]


def _normalized(name):
    """A package name as pip compares them (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _hashed_requirements(text):
    """The requirements of a requirements file with hashes, as (normalized
    name, version, markers or None, hashes), in file order. Fails on a line
    that is neither one of them, a hash nor a comment."""
    requirements = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            requirement = HASHED_REQUIREMENT.fullmatch(line)
            assert requirement, line
            name, _, version, markers = requirement.groups()
            requirements.append((_normalized(name), version, markers, []))
        else:
            digest = HASH_LINE.fullmatch(line)
            assert digest and requirements, line
            requirements[-1][3].append(digest.group(1))
    return requirements


class TestContinuousIntegration:
    """.github/workflows/test.yml. docs/TESTING.md lists the same commands."""

    def test_node_is_supported_and_the_same_for_the_image_ci_and_npm(self):
        builder = re.findall(
            r"^FROM node:(\d+)-alpine AS builder$", (REPO_ROOT / "webui" / "Dockerfile").read_text(), re.M
        )
        setup_node = [
            str(step["with"]["node-version"])
            for step in _workflow_jobs()["frontend"]["steps"]
            if step.get("uses", "").startswith("actions/setup-node@")
        ]
        engines = json.loads((REPO_ROOT / "webui" / "package.json").read_text())["engines"]

        assert len(builder) == 1
        # Node.js 20 reached its end of life in April 2026.
        assert int(builder[0]) >= 22
        assert setup_node == builder
        assert engines == {"node": f">={builder[0]}"}

    def test_python_checks_install_hashed_tools(self):
        # `pip install ruff==... pip-audit==...` resolved the packages they
        # depend on afresh in every run, without hashes.
        commands = _commands("lint")

        assert commands[0] == "pip install --require-hashes -r .github/lint-requirements.txt"
        assert "ruff check --select F,E9 supervisor" in commands
        assert "pip-audit -r supervisor/requirements.txt" in commands

    def test_lint_requirements_pin_every_package_with_its_hashes(self):
        wanted = (REPO_ROOT / ".github" / "lint-requirements.in").read_text()
        pins = {_normalized(name): version for name, version in re.findall(r"^([\w.-]+)==(\S+)$", wanted, re.M)}
        locked = (REPO_ROOT / ".github" / "lint-requirements.txt").read_text()
        requirements = _hashed_requirements(locked)

        assert set(pins) == {"ruff", "pip-audit"}
        # How to regenerate it, from the root of the repository.
        assert LINT_COMPILE in locked
        assert [name for name, _, _, hashes in requirements if not hashes] == []
        # Each one once, with the version of the .in file. A universal
        # resolution can list a package once per version, with markers.
        assert sorted((name, version) for name, version, _, _ in requirements if name in pins) == sorted(pins.items())

    def test_hashed_requirements_read_what_uv_writes(self):
        # The parser rejected a line with extras (--no-strip-extras) as one it
        # does not know, and a second line of a package (one version per
        # range of Python versions, say) replaced the first, hashes and all.
        # Names are compared as pip does: zope.interface is zope-interface.
        a, b = "a" * 64, "b" * 64
        text = (
            f"# This file was autogenerated by uv via the following command:\n#    {LINT_COMPILE}\n"
            f"cachecontrol[filecache]==0.14.4 \\\n    --hash=sha256:{a} \\\n    --hash=sha256:{b}\n"
            "    # via pip-audit\n"
            f"typing-extensions==4.16.0 ; python_full_version < '3.13' \\\n    --hash=sha256:{a}\n"
            "    # via\n    #   cyclonedx-python-lib\n"
            f"zope.interface==7.2 ; python_full_version < '3.12' \\\n    --hash=sha256:{a}\n"
            f"zope.interface==8.0 ; python_full_version >= '3.12' \\\n"
        )

        assert _hashed_requirements(text) == [
            ("cachecontrol", "0.14.4", None, [a, b]),
            ("typing-extensions", "4.16.0", "python_full_version < '3.13'", [a]),
            ("zope-interface", "7.2", "python_full_version < '3.12'", [a]),
            ("zope-interface", "8.0", "python_full_version >= '3.12'", []),
        ]
        # Not pinned, no backslash before the hashes, an option.
        for line in ("ruff>=0.17 \\", "ruff==0.17.3", "--index-url https://example.org/simple"):
            with pytest.raises(AssertionError):
                _hashed_requirements(f"{line}\n")

    def test_lint_requirements_cover_the_python_of_the_lint_job(self):
        # --universal --python-version X resolves for Python X and later, on
        # every platform (the runners are Linux x86_64): the markers of a
        # line say where pip installs it.
        python = [
            str(step["with"]["python-version"])
            for step in _workflow_jobs()["lint"]["steps"]
            if step.get("uses", "").startswith("actions/setup-python@")
        ]
        lowest = re.search(r"--universal --python-version (\d+)\.(\d+) ", LINT_COMPILE)
        locked = _hashed_requirements((REPO_ROOT / ".github" / "lint-requirements.txt").read_text())

        assert len(python) == 1
        assert tuple(int(part) for part in python[0].split(".")[:2]) >= tuple(int(part) for part in lowest.groups())
        # ruff and pip-audit without markers: everywhere.
        tools = sorted((name, markers) for name, _, markers, _ in locked if name in ("ruff", "pip-audit"))
        assert tools == [("pip-audit", None), ("ruff", None)]

    def test_frontend_checks(self):
        commands = _commands("frontend")
        scripts = json.loads((REPO_ROOT / "webui" / "package.json").read_text())["scripts"]

        # svelte-check as npm ci installed it from package-lock.json, through
        # the check script: npm run takes it from node_modules/.bin. `npx -y
        # svelte-check@<version>` resolved its dependencies afresh in every
        # run, and npx downloads a program that is missing there.
        assert commands == ["npm ci", "npm run build", "npm run check", "npm audit --omit=dev --audit-level=high"]
        assert scripts["check"] == "svelte-kit sync && svelte-check --threshold error"

    def test_svelte_check_is_an_exact_dev_dependency_in_the_lockfile(self):
        package = json.loads((REPO_ROOT / "webui" / "package.json").read_text())
        lock = json.loads((REPO_ROOT / "webui" / "package-lock.json").read_text())
        version = package["devDependencies"]["svelte-check"]

        assert re.fullmatch(r"\d+\.\d+\.\d+", version)
        assert lock["packages"][""]["devDependencies"]["svelte-check"] == version
        entry = lock["packages"]["node_modules/svelte-check"]
        assert entry["version"] == version
        assert entry["dev"] is True
        assert entry["resolved"] == f"https://registry.npmjs.org/svelte-check/-/svelte-check-{version}.tgz"
        assert entry["integrity"].startswith("sha512-")

    def test_checkouts_do_not_keep_the_token(self):
        # actions/checkout leaves the job's token in .git/config unless told
        # otherwise, where the build or test code of a dependency can read it.
        checkouts = [
            step
            for job in _workflow_jobs().values()
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        ]

        assert len(checkouts) == len(_workflow_jobs())
        assert [step for step in checkouts if step.get("with", {}).get("persist-credentials") is not False] == []

    def test_actions_are_pinned_to_a_major_version(self):
        actions = [step["uses"] for job in _workflow_jobs().values() for step in job["steps"] if "uses" in step]

        assert actions
        assert [action for action in actions if not re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", action)] == []

    def test_every_check_fails_the_run(self):
        # Hard gates, also while a check fails (an audit after a new advisory,
        # say): no job or step may turn its failure into a pass.
        for name, job in _workflow_jobs().items():
            assert "continue-on-error" not in job, name
            assert [step for step in job["steps"] if "continue-on-error" in step] == [], name

    def test_testing_md_has_the_commands_of_the_workflow(self):
        # The local run in docs/TESTING.md, with the same pinned versions.
        testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()

        assert [command for command in _commands("lint") + _commands("frontend") if command not in testing] == []
        assert NPX_INSTALL.search(testing) is None

    def test_testing_md_says_how_to_regenerate_the_lint_requirements(self):
        # It only pointed at the header of the .txt.
        testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()

        assert re.search(rf"^{re.escape(LINT_COMPILE)}$", testing, re.M)

    def test_npx_runs_only_programs_of_direct_dependencies(self):
        # npx downloads a program that is not in node_modules from the
        # registry, without asking where CI is set: `npx svelte-check@latest`
        # or a tool that is not a dependency of the web UI would bypass
        # package-lock.json as `npx -y svelte-check@3.8.6` did. svelte-kit
        # and svelte-check run through `npm run check`, and anything else run
        # with npx must be a program of a package that package.json depends
        # on directly: npm ci always installs those at the top of
        # node_modules, and links their programs in node_modules/.bin. npx is
        # `npm exec` (also `npm x`): the search finds all three.
        lock = json.loads((REPO_ROOT / "webui" / "package-lock.json").read_text())
        root = lock["packages"][""]
        programs = {
            program
            for package in {**root.get("dependencies", {}), **root.get("devDependencies", {})}
            for program in lock["packages"][f"node_modules/{package}"].get("bin", {})
        }
        texts = {
            "workflow": "\n".join(command for job in _workflow_jobs() for command in _commands(job)),
            "docs/TESTING.md": (REPO_ROOT / "docs" / "TESTING.md").read_text(),
            "scripts/test-cycle.sh": (REPO_ROOT / "scripts" / "test-cycle.sh").read_text(),
        }
        npx = {where: re.findall(r"\b(?:npx|npm\s+(?:exec|x))\s+(\S+)", text) for where, text in texts.items()}
        scripts = json.loads((REPO_ROOT / "webui" / "package.json").read_text())["scripts"]
        check = [command.split()[0] for command in scripts["check"].split("&&")]

        assert npx["workflow"] == []
        # The Playwright commands of the guide and the script: the search
        # finds what it looks for.
        assert "playwright" in npx["docs/TESTING.md"]
        assert "playwright" in npx["scripts/test-cycle.sh"]
        assert {where: [program for program in found if program not in programs] for where, found in npx.items()} == {
            where: [] for where in texts
        }
        assert {where: {"svelte-kit", "svelte-check"} & set(found) for where, found in npx.items()} == {
            where: set() for where in texts
        }
        assert check == ["svelte-kit", "svelte-check"]
        assert set(check) <= programs

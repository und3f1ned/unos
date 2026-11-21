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
import shlex
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
          -d | --no-build | --force-recreate | --remove-orphans) ;;
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
      # no-webui-image: no image deploy-webui:latest has been loaded.
      image) if [ "$2" = inspect ] && has no-webui-image; then exit 1; fi ;;
      # stopped: the supervisor's container exists but does not run; its /data is $STUB_DIR/data.
      cp)
        if [ "$3" = - ]; then tar -C "$STUB_DIR" -cf - data; else cp "$STUB_DIR/${2#unos-supervisor:/}" "$3"; fi ;;
      inspect)
        case "$3" in
          '{{.State.Running}}') if has stopped; then echo false; else echo true; fi ;;
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
          # The version the images get (ADR-0234): one line per build.
          build) echo "UNOS_VERSION=${UNOS_VERSION-<unset>}" >> "$STUB_DIR/build-env"
                 interrupt_at build; has probe-lock && probe_lock; has build-fails && exit 1 ;;
          down) ;;
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
              interrupt_at up
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


def _without_git_variables(environ) -> dict:
    """The environment without GIT_DIR, GIT_INDEX_FILE and the other GIT_* variables of an outer
    git (its hooks, git rebase -x): the sandbox's git commands stay in the sandbox (conftest.py
    removes them as well)."""
    return {key: value for key, value in environ.items() if not key.startswith("GIT_")}


def _git(*args, cwd):
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=_without_git_variables(os.environ))


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
        ["git", "rev-parse", "HEAD"], cwd=sandbox / "install", check=True, capture_output=True, text=True,
        env=_without_git_variables(os.environ),
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
        **_without_git_variables(os.environ),
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
    """$PATH without <command> (a name, or a tuple of names): each directory
    that has one is replaced by a directory with links to its other programs."""
    commands = (command,) if isinstance(command, str) else tuple(command)
    links = sandbox / f"path-without-{'-'.join(commands)}"
    path = []
    for entry in os.environ["PATH"].split(os.pathsep):
        directory = Path(entry)
        if not any((directory / name).exists() for name in commands):
            path.append(entry)
            continue
        links.mkdir(exist_ok=True)
        for program in directory.iterdir():
            if program.name not in commands and not os.path.lexists(links / program.name):
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
        assert _index(calls, "docker compose build") < _index(calls, "docker compose up -d --no-build")
        # No compose down first (deploy.no-legacy-network-recreate, ADR-0207): up recreates the
        # containers of the new images, and the network of a release without the fixed subnet
        # (ADR-0048) is no installation's.
        assert not any(call.startswith("docker compose down") for call in calls)
        assert _index(calls, "curl") < _index(calls, "docker image prune")
        backup = next((sandbox / "backups").iterdir())
        assert sorted(p.name for p in backup.iterdir()) == ["data.tar.gz", "unos.db"]
        assert (backup.stat().st_mode & 0o777) == 0o700
        assert (sandbox / "install" / "VERSION").read_text() == "2\n"

    def test_the_backup_of_a_stopped_supervisor_keeps_the_commits_of_its_log(self, sandbox):
        """A supervisor that stopped without its checkpoint (a crash, or a kill after docker
        stop's grace period) left its last commits in unos.db-wal (ADR-0306): the copy of
        unos.db alone lacked them, so the backup keeps unos.db-wal beside it."""
        data = sandbox / "stub" / "data"
        data.mkdir()
        run = (
            "import os, sqlite3, sys\n"
            "c = sqlite3.connect(sys.argv[1])\n"
            "c.execute('PRAGMA journal_mode=WAL')\n"
            "c.execute('CREATE TABLE users (name TEXT)')\n"
            "c.execute(\"INSERT INTO users VALUES ('admin')\")\n"
            "c.commit()\n"
            "os._exit(0)\n"  # the supervisor's end without its checkpoint
        )
        subprocess.run([sys.executable, "-c", run, str(data / "unos.db")], check=True, timeout=60)
        assert (data / "unos.db-wal").stat().st_size > 0

        result, calls = _run_update(sandbox, "stopped")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "docker cp unos-supervisor:/data/unos.db" in " ".join(calls)
        backup = next((sandbox / "backups").iterdir())
        assert sorted(p.name for p in backup.iterdir()) == ["data.tar.gz", "unos.db", "unos.db-wal"]
        copies = sandbox / "copies"
        copies.mkdir()
        for name in ("unos.db", "unos.db-wal"):
            shutil.copy(backup / name, copies / name)
        with contextlib.closing(sqlite3.connect(copies / "unos.db")) as conn:
            assert conn.execute("SELECT name FROM users").fetchall() == [("admin",)]

    def test_the_new_images_carry_the_version_of_the_new_checkout(self, sandbox):
        # System > Updates shows the version the supervisor image was built with (ADR-0234).
        _git("tag", "v0.1.0", "HEAD", cwd=sandbox / "install")

        result, calls = _run_update(sandbox)

        assert result.returncode == 0, result.stdout + result.stderr
        # One commit after the tag: <tag>-1-g<commit>, as git describe names it.
        built = (sandbox / "stub" / "build-env").read_text().splitlines()
        assert len(built) == 1 and built[0].startswith("UNOS_VERSION=v0.1.0-1-g"), built
        assert _head(sandbox).startswith(built[0].removeprefix("UNOS_VERSION=v0.1.0-1-g"))
        assert f"Updated to: {built[0].removeprefix('UNOS_VERSION=')}" in result.stdout

    @pytest.mark.parametrize("tag_at_install", [None, "v0.9.0"], ids=["no-tag-at-install", "older-tag-at-install"])
    def test_a_release_tagged_after_the_install_names_the_new_images(self, sandbox, tag_at_install):
        # The release is tagged on the new commit and published after the install was cloned.
        # Unless the fetch brings its tag, git describe names the commit (or the older tag with
        # the commits after it), and System > Updates offers the installed release as an update.
        if tag_at_install:
            _git("tag", tag_at_install, "HEAD", cwd=sandbox / "install")
        _git("tag", "v1.0.0", "HEAD", cwd=sandbox / "seed")
        _git("push", "-q", "origin", "v1.0.0", cwd=sandbox / "seed")

        result, _ = _run_update(sandbox)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (sandbox / "stub" / "build-env").read_text().splitlines() == ["UNOS_VERSION=v1.0.0"]
        assert "Updated to: v1.0.0" in result.stdout

    def test_a_forced_rebuild_names_a_release_tagged_on_the_installed_commit(self, sandbox):
        # docs/INSTALL.md: without new commits only a rebuild names the images after the release.
        _git("pull", "-q", "--ff-only", cwd=sandbox / "install")
        _git("tag", "v1.0.0", "HEAD", cwd=sandbox / "seed")
        _git("push", "-q", "origin", "v1.0.0", cwd=sandbox / "seed")

        result, _ = _run_update(sandbox, overrides="confirm() { true; }")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "UNOS is already up to date!" in result.stdout
        assert (sandbox / "stub" / "build-env").read_text().splitlines() == ["UNOS_VERSION=v1.0.0"]

    def test_on_a_spacemit_k1_only_the_supervisor_is_built(self, sandbox):
        # The web UI's image of a K1 comes from another machine (ADR-0373): update.sh builds the
        # supervisor alone, starts the web UI image that is there, and says so.
        result, calls = _run_update(sandbox, overrides="is_spacemit_k1() { true; }")

        assert result.returncode == 0, result.stdout + result.stderr
        assert [call for call in calls if call.startswith("docker compose build")] == [
            "docker compose build --no-cache supervisor"
        ]
        assert "deploy-webui:latest" in result.stdout and "another machine" in result.stdout

    def test_on_a_spacemit_k1_a_rollback_asks_to_load_the_web_ui_image_again(self, sandbox):
        # The rollback tags deploy-webui:latest as the previous image again, so the image of the
        # new version that was loaded for the update loses its tag (ADR-0373).
        result, calls = _run_update(sandbox, "build-fails", overrides="is_spacemit_k1() { true; }")

        assert result.returncode == 1
        assert "docker image tag deploy-webui:previous deploy-webui" in calls
        assert "load the web UI image of the new version again" in " ".join(result.stdout.split())

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
        assert _up_calls(calls) == ["docker compose up -d --no-build --remove-orphans --wait-timeout 1000"]
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
        # Interrupted while compose starts the new containers: the rollback starts the previous
        # version, as it did when an interrupt came between the compose down and up of before.
        before = _head(sandbox)

        result, calls = _run_update(sandbox, "interrupt-at-up", f"signal={signal}")

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
        # interleave its compose up with the first one's, and its rollback
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
            # Held during the build and during the restart (compose up),
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
            ((), "42", SUPERVISOR_START, ["--no-build --remove-orphans"]),
            # Rollback after compose gave up on the new supervisor.
            (("up-fails",), "42", SUPERVISOR_START, ["--no-build --remove-orphans", "--no-build --force-recreate"]),
            # Rollback after the health check over HTTP and HTTPS failed (with
            # 0, the sandbox value, it fails at once). --wait-timeout 0 would
            # mean no limit.
            (("unhealthy",), "0", SUPERVISOR_START, ["--no-build --remove-orphans", "--no-build --force-recreate"]),
            # Rollback after an interrupted restart.
            (("interrupt-at-up", "signal=INT"), "42", SUPERVISOR_START, ["--no-build --remove-orphans", "--no-build --force-recreate"]),
            # A health timeout longer than the supervisor's start.
            ((), "900", 900, ["--no-build --remove-orphans"]),
            (("up-fails",), "900", 900, ["--no-build --remove-orphans", "--no-build --force-recreate"]),
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

        options = ["--no-build --remove-orphans", "--no-build --force-recreate"] if scenario else ["--no-build --remove-orphans"]
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
        assert _up_calls(calls) == [f"docker compose up -d --no-build --remove-orphans --wait-timeout {SUPERVISOR_START}"]
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


def _start_install(tmp_path, *scenario, prelude=""):
    """check_compose_wait, start_unos and create_systemd_service of install.sh
    with the docker stub of the update sandbox, after the shell code `prelude`.
    Returns the result, the docker calls and the ExecStart lines of the unit
    (None without a unit)."""
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
        ["bash", "-c", '. "$LIB"; eval "$PRELUDE"; check_compose_wait; start_unos; create_systemd_service'],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LIB": str(lib),
            "STUB_DIR": str(stub_dir),
            "UNOS_INSTALL_DIR": str(tmp_path / "install"),
            "PRELUDE": prelude,
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
_INSTALL_TARGETS = {"install", "var-lib-unos", "mnt-pools", "etc-unos", "etc-samba", "etc-systemd-system"}

# Logs the call; stubs of _run_install_main.
_LOGGER = '#!/bin/bash\necho "${0##*/} $*" >> "$STUB_DIR/calls"\n'


# The hosts of _run_install_main: what uname -m and dpkg --print-architecture print, and
# /etc/os-release.
DEBIAN_12_ARM64 = ("aarch64", "arm64", 'ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n')
DEBIAN_13_RISCV64 = ("riscv64", "riscv64", 'ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n')
UBUNTU_2404_RISCV64 = ("riscv64", "riscv64", 'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n')
# The compatible of the device tree of a Banana Pi BPI-F3 under the vendor's kernel (ADR-0370).
SPACEMIT_K1 = "spacemit,k1-x\0"


def _run_install_main(tmp_path, *scenario, docker_installed=True, system=DEBIAN_12_ARM64, compatible=None):
    """main() of install.sh as root on `system` (Debian 12 on aarch64 by default), with stub
    commands that log their calls (docker as in the update sandbox) and the
    system paths moved into tmp_path; `compatible` is the host's device tree's, none by
    default (a PC). Without docker_installed, `apt-get install` of docker-ce or docker.io
    installs the docker stub. Returns the result and the calls."""
    machine, dpkg_arch, os_release = system
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
    (tmp_path / "etc-os-release").write_text(os_release)
    if compatible is not None:
        (tmp_path / "proc-device-tree-compatible").write_text(compatible)
    codename = re.search(r"^VERSION_CODENAME=(\w+)$", os_release, re.M).group(1)
    stubs = {
        "uname": f"#!/bin/bash\necho {machine}\n",
        "apt-get": _LOGGER
        + 'if [ "$1" = install ] && [[ " $* " == *" docker-ce "* || " $* " == *" docker.io "* ]]; then\n'
        + '  cp "$STUB_DIR/docker" "$DOCKER_DIR/"\nfi\n',
        # Writes the file after -o.
        "gpg": _LOGGER + 'while [ $# -gt 0 ]; do [ "$1" = -o ] && : > "$2"; shift; done\n',
        "dpkg": _LOGGER + f"echo {dpkg_arch}\n",
        "lsb_release": _LOGGER + f"echo {codename}\n",
        "curl": _LOGGER,
        "systemctl": _LOGGER,
        "udevadm": _LOGGER,
        "git": _LOGGER + "exit 1\n",
    }
    for name, body in stubs.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    paths = ["/etc/os-release", "/etc/apt", "/mnt/pools", "/etc/unos", "/etc/samba", "/etc/systemd/system",
             "/proc/device-tree/compatible", "/etc/udev/rules.d", "/usr/local/sbin", "/var/lib/unos"]
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
            "UNOS_REPO": str(tmp_path / "no-repository"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, (stub_dir / "calls").read_text().splitlines()


# The files of network discovery that install.sh writes (ADR-0275): avahi-daemon's settings,
# the drop-in that makes avahi-daemon read them, and the drop-in of wsdd2.
AVAHI_CONF = "/etc/avahi/avahi-daemon.unos.conf"
AVAHI_DROP_IN = "/etc/systemd/system/avahi-daemon.service.d/unos.conf"
WSDD2_DROP_IN = "/etc/systemd/system/wsdd2.service.d/unos.conf"
DISCOVERY_FILES = (AVAHI_CONF, AVAHI_DROP_IN, WSDD2_DROP_IN)
# What apt-get says when another apt or dpkg holds the lock, as unattended-upgrades can.
DPKG_LOCKED = "E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 4242 (unattended-upgr)"


def _sandbox_path(tmp_path, path):
    """Where _run_configure_discovery puts the file `path` of install.sh (_install_lib)."""
    for root in ("/etc/avahi", "/etc/systemd/system"):
        if path.startswith(f"{root}/"):
            return tmp_path / root.strip("/").replace("/", "-") / path[len(root) + 1 :]
    raise AssertionError(path)


def _settings(path):
    """The lines of a configuration file that are neither empty nor comments."""
    return [line for line in path.read_text().splitlines() if line and not line.startswith("#")]


def _script_heredoc(script, path):
    """The text that install.sh writes to `path` with `cat > PATH << 'EOF'`."""
    match = re.search(rf"^ *cat > {re.escape(path)} << 'EOF'\n(.*?)^EOF$", script, re.M | re.S)
    assert match, path
    return match.group(1)


def _run_configure_discovery(tmp_path, *, wsdd2_missing=False, failing=None):
    """configure_discovery of install.sh with /etc/avahi and /etc/systemd/system moved into
    tmp_path, and stub apt-get, apt-cache and systemctl that log their calls. apt-get adds to
    its line whether all of DISCOVERY_FILES existed when it ran. With wsdd2_missing the package
    sources have no wsdd2, as on Debian 11; the installation of the package `failing` fails, as
    while unattended-upgrades holds dpkg's lock. Returns the result and the calls."""
    bin_dir = tmp_path / "bin"
    stub_dir = tmp_path / "stub"
    for directory in (bin_dir, stub_dir):
        directory.mkdir()
    (stub_dir / "calls").write_text("")
    apt_get = textwrap.dedent(
        """\
        #!/bin/bash
        settings=written
        for file in "$AVAHI_CONF" "$AVAHI_DROP_IN" "$WSDD2_DROP_IN"; do
          [ -f "$file" ] || settings=missing
        done
        echo "apt-get $* (settings $settings)" >> "$STUB_DIR/calls"
        if [ -n "$FAILING" ] && [[ " $* " == *" $FAILING "* ]]; then
          echo "$DPKG_LOCKED" >&2; exit 100
        fi
        """
    )
    apt_cache = textwrap.dedent(
        """\
        #!/bin/bash
        echo "apt-cache $*" >> "$STUB_DIR/calls"
        if [ "$WSDD2_MISSING" = yes ] && [ "$*" = "show wsdd2" ]; then
          echo "N: Unable to locate package wsdd2" >&2; echo "E: No packages found" >&2; exit 100
        fi
        echo "Package: ${*: -1}"
        """
    )
    for name, body in {"apt-get": apt_get, "apt-cache": apt_cache, "systemctl": _LOGGER}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    lib = _install_lib(tmp_path, ["/etc/avahi", "/etc/systemd/system"])
    result = subprocess.run(
        ["bash", "-c", '. "$LIB"; configure_discovery'],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LIB": str(lib),
            "STUB_DIR": str(stub_dir),
            "AVAHI_CONF": str(_sandbox_path(tmp_path, AVAHI_CONF)),
            "AVAHI_DROP_IN": str(_sandbox_path(tmp_path, AVAHI_DROP_IN)),
            "WSDD2_DROP_IN": str(_sandbox_path(tmp_path, WSDD2_DROP_IN)),
            "WSDD2_MISSING": "yes" if wsdd2_missing else "no",
            "FAILING": failing or "",
            "DPKG_LOCKED": DPKG_LOCKED,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, (stub_dir / "calls").read_text().splitlines()


def _install_packages(tmp_path):
    """The packages that install_dependencies of install.sh installs, from the
    `apt-get install` it runs with a stub apt-get."""
    bin_dir = tmp_path / "bin"
    stub_dir = tmp_path / "stub"
    for directory in (bin_dir, stub_dir):
        directory.mkdir()
    (stub_dir / "calls").write_text("")
    (bin_dir / "apt-get").write_text(_LOGGER)
    (bin_dir / "apt-get").chmod(0o755)
    subprocess.run(
        ["bash", "-c", '. "$LIB"; install_dependencies'],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LIB": str(_install_lib(tmp_path, [])),
            "STUB_DIR": str(stub_dir),
        },
        stdin=subprocess.DEVNULL,
        check=True,
        capture_output=True,
        timeout=60,
    )
    calls = (stub_dir / "calls").read_text().splitlines()
    installs = [call.split() for call in calls if call.startswith("apt-get install ")]
    assert len(installs) == 1, installs
    return [word for word in installs[0][2:] if not word.startswith("-")]


# The header that the supervisor's smb.conf starts with (HEADER of supervisor/src/shares/smbconf.py;
# test_rerun_keeps_a_file_that_starts_with_the_unos_header checks that they are the same).
UNOS_SMB_HEADER = (
    "# Generated by UNOS from its database. Do not edit: UNOS rewrites this file\n"
    "# when shares or SMB accounts change (Shares and Users pages).\n"
)


class TestInstallScript:
    def _configure_samba(self, tmp_path, smb_conf, *, etc=None):
        """configure_samba of install.sh with /etc/samba moved (into ``etc``, a new folder by
        default), and a stub systemctl that logs its calls (_systemctl_calls)."""
        if etc is None:
            etc = tmp_path / "samba"
            etc.mkdir()
        if smb_conf is not None:
            (etc / "smb.conf").write_text(smb_conf)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "systemctl").write_text('#!/bin/sh\necho "systemctl $*" >> "$CALLS"\n')
        (bin_dir / "systemctl").chmod(0o755)
        (tmp_path / "systemctl-calls").write_text("")
        # install.sh without its last line (`main "$@"`), with /etc/samba moved.
        script = (REPO_ROOT / "deploy" / "install.sh").read_text().rstrip("\n").rsplit("\n", 1)[0]
        (tmp_path / "install-lib.sh").write_text(script.replace("/etc/samba", str(etc)) + "\n")
        subprocess.run(
            ["bash", "-c", '. "$LIB"; configure_samba'],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "LIB": str(tmp_path / "install-lib.sh"),
                "CALLS": str(tmp_path / "systemctl-calls"),
            },
            check=True,
            capture_output=True,
            timeout=30,
        )
        return etc

    @staticmethod
    def _systemctl_calls(tmp_path):
        return (tmp_path / "systemctl-calls").read_text().splitlines()

    @pytest.mark.parametrize(
        "smb_conf",
        [
            None,
            "[global]\n   server string = %h server (Samba, Ubuntu)\n",
            UNOS_SMB_HEADER + "\n[global]\n   server string = UNOS NAS\n\n[media]\n   path = /mnt/pools/data/media\n",
        ],
        ids=["no-file", "distribution-file", "rerun"],
    )
    def test_smbd_runs_and_nmbd_is_turned_off(self, tmp_path, smb_conf):
        # NetBIOS is off (disable netbios = yes): nmbd, its name server, is stopped and does
        # not start at boot, and the restart gives smbd the new smb ports (ADR-0275).
        self._configure_samba(tmp_path, smb_conf)

        assert self._systemctl_calls(tmp_path) == [
            "systemctl enable smbd",
            "systemctl restart smbd",
            "systemctl disable --now nmbd",
        ]

    # What configure_discovery runs when both packages install (ADR-0275).
    DISCOVERY_CALLS = [
        "systemctl daemon-reload",
        "apt-get install -y -qq --no-install-recommends avahi-daemon (settings written)",
        "systemctl enable avahi-daemon",
        "systemctl restart avahi-daemon",
        "apt-cache show wsdd2",
        "apt-get install -y -qq wsdd2 (settings written)",
        "systemctl enable wsdd2",
        "systemctl restart wsdd2",
    ]

    @staticmethod
    def _text(result):
        """stdout without colors, its lines joined."""
        return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout).split())

    def test_installs_no_package_that_unos_does_not_use(self, tmp_path):
        # mdadm: Debian's package writes an ARRAY line into /etc/mdadm/mdadm.conf (and the
        # initramfs) for every md superblock on the host's disks, so the disks of an old NAS are
        # assembled into arrays that Storage then shows as in use. Nothing of UNOS runs it. The
        # others of the list below are run by nothing either: parted, nvme-cli, lm-sensors,
        # net-tools and wget (ADR-0413). A host that has them keeps them: update.sh removes nothing.
        (tmp_path / "dependencies").mkdir()
        packages = _install_packages(tmp_path / "dependencies")

        for unused in ("mdadm", "parted", "nvme-cli", "lm-sensors", "net-tools", "wget"):
            assert unused not in packages
        # What the code and the scripts do run on the host, or the scripts of install.sh itself.
        assert {"btrfs-progs", "smartmontools", "samba", "samba-common-bin", "samba-vfs-modules",
                "hdparm", "ethtool", "iproute2", "curl", "git", "gnupg", "lsb-release"} <= set(packages)

    def test_every_tool_the_supervisor_needs_on_the_host_comes_from_a_package_installed(self, tmp_path):
        # HOST_TOOLS names the package of each program that the supervisor runs on the host:
        # one that install.sh neither installs nor finds in an essential package would be missing.
        from src.host import HOST_TOOLS

        (tmp_path / "dependencies").mkdir()
        packages = set(_install_packages(tmp_path / "dependencies"))
        # kmod: modprobe, with which the kernel itself loads modules (shares/iscsi.py, ADR-0418).
        essential = {"e2fsprogs", "util-linux", "mount", "udev", "systemd", "systemd-sysv", "passwd", "libc-bin",
                     "kmod"}
        assert set(HOST_TOOLS.values()) <= packages | essential

    @pytest.mark.parametrize("name", ["update.sh", "uninstall.sh"])
    def test_update_and_uninstall_remove_no_package(self, name):
        # A host that already has mdadm keeps it (ADR-0413): the owner removes it. uninstall.sh
        # only tells the owner how to remove Docker (echo lines).
        lines = (REPO_ROOT / "deploy" / name).read_text().splitlines()
        commands = [line for line in lines if not line.lstrip().startswith(("#", "echo"))]
        assert not [
            line for line in commands
            if re.search(r"\b(apt|apt-get|dpkg)\b.*\b(remove|purge|autoremove|-r|-P)\b", line)
        ]

    def test_avahi_daemon_comes_without_the_libnss_mdns_it_recommends(self, tmp_path):
        # libnss-mdns puts mdns4_minimal [NOTFOUND=return] before dns in the hosts line of
        # /etc/nsswitch.conf: the host would no longer resolve the names of a DNS zone that
        # ends in .local, as many Active Directory domains do. install_dependencies installs
        # what its packages recommend, so avahi-daemon is installed on its own line, and wsdd2
        # after its drop-in, where a release without it (Debian 11) stops nothing (ADR-0275).
        (tmp_path / "dependencies").mkdir()
        packages = _install_packages(tmp_path / "dependencies")
        result, calls = _run_configure_discovery(tmp_path)

        assert "samba" in packages
        assert "avahi-daemon" not in packages
        assert "wsdd2" not in packages
        assert result.returncode == 0, result.stdout + result.stderr
        assert [call for call in calls if call.startswith("apt-get ")][0] == (
            "apt-get install -y -qq --no-install-recommends avahi-daemon (settings written)"
        )

    def test_avahi_daemon_announces_ipv4_addresses_only_from_its_first_start(self, tmp_path):
        # With Debian's settings, avahi-daemon announces the host's global IPv6 addresses
        # (and then no link-local one), also over IPv4 (publish-aaaa-on-ipv4). macOS prefers
        # them, and SMB refuses a client that connects from a global address (hosts allow,
        # ADR-0095): Finder would list the NAS and fail to connect. avahi-daemon reads UNOS's
        # file instead, Debian's settings without IPv6, through a drop-in that is in place and
        # loaded before the package is installed, whose installation starts avahi-daemon; the
        # restart gives it to an avahi-daemon that ran before (ADR-0275).
        result, calls = _run_configure_discovery(tmp_path)
        conf = _sandbox_path(tmp_path, AVAHI_CONF)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _settings(conf) == [
            "[server]",
            "use-ipv4=yes",
            "use-ipv6=no",
            "ratelimit-interval-usec=1000000",
            "ratelimit-burst=1000",
            "[wide-area]",
            "enable-wide-area=yes",
            "[publish]",
            "publish-hinfo=no",
            "publish-workstation=no",
            "publish-aaaa-on-ipv4=no",
        ]
        assert _settings(_sandbox_path(tmp_path, AVAHI_DROP_IN)) == [
            "[Service]",
            "ExecStart=",
            f"ExecStart=/usr/sbin/avahi-daemon -s -f {conf}",
        ]
        assert calls[:4] == self.DISCOVERY_CALLS[:4]

    def test_wsdd2_answers_over_ipv4_only_from_its_first_start(self, tmp_path):
        # Over IPv6, wsdd2 opens its TCP ports on tunnels too, whose global addresses
        # UNOS does not filter (ADR-0301). The drop-in is in place and loaded before the package
        # is installed, whose installation starts wsdd2; the restart gives it to a wsdd2
        # that ran before the drop-in existed (ADR-0275).
        result, calls = _run_configure_discovery(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _settings(_sandbox_path(tmp_path, WSDD2_DROP_IN)) == [
            "[Service]",
            "ExecStart=",
            "ExecStart=/usr/sbin/wsdd2 -4",
        ]
        assert calls == self.DISCOVERY_CALLS
        assert "WARN" not in result.stdout
        assert "Network discovery configured: Windows and macOS list this server" in self._text(result)

    def test_without_a_wsdd2_package_the_installation_goes_on(self, tmp_path):
        # Debian 11 has no wsdd2 package, and Ubuntu has it in universe: macOS still lists
        # the server, Windows reaches it only by its address (ADR-0275).
        result, calls = _run_configure_discovery(tmp_path, wsdd2_missing=True)
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == self.DISCOVERY_CALLS[:5]
        assert (
            "[WARN] The package sources have no package wsdd2 (Debian 11 has none; Ubuntu has it in "
            "universe): Windows does not list this server in its Network view; type its address into "
            "File Explorer instead. macOS lists it." in text
        )
        assert "No packages found" not in result.stderr
        assert "Network discovery configured" not in text

    @pytest.mark.parametrize("package", ["avahi-daemon", "wsdd2"])
    def test_a_package_that_fails_to_install_stops_the_installation_with_apts_error(self, tmp_path, package):
        # A package that exists but does not install (a held dpkg lock, a mirror that fails)
        # is not a release without it: apt-get's error shows, and the installation stops as
        # it does for the other packages; running install.sh again finishes it (ADR-0275).
        result, calls = _run_configure_discovery(tmp_path, failing=package)
        text = self._text(result)

        assert result.returncode == 1, result.stdout + result.stderr
        assert DPKG_LOCKED in result.stderr
        assert (
            f"[ERROR] Could not install the package {package} (apt-get's error is above). Run install.sh again."
            in text
        )
        assert "WARN" not in text and "Debian 11" not in text
        # Nothing runs after the failed apt-get.
        failed = next(
            i for i, call in enumerate(self.DISCOVERY_CALLS)
            if call.startswith("apt-get install ") and f" {package} (" in call
        )
        assert calls == self.DISCOVERY_CALLS[: failed + 1]

    def test_main_configures_discovery_once_samba_runs_without_netbios(self):
        script = (REPO_ROOT / "deploy" / "install.sh").read_text()
        body = re.search(r"^main\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        steps = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]

        assert steps.index("install_dependencies") < steps.index("configure_samba")
        assert steps.index("configure_samba") < steps.index("configure_discovery") < steps.index("start_unos")

    @pytest.mark.parametrize(
        "rest", ["\n[global]\n", "\n[global]\n   workgroup = WORKGROUP\n\n[media]\n   path = /mnt/pools/data/media\n"],
        ids=["header", "header-and-shares"],
    )
    def test_rerun_keeps_a_file_that_starts_with_the_unos_header(self, tmp_path, rest):
        """deploy.no-legacy-smb-conf-recognition (ADR-0316): the file that the supervisor writes
        starts with its header (ADR-0074), and a re-run keeps it, with or without the server
        string."""
        from src.shares import smbconf

        assert UNOS_SMB_HEADER == smbconf.HEADER
        etc = self._configure_samba(tmp_path, UNOS_SMB_HEADER + rest)

        assert (etc / "smb.conf").read_text() == UNOS_SMB_HEADER + rest
        assert not (etc / "smb.conf.original").exists()

    @pytest.mark.parametrize(
        "smb_conf",
        [
            "[global]\n   server string = UNOS NAS\n\n[media]\n   path = /mnt/pools/data/media\n",
            "# Notes of the host's administrator.\n" + UNOS_SMB_HEADER + "\n[global]\n",
            "# Generated by UNOS from its database.\n[global]\n",
            UNOS_SMB_HEADER.splitlines(keepends=True)[0] + "[global]\n",
            "   " + UNOS_SMB_HEADER + "\n[global]\n",
            "\ufeff" + UNOS_SMB_HEADER + "\n[global]\n",
            UNOS_SMB_HEADER.replace("\n", "\r\n") + "\r\n[global]\r\n",
        ],
        ids=["server-string", "header-later", "header-sentence", "first-header-line", "indented-header",
             "byte-order-mark", "crlf"],
    )
    def test_a_file_that_does_not_start_with_the_unos_header_is_replaced(self, tmp_path, smb_conf):
        """deploy.no-legacy-smb-conf-recognition (ADR-0316): the server string of the [global]
        that UNOS writes (ADR-0281) marks no file of UNOS, nor does a part of the header or a
        header below the first line, as the supervisor's own check (smbconf.HEADER) counts none of
        them: such a file is handled like any other smb.conf of the host, kept as
        smb.conf.original, as no copy is there yet, and replaced."""
        from src.shares import smbconf

        etc = self._configure_samba(tmp_path, smb_conf)

        assert (etc / "smb.conf").read_bytes() == smbconf.render_config([]).encode()
        assert (etc / "smb.conf.original").read_bytes() == smb_conf.encode()

    def test_a_rerun_keeps_no_copy_of_a_file_it_replaces(self, tmp_path):
        """smb.conf.original keeps the file of the first run, from before UNOS: a file that a later
        run replaces is gone (ADR-0316)."""
        from src.shares import smbconf

        etc = tmp_path / "samba"
        etc.mkdir()
        (etc / "smb.conf.original").write_text("[global]\n   server string = %h server (Samba, Ubuntu)\n")
        self._configure_samba(tmp_path, "[global]\n   server string = UNOS NAS\n", etc=etc)

        assert (etc / "smb.conf").read_bytes() == smbconf.render_config([]).encode()
        assert (etc / "smb.conf.original").read_text() == "[global]\n   server string = %h server (Samba, Ubuntu)\n"

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

    def test_first_install_refuses_samba_s_own_password_changes(self, tmp_path):
        """shares.password-changes-only-through-unos (ADR-0342): from the install on, before the
        supervisor first writes smb.conf, Samba refuses every password change over SAMR."""
        etc = self._configure_samba(tmp_path, None)

        assert "\n   check password script = /bin/false\n" in (etc / "smb.conf").read_text()

    def test_rerun_keeps_the_smb_conf_that_unos_generated(self, tmp_path):
        # The generated file starts with the header line that the rerun looks for.
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
        assert _up_calls(calls) == [f"docker compose up --remove-orphans -d --wait-timeout {SUPERVISOR_START}"]
        assert exec_start == [f"ExecStart=/usr/bin/docker compose up -d --wait-timeout {SUPERVISOR_START}"]
        assert "WARN" not in result.stdout

    def test_the_start_runs_no_compose_down_before_up(self, tmp_path):
        """deploy.no-legacy-network-recreate (ADR-0207): install.sh starts UNOS with compose up
        alone; the compose down for the network of a release without the fixed subnet (ADR-0048)
        is gone, with its comment."""
        result, calls, _ = _start_install(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert not any(call.startswith("docker compose down") for call in calls), calls
        for script in ("install.sh", "update.sh"):
            assert "fixed subnet" not in (REPO_ROOT / "deploy" / script).read_text(encoding="utf-8"), script

    def test_compose_without_wait_timeout_gets_the_plain_command(self, tmp_path):
        # Compose before 2.17 rejects the option: the start, and the unit at
        # every boot, would fail.
        result, calls, exec_start = _start_install(tmp_path, "no-wait-timeout", "compose-version=2.16.0")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == ["docker compose up --remove-orphans -d"]
        assert exec_start == ["ExecStart=/usr/bin/docker compose up -d"]
        assert "Docker Compose 2.16.0 has no `up --wait-timeout`" in result.stdout
        assert "run install.sh again" in result.stdout

    def test_compose_before_2_30_gets_the_option_and_a_warning(self, tmp_path):
        result, calls, exec_start = _start_install(tmp_path, "compose-version=2.26.1")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _up_calls(calls) == [f"docker compose up --remove-orphans -d --wait-timeout {SUPERVISOR_START}"]
        assert exec_start == [f"ExecStart=/usr/bin/docker compose up -d --wait-timeout {SUPERVISOR_START}"]
        assert "Docker Compose 2.26.1 cannot limit its wait" in result.stdout

    def test_the_images_carry_the_version_of_the_checkout(self, tmp_path):
        # System > Updates shows the version the supervisor image was built with (ADR-0234).
        install = tmp_path / "install"
        _git("init", "-q", str(install), cwd=tmp_path)
        (install / "README.md").write_text("UNOS\n")
        _git("add", "-A", cwd=install)
        _git("commit", "-qm", "release", cwd=install)
        _git("tag", "v9.9.9", cwd=install)

        result, calls, exec_start = _start_install(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "stub" / "build-env").read_text().splitlines() == ["UNOS_VERSION=v9.9.9"]

    def test_without_a_git_checkout_the_version_is_unknown(self, tmp_path):
        result, calls, exec_start = _start_install(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "stub" / "build-env").read_text().splitlines() == ["UNOS_VERSION=unknown"]

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

    def test_smartd_is_turned_off(self, tmp_path):
        # UNOS reads the disks' SMART data itself (ADR-0235, ADR-0332). smartd, which the package
        # smartmontools starts, would send every awake disk SMART commands every 30 minutes, and
        # each restarts the disk's standby timer: a disk with an idle timeout of 30 minutes or more
        # would never spin down (ADR-0378).
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "systemctl").write_text('#!/bin/sh\necho "systemctl $*" >> "$CALLS"\n')
        (bin_dir / "systemctl").chmod(0o755)
        (tmp_path / "systemctl-calls").write_text("")
        result = subprocess.run(
            ["bash", "-c", '. "$LIB"; stop_smartd'],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "LIB": str(_install_lib(tmp_path, [])),
                "CALLS": str(tmp_path / "systemctl-calls"),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert self._systemctl_calls(tmp_path) == ["systemctl disable --now smartmontools"]
        assert "smartd is off: UNOS reads the disks' SMART data itself" in self._text(result)

    def test_main_turns_smartd_off_once_unos_runs(self):
        # Not before: an installation that stops earlier leaves the host's SMART monitoring on.
        script = (REPO_ROOT / "deploy" / "install.sh").read_text()
        body = re.search(r"^main\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        steps = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]

        assert steps.index("install_dependencies") < steps.index("start_unos") < steps.index("stop_smartd")

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

    def test_arm64_takes_docker_from_dockers_repository(self, tmp_path):
        result, calls = _run_install_main(tmp_path, docker_installed=False)

        assert "Detected: debian 12 (arm64)" in self._text(result)
        assert "dpkg --print-architecture" in calls
        assert [call for call in calls if call.startswith("apt-get install") and "docker" in call] == [
            "apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
        ]
        assert (tmp_path / "etc-apt" / "sources.list.d" / "docker.list").exists()

    @pytest.mark.parametrize(
        ("system", "compose"),
        [(DEBIAN_13_RISCV64, "docker-compose"), (UBUNTU_2404_RISCV64, "docker-compose-v2")],
        ids=["debian-13", "ubuntu-24.04"],
    )
    def test_riscv64_takes_docker_from_the_distribution(self, tmp_path, system, compose):
        # Docker's repository has no packages for linux/riscv64. Debian 13 and Ubuntu 24.04
        # build Docker Engine, Compose v2 (the docker compose plugin) and buildx for it: Debian
        # as docker-compose, Ubuntu as docker-compose-v2 (ADR-0370).
        result, calls = _run_install_main(tmp_path, docker_installed=False, system=system)

        assert "(riscv64)" in self._text(result)
        installs = [call for call in calls if call.startswith("apt-get install") and "docker" in call]
        assert installs == [f"apt-get install -y -qq docker.io {compose} docker-buildx"]
        assert not [call for call in calls if call.startswith(("curl", "gpg", "lsb_release", "dpkg"))]
        assert list((tmp_path / "etc-apt" / "sources.list.d").iterdir()) == []
        after = calls[calls.index(installs[0]) + 1:]
        assert after[:3] == ["systemctl enable docker", "systemctl start docker", "docker compose version"]

    def test_an_installed_docker_stays_on_riscv64(self, tmp_path):
        result, calls = _run_install_main(tmp_path, system=DEBIAN_13_RISCV64)

        assert not [call for call in calls if call.startswith("apt-get install") and "docker" in call]
        assert "Docker is already installed" in result.stdout

    def test_on_a_spacemit_k1_nvme_power_saving_goes_off_whenever_a_drive_appears(self, tmp_path):
        # The NVMe controller of the SpacemiT K1 hangs under load ("QID 0 timeout, disable
        # controller") unless the drive's APST and the PCIe link's ASPM L1 are off, and the kernel
        # parameters that would turn them off keep the BPI-F3 from booting. The K1 may enumerate
        # PCIe late in the boot, so udev turns them off as each drive appears: ASPM L1 with the
        # PCIe function, APST with the drive's disk, since the driver adds the controller's APST
        # setting only after it has identified the drive. udev applies the rule to the drives
        # there are now. The rule touches the power saving of NVMe drives only, and fstrim.timer
        # stays as it is: every pool keeps TRIM, also one on an NVMe drive (ADR-0370, ADR-0429).
        result, calls = _run_install_main(tmp_path, system=DEBIAN_13_RISCV64, compatible=SPACEMIT_K1)

        script = tmp_path / "usr-local-sbin" / "unos-k1-nvme"
        rules = _settings(tmp_path / "etc-udev-rules.d" / "60-unos-k1-nvme.rules")
        assert rules == [
            'ACTION=="add|change", SUBSYSTEM=="pci", ATTR{class}=="0x010802", ATTR{link/l1_aspm}="0"',
            'ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="nvme*", ENV{DEVTYPE}=="disk", '
            f'RUN+="{script}"',
        ]
        assert os.access(script, os.X_OK)
        k1 = [call for call in calls if call.startswith("udevadm") or "fstrim" in call]
        assert k1 == [
            "udevadm control --reload",
            "udevadm trigger --action=change --subsystem-match=pci --attr-match=class=0x010802",
            "udevadm trigger --action=change --subsystem-match=block --sysname-match=nvme*",
        ]
        assert "SpacemiT K1" in self._text(result)

    def test_the_k1_nvme_script_turns_off_apst_and_aspm_l1_of_each_drive(self, tmp_path):
        """The script the rule runs, against a sysfs of two NVMe drives in tmp_path: 0 in a
        controller's pm_qos_latency_tolerance_us makes the driver turn APST off, and 0 in its PCIe
        function's l1_aspm turns ASPM L1 off (ADR-0370)."""
        _run_install_main(tmp_path / "install", system=DEBIAN_13_RISCV64, compatible=SPACEMIT_K1)
        sys = tmp_path / "sys"
        for n, pci in enumerate(["0001:01:00.0", "0002:01:00.0"]):
            function = sys / "devices" / pci
            (function / "link").mkdir(parents=True)
            (function / "link" / "l1_aspm").write_text("1\n")
            controller = sys / "class" / "nvme" / f"nvme{n}"
            (controller / "power").mkdir(parents=True)
            (controller / "power" / "pm_qos_latency_tolerance_us").write_text("100000\n")
            (controller / "device").symlink_to(function)
        script = (tmp_path / "install" / "usr-local-sbin" / "unos-k1-nvme").read_text()
        assert script.count("/sys/") == 1
        (tmp_path / "k1-nvme").write_text(script.replace("/sys/", f"{sys}/"))

        result = subprocess.run(["sh", str(tmp_path / "k1-nvme")], capture_output=True, text=True, timeout=30)

        assert result.returncode == 0, result.stderr
        for n, pci in enumerate(["0001:01:00.0", "0002:01:00.0"]):
            assert (sys / "class" / "nvme" / f"nvme{n}" / "power" / "pm_qos_latency_tolerance_us").read_text() == "0\n"
            assert (sys / "devices" / pci / "link" / "l1_aspm").read_text() == "0\n"

    # On a SpacemiT K1 (is_spacemit_k1 replaced, as the device tree would answer).
    K1 = "is_spacemit_k1() { true; }"

    def test_on_a_spacemit_k1_only_the_supervisor_is_built_beside_a_loaded_web_ui_image(self, tmp_path):
        # Node.js crashes on the K1 while it builds the web UI (nodejs/node#66845): its image comes
        # from another machine, and install.sh builds the supervisor alone (ADR-0373).
        result, calls, exec_start = _start_install(tmp_path, prelude=self.K1)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "docker image inspect deploy-webui:latest" in calls
        assert [call for call in calls if call.startswith("docker compose build")] == ["docker compose build supervisor"]
        assert any(call.startswith("docker compose up") for call in calls)
        assert exec_start

    def test_on_a_spacemit_k1_without_a_web_ui_image_it_stops_and_says_how_to_load_one(self, tmp_path):
        result, calls, exec_start = _start_install(tmp_path, "no-webui-image", prelude=self.K1)
        text = self._text(result)

        assert result.returncode == 1
        assert not [call for call in calls
                    if call.startswith(("docker compose build", "docker compose up")) and "--help" not in call]
        assert exec_start is None
        assert "docker buildx build --platform linux/riscv64 -t deploy-webui:latest --load webui" in text
        assert "docker save -o deploy-webui.tar deploy-webui:latest" in text
        assert "sudo docker load -i deploy-webui.tar" in text

    def test_elsewhere_both_images_are_built(self, tmp_path):
        result, calls, _ = _start_install(tmp_path, "no-webui-image")

        assert result.returncode == 0, result.stdout + result.stderr
        assert [call for call in calls if call.startswith("docker compose build")] == ["docker compose build"]
        assert "docker image inspect deploy-webui:latest" not in calls

    @pytest.mark.parametrize(
        "compatible",
        [None, "rockchip,rk3588-evb1-v10\0rockchip,rk3588\0", "rockchip,rk3588-armsom-w3\0rockchip,rk3588\0"],
        ids=["pc", "rk3588", "bpi-w3"],
    )
    def test_other_hosts_keep_nvme_power_saving_and_trim(self, tmp_path, compatible):
        result, calls = _run_install_main(tmp_path, compatible=compatible)

        assert not [call for call in calls if call.startswith("udevadm") or "fstrim" in call]
        assert not (tmp_path / "etc-udev-rules.d").exists()
        assert not (tmp_path / "usr-local-sbin").exists()


# The line of uninstall.sh that adds the system's folders of programs to PATH, after those it was
# given: root's PATH may lack them (su without "-" keeps the user's).
_SYSTEM_PATH_LINE = 'export PATH="${PATH:+$PATH:}/usr/sbin:/usr/bin:/sbin:/bin"'


def _uninstall_lib(tmp_path, system_path=None):
    """uninstall.sh without its last line (`main "$@"`). Returns the path of the copy. Its PATH line
    adds the folder `system_path` instead of the system's folders (an empty folder by default), so
    that no test runs the host's hdparm or smartctl, which act on real disks."""
    script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text().rstrip("\n").rsplit("\n", 1)[0]
    assert len(re.findall(r"^\s*(?:export\s+)?PATH\+?=", script, re.M)) == script.count(_SYSTEM_PATH_LINE) <= 1
    if system_path is None:
        system_path = tmp_path / "no-system-programs"
        system_path.mkdir(exist_ok=True)
    script = script.replace(_SYSTEM_PATH_LINE, f'export PATH="${{PATH:+$PATH:}}{system_path}"')
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


# Import backups as the supervisor names them, oldest first (ADR-0362).
_IMPORT_BACKUPS = ("smb.conf.unos-import-backup-20261101_120000_000000",
                   "smb.conf.unos-import-backup-20261112_101530_123456")

# The host's programs that remove_disk_idle_timeouts runs. Its tests run stubs of them and never
# the host's own: hdparm and smartctl act on real disks.
_IDLE_TOOLS = ("lsblk", "hdparm", "smartctl", "udevadm", "timeout")

# The host's whole devices as `lsblk -dnr -o NAME,TYPE,SERIAL` lists them: the system disk, the
# data disks with the serials WD-1 and WD-2, an optical drive that reports WD-1's serial too (as the
# virtual CD drive of a USB enclosure can), a loop device and a disk without a serial.
_DISKS = "mmcblk1 disk 0x8f3a\nsda disk WD-1\nsdb disk WD-2\nsr0 rom WD-1\nloop0 loop \nsdc disk \n"


def _timeout_stub(status=124):
    """timeout(1) of the host: runs the command after its two options; a call listed in
    $STUB_DIR/fail ends with `status`, as when it ran out of time: 124 when its TERM ended the
    command, 137 when its KILL did, which timeout sends to its own process group and so dies of
    too (as here)."""
    ends = "kill -KILL $$" if status == 137 else f"exit {status}"
    return (_LOGGER + f'if grep -qxF -- "${{0##*/}} $*" "$STUB_DIR/fail"; then {ends}; fi\n'
            + 'shift 2\nexec "$@"\n')


# timeout(1) of the host whose KILL did not end smartctl: the kernel holds it (uninterruptible
# sleep), and it keeps open every file it was given, its standard output and errors among them, as
# the process `holder` does here (its pid in $STUB_DIR/holder) until $STUB_DIR/release appears, 30
# seconds at most. timeout ends all the same, by its own KILL (137). It notes in
# $STUB_DIR/tmp-during what $TMPDIR holds meanwhile. Were one of those files the script's own
# output, a pipe here (the test's), as on a host with `uninstall.sh 2>&1 | tee log` or ssh without
# a terminal, its reader would wait for the holder to end.
_STUCK_SMARTCTL_TIMEOUT = (
    _LOGGER
    + textwrap.dedent(
        """\
        case "$*" in
          *smartctl*)
            ls -A "$TMPDIR" > "$STUB_DIR/tmp-during"
            sh -c 'echo $$ > "$STUB_DIR/holder"; i=0
              while [ ! -e "$STUB_DIR/release" ] && [ $i -lt 300 ]; do sleep 0.1; i=$((i + 1)); done' &
            while [ ! -s "$STUB_DIR/holder" ]; do sleep 0.05; done
            kill -KILL $$ ;;
        esac
        shift 2
        exec "$@"
        """
    )
)

# mktemp(1) of the host, which makes its file or folder (-d) in $TMPDIR without a template, as
# Debian's does (macOS's does not).
_MKTEMP_IN_TMPDIR = f'#!/bin/bash\nexec {shutil.which("mktemp")} "$@" "$TMPDIR/tmp.XXXXXXXXXX"\n'


def _smartctl_stub(*lines, status=0):
    """smartctl that logs its call, fails as listed in $STUB_DIR/fail, and otherwise prints `lines`
    and exits with `status`."""
    printed = " ".join(shlex.quote(line) for line in lines)
    return _LOGGER + _FAILS + f"printf '%s\\n' {printed}\nexit {status}\n"


# What smartctl -o on -c prints of the disk's own automatic offline data collection once it is on
# (smartmontools' ataprint.cpp; the last line as the dev device's smartctl -c printed it).
_SCANS_TURNED_ON = (
    "SMART Automatic Offline Testing Enabled every four hours.",
    "Offline data collection status:  (0x82)\tOffline data collection activity",
    "\t\t\t\t\twas completed without error.",
    "\t\t\t\t\tAuto Offline Data Collection: Enabled.",
)

_LSBLK = "lsblk -dnr -o NAME,TYPE,SERIAL"
_RELOAD = "udevadm control --reload"


def _timer_off(path):
    """The calls that turn off the idle timeout of the disk at `path`."""
    return [f"timeout --kill-after=5 30 hdparm -S 0 {path}", f"hdparm -S 0 {path}"]


def _scans_on(path):
    """The calls that turn the disk's own automatic offline scans on again and read them back."""
    return [f"timeout --kill-after=5 30 smartctl -o on -c {path}", f"smartctl -o on -c {path}"]


def _shown(line):
    """A line of the rule as the uninstallation shows it, without the spaces and tabs around it:
    each byte that is no printable ASCII character is a question mark."""
    return "".join(ch if " " <= ch <= "~" else "?" * len(ch.encode()) for ch in line.strip(" \t"))


def _records(path):
    """The records of UNOS's rule at `path` ("# UNOS turned off ... serial <serial>" lines), None
    without the file."""
    if not path.exists():
        return None
    return [line for line in path.read_text().splitlines() if line.startswith("# UNOS turned off")]


class TestUninstallScript:
    def _run(
        self, tmp_path, commands="remove_samba_passwords", *, passwd=_PASSWD, passdb=_PASSDB, fail=(), samba=(),
        stubs=None, without=(), system_path=None,
    ):
        """Functions of uninstall.sh (`commands`), under its `set -e`, with stub
        getent, pdbedit and systemctl that log their calls, and with SAMBA_DIR
        in tmp_path holding the files named in `samba`. The command lines in
        `fail` fail. Returns the result, the calls and passdb afterwards. Stubs
        of the commands that change accounts log their calls too, so a call
        would show. `stubs` adds stubs (name: script body); the host's own
        programs named in `without` are not on PATH. The script's PATH line
        adds the folder `system_path` (_uninstall_lib)."""
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
                "PATH": f"{bin_dir}:{_path_without(without, tmp_path) if without else os.environ['PATH']}",
                "LIB": str(_uninstall_lib(tmp_path, system_path)),
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

    def test_the_nfs_exports_of_unos_are_removed_and_no_longer_exported(self, tmp_path):
        # UNOS's own export file (ADR-0292); /etc/exports and the other files are not UNOS's.
        own = tmp_path / "unos.exports"
        own.write_text("/mnt/pools/data/media 192.168.1.0/24(rw,sync)\n")
        other = tmp_path / "other.exports"
        other.write_text("/srv/other 10.0.0.0/8(ro)\n")

        result, calls, _ = self._run(
            tmp_path, f'NFS_EXPORTS_FILE="{own}"; remove_nfs_exports', stubs={"exportfs": _LOGGER + _FAILS}
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert not own.exists() and other.exists()
        assert calls == ["exportfs -ra"]
        assert "NFS exports of UNOS removed" in result.stdout

    def test_without_nfs_exports_of_unos_nothing_runs(self, tmp_path):
        result, calls, _ = self._run(
            tmp_path, f'NFS_EXPORTS_FILE="{tmp_path / "unos.exports"}"; remove_nfs_exports',
            stubs={"exportfs": _LOGGER},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == []

    def test_a_failed_exportfs_does_not_stop_the_uninstallation(self, tmp_path):
        own = tmp_path / "unos.exports"
        own.write_text("/mnt/pools/data/media 192.168.1.0/24(rw,sync)\n")

        result, calls, _ = self._run(
            tmp_path, f'NFS_EXPORTS_FILE="{own}"; remove_nfs_exports; echo after',
            fail=("exportfs -ra",), stubs={"exportfs": _LOGGER + _FAILS},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert not own.exists() and calls == ["exportfs -ra"]
        assert "exportfs -ra failed: the host may keep exporting the folders of UNOS until" in self._text(result)
        assert result.stdout.rstrip().endswith("after")

    def test_main_removes_the_nfs_exports_after_unos_has_stopped(self):
        script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text()
        body = re.search(r"^main\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        steps = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]

        assert steps.index("stop_and_remove_containers") < steps.index("remove_nfs_exports") < steps.index(
            "print_completion")
        assert 'NFS_EXPORTS_FILE="/etc/exports.d/unos.exports"' in script

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

    def test_the_completion_names_the_docker_packages_of_both_sources(self, tmp_path):
        # install.sh takes Docker from Docker's repository, and on riscv64 from the
        # distribution (ADR-0370).
        result, _, _ = self._run(tmp_path, "print_completion")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "  apt-get purge docker-ce docker-ce-cli containerd.io\n" in result.stdout
        assert "  apt-get purge docker.io docker-buildx docker-compose\n" in result.stdout
        assert "SpacemiT K1" not in result.stdout

    def test_the_completion_names_the_kept_nvme_rule_of_a_spacemit_k1(self, tmp_path):
        # The pools stay, so the udev rule that keeps the K1's NVMe drives from hanging stays
        # (ADR-0370).
        rule = tmp_path / "60-unos-k1-nvme.rules"
        rule.write_text("# UNOS\n")

        result, calls, _ = self._run(tmp_path, f'K1_NVME_RULE="{rule}"; print_completion')

        assert result.returncode == 0, result.stdout + result.stderr
        assert rule.exists() and calls == []
        assert f"SpacemiT K1: the udev rule {rule} keeps turning off the power saving" in self._text(result)
        assert "fstrim" not in result.stdout

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
            (("smb.conf", _IMPORT_BACKUPS[1]), "stays", _IMPORT_BACKUPS[1:]),
            # Two imports of files that UNOS did not write (ADR-0362), and a file of another name.
            (("smb.conf", "smb.conf.unos-import-backup-notes.txt", *_IMPORT_BACKUPS[::-1]), "stays", _IMPORT_BACKUPS),
            (("smb.conf",), "stays", ()),
            # install.sh kept the file it replaced.
            (("smb.conf", "smb.conf.original"), "original", ()),
            (("smb.conf", "smb.conf.original", _IMPORT_BACKUPS[0]), "original", ()),
        ],
    )
    def test_the_completion_says_what_happens_to_smb_conf(self, tmp_path, samba, says, backup):
        result, calls, _ = self._run(tmp_path, "restore_samba_config; print_completion", samba=samba)
        text = self._text(result)
        folder = tmp_path / "samba"

        assert result.returncode == 0, result.stdout + result.stderr
        if says == "original":
            # The file from before UNOS leaves NetBIOS on: nmbd, which install.sh turned off,
            # runs again, also after a reboot (ADR-0275).
            assert calls == ["systemctl enable nmbd", "systemctl restart smbd nmbd"]
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
        # The copies that the supervisor keeps of an smb.conf it did not write when
        # it imports its shares, oldest first (ADR-0362); smb.conf.original is the
        # one that install.sh kept.
        if backup:
            listed = "".join(f"  {folder}/{name}\n" for name in backup)
            assert (
                "The smb.conf files that UNOS replaced with its own (UNOS imported their\n"
                "shares) are kept, each named with the UTC time it was replaced, oldest\n"
                f"first:\n{listed}Check one before you copy it back: it may give every Samba account\n"
                "access to its shares.\n"
            ) in result.stdout
        assert ("unos-import-backup" in text) is bool(backup)
        assert "The file from before UNOS is" not in text

    def test_with_smb_conf_original_smbd_restarts_once(self, tmp_path):
        """The restore of smb.conf.original restarts smbd, which also ends the connections of the
        Samba passwords that were removed: no restart of their own comes before it (ADR-0322)."""
        result, calls, _ = self._run(
            tmp_path, "remove_samba_passwords; restore_samba_config", samba=("smb.conf", "smb.conf.original")
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [
            "getent passwd",
            "pdbedit -L",
            "pdbedit -x -u alice",
            "pdbedit -x -u bob",
            "systemctl enable nmbd",
            "systemctl restart smbd nmbd",
        ]
        assert (tmp_path / "samba" / "smb.conf").read_text() == "smb.conf.original\n"
        assert "WARN" not in result.stdout

    def test_a_restart_that_fails_after_the_restore_is_reported(self, tmp_path):
        """The restart after the restore is also the one that ends the removed passwords'
        connections: when it fails, the uninstallation says so and goes on (ADR-0322)."""
        result, calls, _ = self._run(
            tmp_path,
            "remove_samba_passwords; restore_samba_config; print_completion",
            fail=("systemctl restart smbd nmbd",),
            samba=("smb.conf", "smb.conf.original"),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[-2:] == ["systemctl enable nmbd", "systemctl restart smbd nmbd"]
        assert (
            "Could not restart Samba (smbd and nmbd): until they are restarted, Samba may still use UNOS's "
            "smb.conf and keep open SMB connections. Restart them with: systemctl restart smbd nmbd"
            in self._text(result)
        )
        assert "UNOS Uninstallation Complete!" in result.stdout

    def test_the_restore_copies_next_to_smb_conf_and_renames_before_samba_restarts(self, tmp_path):
        """The copy goes next to smb.conf, keeping the original's mode, and one rename puts it in
        place before Samba restarts with it; no temporary file stays (ADR-0322)."""
        real = {name: shutil.which(name) for name in ("cp", "mv")}
        folder = tmp_path / "samba"
        result, calls, _ = self._run(
            tmp_path,
            "chmod 640 \"$SAMBA/smb.conf.original\"; remove_samba_passwords; restore_samba_config",
            samba=("smb.conf", "smb.conf.original"),
            stubs={name: _LOGGER + f'exec {path} "$@"\n' for name, path in real.items()},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        staged = f"{folder}/.smb.conf.unos-restore.unos-tmp"
        assert calls[-4:] == [
            f"cp -p {folder}/smb.conf.original {staged}",
            f"mv -f {staged} {folder}/smb.conf",
            "systemctl enable nmbd",
            "systemctl restart smbd nmbd",
        ]
        assert (folder / "smb.conf").read_text() == "smb.conf.original\n"
        assert stat.S_IMODE((folder / "smb.conf").stat().st_mode) == 0o640
        assert sorted(path.name for path in folder.iterdir()) == ["smb.conf", "smb.conf.original"]

    def test_a_rename_that_fails_leaves_smb_conf_whole(self, tmp_path):
        """mv fails (an immutable smb.conf): smb.conf stays as UNOS wrote it, the temporary file
        goes, and the removed passwords' connections end with a restart of their own (ADR-0322)."""
        folder = tmp_path / "samba"
        result, calls, _ = self._run(
            tmp_path,
            "remove_samba_passwords; restore_samba_config; print_completion",
            samba=("smb.conf", "smb.conf.original"),
            # Only the rename over smb.conf fails; the pdbedit stub moves its passdb too.
            stubs={
                "mv": _LOGGER
                + 'case "$*" in */smb.conf) echo "mv: cannot move: Operation not permitted" >&2; exit 1 ;; esac\n'
                + f'exec {shutil.which("mv")} "$@"\n'
            },
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (folder / "smb.conf").read_text() == "smb.conf\n"
        assert sorted(path.name for path in folder.iterdir()) == ["smb.conf", "smb.conf.original"]
        assert calls[-2:] == [
            f"mv -f {folder}/.smb.conf.unos-restore.unos-tmp {folder}/smb.conf",
            "systemctl try-restart smbd",
        ]
        assert "systemctl restart smbd nmbd" not in calls and "systemctl enable nmbd" not in calls
        assert "it is still the file UNOS wrote" in self._text(result)

    def test_a_temporary_file_that_cannot_go_does_not_stop_the_uninstallation(self, tmp_path):
        """A temporary file left by a run that was killed in the restore, in an /etc/samba whose
        entries cannot be removed: the restore fails, and the uninstallation goes on to its end."""
        folder = tmp_path / "samba"
        rm = _LOGGER + "echo \"rm: cannot remove: Read-only file system\" >&2\nexit 1\n"
        cp = _LOGGER + "exit 1\n"
        result, calls, _ = self._run(
            tmp_path,
            "remove_samba_passwords; restore_samba_config; print_completion",
            samba=("smb.conf", "smb.conf.original"),
            stubs={"rm": rm, "cp": cp},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"rm -f {folder}/.smb.conf.unos-restore.unos-tmp" in calls
        assert calls[-1] == "systemctl try-restart smbd"
        assert "UNOS Uninstallation Complete!" in result.stdout

    def test_a_copy_that_fails_halfway_leaves_smb_conf_whole(self, tmp_path):
        """smb.conf.original is copied next to smb.conf and renamed over it: a copy that stops
        halfway (a full /etc) leaves smb.conf as UNOS wrote it, and no temporary file. The removed
        passwords' connections still end, with a restart of their own (ADR-0322)."""
        cp = _LOGGER + 'printf partial > "$2"\nexit 1\n'
        result, calls, _ = self._run(
            tmp_path,
            "remove_samba_passwords; restore_samba_config; print_completion",
            samba=("smb.conf", "smb.conf.original"),
            stubs={"cp": cp},
        )
        folder = tmp_path / "samba"

        assert result.returncode == 0, result.stdout + result.stderr
        assert (folder / "smb.conf").read_text() == "smb.conf\n"
        assert sorted(path.name for path in folder.iterdir()) == ["smb.conf", "smb.conf.original"]
        assert calls == [
            "getent passwd",
            "pdbedit -L",
            "pdbedit -x -u alice",
            "pdbedit -x -u bob",
            f"cp -p {folder}/smb.conf.original {folder}/.smb.conf.unos-restore.unos-tmp",
            "systemctl try-restart smbd",
        ]
        assert "Restarted Samba (smbd): open SMB connections were ended" in result.stdout

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
        assert f"cp -p {folder}/smb.conf.original {folder}/.smb.conf.unos-restore.unos-tmp" in calls
        # nmbd is not turned on for UNOS's smb.conf, and smbd restarts once, for bob's removed
        # password (ADR-0322).
        assert "systemctl restart smbd nmbd" not in calls
        assert "systemctl enable nmbd" not in calls
        assert calls.count("systemctl try-restart smbd") == 1
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
            f"({folder}/smb.conf.original): it is still the file UNOS wrote. Copy it back, then restart Samba:"
            in text
        )
        block = completion[completion.index("Samba configuration:") : completion.index("Network discovery:")]
        commands = [line.strip() for line in block.splitlines() if line.startswith("  ")]
        assert commands == [
            f"cp {folder}/smb.conf.original {folder}/smb.conf",
            "systemctl enable nmbd",
            "systemctl restart smbd nmbd",
        ]
        assert "is the file from before UNOS again" not in text
        assert (folder / "smb.conf").read_text() == "smb.conf\n"

    def test_the_completion_says_that_network_discovery_stays(self, tmp_path):
        # avahi-daemon and wsdd2 are system packages, kept like samba, with the files that
        # keep them to IPv4. /etc/unos goes, so avahi-daemon's settings are not there: without
        # them it would not start (ADR-0275).
        result, calls, _ = self._run(tmp_path, "print_completion")
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == []
        assert "  - System packages (btrfs-progs, samba, avahi-daemon, wsdd2, etc.)\n" in result.stdout
        assert (
            "Network discovery: avahi-daemon and wsdd2 (where install.sh could install it) keep running, so "
            "Windows and macOS still list this server while Samba runs, over IPv4 only, as UNOS set them up: "
            f"{WSDD2_DROP_IN} runs wsdd2 -4 {AVAHI_DROP_IN} makes avahi-daemon read {AVAHI_CONF}, without IPv6 "
            "To stop wsdd2, run: systemctl disable --now wsdd2 "
            "To give avahi-daemon its own settings back (/etc/avahi/avahi-daemon.conf), remove the drop-in "
            f"and {AVAHI_CONF}, then run: systemctl daemon-reload systemctl restart avahi-daemon" in text
        )
        assert not any(path.startswith("/etc/unos/") for path in DISCOVERY_FILES)

    def test_the_completion_says_how_to_turn_smartd_on_again(self, tmp_path):
        # install.sh turned smartd off, since UNOS read SMART itself (ADR-0378); without UNOS,
        # nothing watches the disks until it runs again.
        result, calls, _ = self._run(tmp_path, "print_completion")
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == []
        assert (
            "SMART monitoring: install.sh turned off smartd, since UNOS read the disks' SMART data "
            "itself. To have smartd watch the disks again, run: systemctl enable --now smartmontools" in text
        )

    # --- the disks' idle timeouts (storage.disk-spindown, ADR-0378) ---

    def _run_idle(self, tmp_path, rule, *, listing=_DISKS, missing=(), fail=(), stubs=None,
                  commands="remove_disk_idle_timeouts; print_completion", system_path=None):
        """`commands` with UNOS's udev rule of the idle timeouts in tmp_path holding `rule` (no file
        for None), and stubs of _IDLE_TOOLS that log their calls: lsblk lists `listing`, smartctl
        prints that the scans are on, timeout runs the command after its options, and those in
        `missing` are not there. The host's own are never on PATH (nor in `system_path`, the
        folder that the script adds to PATH). The command lines in `fail` fail. Returns the
        result, the calls and the rule's path."""
        path = tmp_path / "90-unos-disk-spindown.rules"
        if rule is not None:
            path.write_text(rule)
        tools = {
            "lsblk": _LOGGER + _FAILS + f"cat <<'EOF'\n{listing}EOF\n",
            "hdparm": _LOGGER + _FAILS,
            "smartctl": _smartctl_stub(*_SCANS_TURNED_ON),
            "udevadm": _LOGGER + _FAILS,
            "timeout": _timeout_stub(),
        }
        result, calls, _ = self._run(
            tmp_path,
            f'DISK_IDLE_RULE="{path}"; {commands}',
            fail=fail,
            stubs={**{name: body for name, body in tools.items() if name not in missing}, **(stubs or {})},
            without=_IDLE_TOOLS,
            system_path=system_path,
        )
        return result, calls, path

    @staticmethod
    def _paragraph(result, start):
        """The lines of the paragraph of the completion that starts with `start`, [] without one."""
        completion = result.stdout[result.stdout.index("UNOS Uninstallation Complete!"):]
        if start not in completion:
            return []
        return completion[completion.index(start):].split("\n\n")[0].splitlines()

    def _left(self, result):
        """The indented lines of the completion's paragraph on what was not undone: for each, what
        is left and why, then the command."""
        return [line for line in self._paragraph(result, "Disk idle timeouts left:") if line.startswith("  ")]

    @staticmethod
    def _two_disks():
        """The rule of the disks WD-1 (20 minutes; UNOS turned its automatic offline scans off) and
        WD-2 (1 hour), as the supervisor writes it."""
        from src.storage import spindown

        return spindown.render({"WD-1": 20, "WD-2": 60}, frozenset({"WD-1"}))

    @staticmethod
    def _read_rule(monkeypatch, path):
        """What spindown.read_rule reads from the rule file at `path`, as it is (\r and all)."""
        from src import host
        from src.storage import spindown

        text = path.read_bytes().decode()
        monkeypatch.setattr(host, "run_on_host", lambda cmd, **kwargs: host.HostResult(0, text, ""))
        return spindown.read_rule()

    def test_without_the_rule_nothing_runs_and_nothing_is_said(self, tmp_path):
        result, calls, _ = self._run_idle(tmp_path, None, commands="remove_disk_idle_timeouts")

        assert result.returncode == 0, result.stdout + result.stderr
        assert (calls, result.stdout, result.stderr) == ([], "", "")

        again = tmp_path / "again"
        again.mkdir()
        result, calls, _ = self._run_idle(again, None)
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == []
        assert "idle timeout" not in result.stdout.lower()

    def test_what_the_rule_set_on_its_disks_is_undone_then_the_rule_goes(self, tmp_path):
        """Once UNOS has stopped, each disk the rule names gets the Off of the Storage page (hdparm
        -S 0), and the disk whose automatic offline scans UNOS turned off gets them on again
        (smartctl -o on, with -c to read them back), with the host's tools, each under the host's
        timeout; then the rule goes and udev reads its rules again. No other disk is touched, nor
        the optical drive that reports WD-1's serial too."""
        result, calls, path = self._run_idle(tmp_path, self._two_disks())
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert not path.exists()
        assert calls == [
            _LSBLK,
            *_timer_off("/dev/sda"),
            *_scans_on("/dev/sda"),
            *_timer_off("/dev/sdb"),
            _RELOAD,
        ]
        assert "WARN" not in result.stdout
        assert f"Removed UNOS's udev rule {path}" in text
        assert "Turned off the idle timeout of /dev/sda (serial WD-1)" in text
        assert "Turned on again the automatic offline scans of /dev/sda (serial WD-1)" in text
        assert "Turned off the idle timeout of /dev/sdb (serial WD-2)" in text
        # The end says what the disks are left with: no timer until they restart, as after Off.
        assert self._paragraph(result, "Disk idle timeouts:") == [
            "Disk idle timeouts: these disks had an idle timeout set in UNOS, now turned",
            "off. They have no standby timer until they restart; then they have their",
            "own, or the one that /etc/hdparm.conf sets:",
            "  /dev/sda (serial WD-1)",
            "  /dev/sdb (serial WD-2)",
        ]
        assert self._left(result) == []

    @pytest.mark.parametrize("tool", ["hdparm", "smartctl"])
    @pytest.mark.parametrize("signal_name, status", [("INT", 130), ("TERM", 143), ("HUP", 129)])
    def test_an_uninstallation_stopped_on_the_way_keeps_the_rule_for_the_next_run(
        self, tmp_path, signal_name, status, tool
    ):
        """The rule goes only once its disks are done: a run that stops on the way leaves it whole,
        and the next run undoes it all. Ctrl-C reaches only the script, not timeout and the tool,
        which run in a process group of their own, and bash does not stop for a SIGINT that its
        command survived: the step stops once that command has ended (its signal comes from the
        command here, as the terminal's comes while it runs), keeps the rule whole and ends the
        uninstallation with what to do. So does a TERM, and a HUP (a dropped SSH session), and
        none leaves the command's temporary folder behind. hdparm runs first, then smartctl."""
        rule = self._two_disks()
        tmp = tmp_path / "tmp"
        tmp.mkdir()
        kill = f'kill -{signal_name} "$PPID"\n'
        printed = "printf '%s\\n' " + " ".join(shlex.quote(line) for line in _SCANS_TURNED_ON) + "\n"
        stopped = _LOGGER + (printed if tool == "smartctl" else "") + kill
        result, calls, path = self._run_idle(
            tmp_path, rule, stubs={tool: stopped, "mktemp": _MKTEMP_IN_TMPDIR},
            commands=f'export TMPDIR="{tmp}"; remove_disk_idle_timeouts; print_completion')

        assert result.returncode == status, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *(_scans_on("/dev/sda") if tool == "smartctl" else [])]
        assert path.read_text() == rule
        assert list(tmp.iterdir()) == []
        assert (f"Stopped: UNOS's udev rule {path} stays as it was, and the disks it names may keep what "
                "UNOS set. Run the uninstallation again to finish." in self._text(result))
        assert "UNOS Uninstallation Complete!" not in result.stdout

        again = tmp_path / "again"
        again.mkdir()
        result, calls, path = self._run_idle(again, path.read_text())
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD]
        assert not path.exists() and self._left(result) == []

    @pytest.mark.parametrize("signal_name", ["INT", "TERM", "HUP"])
    def test_after_the_step_a_signal_ends_the_uninstallation_at_once_again(self, tmp_path, signal_name):
        """The step holds a Ctrl-C, a TERM or a HUP back only while it undoes the disks: after it,
        each ends the uninstallation at once, as before the step."""
        result, calls, path = self._run_idle(
            tmp_path, self._two_disks(), commands=f'remove_disk_idle_timeouts; kill -{signal_name} $$; echo "went on"')

        assert result.returncode == -getattr(signal, f"SIG{signal_name}"), result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD]
        assert not path.exists()
        assert "went on" not in result.stdout

    def test_a_record_without_a_timeout_gets_only_its_scans_on(self, tmp_path):
        """An Off whose smartctl -o on failed keeps the disk's record without its line (the record
        comes last): the disk gets its scans on, and no hdparm -S 0."""
        from src.storage import spindown

        result, calls, path = self._run_idle(tmp_path, spindown.render({"WD-2": 60}, frozenset({"WD-1"})))

        assert result.returncode == 0, result.stdout + result.stderr
        assert not path.exists()
        assert calls == [_LSBLK, *_timer_off("/dev/sdb"), *_scans_on("/dev/sda"), _RELOAD]
        assert self._paragraph(result, "Disk idle timeouts:")[3:] == ["  /dev/sdb (serial WD-2)"]
        assert "Turned on again the automatic offline scans of /dev/sda (serial WD-1)" in self._text(result)
        assert self._left(result) == []

    def test_a_disk_that_is_not_attached_or_not_alone_with_its_serial_is_named(self, tmp_path):
        """WD-7 is not attached, and two disks report WD-2: neither is touched, and the end names
        what is left on them with the command, for the disk's path once it is known."""
        from src.storage import spindown

        rule = spindown.render({"WD-1": 20, "WD-2": 60, "WD-7": 30}, frozenset({"WD-1", "WD-7"}))
        result, calls, path = self._run_idle(tmp_path, rule, listing=_DISKS + "sdd disk WD-2\n")
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), _RELOAD]
        assert self._left(result) == [
            "  The disk with the serial WD-2 may keep its idle timeout until it restarts: more than one "
            "disk reports this serial (/dev/sdb /dev/sdd).",
            "    hdparm -S 0 DISK",
            "  The disk with the serial WD-7 may keep its idle timeout until it restarts: it is not attached.",
            "    hdparm -S 0 DISK",
            "  The disk with the serial WD-7 has its automatic offline scans off: it is not attached.",
            "    smartctl -o on DISK",
        ]
        assert (
            "Disk idle timeouts left: the uninstallation could not undo all that UNOS set on the disks of "
            "its udev rule. To undo the rest, run as root:" in text
        )
        assert (
            "DISK: the path of the disk with that serial, which lsblk -o PATH,SERIAL shows while it is "
            "attached." in text
        )
        assert "The disk with the serial WD-7 has its automatic offline scans off: it is not attached " \
               "(see the end of the uninstallation)" in text
        # The rule keeps WD-7's record, and only that.
        assert _records(path) == [spindown.RECORD.format(serial="WD-7")]

    def test_scans_left_off_keep_their_records_for_a_new_installation_or_another_run(self, tmp_path, monkeypatch):
        """smartctl fails on WD-1 and WD-7 is not attached: the rule then holds only their records
        and comments, which udev runs nothing of and which read_rule takes as records without a
        timeout: a new installation's Off turns their scans on where SMART is on on the disk, and so
        does another run of the uninstallation, which then removes the rule. The rule is 0644 as
        udev's other rules, also under root's umask 077, with which the new file would be 0600."""
        from src.storage import spindown

        rule = spindown.render({"WD-1": 20, "WD-2": 60, "WD-7": 30}, frozenset({"WD-1", "WD-7"}))
        result, calls, path = self._run_idle(tmp_path, rule, fail=("smartctl -o on -c /dev/sda",),
                                             commands="umask 077; remove_disk_idle_timeouts; print_completion")

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD]
        kept = path.read_text()
        assert kept.endswith("\n") and all(line.startswith("# ") for line in kept.splitlines())
        assert self._read_rule(monkeypatch, path) == spindown.Rule({}, frozenset({"WD-1", "WD-7"}))
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert list(tmp_path.glob("*.unos-tmp*")) == []
        assert (f"{path} now holds only the records of the disks whose automatic offline scans are still off "
                "(see the end of the uninstallation)") in self._text(result)
        assert self._left(result)[::2] == [
            "  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: smartctl -o on "
            "failed (exit status 1).",
            "  The disk with the serial WD-7 may keep its idle timeout until it restarts: it is not attached.",
            "  The disk with the serial WD-7 has its automatic offline scans off: it is not attached.",
        ]
        # Not where SMART is off on the disk: the supervisor reads no scans of such a disk, and the
        # uninstallation sends -o on only with -s on, which it leaves to the user.
        assert self._paragraph(result, f"Disk idle timeouts: UNOS's udev rule {path}") == [
            f"Disk idle timeouts: UNOS's udev rule {path} now holds only the",
            "records of the disks whose automatic offline scans are still off (above),",
            "which udev ignores. A new installation of UNOS reads them: saving Off for",
            "such a disk turns its scans on again, unless SMART is off on the disk: then",
            "only smartctl -s on -o on does. Once they are on, remove the rule:",
            f"  rm {path}",
        ]

        again = tmp_path / "again"
        again.mkdir()
        result, calls, path = self._run_idle(again, kept, listing=_DISKS + "sdd disk WD-7\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_scans_on("/dev/sda"), *_scans_on("/dev/sdd"), _RELOAD]
        assert not path.exists() and self._left(result) == []
        assert "Disk idle timeouts" not in result.stdout

    @pytest.mark.parametrize("rm_fails", [False, True], ids=["removed", "kept"])
    def test_records_that_cannot_be_written_leave_the_rule_removed_or_whole(self, tmp_path, rm_fails):
        """WD-7 is not attached, and the rule cannot be replaced by its records: it is removed all
        the same (it would set the timeouts at every boot), and the end names WD-7's scans; when it
        cannot be removed either, it stays whole, records and all."""
        from src.storage import spindown

        rule = spindown.render({"WD-1": 20, "WD-7": 30}, frozenset({"WD-7"}))
        mv = _LOGGER + 'echo "mv: cannot move: Read-only file system" >&2\nexit 1\n'
        rm = (_LOGGER + 'case "$*" in *90-unos-disk-spindown.rules) echo "rm: cannot remove: Read-only file system"'
              + f' >&2; exit 1 ;; esac\nexec {shutil.which("rm")} "$@"\n')
        result, calls, path = self._run_idle(tmp_path, rule, stubs={"mv": mv, **({"rm": rm} if rm_fails else {})})
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert list(tmp_path.glob("*.unos-tmp*")) == []
        moves = [call for call in calls if call.startswith("mv ")]
        rule_path = re.escape(str(path))
        assert len(moves) == 1 and re.fullmatch(rf"mv -f -- {rule_path}\.unos-tmp\.\d+ {rule_path}", moves[0])
        assert (f"Could not keep the records of the disks whose automatic offline scans are still off in {path}: "
                "the end of the uninstallation names them") in text
        assert self._left(result)[-2:] == [
            "  The disk with the serial WD-7 has its automatic offline scans off: it is not attached.",
            "    smartctl -o on DISK",
        ]
        if rm_fails:
            assert path.read_text() == rule
            assert _RELOAD not in calls
            assert f"Could not remove {path} (see the end of the uninstallation)" in text
        else:
            assert not path.exists()
            assert calls[-1] == _RELOAD
            assert f"Removed UNOS's udev rule {path}" in text

    @pytest.mark.parametrize("case", ["rule", "no-rule", "rm-fails"])
    def test_temporary_files_of_a_killed_run_go(self, tmp_path, case):
        """A run killed between the write of the records and their rename (write_disk_idle_records)
        leaves <rule>.unos-tmp.<pid> next to the rule, which udev does not read: the next run removes
        it, also when the rule is gone, and only the files of that exact name. One that cannot be
        removed is named, and the uninstallation goes on."""
        name = "90-unos-disk-spindown.rules"
        left = [f"{name}.unos-tmp.4242", f"{name}.unos-tmp.1"]
        others = [f"{name}.unos-tmp.", f"{name}.unos-tmp.12a", f"{name}.unos-tmp.1.bak", f"{name}.unos-tmp.-1",
                  f"x{name}.unos-tmp.12", "60-other.rules.unos-tmp.12", f"{name}.unos-tmp"]
        for entry in left + others:
            (tmp_path / entry).write_text("# UNOS turned off\n")
        rm = (_LOGGER + 'case "$*" in *.unos-tmp.*) echo "rm: cannot remove: Read-only file system" >&2; exit 1 ;; '
              + f'esac\nexec {shutil.which("rm")} "$@"\n')
        result, calls, path = self._run_idle(
            tmp_path, None if case == "no-rule" else self._two_disks(),
            stubs={"rm": rm} if case == "rm-fails" else None)

        assert result.returncode == 0, result.stdout + result.stderr
        assert not path.exists()
        assert [entry for entry in left if (tmp_path / entry).exists()] == (left if case == "rm-fails" else [])
        assert all((tmp_path / entry).exists() for entry in others)
        if case == "rm-fails":
            text = self._text(result)
            for entry in sorted(left):
                assert (f"Could not remove {tmp_path / entry}, a temporary file of an earlier uninstallation, which "
                        f"udev does not read: remove it with rm {tmp_path / entry}") in text
        else:
            assert "WARN" not in result.stdout
        assert "UNOS Uninstallation Complete!" in result.stdout

    @pytest.mark.parametrize(
        "missing, expected_calls, left, records",
        [
            (
                ("hdparm",),
                [_LSBLK, *_scans_on("/dev/sda"), _RELOAD],
                [
                    "  The disk with the serial WD-1 (/dev/sda) may keep its idle timeout until it restarts: "
                    "the host has no hdparm (package hdparm).",
                    "    hdparm -S 0 /dev/sda",
                    "  The disk with the serial WD-2 (/dev/sdb) may keep its idle timeout until it restarts: "
                    "the host has no hdparm (package hdparm).",
                    "    hdparm -S 0 /dev/sdb",
                ],
                None,
            ),
            (
                # The supervisor's container, whose smartctl UNOS used, is gone by now.
                ("smartctl",),
                [_LSBLK, *_timer_off("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD],
                [
                    "  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: the host "
                    "has no smartctl (package smartmontools).",
                    "    smartctl -o on /dev/sda",
                ],
                ["WD-1"],
            ),
            # udev also reads a changed rules folder by itself.
            (
                ("udevadm",),
                [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb")],
                [],
                None,
            ),
            (
                ("hdparm", "smartctl", "udevadm"),
                [_LSBLK],
                [
                    "  The disk with the serial WD-1 (/dev/sda) may keep its idle timeout until it restarts: "
                    "the host has no hdparm (package hdparm).",
                    "    hdparm -S 0 /dev/sda",
                    "  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: the host "
                    "has no smartctl (package smartmontools).",
                    "    smartctl -o on /dev/sda",
                    "  The disk with the serial WD-2 (/dev/sdb) may keep its idle timeout until it restarts: "
                    "the host has no hdparm (package hdparm).",
                    "    hdparm -S 0 /dev/sdb",
                ],
                ["WD-1"],
            ),
        ],
        ids=["no-hdparm", "no-smartctl", "no-udevadm", "none"],
    )
    def test_a_missing_tool_leaves_its_part_named(self, tmp_path, missing, expected_calls, left, records):
        from src.storage import spindown

        result, calls, path = self._run_idle(tmp_path, self._two_disks(), missing=missing)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _records(path) == (None if records is None else [spindown.RECORD.format(serial=s) for s in records])
        assert calls == expected_calls
        assert self._left(result) == left
        assert "DISK" not in result.stdout
        # Without udevadm there is nothing to fail.
        assert "control --reload failed" not in result.stdout
        assert "UNOS Uninstallation Complete!" in result.stdout

    @pytest.mark.parametrize(
        "fail, expected_calls, left, records, warning",
        [
            (
                "hdparm -S 0 /dev/sda",
                [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD],
                [
                    "  The disk with the serial WD-1 (/dev/sda) may keep its idle timeout until it restarts: "
                    "hdparm -S 0 failed (exit status 1).",
                    "    hdparm -S 0 /dev/sda",
                ],
                None,
                None,
            ),
            (
                "smartctl -o on -c /dev/sda",
                [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD],
                [
                    "  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: "
                    "smartctl -o on failed (exit status 1).",
                    "    smartctl -o on /dev/sda",
                ],
                ["WD-1"],
                None,
            ),
            (
                _LSBLK,
                [_LSBLK, _RELOAD],
                [
                    "  The disk with the serial WD-1 may keep its idle timeout until it restarts: the disks "
                    "could not be listed (lsblk failed).",
                    "    hdparm -S 0 DISK",
                    "  The disk with the serial WD-1 has its automatic offline scans off: the disks could not "
                    "be listed (lsblk failed).",
                    "    smartctl -o on DISK",
                    "  The disk with the serial WD-2 may keep its idle timeout until it restarts: the disks "
                    "could not be listed (lsblk failed).",
                    "    hdparm -S 0 DISK",
                ],
                ["WD-1"],
                None,
            ),
            (
                _RELOAD,
                [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD],
                [],
                None,
                "udevadm control --reload failed: udev reads its rules again by itself",
            ),
        ],
        ids=["hdparm", "smartctl", "lsblk", "udevadm"],
    )
    def test_a_failure_does_not_stop_the_uninstallation_and_is_named(
        self, tmp_path, fail, expected_calls, left, records, warning
    ):
        from src.storage import spindown

        # uninstall.sh runs with set -e: a failing command outside a condition would end it.
        result, calls, path = self._run_idle(tmp_path, self._two_disks(), fail=(fail,))

        assert result.returncode == 0, result.stdout + result.stderr
        assert _records(path) == (None if records is None else [spindown.RECORD.format(serial=s) for s in records])
        assert calls == expected_calls
        assert self._left(result) == left
        if left:
            what = left[0].strip().removesuffix(".")
            assert f"{what} (see the end of the uninstallation)" in self._text(result)
        if warning:
            assert warning in self._text(result)
        assert "UNOS Uninstallation Complete!" in result.stdout

    @pytest.mark.parametrize("status", [124, 137], ids=["term", "kill"])
    def test_a_disk_that_does_not_answer_is_named(self, tmp_path, status):
        """The host's timeout ended hdparm with its TERM (124) or its KILL, which ends timeout too
        (137)."""
        result, calls, path = self._run_idle(
            tmp_path, self._two_disks(), fail=("timeout --kill-after=5 30 hdparm -S 0 /dev/sdb",),
            stubs={"timeout": _timeout_stub(status)},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert not path.exists()
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), _timer_off("/dev/sdb")[0], _RELOAD]
        assert self._left(result) == [
            "  The disk with the serial WD-2 (/dev/sdb) may keep its idle timeout until it restarts: "
            "hdparm -S 0 did not answer within 30 seconds.",
            "    hdparm -S 0 /dev/sdb",
        ]
        # Not bash's line on a command that a signal ended ("Killed"): the end says what happened.
        assert "Killed" not in result.stderr

    def test_a_smartctl_that_outlives_its_kill_does_not_hold_the_uninstallation(self, tmp_path):
        """A command that the kernel holds in uninterruptible sleep outlives the KILL of the host's
        timeout, which ends all the same: the script goes on after at most 35 seconds, and so does
        whatever reads its output to the end (the test's pipes here). The command's output and
        errors go to files of a temporary folder, read once timeout has returned, and its standard
        input is /dev/null: it keeps none of the script's own files open, neither the pipe of a
        command substitution, for which bash would wait as long as the kernel holds it, nor the
        script's output, which may be a pipe too (`uninstall.sh 2>&1 | tee log`, ssh without a
        terminal). The folder is in $TMPDIR, not next to the rule, and goes."""
        from src.storage import spindown

        tmp = tmp_path / "tmp"
        tmp.mkdir()
        stub_dir = tmp_path / "stub"
        try:
            result, calls, path = self._run_idle(
                tmp_path, self._two_disks(), stubs={"timeout": _STUCK_SMARTCTL_TIMEOUT, "mktemp": _MKTEMP_IN_TMPDIR},
                commands=f'export TMPDIR="{tmp}"; remove_disk_idle_timeouts; print_completion',
            )
            holder = int((stub_dir / "holder").read_text())
            try:
                os.kill(holder, 0)
                stuck = True
            except ProcessLookupError:
                stuck = False
        finally:
            (stub_dir / "release").touch()

        # The uninstallation has ended while the stuck smartctl still holds its output.
        assert stuck
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), _scans_on("/dev/sda")[0], *_timer_off("/dev/sdb"), _RELOAD]
        assert self._left(result) == [
            "  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: smartctl -o on "
            "did not answer within 30 seconds.",
            "    smartctl -o on /dev/sda",
        ]
        assert _records(path) == [spindown.RECORD.format(serial="WD-1")]
        assert "Killed" not in result.stderr
        # One temporary folder in $TMPDIR while smartctl ran, none after it, and none next to the rule.
        assert len((stub_dir / "tmp-during").read_text().split()) == 1
        assert list(tmp.iterdir()) == []
        assert sorted(entry.name for entry in tmp_path.iterdir() if entry.name.startswith(path.name)) == [path.name]

    def test_without_a_temporary_folder_for_their_output_the_tools_do_not_run(self, tmp_path):
        """mktemp fails (a full or read-only /tmp): neither tool runs, since its output and errors
        would otherwise go where a command stuck in the kernel keeps them (run_bounded). What is
        left is named with the reason, and the uninstallation goes on."""
        from src.storage import spindown

        result, calls, path = self._run_idle(
            tmp_path, self._two_disks(), stubs={"mktemp": _LOGGER + 'echo "mktemp: failed" >&2\nexit 1\n'})

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, "mktemp -d", "mktemp -d", "mktemp -d", _RELOAD]
        assert self._left(result) == [
            "  The disk with the serial WD-1 (/dev/sda) may keep its idle timeout until it restarts: no temporary "
            "folder for the output of hdparm could be made (mktemp -d failed).",
            "    hdparm -S 0 /dev/sda",
            "  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: no temporary folder "
            "for the output of smartctl could be made (mktemp -d failed).",
            "    smartctl -o on /dev/sda",
            "  The disk with the serial WD-2 (/dev/sdb) may keep its idle timeout until it restarts: no temporary "
            "folder for the output of hdparm could be made (mktemp -d failed).",
            "    hdparm -S 0 /dev/sdb",
        ]
        assert _records(path) == [spindown.RECORD.format(serial="WD-1")]
        assert "UNOS Uninstallation Complete!" in result.stdout

    def test_the_errors_of_the_tools_are_shown(self, tmp_path):
        """A tool's errors go to a file while it runs (run_bounded) and are shown once it has
        ended, on the script's standard error; its standard output is not shown."""
        hdparm = _LOGGER + 'echo "$3: HDIO_DRIVE_CMD(setidle) failed: Input/output error" >&2\nexit 5\n'
        smartctl = (_LOGGER + 'echo "smartctl: a warning of smartctl" >&2\n'
                    + "printf '%s\\n' " + " ".join(shlex.quote(line) for line in _SCANS_TURNED_ON) + "\n")
        result, calls, _ = self._run_idle(tmp_path, self._two_disks(), stubs={"hdparm": hdparm, "smartctl": smartctl})

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD]
        assert result.stderr.splitlines() == [
            "/dev/sda: HDIO_DRIVE_CMD(setidle) failed: Input/output error",
            "smartctl: a warning of smartctl",
            "/dev/sdb: HDIO_DRIVE_CMD(setidle) failed: Input/output error",
        ]
        assert "Turned on again the automatic offline scans of /dev/sda (serial WD-1)" in self._text(result)
        assert "Auto Offline Data Collection" not in result.stdout

    @pytest.mark.parametrize(
        "printed, why, command",
        [
            # SMART is off on the disk: smartctl says so and exits 0 without sending -o (ataprint.cpp),
            # so the command to run turns SMART on too, which the uninstallation does not do itself.
            (
                ("SMART Disabled. Use option -s with argument 'on' to enable it.",
                 "(override with '-T permissive' option)"),
                "SMART is off on the disk, and smartctl sends -o on only to a disk with SMART on; smartctl -s on "
                "-o on turns SMART on too",
                "smartctl -s on -o on /dev/sda",
            ),
            ((_SCANS_TURNED_ON[1], "\t\t\t\t\twas aborted by an interrupting command from host.",
              "\t\t\t\t\tAuto Offline Data Collection: Disabled."), "smartctl -o on left them off (exit status 0)",
             "smartctl -o on /dev/sda"),
            (("smartctl 7.3 2022-02-28 r5338 [aarch64-linux-6.1.0] (local build)",), "smartctl -o on read no state",
             "smartctl -o on /dev/sda"),
        ],
        ids=["smart-off", "still-off", "no-state"],
    )
    def test_scans_that_smartctl_does_not_show_on_are_named(self, tmp_path, printed, why, command):
        """smartctl -o on exits 0 without proving the scans on: -c reads them back, as the
        supervisor does, and only "Auto Offline Data Collection: Enabled." counts."""
        from src.storage import spindown

        result, calls, path = self._run_idle(
            tmp_path, self._two_disks(), stubs={"smartctl": _smartctl_stub(*printed)})

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD]
        assert "Turned on again" not in result.stdout
        assert self._left(result) == [
            f"  The disk with the serial WD-1 (/dev/sda) has its automatic offline scans off: {why}.",
            f"    {command}",
        ]
        assert _records(path) == [spindown.RECORD.format(serial="WD-1")]
        # The kept rule's header says how to turn the scans on, also where SMART is off.
        assert "smartctl -s on -o on DISK where SMART is off on the disk" in " ".join(
            line.removeprefix("# ") for line in path.read_text().splitlines())

    def test_a_rule_that_cannot_be_removed_is_named_and_its_disks_are_still_undone(self, tmp_path):
        """It sets UNOS's timeouts again at every boot, which the end says, also of the disks whose
        timeout it turned off; its lines edited by hand stay with it."""
        rm = (_LOGGER + 'case "$*" in *90-unos-disk-spindown.rules) echo "rm: cannot remove: Read-only file system"'
              + f' >&2; exit 1 ;; esac\nexec {shutil.which("rm")} "$@"\n')
        edited = 'ACTION=="add", KERNEL=="sdb", RUN+="/sbin/hdparm -S 1 /dev/sdb"'
        rule = self._two_disks() + edited + "\n"
        result, calls, path = self._run_idle(tmp_path, rule, stubs={"rm": rm})

        assert result.returncode == 0, result.stdout + result.stderr
        assert path.read_text() == rule
        # The rm stub logs the removal of each command's temporary folder too, made by mktemp outside
        # the rules folder.
        folders = [call for call in calls if call.startswith("rm -rf -- ")]
        assert len(folders) == 3 and not [call for call in folders if str(tmp_path) in call]
        calls = [call for call in calls if call not in folders]
        # Nothing for udev to read again.
        assert calls == [
            _LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), f"rm -f -- {path}"]
        assert self._paragraph(result, "Disk idle timeouts:") == [
            "Disk idle timeouts: these disks had an idle timeout set in UNOS, now turned",
            "off. They have no standby timer until they restart; then they have their",
            "timeout of UNOS's rule again, until it is removed (see below):",
            "  /dev/sda (serial WD-1)",
            "  /dev/sdb (serial WD-2)",
        ]
        assert self._paragraph(result, f"Disk idle timeouts: UNOS's udev rule {path}") == [
            f"Disk idle timeouts: UNOS's udev rule {path} could not be removed: it",
            "sets the idle timeouts again at every boot. Remove it with:",
            f"  rm {path}",
        ]
        assert self._paragraph(result, "Disk idle timeouts: these lines") == [
            "Disk idle timeouts: these lines of UNOS's udev rule are not as UNOS writes",
            "them (edited by hand), so the uninstallation ran nothing for them:",
            f"  {edited}",
        ]
        assert f"Could not remove {path} (see the end of the uninstallation)" in self._text(result)
        assert "UNOS Uninstallation Complete!" in result.stdout

    _NOT_READ = [
        "It stays, and so does what it set: the idle timeouts of the disks it names,",
        "at every boot, and their own automatic offline scans, which UNOS turned off",
        "where a line \"# UNOS turned off the automatic offline data collection of the",
        "disk with the serial ...\" says so. Run for each disk it names, with the",
        "disk's path (lsblk -o PATH,SERIAL) for DISK:",
        "  hdparm -S 0 DISK",
        "and, for each disk such a line names:",
        "  smartctl -o on DISK",
        "(smartctl -s on -o on DISK where SMART is off on the disk, which turns SMART",
        "on too). Then remove the rule:",
    ]

    def test_a_rule_that_cannot_be_read_stays_with_its_disks(self, tmp_path):
        """It is the only record of the disks whose scans UNOS turned off: it is not removed, and
        nothing is run on a disk."""
        (tmp_path / "90-unos-disk-spindown.rules").mkdir()
        result, calls, path = self._run_idle(tmp_path, None)
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert path.is_dir() and calls == []
        assert f"Could not read {path}, so it stays (see the end of the uninstallation)" in text
        assert self._paragraph(result, "Disk idle timeouts:") == [
            f"Disk idle timeouts: UNOS's udev rule {path} could not be read.",
            *self._NOT_READ,
            f"  rm {path}",
        ]
        assert "UNOS Uninstallation Complete!" in result.stdout

    def test_a_rule_with_a_nul_byte_stays_and_nothing_runs(self, tmp_path):
        """Bash drops NUL bytes from what it reads, which would join the parts around one into a
        line of UNOS's that read_rule does not count, nor udev, which ends a line there: such a
        rule is not read, as one that cannot be read."""
        from src.storage import spindown

        rule = (self._two_disks() + spindown._RULE.format(serial="WD-9", value=240).replace("ACTION", "ACT\0ION")
                + "\n" + spindown.RECORD.format(serial="WD\0-9") + "\n")
        result, calls, path = self._run_idle(tmp_path, rule, listing=_DISKS + "sdd disk WD-9\n")
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [] and path.read_bytes() == rule.encode()
        assert "null byte" not in result.stderr
        assert (f"{path} holds a NUL byte, which UNOS never writes: it stays, and nothing is run for it "
                "(see the end of the uninstallation)") in text
        assert self._paragraph(result, "Disk idle timeouts:") == [
            f"Disk idle timeouts: UNOS's udev rule {path} holds a NUL byte,",
            "which UNOS never writes, so the uninstallation ran nothing for it.",
            *self._NOT_READ,
            f"  rm {path}",
        ]

    def test_lines_edited_by_hand_are_named_and_go_with_the_rule(self, tmp_path):
        """Only the lines that UNOS writes count. The file is read as text, never run: a disk that
        another line names is not touched (WD-2 is attached as /dev/sdb), and the end shows those
        lines, other than comments, a control character as a question mark, so that it cannot drive
        the terminal. They go with the rule, which keeps only UNOS's lines, as the supervisor's
        writes of it do."""
        from src.storage import spindown

        pwned = tmp_path / "pwned"
        edited = [
            spindown._RULE.format(serial="WD-2", value=180),  # 15 minutes: not offered
            spindown._RULE.format(serial=f"$(touch {pwned})", value=240),
            spindown.RECORD.format(serial="WD-2 /dev/sdb"),
            'ACTION=="add", KERNEL=="sdb", RUN+="/sbin/hdparm -S 1 /dev/sdb"',
            'action=="add", subsystem=="block", RUN+="/bin/echo \x1b[31mred"',
        ]
        rule = (
            spindown.render({"WD-1": 20}, frozenset({"WD-1"}))
            # Blanks around a line of UNOS's: read_rule strips them.
            + " \t" + spindown._RULE.format(serial="WD-1", value=240) + "  \n"
            + "# A note of the owner, which does nothing\n\n"
            + "".join(f"{line}\n" for line in edited)
        )
        result, calls, path = self._run_idle(tmp_path, rule)

        assert result.returncode == 0, result.stdout + result.stderr
        assert not path.exists() and not pwned.exists()
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), _RELOAD]
        assert self._paragraph(result, "Disk idle timeouts: these lines") == [
            "Disk idle timeouts: these lines of UNOS's udev rule are not as UNOS writes",
            "them (edited by hand). The uninstallation ran nothing for them, and they",
            "are no longer in the rule:",
            *(f"  {_shown(line)}" for line in edited),
        ]
        assert "\x1b[31mred" not in result.stdout and '/bin/echo ?[31mred"' in result.stdout
        assert "A note of the owner" not in result.stdout
        assert self._left(result) == []

    @pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
    def test_the_lines_that_count_are_those_the_supervisor_reads(self, tmp_path, monkeypatch, locale):
        """spindown.read_rule decides which lines are UNOS's: the exact form that render writes,
        with a serial that _RULE_SERIAL accepts and the hdparm -S value of an offered timeout, in
        lines read as udev reads a file without NUL bytes (ended by \\n or \\r, with spaces and tabs
        around them; udev also ends a line at a NUL byte, which UNOS never writes). The
        uninstallation takes the same lines, and no other, in any locale. A rule with a NUL byte it
        does not read at all (test_a_rule_with_a_nul_byte_stays_and_nothing_runs)."""
        from src.storage import spindown

        serials = ["aZ09#+.:=@_-", "x" * 64, "-", "x" * 65, "a b", "a/b", "a*b", "a?b", "a[b]", "a,b", "a'b",
                   "é", "a$b", "a\\b", "a;b", "a|b", "a`b", "a~b", "a&b", "a<b", "a{b}", "a!b", "a%b", "a^b"]
        values = [0, 1, 12, 59, 60, 61, 120, 180, 239, 240, 241, 242, 243, 244, 245, 246, 247, 248, 249, 250, 251,
                  252, 255, 256, 1000, "060", "0250", "00", "18446744073709551676", "٦٠"]
        lines = [spindown._RULE.format(serial=serial, value=240) for serial in serials]
        lines += [spindown.RECORD.format(serial=serial) for serial in serials]
        lines += [spindown._RULE.format(serial=f"V{value}", value=value) for value in values]
        rule, record = spindown._RULE.format, spindown.RECORD.format
        text = "".join(f"{line}\n" for line in lines) + (
            # Ended by \r alone or by \r\n, with spaces and tabs around: lines of UNOS's.
            rule(serial="CR1", value=240) + "\r" + rule(serial="CR2", value=241) + "\r\n"
            + " \t" + record(serial="CR1") + "\t \r\n"
            # Other blanks, and a form feed, which ends no line: none.
            + " " + rule(serial="NBSP", value=240) + "\n"
            + record(serial="IDEO") + "　\n"
            + rule(serial="UNIT", value=240) + "\x1f\n"
            + rule(serial="VT", value=240) + "\v\n"
            + rule(serial="FF1", value=240) + "\f" + rule(serial="FF2", value=240) + "\n"
        )
        path = tmp_path / "rule"
        path.write_text(text)
        expected = self._read_rule(monkeypatch, path)
        disks = list(dict.fromkeys([*expected.timeouts, *expected.scans_off]))
        assert len(expected.timeouts) == 3 + 11 + 2 and len(expected.scans_off) == 3 + 1

        result, calls, _ = self._run_idle(
            tmp_path, text, listing="".join(f"sd{i} disk {serial}\n" for i, serial in enumerate(disks)),
            commands=f"LC_ALL={locale}; remove_disk_idle_timeouts; print_completion",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        serial_of = {f"/dev/sd{i}": serial for i, serial in enumerate(disks)}
        timed = [serial_of[call.split()[-1]] for call in calls if call.startswith("hdparm ")]
        recorded = [serial_of[call.split()[-1]] for call in calls if call.startswith("smartctl ")]
        assert (timed, set(recorded)) == (list(expected.timeouts), expected.scans_off)
        assert self._left(result) == []
        counted = {spindown._RULE.format(serial=serial, value=spindown.hdparm_value(minutes))
                   for serial, minutes in expected.timeouts.items()}
        counted |= {spindown.RECORD.format(serial=serial) for serial in expected.scans_off}
        counted |= {spindown._RULE.format(serial=f"V{value}", value=value) for value in ("060", "0250")}
        read = [line.strip(" \t") for line in text.replace("\r", "\n").split("\n")]
        assert self._paragraph(result, "Disk idle timeouts: these lines")[3:] == [
            f"  {_shown(line)}" for line in read if line and line not in counted
        ]

    def test_the_rule_is_the_supervisors(self):
        from src.storage import spindown

        script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text()

        assert f'DISK_IDLE_RULE="{spindown.RULES_PATH}"' in script
        assert spindown.HDPARM_PATH == "/sbin/hdparm" and "/sbin/hdparm -S" in script
        assert f"DISK_IDLE_RECORD='{spindown.RECORD.format(serial='')}'" in script

    def test_main_undoes_the_idle_timeouts_once_unos_has_stopped_and_before_its_files_go(self):
        """The supervisor writes the rule: it must not write it again meanwhile, so its container
        is gone (stop_and_remove_containers removes every container named unos*, unos-supervisor
        too) and the unos service has stopped. The step comes before remove_files, which removes
        /opt/unos and the script with it (install.sh puts it nowhere else): a run stopped in the
        step leaves the rule whole, and the script there to run again."""
        script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text()
        body = re.search(r"^main\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        steps = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]

        assert (
            steps.index("confirm_uninstall")
            < steps.index("stop_and_remove_containers")
            < steps.index("remove_systemd_service")
            < steps.index("remove_disk_idle_timeouts")
            < steps.index("remove_files")
            < steps.index("print_completion")
        )
        assert steps.index("remove_disk_idle_timeouts") == steps.index("remove_systemd_service") + 1
        stop = re.search(r"^stop_and_remove_containers\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        assert 'docker ps -a --filter "name=unos" -q | xargs -r docker rm -f' in stop
        compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.yml").read_text())
        assert compose["services"]["supervisor"]["container_name"] == "unos-supervisor"
        remove_files = re.search(r"^remove_files\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)
        assert 'rm -rf "$UNOS_INSTALL_DIR"' in remove_files
        assert "sudo /opt/unos/deploy/uninstall.sh" in (REPO_ROOT / "README.md").read_text()

    def test_the_script_adds_the_system_folders_of_programs_to_path(self):
        """Root's PATH may lack /usr/sbin and /sbin (su without "-" keeps the user's PATH, without
        them on Debian), where hdparm, smartctl, udevadm and exportfs are: the script adds them at
        its top, after the folders it was given, which keep their order."""
        script = (REPO_ROOT / "deploy" / "uninstall.sh").read_text()
        top = script[: script.index("\nlog_info() {")]

        assert script.count(_SYSTEM_PATH_LINE) == 1
        assert f"\n{_SYSTEM_PATH_LINE}\n" in top and top.index("\nset -e\n") < top.index(_SYSTEM_PATH_LINE)

    def test_the_tools_outside_the_path_given_are_found(self, tmp_path):
        """hdparm, smartctl and udevadm only in a folder that stands for /usr/sbin, which the PATH
        given has not: the script finds them there (the folders it adds, _uninstall_lib), and
        does not say that the host has no hdparm or smartctl."""
        sbin = tmp_path / "sbin"
        sbin.mkdir()
        tools = {"hdparm": _LOGGER + _FAILS, "smartctl": _smartctl_stub(*_SCANS_TURNED_ON), "udevadm": _LOGGER + _FAILS}
        for name, body in tools.items():
            (sbin / name).write_text(body)
            (sbin / name).chmod(0o755)

        result, calls, path = self._run_idle(tmp_path, self._two_disks(), missing=tuple(tools), system_path=sbin)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [_LSBLK, *_timer_off("/dev/sda"), *_scans_on("/dev/sda"), *_timer_off("/dev/sdb"), _RELOAD]
        assert not path.exists() and self._left(result) == []
        assert "the host has no" not in result.stdout

    def test_the_confirmation_names_the_idle_timeouts(self, tmp_path):
        result = subprocess.run(
            ["bash", "-c", '. "$LIB"; confirm_uninstall'],
            env={**os.environ, "LIB": str(_uninstall_lib(tmp_path))},
            input="no\n",
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert (
            "  - The disk idle timeouts set in UNOS (/etc/udev/rules.d/90-unos-disk-spindown.rules):\n"
            "    the disks get none, and their own automatic offline scans that UNOS\n"
            "    turned off are turned on again\n"
        ) in result.stdout

    @pytest.mark.parametrize("volume", [True, False], ids=["data", "no-data"])
    def test_a_backup_is_made_only_of_the_data_volume_that_is_there(self, tmp_path, volume):
        """A run after one that removed the Docker volumes, such as one stopped while it undid the
        disks' idle timeouts, which says to run it again, finds no data volume: docker run -v would
        create it empty, and the newest final backup would be an empty archive beside the real one.
        It says so, asks nothing and backs nothing up."""
        backups = tmp_path / "backups"
        result, calls, _ = self._run(
            tmp_path, f'BACKUP_DIR="{backups}"; backup_before_uninstall <<< y',
            fail=() if volume else ("docker volume inspect deploy_unos-data",),
            stubs={"docker": _LOGGER + _FAILS},
        )
        text = self._text(result)

        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[0] == "docker volume inspect deploy_unos-data"
        if volume:
            assert len(calls) == 2 and re.fullmatch(
                rf"docker run --rm -v deploy_unos-data:/data -v {re.escape(str(backups))}:/backup alpine tar czf "
                r"/backup/unos-final-backup-\d{8}-\d{6}\.tar\.gz -C /data \.", calls[1])
            assert f"Backup created at {backups}" in text
        else:
            assert calls == ["docker volume inspect deploy_unos-data"]
            assert not backups.exists() and "Backup created" not in text
            assert ("No backup: Docker shows no volume deploy_unos-data with UNOS's data (an earlier run of the "
                    "uninstallation removes it)") in text


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


def _e2e_admin():
    """The administrator that the E2E specs sign in as: E2E_ADMIN of webui/tests/fixtures.ts."""
    text = (REPO_ROOT / "webui" / "tests" / "fixtures.ts").read_text()
    found = re.search(
        r"^export const E2E_ADMIN = \{ username: '([^'\\]+)', password: '([^'\\]+)' \} as const;$", text, re.M
    )
    assert found, "webui/tests/fixtures.ts: no E2E_ADMIN"
    return {"username": found[1], "password": found[2]}


class TestTestRunner:
    """The test-runner service of the test profile (webui/Dockerfile.test)."""

    def test_image_has_the_playwright_version_of_package_lock(self):
        # The image carries the browsers of exactly one Playwright version.
        lock = json.loads((REPO_ROOT / "webui" / "package-lock.json").read_text())
        version = lock["packages"]["node_modules/@playwright/test"]["version"]
        dockerfile = (REPO_ROOT / "webui" / "Dockerfile.test").read_text()

        images = re.findall(
            r"^FROM\s+mcr\.microsoft\.com/playwright:v([^\s@-]+)(?:-\w+)?@sha256:[0-9a-f]{64}\s*$",
            dockerfile,
            re.MULTILINE,
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
        # The account of webui/tests/fixtures.ts, in the form of the compose file.
        assert f"-d {json.dumps(_e2e_admin())}" in setup
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

    def test_playwright_runs_from_the_web_uis_node_modules_never_through_npx(self, tmp_path):
        # npx downloads a program that is missing from node_modules/.bin, unpinned, without asking
        # where CI is set (ADR-0423): a missing Playwright is an error instead.
        script = (REPO_ROOT / "scripts" / "test-cycle.sh").read_text()
        tail = '\n# Handle Ctrl+C\ntrap cleanup EXIT\n\n# Run main\nmain "$@"\n'
        assert script.endswith(tail)
        for directory in ("scripts", "bin", "webui/node_modules/.bin"):
            (tmp_path / directory).mkdir(parents=True)
        (tmp_path / "scripts" / "test-cycle-lib.sh").write_text(script[: -len(tail)] + "\n")
        stubs = {
            "bin/npx": 'echo "npx $*" >> "$STUB_DIR/calls"\n',
            "bin/npm": 'echo "npm $*" >> "$STUB_DIR/calls"\n',
            "webui/node_modules/.bin/playwright": 'echo "$(basename "$PWD"): playwright $*" >> "$STUB_DIR/calls"\n',
        }
        for name, body in stubs.items():
            (tmp_path / name).write_text("#!/bin/bash\n" + body)
            (tmp_path / name).chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", '. "$LIB"; run_e2e_tests && run_quick_test'],
            env={**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
                 "LIB": str(tmp_path / "scripts" / "test-cycle-lib.sh"), "STUB_DIR": str(tmp_path)},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "calls").read_text().splitlines() == [
            "webui: playwright install chromium --with-deps",
            "webui: playwright test --reporter=list",
            "webui: playwright test auth.spec.ts api.spec.ts --reporter=list",
        ]

    def test_creates_the_administrator_the_specs_sign_in_as(self):
        script = (REPO_ROOT / "scripts" / "test-cycle.sh").read_text()
        bodies = re.findall(r"-d '(\{[^'\n]*\})'", script)

        assert [json.loads(body) for body in bodies] == [_e2e_admin()]

    def test_testing_md_starts_the_stack_with_the_same_limit(self):
        testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()
        ups = re.findall(r"^docker compose -f docker-compose\.test\.yml up\b.*$", testing, re.M)

        assert ups
        assert _wait_timeouts(ups) == [_supervisor_decided_within("docker-compose.test.yml")] * len(ups)


def _guide_heredoc(guide, path):
    """The text that the manual installation of docs/INSTALL.md writes to `path` with
    `sudo tee PATH > /dev/null << 'EOF'`."""
    match = re.search(rf"^sudo tee {re.escape(path)} > /dev/null << 'EOF'\n(.*?)^EOF$", guide, re.M | re.S)
    assert match, path
    return match.group(1)


class TestInstallGuide:
    """docs/INSTALL.md: a manual installation installs the packages of install.sh, runs
    Samba and network discovery as install.sh does, and its systemd unit starts compose as
    install.sh does."""

    def test_unit_starts_compose_as_install_sh_does(self, tmp_path):
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        unit = _guide_heredoc(guide, "/etc/systemd/system/unos.service")
        _, _, exec_start = _start_install(tmp_path)

        assert re.findall(r"^ExecStart=.*$", unit, re.M) == exec_start

    def test_installs_the_packages_of_install_sh(self, tmp_path):
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        step = guide[guide.index("### Step 1: Install System Dependencies") : guide.index("### Step 2:")]
        command = re.search(r"^sudo apt install -y (.*?)\n```", step, re.M | re.S).group(1)

        assert command.replace("\\", " ").split() == _install_packages(tmp_path)

    def test_turns_smartd_off_as_install_sh_does(self):
        # UNOS reads SMART data itself, and smartd's reads would keep a disk with an idle timeout
        # of 30 minutes or more from spinning down (stop_smartd of install.sh, ADR-0378).
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        script = (REPO_ROOT / "deploy" / "install.sh").read_text()
        step = guide[guide.index("### Step 1: Install System Dependencies") : guide.index("### Step 2:")]
        stop = re.search(r"^stop_smartd\(\) \{\n(.*?)^\}", script, re.M | re.S).group(1)

        assert [line for line in step.splitlines() if line.startswith("sudo systemctl")] == [
            "sudo systemctl disable --now smartmontools"
        ]
        assert "    systemctl disable --now smartmontools\n" in stop

    def test_samba_runs_without_netbios_and_discovery_as_install_sh_sets_it_up(self, tmp_path):
        # What configure_samba and configure_discovery run (ADR-0275): the files that keep
        # avahi-daemon and wsdd2 to IPv4 written and loaded before their packages are
        # installed, avahi-daemon without what it recommends, as in install.sh. The guide
        # leaves out install.sh's look for a wsdd2 package (apt-cache show): it says what
        # to leave out on Debian 11.
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()
        script = (REPO_ROOT / "deploy" / "install.sh").read_text()
        step = guide[guide.index("### Step 5: ") : guide.index("### Step 6: ")]
        commands = [line for line in step.splitlines() if line.startswith("sudo ")]
        _, calls = _run_configure_discovery(tmp_path)
        mkdir = "mkdir -p /etc/avahi /etc/systemd/system/avahi-daemon.service.d /etc/systemd/system/wsdd2.service.d"

        def as_typed(call):
            """A call of configure_discovery as the guide's reader types it."""
            if call.startswith("apt-get install -y -qq "):
                return "sudo apt install -y " + call.removeprefix("apt-get install -y -qq ").removesuffix(
                    " (settings written)"
                )
            return f"sudo {call}"

        assert commands == [
            "sudo systemctl enable --now smbd",
            "sudo systemctl disable --now nmbd",
            f"sudo {mkdir}",
            *(f"sudo tee {path} > /dev/null << 'EOF'" for path in DISCOVERY_FILES),
            *(as_typed(call) for call in calls if not call.startswith("apt-cache ")),
        ]
        assert f"\n    {mkdir}\n" in script
        for path in DISCOVERY_FILES:
            assert _guide_heredoc(step, path) == _script_heredoc(script, path), path

    def test_has_no_upgrade_path_from_older_versions(self):
        # UNOS keeps no backward compatibility (ADR-0207): the chapter for installations of
        # 1.0.0 or earlier and its commands are gone, also the sed command for older units.
        guide = (REPO_ROOT / "docs" / "INSTALL.md").read_text()

        assert "1.0.0" not in guide
        assert "/etc/systemd/system/unos.service" not in "\n".join(
            line for line in guide.splitlines() if line.lstrip().startswith("sudo sed")
        )


def _dockerignore(context):
    lines = (REPO_ROOT / context / ".dockerignore").read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def _pip_layer(lines):
    """The index of the RUN that installs requirements.txt in the stripped lines of
    supervisor/Dockerfile: the only one that starts with `RUN pip install`."""
    starts = [i for i, line in enumerate(lines) if line.startswith("RUN pip install")]
    assert len(starts) == 1, starts
    return starts[0]


def _instruction(lines, start):
    """The instruction that starts at the stripped line `start`, its continuation lines joined."""
    parts = []
    for line in lines[start:]:
        parts.append(line.removesuffix("\\").strip())
        if not line.endswith("\\"):
            break
    return " ".join(parts)


class TestBuildContexts:
    def test_dockerignore_keeps_what_the_test_images_copy(self):
        for context in ("webui", "supervisor"):
            assert not any(p.strip("/*") == "tests" for p in _dockerignore(context)), context
        assert "COPY tests/" in (REPO_ROOT / "webui" / "Dockerfile.test").read_text()
        assert "COPY tests/fixtures/" in (REPO_ROOT / "supervisor" / "Dockerfile.test").read_text()

    def test_the_test_stack_has_no_mock_layer(self):
        """Nothing imported supervisor/tests/mocks, and no code read UNOS_TEST_MODE: both are gone
        (testing.remove-dead-mock-layer, ADR-0207)."""
        assert not (REPO_ROOT / "supervisor" / "tests" / "mocks").exists()
        assert "mocks" not in (REPO_ROOT / "supervisor" / "Dockerfile.test").read_text()
        assert "UNOS_TEST_MODE" not in (REPO_ROOT / "deploy" / "docker-compose.test.yml").read_text()
        assert "mocks" not in (REPO_ROOT / "supervisor" / "pytest.ini").read_text()
        testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()
        assert "tests/mocks" not in testing and "UNOS_TEST_MODE" not in testing

    def test_the_web_ui_has_one_caddyfile(self):
        """webui/Caddyfile, which webui/Dockerfile copies into the image, is the repository's only
        Caddyfile, and no compose file mounts one over it (deploy.one-caddyfile, ADR-0201)."""
        listed = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=REPO_ROOT,
                                capture_output=True, text=True, timeout=60)
        if listed.returncode != 0:
            pytest.skip(f"git ls-files failed: {listed.stderr.strip()}")
        assert [name for name in listed.stdout.split("\0") if Path(name).name == "Caddyfile"] == ["webui/Caddyfile"]
        assert "COPY Caddyfile /etc/caddy/Caddyfile" in (REPO_ROOT / "webui" / "Dockerfile").read_text()
        for compose in ("docker-compose.yml", "docker-compose.test.yml"):
            volumes = _compose_file(compose)["services"]["webui"].get("volumes", [])
            assert not [volume for volume in volumes if "Caddyfile" in str(volume)], compose

    def test_caddy_refuses_a_header_block_over_16_kb(self):
        """Caddy is the web UI's and the API's only entry from the network (port 8080 is not
        published): its limit bounds what a client can make the proxy and uvicorn hold before
        any credentials are checked (security.server-limits, ADR-0333)."""
        caddyfile = (REPO_ROOT / "webui" / "Caddyfile").read_text()
        servers = re.search(r"^\tservers \{\n(.*?)^\t\}", caddyfile, re.MULTILINE | re.DOTALL)

        assert servers, "the global options have no servers block"
        assert re.search(r"^\t\tmax_header_size 16KB$", servers.group(1), re.MULTILINE), servers.group(1)

    def test_nothing_refers_to_a_copy_of_the_caddyfile_in_deploy(self):
        """Only the ledger entry that deleted it names the copy that deploy/ kept of webui/Caddyfile
        (deploy.one-caddyfile, step 2): no script, test, document or ADR."""
        copy = "deploy/" + "Caddyfile"  # put together, so that this test does not name it itself
        found = subprocess.run(["git", "grep", "--untracked", "-l", "-F", copy], cwd=REPO_ROOT,
                               capture_output=True, text=True, timeout=60)
        if found.returncode not in (0, 1):
            pytest.skip(f"git grep failed: {found.stderr.strip()}")
        assert found.stdout.splitlines() == ["feature-list.json"]

    def test_dockerignore_drops_local_build_output(self):
        assert {"node_modules", "build", ".svelte-kit"} <= _dockerignore("webui")
        assert {"**/__pycache__", "**/*.pyc"} <= _dockerignore("supervisor")

    def test_the_supervisor_image_carries_the_version_it_was_built_with(self):
        # ADR-0234: the version of System > Updates and General. After the code and the
        # packages, so that a new version rebuilds only the last layers.
        lines = [line.strip() for line in (REPO_ROOT / "supervisor" / "Dockerfile").read_text().splitlines()]
        arg = lines.index("ARG UNOS_VERSION=unknown")
        assert lines[arg + 1] == "ENV UNOS_VERSION=${UNOS_VERSION}"
        assert arg > lines.index("COPY src/ ./src/") > _pip_layer(lines)
        build = _compose_file("docker-compose.yml")["services"]["supervisor"]["build"]
        assert build == {"context": "../supervisor", "args": {"UNOS_VERSION": "${UNOS_VERSION:-unknown}"}}

    def test_restic_comes_in_a_layer_of_its_own_after_the_pip_layer(self):
        """The off-device copy runs restic in the supervisor's container (ADR-0337). Its own RUN after
        pip's keeps the apt-get and pip layers above it cached: a device that builds the image downloads
        only restic, and the comment above the layer says so."""
        text = (REPO_ROOT / "supervisor" / "Dockerfile").read_text()
        lines = [line.strip() for line in text.splitlines()]
        restic = ("RUN apt-get update && apt-get install -y --no-install-recommends restic "
                  "&& rm -rf /var/lib/apt/lists/*")
        assert lines.count(restic) == 1
        at = lines.index(restic)
        assert _pip_layer(lines) < at < lines.index("COPY src/ ./src/")
        comment = []
        for line in reversed(lines[:at]):
            if not line.startswith("#"):
                break
            comment.insert(0, line)
        assert "ADR-0337" in " ".join(comment) and "cached" in " ".join(comment), comment
        assert "restic" not in text.split("COPY requirements.txt")[0]

    def test_the_pip_layer_builds_on_riscv64_and_keeps_no_toolchain_there(self):
        """The base's pip 24.0 takes no linux/riscv64 wheel, and five packages publish none for it:
        the pip layer first installs a pip of 24.1 or later, and on riscv64 installs what compiles
        them, builds them and removes it again in the same RUN, so that no layer of the image keeps
        the Rust toolchain. Elsewhere it installs the requirements as before (ADR-0370)."""
        lines = [line.strip() for line in (REPO_ROOT / "supervisor" / "Dockerfile").read_text().splitlines()]
        layer = _instruction(lines, _pip_layer(lines))
        toolchain = "cargo rustc pkg-config libssl-dev libffi-dev make"

        pip = re.match(r"RUN pip install --no-cache-dir pip==(\d+)\.(\d+)(?:\.\d+)? && ", layer)
        assert pip and (int(pip[1]), int(pip[2])) >= (24, 1), layer
        other, riscv64 = re.fullmatch(
            r'.*? && if \[ "\$\(dpkg --print-architecture\)" != riscv64 \]; then (.*?); else (.*?); fi', layer
        ).groups()
        assert other == "pip install --no-cache-dir -r requirements.txt"
        steps = riscv64.split(" && ")
        assert steps == [
            "apt-get update",
            f"apt-get install -y --no-install-recommends {toolchain}",
            "pip install --no-cache-dir -r requirements.txt",
            f"apt-get purge -y --auto-remove {toolchain}",
            "rm -rf /var/lib/apt/lists/* /root/.cargo",
        ]


# A FROM line that names its image by tag and digest, `name:tag@sha256:<64 hex>`, with an
# optional --platform and stage name (ADR-0305). The digest decides what a build starts from. The
# tag is what Dependabot follows when it proposes a newer digest: for a digest without a tag it
# would propose the digest of `latest`.
PINNED_FROM = re.compile(
    r"(?i:FROM)\s+(?:(?i:--platform)=\S+\s+)?[a-z0-9][a-z0-9._/-]*:\w[\w.-]{0,127}@sha256:[0-9a-f]{64}"
    r"(?:\s+(?i:AS)\s+[\w.-]+)?"
)
# Any FROM line: the image it starts from and the stage it names.
FROM_LINE = re.compile(r"(?i:FROM)\s+(?:(?i:--platform)=\S+\s+)?(?P<image>\S+)(?:\s+(?i:AS)\s+(?P<stage>\S+))?")
# Folders that hold no Dockerfile of the repository: packages, environments and build output.
NOT_SOURCES = {".git", ".venv", ".venv-lint", "node_modules", ".svelte-kit", "__pycache__", ".pytest_cache",
               ".standin"}
NOT_SOURCE_PATHS = {"tmp", "webui/build", "webui/test-results", "webui/playwright-report"}


def _dockerfiles():
    """Every Dockerfile of the repository (Dockerfile, Dockerfile.test, Containerfile and the like)."""
    found = []
    for folder, dirs, files in os.walk(REPO_ROOT):
        here = Path(folder).relative_to(REPO_ROOT)
        dirs[:] = sorted(d for d in dirs if d not in NOT_SOURCES and (here / d).as_posix() not in NOT_SOURCE_PATHS)
        found += [Path(folder, name) for name in sorted(files)
                  if "dockerfile" in name.lower() or "containerfile" in name.lower()]
    return found


def _unpinned_from_lines(text):
    """The FROM lines of a Dockerfile that start from an image without its tag and digest. A line
    that starts from `scratch` or from a stage that an earlier FROM line named pulls nothing."""
    stages, unpinned = {"scratch"}, []
    for line in text.splitlines():
        if not re.match(r"\s*FROM\b", line, re.I):
            continue
        parsed = FROM_LINE.fullmatch(line.strip())
        if not (parsed and parsed["image"].lower() in stages) and not PINNED_FROM.fullmatch(line.strip()):
            unpinned.append(line.strip())
        if parsed and parsed["stage"]:
            stages.add(parsed["stage"].lower())
    return unpinned


class TestBaseImages:
    """The images start from base images pinned by digest (ADR-0305)."""

    def test_every_from_line_names_its_image_by_tag_and_digest(self):
        dockerfiles = _dockerfiles()
        names = {path.relative_to(REPO_ROOT).as_posix() for path in dockerfiles}
        assert {"supervisor/Dockerfile", "supervisor/Dockerfile.test", "webui/Dockerfile",
                "webui/Dockerfile.test"} <= names, names

        unpinned = [f"{path.relative_to(REPO_ROOT).as_posix()}: {line}"
                    for path in dockerfiles for line in _unpinned_from_lines(path.read_text())]

        assert unpinned == []

    @pytest.mark.parametrize(
        "line",
        ["FROM python:3.11-slim", "FROM python@sha256:" + "a" * 64, "from python:3.11-slim AS base",
         "FROM python:3.11-slim@sha256:" + "a" * 63, "FROM ${BASE}", "FROM python:${TAG}@sha256:" + "a" * 64,
         "FROM --platform=$BUILDPLATFORM node:22-alpine AS builder"],
    )
    def test_a_from_line_without_tag_and_digest_is_found(self, line):
        assert _unpinned_from_lines(f"{line}\nRUN true\n") == [line]

    def test_pinned_lines_scratch_and_earlier_stages_pass(self):
        digest = "@sha256:" + "a" * 64
        text = (f"FROM --platform=$BUILDPLATFORM node:22-alpine{digest} AS builder\nRUN true\n"
                f"from\tcaddy:2-alpine{digest}\tas final\nFROM builder\nFROM Final AS again\nFROM scratch\n")

        assert _unpinned_from_lines(text) == []

    def test_the_supervisor_images_start_from_the_same_base(self):
        # The test image runs the code on the base of the image it stands in for, and a new
        # digest moves both (Dependabot's docker group in .github/dependabot.yml).
        def bases(name):
            return re.findall(r"^FROM (\S+)", (REPO_ROOT / "supervisor" / name).read_text(), re.M)

        assert bases("Dockerfile")
        assert bases("Dockerfile.test") == bases("Dockerfile")


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
    """docker ps shows whether the containers work, and webui starts only once
    the supervisor answers: compose waits for its health, and webui's command
    for the API, also when dockerd starts it at boot."""

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_has_no_obsolete_version_key(self, compose_file):
        # Compose ignores it and warns about it on every command.
        assert "version" not in _compose_file(compose_file)

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_webui_starts_once_the_supervisor_is_healthy(self, compose_file):
        services = _compose_file(compose_file)["services"]

        assert services["webui"]["depends_on"] == {"supervisor": {"condition": "service_healthy"}}
        assert services["supervisor"]["healthcheck"]["test"][0] == "CMD"

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_webui_waits_for_the_api_whoever_starts_it(self, compose_file, tmp_path):
        # depends_on holds for docker compose up only. At boot dockerd starts
        # the containers it still has by their restart policy, at once (after
        # the reboot of 2026-11-08 the web UI started 34 ms before the
        # supervisor), so webui's command waits for the API and only then runs
        # the image's CMD (ADR-0307). Here its wget fails three times, with the
        # errors busybox's prints while the supervisor's name and then its port
        # do not answer yet. As compose passes the command: $$ is a $.
        command = [_compose_string(arg) for arg in _compose_file(compose_file)["services"]["webui"]["command"]]
        assert command[:2] == ["/bin/sh", "-c"], command
        image_cmd = re.search(r"^CMD (\[.*\])$", (REPO_ROOT / "webui" / "Dockerfile").read_text(), re.M)
        assert image_cmd, "no CMD in webui/Dockerfile"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        calls_file = tmp_path / "calls"
        fakes = {
            "wget": textwrap.dedent("""\
                echo "wget $*" >> "$CALLS"
                case $(grep -c "^wget " "$CALLS") in
                    1 | 2) echo "wget: bad address 'supervisor:8080'" >&2; exit 1 ;;
                    3) echo "wget: can't connect to remote host (172.31.254.3): Connection refused" >&2; exit 1 ;;
                esac"""),
            "sleep": 'echo "sleep $*" >> "$CALLS"',
            "caddy": 'echo "caddy $*" >> "$CALLS"',
        }
        for name, body in fakes.items():
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_dir / name).chmod(0o755)

        result = subprocess.run(command, env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls_file)},
                                capture_output=True, text=True, timeout=60)

        output = (result.stdout + result.stderr).splitlines()
        calls = calls_file.read_text().splitlines()
        assert result.returncode == 0, output
        # Caddy only after the API answered, started as the image starts it.
        assert [call.split()[0] for call in calls] == ["wget", "sleep"] * 3 + ["wget", "caddy"]
        assert calls[-1] == " ".join(json.loads(image_cmd.group(1)))
        for call in (call for call in calls if call.startswith("wget ")):
            # The health route through the compose network (Caddy's upstream in
            # webui/Caddyfile); without a proxy, as the supervisor's own check;
            # a timeout, so that a supervisor that accepts but does not answer
            # cannot hold the loop; and no file in the container's writable
            # layer, which is on the system disk (ADR-0214).
            assert call.endswith(" http://supervisor:8080/api/v1/health"), call
            assert " -Y off " in call and " -T " in call and " -O /dev/null " in call, call
        # A log line per new error, not per try (ADR-0214), so that a cause
        # that does not go away shows in docker logs, once.
        assert len(output) == 2, output
        assert "bad address 'supervisor:8080'" in output[0] and "Connection refused" in output[1], output

    @pytest.mark.parametrize("status, exit_code", [(200, 0), (503, 1), (None, 1)])
    def test_supervisor_is_healthy_only_while_the_api_answers(self, status, exit_code):
        check = _compose_file("docker-compose.yml")["services"]["supervisor"]["healthcheck"]
        # The image (python:3.11-slim) has neither curl nor wget, and a Python that only asked for
        # the health cost a K1 0.5 s of CPU every interval; bash costs it 0.01 s (ADR-0375).
        assert check["test"][:3] == ["CMD", "bash", "-c"]
        # Compose reads $$ as $.
        code = check["test"][3].replace("$$", "$")
        assert code.count(SUPERVISOR_HEALTH_URL) == 1
        # The check waits for the answer less long than Docker gives it.
        request_timeout = re.search(r"read -r -t (\d+) ", code)
        assert request_timeout, code
        assert int(request_timeout.group(1)) < _seconds(check["timeout"])

        env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
        with _http_server(None) as (proxy_port, _), _http_server(status) as (port, paths):
            # A proxy in the container's environment must not get the request.
            env["http_proxy"] = env["HTTP_PROXY"] = f"http://127.0.0.1:{proxy_port}"
            result = subprocess.run(
                ["bash", "-c", code.replace(SUPERVISOR_HEALTH_URL, f"http://127.0.0.1:{port}/api/v1/health")],
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
        assert re.fullmatch(r"caddy:[\w.]+-alpine@sha256:[0-9a-f]{64}", stages[-1])
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


# The limit of the supervisor's tmpfs at /tmp in both compose files (size=192m, ADR-0406): the
# largest legitimate use (_largest_tmp_use) with a margin, and well within a quarter of the 1 GB
# of RAM that UNOS documents as its minimum (README.md, docs/INSTALL.md).
SUPERVISOR_TMPFS = 192 * 1024**2
MINIMUM_RAM = 1000**3
# What an off-device copy that runs meanwhile may keep in /tmp: restic's pack files, about 16 MiB each,
# a few at a time (ADR-0406). The cache of an S3 store is not bounded: its copy fails with a full tmpfs.
RESTIC_PACKS = 80 * 1024**2
_TMPFS_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _supervisor_tmpfs(compose_file, path="/tmp"):
    """The options of the supervisor's tmpfs at `path` in `compose_file`, as
    Compose passes them to Docker (HostConfig.Tmpfs): "/tmp:noexec,size=192m"
    gives {"noexec": None, "size": "192m"}."""
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


# ==================== The served certificate (security.custom-certificates, ADR-0404) ====================

SERVED_FILE = "/served-certificate/served.pem"


class TestServedCertificate:
    """The supervisor hands the certificate an administrator chose to the web UI's Caddy through
    one file in a volume that only the two containers mount, the web UI's read-only; the web UI's
    command serves it only when Caddy validates it, else Caddy's local CA (ADR-0404)."""

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_only_the_supervisor_and_the_web_ui_mount_the_served_certificate(self, compose_file):
        compose = _compose_file(compose_file)
        mounts = {
            name: [volume for volume in service.get("volumes", []) if str(volume).split(":")[1:2] == ["/served-certificate"]]
            for name, service in compose["services"].items()
        }
        volume = mounts["supervisor"][0].split(":")[0]

        assert mounts["supervisor"] == [f"{volume}:/served-certificate"]
        assert mounts["webui"] == [f"{volume}:/served-certificate:ro"]
        assert [name for name, found in mounts.items() if found] == ["supervisor", "webui"]
        # A named volume of the compose project: no folder of the host, which other programs see.
        assert volume in compose["volumes"]
        assert not [v for s in compose["services"].values() for v in s.get("volumes", [])
                    if str(v).startswith(f"{volume}:") and v not in (mounts["supervisor"] + mounts["webui"])]

    def test_the_supervisor_reloads_the_test_stacks_web_ui(self):
        """The test stack names its containers otherwise: the supervisor reloads the Caddy of its own."""
        compose = _compose_file("docker-compose.test.yml")
        env = dict(item.split("=", 1) for item in compose["services"]["supervisor"]["environment"])

        assert env["UNOS_WEBUI_CONTAINER"] == compose["services"]["webui"]["container_name"]
        production = _compose_file("docker-compose.yml")["services"]
        from src.config import Settings

        assert Settings().webui_container == production["webui"]["container_name"] == "unos-webui"

    def test_the_caddyfile_has_a_site_for_each_tls(self):
        caddyfile = (REPO_ROOT / "webui" / "Caddyfile").read_text()

        local = re.search(r"^\(tls-local\) \{\n(.*?)^\}$", caddyfile, re.M | re.S)
        served = re.search(r"^\(tls-served\) \{\n(.*?)^\}$", caddyfile, re.M | re.S)
        assert local and served
        assert local.group(1).splitlines()[0] == "\t{$UNOS_HOSTNAME:unos}, https:// {"
        assert "\t\ttls internal\n" in local.group(1)
        # Every name gets the served certificate: no address of its own for which Caddy would issue
        # another from its local CA and prefer for that name and for clients without one.
        assert served.group(1).splitlines()[0] == "\thttps:// {"
        assert f"\t\ttls {SERVED_FILE} {SERVED_FILE}\n" in served.group(1)
        for site in (local, served):
            assert "\t\timport unos-site\n" in site.group(1)
        assert re.findall(r"^import .*$", caddyfile, re.M) == [
            "import tls-{$UNOS_TLS:local}", "import /app-names/{$UNOS_APP_NAMES:on}/*.caddy"]
        # No OCSP requests from the LAN, and no staple that Caddy keeps refreshing on the disk.
        assert re.search(r"^\tocsp_stapling off$", caddyfile.split("\n}\n", 1)[0], re.M)
        # The supervisor writes that file, and reloads Caddy with that Caddyfile and those modes (as
        # the module sets them: the tests' own fixture gives each test a folder of its own).
        # The folder is the default of its setting, UNOS_SERVED_CERTIFICATE_DIR (ADR-0419).
        from src.config import Settings

        assert Settings.model_fields["served_certificate_dir"].default == SERVED_FILE.rsplit("/", 1)[0]
        module = (REPO_ROOT / "supervisor" / "src" / "security" / "serving.py").read_text()
        assert '\nSERVED_DIR = get_settings().served_certificate_dir\nSERVED_FILE = "served.pem"\n' in module
        assert '\nWEBUI_CADDYFILE = "/etc/caddy/Caddyfile"\n' in module
        assert '\nTLS_MODES = ("served", "local")\n' in module

    def test_the_image_build_adapts_both_sites(self):
        """A Caddyfile that does not adapt fails the build, not the web UI's start."""
        dockerfile = (REPO_ROOT / "webui" / "Dockerfile").read_text()
        final = dockerfile[dockerfile.rindex("\nFROM "):]

        assert "RUN caddy adapt --config /etc/caddy/Caddyfile > /dev/null" \
            " && UNOS_TLS=served caddy adapt --config /etc/caddy/Caddyfile > /dev/null\n" in final
        assert final.index("COPY Caddyfile /etc/caddy/Caddyfile") < final.index("RUN caddy adapt")

    def _start(self, compose_file, tmp_path, *, served: bool, validates: bool):
        """Run the web UI's command with a supervisor that answers at once and a fake caddy: the
        calls of caddy with the UNOS_TLS each had, and what the command printed."""
        command = [_compose_string(arg) for arg in _compose_file(compose_file)["services"]["webui"]["command"]]
        served_file = tmp_path / "served.pem"
        if served:
            served_file.write_text("bundle")
        script = command[2]
        assert script.count(SERVED_FILE) == 1, script
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        calls = tmp_path / "calls"
        fakes = {
            "wget": "exit 0",
            "caddy": textwrap.dedent(f"""\
                echo "UNOS_TLS=${{UNOS_TLS:-}} caddy $*" >> "{calls}"
                if [ "$1" = validate ]; then
                    echo '{{"level":"info","msg":"using config from file"}}' >&2
                    {'exit 0' if validates else 'echo "Error: loading certificates: tls: private key does not match public key" >&2; exit 1'}
                fi"""),
        }
        for name, body in fakes.items():
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_dir / name).chmod(0o755)
        result = subprocess.run(["/bin/sh", "-c", script.replace(SERVED_FILE, str(served_file))],
                                env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, text=True,
                                timeout=60)
        assert result.returncode == 0, result.stderr
        return calls.read_text().splitlines(), (result.stdout + result.stderr).splitlines()

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_without_a_served_certificate_caddy_serves_its_local_ca(self, compose_file, tmp_path):
        calls, output = self._start(compose_file, tmp_path, served=False, validates=True)

        assert calls == ["UNOS_TLS=local caddy run --config /etc/caddy/Caddyfile"]
        assert output == []

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_a_served_certificate_that_validates_is_served(self, compose_file, tmp_path):
        calls, output = self._start(compose_file, tmp_path, served=True, validates=True)

        assert calls == ["UNOS_TLS=served caddy validate --config /etc/caddy/Caddyfile",
                         "UNOS_TLS=served caddy run --config /etc/caddy/Caddyfile"]
        assert output == []

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_a_served_certificate_that_does_not_load_leaves_the_local_ca(self, compose_file, tmp_path):
        calls, output = self._start(compose_file, tmp_path, served=True, validates=False)

        assert calls == ["UNOS_TLS=served caddy validate --config /etc/caddy/Caddyfile",
                         "UNOS_TLS=local caddy run --config /etc/caddy/Caddyfile"]
        # One line, with Caddy's reason: the last it printed.
        assert output == ["unos-webui: the served certificate does not load, Caddy's local CA instead: "
                          "Error: loading certificates: tls: private key does not match public key"]



# ==================== The app names (system.remote-access, ADR-0420) ====================

SITES_FILE = "/app-names/on/sites.caddy"


class TestAppNamesVolume:
    """The supervisor writes the app names' Caddy sites into a volume that only the two containers
    mount, the web UI's read-only; webui/Caddyfile imports them, and the web UI's command starts Caddy
    with them only when Caddy validates them, else without them (ADR-0420)."""

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_only_the_supervisor_and_the_web_ui_mount_the_app_names(self, compose_file):
        compose = _compose_file(compose_file)
        mounts = {
            name: [volume for volume in service.get("volumes", []) if str(volume).split(":")[1:2] == ["/app-names"]]
            for name, service in compose["services"].items()
        }
        volume = mounts["supervisor"][0].split(":")[0]

        assert mounts["supervisor"] == [f"{volume}:/app-names"]
        assert mounts["webui"] == [f"{volume}:/app-names:ro"]
        assert [name for name, found in mounts.items() if found] == ["supervisor", "webui"]
        assert volume in compose["volumes"]

    def test_the_caddyfile_imports_the_sites_the_supervisor_writes(self):
        caddyfile = (REPO_ROOT / "webui" / "Caddyfile").read_text()
        module = (REPO_ROOT / "supervisor" / "src" / "network" / "app_names.py").read_text()

        assert "\nimport /app-names/{$UNOS_APP_NAMES:on}/*.caddy\n" in caddyfile
        # After the global options and the web UI's own sites; never inside a site.
        assert caddyfile.index("import /app-names/") > caddyfile.index("import tls-{$UNOS_TLS:local}")
        assert '\nAPP_NAMES_DIR = "/app-names"\n' in module
        assert '\nSITES_DIR = "on"\nSITES_FILE = "sites.caddy"\n' in module

    def _start(self, compose_file, tmp_path, *, names: bool, validates: bool):
        """The web UI's command with a supervisor that answers at once and a fake caddy: the calls of
        caddy with the UNOS_TLS and UNOS_APP_NAMES each had, and what the command printed."""
        command = [_compose_string(arg) for arg in _compose_file(compose_file)["services"]["webui"]["command"]]
        sites = tmp_path / "sites.caddy"
        if names:
            sites.write_text("jellyfin-unos.local {}\n")
        script = command[2]
        assert script.count(SITES_FILE) == 1, script
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        calls = tmp_path / "calls"
        fakes = {
            "wget": "exit 0",
            "caddy": textwrap.dedent(f"""\
                echo "UNOS_TLS=${{UNOS_TLS:-}} UNOS_APP_NAMES=${{UNOS_APP_NAMES:-}} caddy $*" >> "{calls}"
                if [ "$1" = validate ]; then
                    {'exit 0' if validates else 'echo "Error: adapting config using caddyfile: ambiguous site definition" >&2; exit 1'}
                fi"""),
        }
        for name, body in fakes.items():
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_dir / name).chmod(0o755)
        result = subprocess.run(["/bin/sh", "-c", script.replace(SITES_FILE, str(sites))],
                                env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, text=True,
                                timeout=60)
        assert result.returncode == 0, result.stderr
        return calls.read_text().splitlines(), (result.stdout + result.stderr).splitlines()

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_without_names_caddy_starts_without_asking(self, compose_file, tmp_path):
        calls, output = self._start(compose_file, tmp_path, names=False, validates=True)

        assert calls == ["UNOS_TLS=local UNOS_APP_NAMES=off caddy run --config /etc/caddy/Caddyfile"]
        assert output == []

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_names_that_validate_are_served(self, compose_file, tmp_path):
        calls, output = self._start(compose_file, tmp_path, names=True, validates=True)

        assert calls == ["UNOS_TLS=local UNOS_APP_NAMES=on caddy validate --config /etc/caddy/Caddyfile",
                         "UNOS_TLS=local UNOS_APP_NAMES=on caddy run --config /etc/caddy/Caddyfile"]
        assert output == []

    @pytest.mark.parametrize("compose_file", COMPOSE_FILES)
    def test_names_that_do_not_load_leave_the_web_ui_without_them(self, compose_file, tmp_path):
        calls, output = self._start(compose_file, tmp_path, names=True, validates=False)

        assert calls == ["UNOS_TLS=local UNOS_APP_NAMES=on caddy validate --config /etc/caddy/Caddyfile",
                         "UNOS_TLS=local UNOS_APP_NAMES=off caddy run --config /etc/caddy/Caddyfile"]
        assert output == ["unos-webui: the app names do not load, the web UI starts without them: "
                          "Error: adapting config using caddyfile: ambiguous site definition"]

def _largest_tmp_use():
    """The most legitimate work keeps in the supervisor's /tmp at once, from
    the limits in src/backup/router.py: a restore, and a backup made
    meanwhile. Uploads write nothing there: an uploaded backup is checked in
    one pass, as a stream (_inspect_archive).

    A restore (one at a time) reads the archive in one pass and extracts
    only the database there (_read_archive, ADR-0364): at most MAX_DB_BYTES.
    A backup made meanwhile (Create Backup or a scheduled backup task)
    collects its files there: a copy of the database and the small files
    (the manifest, and a backup task's scheduled tasks), within the limits of
    a restore (the database MAX_DB_BYTES, any other file MAX_CONFIG_BYTES, at
    most MAX_MEMBERS entries, MAX_TAR_BYTES in all), or it could not be
    restored. deploy/update.sh copies the database there the same way
    (backup_database_online). An off-device copy meanwhile keeps restic's
    pack files there (RESTIC_PACKS)."""
    from src.backup.router import MAX_CONFIG_BYTES, MAX_DB_BYTES, MAX_MEMBERS, MAX_TAR_BYTES

    restore = MAX_DB_BYTES
    backup = min(MAX_TAR_BYTES, MAX_DB_BYTES + MAX_MEMBERS * MAX_CONFIG_BYTES)
    return restore + backup + RESTIC_PACKS


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
    def test_the_tmpfs_leaves_a_board_with_the_minimum_ram_most_of_it(self, compose_file):
        # A tmpfs takes RAM for the files it holds, up to its size: at most a quarter of 1 GB (ADR-0406).
        # The size is fixed, not a percentage, so that the backup limits can be held against it.
        size = _tmpfs_bytes(_supervisor_tmpfs(compose_file)["size"])

        assert size <= MINIMUM_RAM // 4
        assert _largest_tmp_use() <= size < 2 * _largest_tmp_use()

    def test_the_backup_limits_are_the_ones_the_tmpfs_is_sized_for(self):
        from src.backup.router import MAX_ARCHIVE_BYTES, MAX_DB_BYTES, MAX_TAR_BYTES

        assert (MAX_DB_BYTES, MAX_TAR_BYTES, MAX_ARCHIVE_BYTES) == (48 * 1024**2, 64 * 1024**2, 64 * 1024**2)

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
        text = " ".join(paragraphs[0].split())
        assert f"at most {size // 1024**2} MiB" in text
        assert "1 GB" in text

    @staticmethod
    def _run_online_backup(tmp_path, tmpdir_writable, wal=False):
        """update.sh's backup_database_online, its Python program run as there
        (python -, the container's environment): TMPDIR is the tmpfs, TEMP a
        stand-in for /var/tmp, which tempfile would take after it. Returns the
        result and the temporary directories the program tried to make (the
        audit event comes before the mkdir). wal: the database writes ahead, as
        the supervisor's does (ADR-0306), and a connection of its pool stays
        open meanwhile with the row 'recent' committed to unos.db-wal."""
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
        pool = sqlite3.connect(db) if wal else None
        try:
            if pool is not None:
                assert pool.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
                pool.execute("INSERT INTO users VALUES ('recent')")
                pool.commit()
            result = subprocess.run(
                [sys.executable, "-"], input=hook.encode() + program.group(1).encode(),
                capture_output=True, env=env, cwd=tmp_path, timeout=60,
            )
        finally:
            tmpdir.chmod(0o700)
            if pool is not None:
                pool.close()
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

    def test_the_database_copy_of_update_sh_stands_alone_with_the_last_commits(self, tmp_path):
        """The backup's unos.db holds the commits still in unos.db-wal, in one file with the
        rollback journal, which SQLite reads alone: a file marked for the write-ahead log makes
        unos.db-wal and unos.db-shm wherever it is read, and cannot be read where SQLite may not
        write. (Put back by hand, any copy needs the live unos.db-wal and unos.db-shm deleted
        first: SQLite applies a log it finds beside a database, whatever the file's header says,
        docs/INSTALL.md.)"""
        result, made, tmpdir, var_tmp = self._run_online_backup(tmp_path, tmpdir_writable=True, wal=True)

        assert result.returncode == 0, result.stderr.decode()
        copies = tmp_path / "copies"
        copies.mkdir()
        (copies / "unos.db").write_bytes(result.stdout)
        assert (result.stdout[18], result.stdout[19]) == (1, 1)  # the rollback journal
        with contextlib.closing(sqlite3.connect(copies / "unos.db")) as conn:
            assert conn.execute("SELECT name FROM users").fetchall() == [("admin",), ("recent",)]
        assert os.listdir(copies) == ["unos.db"]
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
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
# A workflow's `uses:` line, its value and what follows it on the line.
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
# An action pinned to a full commit SHA, with the version of its release in a comment, as Dependabot
# writes and updates it: `uses: actions/checkout@<40 hex digits> # v7.0.1` (ADR-0423). A tag can be
# moved to other code; a commit cannot.
PINNED_ACTION = re.compile(r"[A-Za-z0-9][\w-]*/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"\s+# v\d+\.\d+\.\d+")
# The tools of the environment in which scripts/audit-python.sh resolves the supervisor's
# dependencies for pip-audit (ADR-0423).
AUDIT_ENV = REPO_ROOT / ".github" / "audit-env-requirements.txt"
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit-python.sh"
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


def _unpinned_actions(text):
    """The `uses:` lines of a workflow that do not name a full commit SHA with the version of its
    release in a comment, stripped."""
    unpinned = []
    for line in text.splitlines():
        uses = USES_LINE.match(line)
        if uses and not (PINNED_ACTION.fullmatch(uses[1]) and VERSION_COMMENT.fullmatch(uses[2])):
            unpinned.append(line.strip())
    return unpinned


def _commands(job):
    return [step["run"] for step in _workflow_jobs()[job]["steps"] if "run" in step]


def _make_dry_run(target, *variables):
    """The commands that `make -n <target>` prints, from the root of the repository."""
    # Without the variables of an outer make, when the suite itself runs under make test.
    env = {key: value for key, value in os.environ.items() if key not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target, *variables],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=True, env=env,
    )
    return result.stdout


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


class TestContainerLogs:
    """Docker rotates the json-file logs of the UNOS containers and of the containers UNOS creates
    (ADR-0330): a container that logs much cannot fill the system disk."""

    def test_each_log_of_the_unos_containers_is_capped(self):
        services = _compose_file("docker-compose.yml")["services"]

        assert set(services) == {"supervisor", "webui"}
        for name, service in services.items():
            assert service.get("logging") == {
                "driver": "json-file", "options": {"max-size": "10m", "max-file": "3"},
            }, name


class TestContinuousIntegration:
    """.github/workflows/test.yml. docs/TESTING.md lists the same commands."""

    # The major version of Node.js in the nodejs package of an Alpine release (pkgs.alpinelinux.org;
    # 3.22 has 22.23.5 on linux/riscv64 among the others, read on 2026-11-13). A newer Alpine is
    # checked and added here before the builder moves to it.
    ALPINE_NODE = {"3.22": "22"}

    def test_node_is_supported_and_the_same_for_the_image_ci_and_npm(self):
        # The builder runs Alpine's Node.js, whose major version the Alpine release fixes; the
        # constraint nodejs~<major> makes apk fail the build on a release with another one
        # (ADR-0370).
        dockerfile = (REPO_ROOT / "webui" / "Dockerfile").read_text()
        alpine = re.findall(r"^FROM alpine:(\d+\.\d+)@sha256:[0-9a-f]{64} AS builder$", dockerfile, re.M)
        builder = re.findall(r"^RUN apk add --no-cache nodejs~(\d+) npm$", dockerfile, re.M)
        setup_node = [
            str(step["with"]["node-version"])
            for step in _workflow_jobs()["frontend"]["steps"]
            if step.get("uses", "").startswith("actions/setup-node@")
        ]
        engines = json.loads((REPO_ROOT / "webui" / "package.json").read_text())["engines"]

        assert len(alpine) == 1 and len(builder) == 1
        assert self.ALPINE_NODE.get(alpine[0]) == builder[0], alpine
        # Node.js 20 reached its end of life in April 2026.
        assert int(builder[0]) >= 22
        assert setup_node == builder
        assert engines == {"node": f">={builder[0]}"}

    def test_dependabot_keeps_the_alpine_release_of_the_builder(self):
        # A new Alpine release can bring another major version of Node.js: Dependabot proposes
        # new digests of alpine:3.22 only (ADR-0370).
        config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
        docker = next(update for update in config["updates"] if update["package-ecosystem"] == "docker")
        ignored = {rule["dependency-name"]: rule.get("update-types") for rule in docker["ignore"]}

        assert ignored["alpine"] == ["version-update:semver-major", "version-update:semver-minor"]
        assert "node" not in ignored

    def test_python_checks_install_hashed_tools(self):
        # `pip install ruff==... pip-audit==...` resolved the packages they
        # depend on afresh in every run, without hashes.
        commands = _commands("lint")

        assert commands[0] == "pip install --require-hashes --only-binary=:all: -r .github/lint-requirements.txt"
        assert "make lint" in commands
        # pip-audit -r installed the latest pip, setuptools and wheel, unpinned, into the
        # environment in which it resolved the requirements (ADR-0423).
        assert "scripts/audit-python.sh" in commands
        assert not [command for command in commands if "pip-audit" in command]
        # make lint runs ruff from PATH there, where the hashed install put it, over the supervisor
        # and the repository's tooling, and mypy over the supervisor against its packages, which
        # are not hashed: a venv of their own after the audit, never the tools' Python, and the
        # PYTHON of make lint there (ADR-0439).
        packages = ("python -m venv supervisor/.venv && supervisor/.venv/bin/pip install --only-binary=:all: "
                    "--no-binary=nut2 -r supervisor/requirements-dev.txt")
        assert commands.index("scripts/audit-python.sh") < commands.index(packages) < commands.index("make lint")
        lint = _make_dry_run("lint", "RUFF=ruff", "MYPY=mypy", "PYTHON=supervisor/.venv/bin/python").splitlines()
        assert "ruff check supervisor scripts .claude/hooks" in lint
        assert f"mypy --python-executable {REPO_ROOT}/supervisor/.venv/bin/python supervisor/src" in lint

    def test_lint_requirements_pin_every_package_with_its_hashes(self):
        wanted = (REPO_ROOT / ".github" / "lint-requirements.in").read_text()
        pins = {_normalized(name): version for name, version in re.findall(r"^([\w.-]+)==(\S+)$", wanted, re.M)}
        locked = (REPO_ROOT / ".github" / "lint-requirements.txt").read_text()
        requirements = _hashed_requirements(locked)

        assert set(pins) == {"ruff", "pip-audit", "mypy"}
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
        # The tools without markers: everywhere.
        tools = sorted((name, markers) for name, _, markers, _ in locked if name in ("ruff", "pip-audit", "mypy"))
        assert tools == [("mypy", None), ("pip-audit", None), ("ruff", None)]

    def test_frontend_checks(self):
        commands = _commands("frontend")
        scripts = json.loads((REPO_ROOT / "webui" / "package.json").read_text())["scripts"]

        # svelte-check as npm ci installed it from package-lock.json, through
        # the check script: npm run takes it from node_modules/.bin. `npx -y
        # svelte-check@<version>` resolved its dependencies afresh in every
        # run, and npx downloads a program that is missing there.
        assert commands == ["npm ci", "make frontend", "npm audit --omit=dev --audit-level=high"]
        # make frontend runs both, also when the type check fails (ADR-0190).
        dry_run = _make_dry_run("frontend", "NPM=npm").splitlines()
        assert {"cd webui && npm run check", "cd webui && npm run build"} <= set(dry_run)
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

    def test_every_action_is_pinned_to_a_commit_with_the_version_of_its_release(self):
        # A tag such as v7 can be moved to other code by whoever controls the action's repository,
        # and the next run takes it with the job's token. Every workflow, not only test.yml.
        assert [path.name for path in WORKFLOWS] == ["audit.yml", "test.yml"]
        for path in WORKFLOWS:
            text = path.read_text()
            jobs = yaml.safe_load(text)["jobs"]
            parsed = sorted([step["uses"] for job in jobs.values() for step in job.get("steps", []) if "uses" in step]
                            + [job["uses"] for job in jobs.values() if "uses" in job])
            found = sorted(uses[1] for uses in map(USES_LINE.match, text.splitlines()) if uses)

            # The line search sees every action that GitHub runs: none written in another form.
            assert parsed and found == parsed, path.name
            assert _unpinned_actions(text) == [], path.name

    def test_the_pin_check_refuses_a_tag_a_short_sha_and_a_missing_version(self):
        sha = "3d3c42e5aac5ba805825da76410c181273ba90b1"
        pinned = (f"      - uses: actions/checkout@{sha} # v7.0.1\n"
                  f"        uses: docker/setup-buildx-action@{sha} # v4.4.1\n"
                  f"      - uses: github/codeql-action/init@{sha} # v4.31.2\n")

        assert _unpinned_actions(pinned) == []
        for line in (
            "      - uses: actions/checkout@v7",  # a tag only
            "      - uses: actions/checkout@v7.0.1 # v7.0.1",  # a full tag, even with the comment
            f"      - uses: actions/checkout@{sha}",  # no version
            f"      - uses: actions/checkout@{sha} # latest",  # a comment without a version
            f"      - uses: actions/checkout@{sha} # v7",  # only the major version
            f"      - uses: actions/checkout@{sha[:12]} # v7.0.1",  # a short SHA
            f"      - uses: actions/checkout@{sha.upper()} # v7.0.1",  # not as git writes it
            "      - uses: actions/checkout@main # v7.0.1",  # a branch
            f"      - uses: 'actions/checkout@{sha}' # v7.0.1",  # quoted
            f"      - uses: ./.github/actions/local@{sha} # v1.0.0",  # a local action
        ):
            assert _unpinned_actions(f"steps:\n{line}\n") == [line.strip()], line

    def test_an_action_has_the_same_pin_in_every_workflow(self):
        # Dependabot moves them as one group; a pin left behind would run older code in one job.
        pins = {}
        for path in WORKFLOWS:
            for uses in map(USES_LINE.match, path.read_text().splitlines()):
                if uses:
                    action, commit = uses[1].split("@")
                    pins.setdefault(action, set()).add((commit, uses[2].strip()))

        assert set(pins) == {"actions/checkout", "actions/setup-python", "actions/setup-node",
                             "docker/setup-buildx-action"}
        assert {action: len(found) for action, found in pins.items()} == {action: 1 for action in pins}

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
        # The script runs Playwright from node_modules/.bin (TestTestCycle). The Playwright
        # commands of the guide: the search finds what it looks for.
        assert npx["scripts/test-cycle.sh"] == []
        assert "playwright" in npx["docs/TESTING.md"]
        assert {where: [program for program in found if program not in programs] for where, found in npx.items()} == {
            where: [] for where in texts
        }
        assert {where: {"svelte-kit", "svelte-check"} & set(found) for where, found in npx.items()} == {
            where: set() for where in texts
        }
        assert check == ["svelte-kit", "svelte-check"]
        assert set(check) <= programs


# Stand-ins for the tools of scripts/audit-python.sh. PYTHON answers the version check and makes
# the environment, whose python fakes pip and runs the conversion with the real Python; pip's
# dry-run writes the report $REPORT. Each logs its call to $LOG, the -c code left out.
AUDIT_FAKES = {
    "python3.11": """
        case "$1" in
            -c) echo "python -c" >> "$LOG"; echo "$FAKE_VERSION" ;;
            -m) echo "python $*" >> "$LOG"; [ "$2" = venv ] && mkdir -p "$3/bin" && cp "$ENV_PYTHON" "$3/bin/python" ;;
            *) exit 9 ;;
        esac
    """,
    "env-python": """
        if [ "$1" = -c ]; then echo "env-python -c ${*:3}" >> "$LOG"; exec "$REAL_PYTHON" "$@"; fi
        echo "env-python $*" >> "$LOG"
        [ "$1 $2 $3" = "-m pip install" ] || exit 9
        if [ -n "$FAIL_ON" ]; then case " $* " in *" $FAIL_ON "*) exit 1 ;; esac; fi
        while [ $# -gt 0 ]; do
            if [ "$1" = --report ]; then cp "$REPORT" "$2"; fi
            shift
        done
    """,
    "pip-audit": """
        echo "pip-audit $*" >> "$LOG"
        cat "${@: -1}" >> "$LOG"
        exit "${AUDIT_STATUS:-0}"
    """,
}
RESOLVED = {"install": [{"metadata": {"name": "fastapi", "version": "0.162.0"}},
                        {"metadata": {"name": "nut2", "version": "2.1.1"}}]}


class TestAuditEnvironment:
    """scripts/audit-python.sh, the Python part of make audit and of the CI audits: pip-audit of the
    supervisor's runtime dependencies, resolved by pinned, hashed tools (ADR-0423)."""

    def _run(self, tmp_path, report=RESOLVED, version="3.11", fail_on="", audit_status=0):
        bin_dir, tmp = tmp_path / "bin", tmp_path / "tmp"
        bin_dir.mkdir()
        tmp.mkdir()
        for name, body in AUDIT_FAKES.items():
            (bin_dir / name).write_text("#!/bin/bash\n" + textwrap.dedent(body).strip() + "\n")
            (bin_dir / name).chmod(0o755)
        (tmp_path / "report.json").write_text(json.dumps(report))
        (tmp_path / "log").write_text("")
        result = subprocess.run(
            ["bash", str(AUDIT_SCRIPT)], cwd=tmp_path, capture_output=True, text=True, timeout=60, env={
                **os.environ, "PYTHON": str(bin_dir / "python3.11"), "PIP_AUDIT": str(bin_dir / "pip-audit"),
                "ENV_PYTHON": str(bin_dir / "env-python"), "REAL_PYTHON": sys.executable, "TMPDIR": str(tmp),
                "LOG": str(tmp_path / "log"), "REPORT": str(tmp_path / "report.json"), "FAKE_VERSION": version,
                "FAIL_ON": fail_on, "AUDIT_STATUS": str(audit_status),
            })
        return result, (tmp_path / "log").read_text().splitlines(), sorted(os.listdir(tmp))

    def test_the_environment_pins_pip_setuptools_and_wheel_with_their_hashes(self):
        requirements = _hashed_requirements(AUDIT_ENV.read_text())

        # wheel depends on packaging, and pip's hash-checking mode wants every package it installs.
        assert sorted(name for name, _, _, _ in requirements) == ["packaging", "pip", "setuptools", "wheel"]
        assert [name for name, version, markers, hashes in requirements
                if not re.fullmatch(r"\d+(\.\d+)+", version) or markers or not hashes] == []

    def test_its_pip_and_packaging_are_those_of_the_lint_tools(self):
        # One version of each among the check tools; the hashes that uv wrote for the lint tools
        # confirm those of the hand-written file (pip checks setuptools' and wheel's).
        audit = {name: (version, set(hashes)) for name, version, _, hashes in _hashed_requirements(AUDIT_ENV.read_text())}
        lint = {name: (version, set(hashes)) for name, version, _, hashes in
                _hashed_requirements((REPO_ROOT / ".github" / "lint-requirements.txt").read_text())}

        for name in ("pip", "packaging"):
            assert audit[name][0] == lint[name][0], name
            assert audit[name][1] <= lint[name][1], name

    def test_it_resolves_with_the_pinned_tools_and_audits_without_pip(self, tmp_path):
        result, calls, left = self._run(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        version, venv, tools, resolve, convert, audit, *resolved = calls
        work = venv.removeprefix("python -m venv ").removesuffix("/env")
        quiet = "--disable-pip-version-check --quiet --no-input"
        assert version == "python -c"
        assert Path(work).parent == tmp_path / "tmp"
        # The pinned tools with their hashes, wheels only: no newer pip, setuptools or wheel.
        assert tools == f"env-python -m pip install {quiet} --require-hashes --only-binary=:all: -r {AUDIT_ENV}"
        # The resolution of pip-audit -r, with that pip: every package, also one the environment
        # has; wheels only but nut2, whose sdist the pinned setuptools builds, not one from the index.
        assert resolve == (f"env-python -m pip install {quiet} --dry-run --ignore-installed --no-build-isolation "
                           f"--only-binary=:all: --no-binary=nut2 --report {work}/report.json "
                           f"-r {REPO_ROOT / 'supervisor' / 'requirements.txt'}")
        assert convert == f"env-python -c {work}/report.json"
        assert audit == f"pip-audit --disable-pip --no-deps --strict -r {work}/resolved.txt"
        assert resolved == ["fastapi==0.162.0", "nut2==2.1.1"]
        assert left == []

    @pytest.mark.parametrize("fail_on, steps", [("--require-hashes", 3), ("--dry-run", 4)])
    def test_a_failed_step_stops_before_the_audit(self, tmp_path, fail_on, steps):
        result, calls, left = self._run(tmp_path, fail_on=fail_on)

        assert result.returncode != 0
        assert len(calls) == steps and fail_on in calls[-1]
        assert left == []

    def test_a_resolution_without_packages_is_no_pass(self, tmp_path):
        result, calls, left = self._run(tmp_path, report={"install": []})

        assert result.returncode != 0
        assert "no package" in result.stderr
        assert not [call for call in calls if call.startswith("pip-audit")]
        assert left == []

    def test_a_finding_fails_it(self, tmp_path):
        result, calls, left = self._run(tmp_path, audit_status=1)

        assert result.returncode == 1
        assert calls[5].startswith("pip-audit ")
        assert left == []

    def test_it_resolves_for_the_python_of_the_image_only(self, tmp_path):
        # Another Python resolves other versions (markers, wheels): an audit of what is not installed.
        result, calls, left = self._run(tmp_path, version="3.15")

        assert result.returncode != 0
        assert "3.11" in result.stderr and "3.15" in result.stderr
        assert calls == ["python -c"] and left == []


def _empty_distributions(folder, name, version):
    """A wheel and an sdist of an empty package `name` in `folder`, for pip's --find-links."""
    import tarfile
    import zipfile

    info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(folder / f"{name}-{version}-py3-none-any.whl", "w") as wheel:
        wheel.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        wheel.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(f"{info}/RECORD", f"{info}/METADATA,,\n{info}/WHEEL,,\n{info}/RECORD,,\n")
    source = folder / f"{name}-{version}"
    source.mkdir()
    (source / "setup.py").write_text(f"from setuptools import setup\nsetup(name={name!r}, version={version!r}, py_modules=[])\n")
    (source / "PKG-INFO").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    with tarfile.open(folder / f"{name}-{version}.tar.gz", "w:gz") as sdist:
        sdist.add(source, arcname=source.name)
    shutil.rmtree(source)


class TestWheelsOnly:
    """pip installs take wheels only (ADR-0423): make setup, the CI jobs, the test image and the audit
    environment. nut2 publishes only its sdist, which their command lines name after
    --only-binary=:all:. The supervisor's image is left to pip's default, which with exact pins takes
    a wheel wherever the pinned version has one: its pip layer, a hundred MB on the dev device's SD
    card (ADR-0214), stays as it was."""

    # The one package without a wheel, after the option that it narrows: --only-binary=:all: after
    # it would clear it.
    WHEELS_ONLY = "--only-binary=:all: --no-binary=nut2"

    def test_the_image_keeps_its_pip_layer_and_pips_default(self):
        runtime = (REPO_ROOT / "supervisor" / "requirements.txt").read_text()
        dev = (REPO_ROOT / "supervisor" / "requirements-dev.txt").read_text()
        lines = [line.strip() for line in (REPO_ROOT / "supervisor" / "Dockerfile").read_text().splitlines()]

        # The image copies requirements.txt before its pip layer: an option there changes the layer.
        assert [line for line in runtime.splitlines() if line.startswith("-")] == []
        assert re.search(r"^nut2==", runtime, re.M)
        assert [line for line in dev.splitlines() if line.startswith("-")] == ["-r requirements.txt"]
        assert "binary" not in _instruction(lines, _pip_layer(lines))

    def test_every_install_of_the_supervisors_requirements_lets_nut2_come_as_its_sdist(self):
        texts = {path.name: path.read_text() for path in WORKFLOWS}
        for path in ("docs/TESTING.md", "supervisor/Dockerfile.test", "scripts/audit-python.sh"):
            texts[path] = (REPO_ROOT / path).read_text()
        # scripts/audit-python.sh writes its resolution over two lines.
        texts["scripts/audit-python.sh"] = texts["scripts/audit-python.sh"].replace("\\\n", " ")
        installs = {where: [line for line in re.findall(r"\bpip install\b[^\n`]*", text)
                            if re.search(r'-r\s+"?(?:\$root/)?(?:supervisor/)?requirements(?:-dev)?\.txt\b', line)]
                    for where, text in texts.items()}

        assert [where for where, found in installs.items() if not found] == []
        assert {where: [line for line in found if self.WHEELS_ONLY not in line] for where, found in installs.items()} == {
            where: [] for where in texts
        }

    def test_pip_takes_nut2_as_its_sdist_and_every_other_package_as_a_wheel(self, tmp_path):
        # With the pip of this Python and no index, each package offered as a wheel and an sdist:
        # the command line's --only-binary=:all: --no-binary=nut2 leaves nut2 its sdist and gives
        # every other package of the requirement files, read after it, through requirements-dev.txt
        # too, a wheel; and --no-build-isolation builds nut2 with the setuptools of the environment,
        # as the audit's resolution does (its flags, which TestAuditEnvironment checks).
        pytest.importorskip("setuptools")
        index = tmp_path / "index"
        index.mkdir()
        for name in ("alpha", "beta", "nut2"):
            _empty_distributions(index, name, "1.0")
        options = {
            name: [line for line in (REPO_ROOT / "supervisor" / name).read_text().splitlines() if line.startswith("-")]
            for name in ("requirements.txt", "requirements-dev.txt")
        }
        (tmp_path / "requirements.txt").write_text("\n".join(options["requirements.txt"] + ["alpha==1.0", "nut2==1.0"]) + "\n")
        (tmp_path / "requirements-dev.txt").write_text("\n".join(options["requirements-dev.txt"] + ["beta==1.0"]) + "\n")

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--isolated", "--disable-pip-version-check", "--quiet", "--no-input",
             "--no-cache-dir", "--no-index", "--find-links", str(index), "--dry-run", "--ignore-installed",
             "--no-build-isolation", *self.WHEELS_ONLY.split(), "--report", str(tmp_path / "report.json"),
             "-r", str(tmp_path / "requirements-dev.txt")],
            capture_output=True, text=True, timeout=120,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads((tmp_path / "report.json").read_text())
        assert {item["metadata"]["name"]: Path(item["download_info"]["url"]).name for item in report["install"]} == {
            "alpha": "alpha-1.0-py3-none-any.whl", "beta": "beta-1.0-py3-none-any.whl", "nut2": "nut2-1.0.tar.gz",
        }

    def test_every_pip_install_of_the_workflows_the_guide_and_the_test_image_takes_wheels_only(self):
        texts = {path.name: path.read_text() for path in WORKFLOWS}
        for path in ("docs/TESTING.md", "supervisor/Dockerfile.test"):
            texts[path] = (REPO_ROOT / path).read_text()
        installs = {where: re.findall(r"\bpip install\b[^\n`]*", text) for where, text in texts.items()}

        assert [where for where, found in installs.items() if not found] == []
        assert {where: [line for line in found if "--only-binary=:all:" not in line]
                for where, found in installs.items()} == {where: [] for where in texts}

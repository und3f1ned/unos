"""deploy/update.sh, the test compose file and the image build contexts.

update.sh runs in a sandbox: a local git "origin", a clone as the install
directory, and stub docker/curl commands on PATH that log their calls. git is
logged too, and flock is a stand-in that takes a real lock. Nothing reaches
Docker, the network or the host.
"""

import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]

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
        case "$2" in
          build) interrupt_at build; has probe-lock && probe_lock; has build-fails && exit 1 ;;
          down) interrupt_at down ;;
          up) [[ " $* " == *" --force-recreate "* ]] && touch "$STUB_DIR/rolled-back"
              has probe-lock && probe_lock
              # A process that outlives the update, as a detached one does.
              if has leave-process; then sleep 60 > /dev/null 2>&1 & echo $! > "$STUB_DIR/leftover"; fi ;;
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


def _run_update(sandbox, *scenario, **extra_env):
    (sandbox / "stub" / "scenario").write_text("".join(f"{s}\n" for s in scenario))
    env = {
        **os.environ,
        "PATH": f"{sandbox / 'bin'}:{os.environ['PATH']}",
        "STUB_DIR": str(sandbox / "stub"),
        "UNOS_INSTALL_DIR": str(sandbox / "install"),
        "UNOS_HEALTH_TIMEOUT": "0",
        "LIB": str(sandbox / "update-lib.sh"),
        "BACKUPS": str(sandbox / "backups"),
        "LOCK": str(sandbox / "update.lock"),
        "PYTHON": sys.executable,
        "REAL_GIT": shutil.which("git"),
        **extra_env,
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'echo $$ > "$STUB_DIR/pgid"; . "$LIB"; check_root() { :; }; BACKUP_DIR="$BACKUPS"; LOCK_FILE="$LOCK"; main',
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

    def test_health_timeout_must_be_a_number(self, sandbox):
        before = _head(sandbox)

        result, calls = _run_update(sandbox, UNOS_HEALTH_TIMEOUT="5m")

        assert result.returncode == 1
        assert "UNOS_HEALTH_TIMEOUT" in result.stdout
        assert calls == []
        assert _head(sandbox) == before

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
            assert "docker compose up -d --no-build" in calls
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
        assert "docker compose up -d --no-build" in calls
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

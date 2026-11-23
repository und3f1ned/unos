"""scripts/dev, the dev device's tooling, tested without the device and without dev.env.

Each test writes its own dev.env in tmp_path and points UNOS_DEV_ENV_FILE at it. A stub ssh
on PATH stands in for the device: it records its command line, environment and standard
input, asks SSH_ASKPASS for the password like ssh does, answers like the device, or runs the
remote command here (with stubs for unshare and mount) to exercise the device lock.

Covered: every shell script and every script it runs on the device parses (bash -n, also
with bash 3.2 where /bin/bash is that); env.sh refuses a dev.env whose mode is not 600,
explains a missing one, takes values literally and keeps secrets out of the environment, and
in a linked git worktree without a dev.env uses the main checkout's (UNOS_DEV_ENV_FILE still
wins); askpass.sh answers only the device's password prompt; no secret reaches ssh's command
line or environment; sd.sh measures; the lock is taken, reported and released; check.sh shows
every container's log configuration, redacted, and no host name; deploy.sh up waits longer
than Docker takes to decide the containers' health; deploy.sh backup (its device side run
here with stubs for docker and GNU tar) writes a private, complete backup, prints nothing of
it and keeps the newest 3; api.py redacts secrets in what it prints; its relay, run here
against a stand-in API, keeps the token in a private cache, reuses it, signs in once after a
401, never reads a cache that is not its own private file, calls only a loopback address and
deletes the cache on --forget-token; tree.py's manifests and comparisons on fixture trees,
its git listing despite a hook's GIT_* variables, and status, sync and rollback end to end
against a fixture "device checkout"; immich-ml-probe.py, run as python3 -I -B - against a
stand-in of Immich's machine learning, sends Immich's requests for its default models, prints
status, timings and the answers' shapes without an embedding's numbers or control characters,
and writes nothing; a static scan finds no secret put on a command line by any script; and the
two device skills say what the scripts do (which lock, which report SD
writes) and run every other command on the device under sd.sh measure.

git exports GIT_INDEX_FILE (and more) to its hooks, and the pre-commit hook runs these tests:
every test runs without the GIT_* variables, and the fixtures' git commands get an environment
without them, so that they cannot write to the outer repository's index.
"""

import ast
import contextlib
import email.parser
import email.policy
import fcntl
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import textwrap
import threading
import time
import types
import zlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV = REPO_ROOT / "scripts" / "dev"
SHELL_SCRIPTS = sorted(
    p for p in DEV.rglob("*") if p.is_file() and p.suffix != ".py" and p.read_bytes().startswith((b"#!/usr/bin/env bash", b"# shellcheck shell=bash"))
)
PYTHON_SCRIPTS = sorted(DEV.rglob("*.py"))
SECRET_NAME = re.compile(r"PASS|TOKEN|SECRET|API_KEY|PRIVATE_KEY")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture(autouse=True)
def no_outer_git_environment(monkeypatch):
    """Without GIT_INDEX_FILE, GIT_DIR and the rest that a git hook exports: with `git commit -a`
    GIT_INDEX_FILE names the outer repository's index, and `git add` in a fixture would write
    the fixture's files into the commit being made."""
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)


def without_git_variables() -> dict[str, str]:
    """os.environ without GIT_DIR, GIT_INDEX_FILE and the other GIT_* variables of an outer git
    (a hook, `git rebase -x`), for the git commands of the tests."""
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def bashes() -> list[str]:
    """bash on PATH and, when it is another bash (macOS: 3.2), /bin/bash."""
    found = []
    for candidate in (shutil.which("bash"), "/bin/bash"):
        if candidate and os.path.exists(candidate) and os.path.realpath(candidate) not in {os.path.realpath(b) for b in found}:
            found.append(candidate)
    return found


def load(name: str) -> types.ModuleType:
    """A script of scripts/dev as a module, without writing bytecode next to it."""
    path = DEV / name
    module = types.ModuleType("scripts_dev_" + path.stem)
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


api = load("api.py")
tree = load("tree.py")


class Device:
    """A fixture dev.env and a stub ssh, for running the scripts in tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.log = tmp_path / "ssh-log"
        self.bin.mkdir()
        self.log.mkdir()
        self.ssh_password = "ssh-" + secrets.token_hex(6)
        # Characters a shell would act on: env.sh must take the value literally.
        self.admin_password = "ad$min'\"pw " + secrets.token_hex(4)
        self.tx_password = "tx-" + secrets.token_hex(6)
        self.env_file = tmp_path / "dev.env"
        self.write_env()
        self.lock = tmp_path / "unos-dev.lock"
        self.stub("ssh", STUB_SSH)

    def write_env(self, extra: str = "", mode: int = 0o600, ssh_password: str | None = None) -> None:
        pw = self.ssh_password if ssh_password is None else ssh_password
        self.env_file.write_text(
            "# fixture\n"
            "UNOS_DEV_HOST=10.0.0.9\n"
            "UNOS_DEV_SSH_USER=root\n"
            f"UNOS_DEV_SSH_PASSWORD={pw}\n"
            "UNOS_DEV_REPO=/opt/unos\n"
            "UNOS_DEV_SYSTEM_DISK=mmcblk1\n"
            "UNOS_DEV_ADMIN_USER=admin\n"
            f"UNOS_DEV_ADMIN_PASSWORD={self.admin_password}\n"
            "TRANSMISSION_USER=tx\n"
            f"TRANSMISSION_PASS={self.tx_password}\n" + extra
        )
        os.chmod(self.env_file, mode)

    @property
    def secrets(self) -> list[str]:
        return [self.ssh_password, self.admin_password, self.tx_password]

    def stub(self, name: str, source: str) -> None:
        path = self.bin / name
        path.write_text(source.replace("@PYTHON@", sys.executable))
        path.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.tmp),
            "TMPDIR": str(self.tmp),
            "LANG": "C",
            "UNOS_DEV_ENV_FILE": str(self.env_file),
            "UNOS_DEV_LOCK": str(self.lock),
            "UNOS_DEV_STATE_DIR": str(self.tmp / "state"),
            "FAKE_SSH_LOG": str(self.log),
        }
        env.update(extra)
        return env

    def bash(self, script: str, bash: str = "bash", **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [bash, "-c", script], cwd=DEV, env=self.env(**extra), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
        )

    def run(self, *argv: str, input: str | None = None, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(a) for a in argv], cwd=REPO_ROOT, env=self.env(**extra), capture_output=True, text=True,
            input=input, stdin=None if input is not None else subprocess.DEVNULL, timeout=60,
        )


# The stub ssh. FAKE_SSH_MODE: record (default) records argv, the environment and standard
# input, asks SSH_ASKPASS like ssh, and prints FAKE_SSH_LOG/reply; sectors answers growing
# sector counts and, as ssh without -n, reads all of its standard input; local runs the remote
# command here with bash.
STUB_SSH = textwrap.dedent(
    """\
    #!@PYTHON@
    import os, subprocess, sys
    log = os.environ["FAKE_SSH_LOG"]
    args = sys.argv[1:]
    mode = os.environ.get("FAKE_SSH_MODE", "record")
    command = " ".join(args[args.index("--") + 2:]) if "--" in args else ""
    if mode == "local":
        os.execvp("bash", ["bash", "-c", command])
    if mode == "sectors":
        if "-n" not in args[:args.index("--")]:
            sys.stdin.buffer.read()
        path = os.path.join(log, "sectors")
        n = int(open(path).read()) if os.path.exists(path) else 1000
        open(path, "w").write(str(n + 100))
        print(n)
        sys.exit(0)
    with open(os.path.join(log, "argv"), "a") as f:
        f.write("\\0".join(args) + "\\n")
    with open(os.path.join(log, "env"), "a") as f:
        f.write("\\0".join(f"{k}={v}" for k, v in os.environ.items()) + "\\n")
    if os.environ.get("SSH_ASKPASS"):
        dest = args[args.index("--") + 1]
        answer = subprocess.run([os.environ["SSH_ASKPASS"], f"{dest}'s password: "], capture_output=True, text=True)
        with open(os.path.join(log, "askpass"), "w") as f:
            f.write(answer.stdout)
    data = sys.stdin.buffer.read()
    with open(os.path.join(log, "stdin"), "ab") as f:
        f.write(data)
    reply = os.path.join(log, "reply")
    if os.path.exists(reply):
        sys.stdout.write(open(reply).read())
    """
)

# Stand-ins for running the lock's remote side here: no mount namespace, no remount.
STUB_UNSHARE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    while [ $# -gt 0 ]; do
        case $1 in
            --propagation) shift 2 ;;
            --*) shift ;;
            *) break ;;
        esac
    done
    exec "$@"
    """
)
STUB_MOUNT = "#!/usr/bin/env bash\nexit 0\n"
# flock FD with -n or -w SECONDS, as util-linux's (macOS has none): the lock belongs to the
# open file shared with the calling shell, so it stays after this process exits.
STUB_FLOCK = textwrap.dedent(
    """\
    #!@PYTHON@
    import fcntl, sys, time
    args = sys.argv[1:]
    wait = 0.0
    if args[0] == "-n":
        args = args[1:]
    elif args[0] == "-w":
        wait, args = float(args[1]), args[2:]
    fd, deadline = int(args[0]), time.time() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sys.exit(0)
        except BlockingIOError:
            if time.time() >= deadline:
                sys.exit(1)
            time.sleep(0.1)
    """
)


@pytest.fixture
def device(tmp_path):
    return Device(tmp_path)


# --- the scripts parse ----------------------------------------------------------------------


def test_the_scripts_are_found():
    names = {p.relative_to(DEV).as_posix() for p in SHELL_SCRIPTS + PYTHON_SCRIPTS}
    assert {"env.sh", "ssh.sh", "askpass.sh", "sd.sh", "deploy.sh", "check.sh", "linux-tests.sh",
            "ipv6-filter.sh", "scenario.sh", "bin/testparm", "api.py", "tree.py", "scenario.py"} <= names


@pytest.mark.parametrize("bash", bashes())
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.relative_to(DEV).as_posix())
def test_every_shell_script_parses(script, bash):
    result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", PYTHON_SCRIPTS, ids=lambda p: p.name)
def test_python_helpers_parse_as_python_3_9(script):
    # The device runs tree.py with its own python3; a workstation may have an old one.
    ast.parse(script.read_text(), filename=str(script), feature_version=(3, 9))


@pytest.mark.parametrize("script", SHELL_SCRIPTS + PYTHON_SCRIPTS, ids=lambda p: p.relative_to(DEV).as_posix())
def test_scripts_are_executable_except_env_sh(script):
    if script.name == "env.sh":
        assert not os.access(script, os.X_OK), "env.sh is sourced, not run"
        return
    assert os.access(script, os.X_OK)
    first = script.read_text().splitlines()[0]
    assert first in ("#!/usr/bin/env bash", "#!/usr/bin/env python3")


@pytest.mark.parametrize(
    "argv",
    [["deploy.sh", "--help"], ["sd.sh", "--help"], ["check.sh", "--help"], ["linux-tests.sh", "--help"],
     ["ssh.sh", "--help"], ["api.py", "--help"], ["tree.py", "--help"], ["ipv6-filter.sh", "--help"],
     ["scenario.sh", "--help"], ["scenario.py", "--help"], ["immich-ml-probe.py", "--help"]],
    ids=lambda a: a[0],
)
def test_help_needs_no_dev_env(device, argv):
    device.env_file.unlink()
    result = device.run(DEV / argv[0], *argv[1:])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_deploy_sh_refuses_unknown_steps_without_contacting_anything(device):
    for argv in (["bogus"], ["sync", "--force"], ["up", "everything"], []):
        result = device.run(DEV / "deploy.sh", *argv)
        assert result.returncode == 2, argv
    assert not any(device.log.iterdir())


# The scripts that run on the device are strings in these files, defined before "# --- main".
REMOTE_SCRIPTS = {"deploy.sh": ["status"], "check.sh": [], "linux-tests.sh": [], "ipv6-filter.sh": ["status"],
                  "bin/testparm": ["-s", "--suppress-prompt", "FILE"]}


@pytest.mark.parametrize("bash", bashes())
@pytest.mark.parametrize("name", sorted(REMOTE_SCRIPTS))
def test_the_scripts_for_the_device_parse(device, name, bash):
    """Load a script's definitions only (up to "# --- main", with env.sh by its absolute
    path), run bash -n on every REMOTE_* script, DEV_RO_ROOT, the lock's remote side and the
    SD measurement, and compile the Python embedded in them."""
    source = (DEV / name).read_text()
    assert "\n# --- main\n" in source
    definitions = source.split("\n# --- main\n", 1)[0]
    definitions = definitions.replace('. "$(dirname "${BASH_SOURCE[0]}")/env.sh"', f'. "{DEV}/env.sh"')
    definitions = definitions.replace('. "$here/env.sh"', f'. "{DEV}/env.sh"')
    probe = device.tmp / "definitions.sh"
    probe.write_text(definitions)
    script = (
        '. "$0" || exit 9; for v in $(compgen -v REMOTE_) DEV_RO_ROOT dev__lock_script dev__sd_script; do '
        'echo "$v"; "$BASH" -n <<< "${!v}" || { echo "SYNTAX $v"; exit 8; }; done'
    )
    args = [str(probe) if a == "FILE" else a for a in REMOTE_SCRIPTS[name]]
    result = subprocess.run([bash, "-c", script, str(probe), *args], cwd=DEV, env=device.env(),
                            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    checked = result.stdout.split()
    assert "DEV_RO_ROOT" in checked and len(checked) >= 3, checked
    for heredoc in re.findall(r'<< "PY"[^\n]*\n(.*?)\nPY\n', source, flags=re.S):
        compile(heredoc, f"{name}: python", "exec")


# --- env.sh ---------------------------------------------------------------------------------


@pytest.mark.parametrize("bash", bashes())
def test_env_sh_explains_a_missing_dev_env(device, bash):
    device.env_file.unlink()
    result = device.bash('. ./env.sh || exit 9; echo loaded', bash=bash)
    assert result.returncode == 9
    assert "loaded" not in result.stdout
    assert str(device.env_file) in result.stderr
    assert "dev.env.example" in result.stderr and "chmod 600" in result.stderr and "make setup" in result.stderr
    # The passwords are the owner's to fill in (ADR-0024), not an agent's.
    assert "the owner fills in the passwords" in result.stderr


@pytest.mark.parametrize("bash", bashes())
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o700, 0o400])
def test_env_sh_refuses_a_dev_env_whose_mode_is_not_600(device, bash, mode):
    device.write_env(mode=mode)
    result = device.bash('. ./env.sh || exit 9; echo loaded', bash=bash)
    assert result.returncode == 9
    assert "loaded" not in result.stdout
    assert f"mode {mode:o}" in result.stderr and f"chmod 600 {device.env_file}" in result.stderr
    for value in device.secrets:
        assert value not in result.stdout + result.stderr


@pytest.mark.parametrize("bash", bashes())
def test_env_sh_loads_settings_and_keeps_secrets_out_of_the_environment(device, bash):
    device.write_env(extra='export UNOS_DEV_LOCK_WAIT=5\r\n  # indented comment\n\nUNOS_DEV_KNOWN_HOSTS="/x y/known_hosts"\n')
    script = """
        . ./env.sh || exit 9
        printf '%s|' "$UNOS_DEV_HOST" "$UNOS_DEV_SSH_USER" "$UNOS_DEV_REPO" "$UNOS_DEV_SYSTEM_DISK" \
            "$UNOS_DEV_ADMIN_USER" "$TRANSMISSION_USER" "$UNOS_DEV_LOCK_WAIT" "$UNOS_DEV_KNOWN_HOSTS"
        echo
        env | grep -c -e PASS -e SECRET -e TOKEN || true
        set | grep -c -e '^UNOS_DEV_SSH_PASSWORD=' -e '^UNOS_DEV_ADMIN_PASSWORD=' -e '^TRANSMISSION_PASS=' || true
        dev_secret UNOS_DEV_SSH_PASSWORD
        dev_secret UNOS_DEV_ADMIN_PASSWORD
        dev_secret TRANSMISSION_PASS
    """
    result = device.bash(script, bash=bash)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "10.0.0.9|root|/opt/unos|mmcblk1|admin|tx|5|/x y/known_hosts|"
    assert lines[1:3] == ["0", "0"], "a secret is in the environment or in a shell variable"
    assert lines[3:] == device.secrets


def test_dev_secret_is_only_for_secrets_and_fails_on_an_empty_value(device):
    device.write_env(ssh_password="")
    result = device.bash(". ./env.sh || exit 9; dev_secret UNOS_DEV_SSH_PASSWORD; echo rc=$?; "
                         "dev_has_secret UNOS_DEV_SSH_PASSWORD; echo has=$?; dev_secret UNOS_DEV_HOST; echo host=$?")
    assert result.stdout.split() == ["rc=1", "has=1", "host=2"]
    assert "not a secret's name" in result.stderr


def test_the_environment_wins_over_dev_env_for_settings(device):
    result = device.bash('. ./env.sh || exit 9; echo "$UNOS_DEV_REPO $UNOS_DEV_HOST"', UNOS_DEV_REPO="/srv/unos")
    assert result.stdout.split() == ["/srv/unos", "10.0.0.9"]


def test_env_sh_reports_a_bad_line_by_number_only(device):
    device.write_env(extra=f"this line holds {device.tx_password}\n")
    result = device.bash(". ./env.sh || exit 9")
    assert result.returncode == 9
    assert "line 11 is not NAME=value" in result.stderr
    assert device.tx_password not in result.stderr


def test_env_sh_refuses_a_repeated_name(device):
    device.write_env(extra="UNOS_DEV_HOST=10.0.0.10\n")
    result = device.bash(". ./env.sh || exit 9")
    assert result.returncode == 9 and "repeats UNOS_DEV_HOST" in result.stderr


@pytest.mark.parametrize(
    "line", ["UNOS_DEV_HOST=10.0.0.9;reboot", "UNOS_DEV_REPO=opt/unos", "UNOS_DEV_REPO=/opt/../etc",
             "UNOS_DEV_SYSTEM_DISK=sda;x", "UNOS_DEV_SSH_USER=Root$x", "UNOS_DEV_LOCK_WAIT=10s"],
)
def test_env_sh_refuses_values_that_would_reach_a_remote_command(device, line):
    name = line.split("=", 1)[0]
    text = device.env_file.read_text()
    text = re.sub(rf"^{name}=.*\n", "", text, flags=re.M) + line + "\n"
    device.env_file.write_text(text)
    result = device.bash(". ./env.sh || exit 9")
    assert result.returncode == 9, result.stdout
    assert name in result.stderr


def test_dev_env_example_names_every_setting_with_empty_secrets(device):
    example = (REPO_ROOT / "dev.env.example").read_text()
    names = re.findall(r"^([A-Z_][A-Z0-9_]*)=(.*)$", example, flags=re.M)
    assert [n for n, _ in names] == [
        "UNOS_DEV_HOST", "UNOS_DEV_SSH_USER", "UNOS_DEV_SSH_PASSWORD", "UNOS_DEV_REPO", "UNOS_DEV_SYSTEM_DISK",
        "UNOS_DEV_ADMIN_USER", "UNOS_DEV_ADMIN_PASSWORD", "TRANSMISSION_USER", "TRANSMISSION_PASS",
    ]
    assert all(value == "" for name, value in names if SECRET_NAME.search(name))
    # A copy works as it is (make setup copies it with mode 600).
    shutil.copy(REPO_ROOT / "dev.env.example", device.env_file)
    os.chmod(device.env_file, 0o600)
    result = device.bash('. ./env.sh || exit 9; echo "$UNOS_DEV_HOST"')
    assert result.returncode == 0, result.stderr


def test_dev_env_example_measures_the_writes_of_the_bpi_w3s_emmc():
    """The dev device runs from its eMMC mmcblk0, and its SD card mmcblk1 is wiped (ADR-0470): a
    copy of the template counts the eMMC's writes, not those of the idle card."""
    example = (REPO_ROOT / "dev.env.example").read_text()
    assert re.findall(r"^UNOS_DEV_SYSTEM_DISK=(.*)$", example, flags=re.M) == ["mmcblk0"]


def test_git_ignores_dev_env_but_not_the_example():
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    env = without_git_variables()
    ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-q", "dev.env"], env=env)
    example = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-q", "dev.env.example"], env=env)
    assert ignored.returncode == 0 and example.returncode == 1


# --- env.sh in git worktrees ----------------------------------------------------------------


@pytest.fixture
def worktree(device):
    """A main checkout whose scripts/dev holds env.sh, and a linked worktree of it (git worktree
    add): parallel work happens in worktrees (ADR-0210), and git ignores dev.env, so a new
    worktree has none of its own."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    main, linked = device.tmp / "main", device.tmp / "linked"
    make_tree(main, {".gitignore": "*.env\ntmp/\n", "scripts/dev/env.sh": (DEV / "env.sh").read_text()})
    git_init(main)
    git(main, "worktree", "add", "-q", "-b", "feature", str(linked))
    return main, linked


def env_sh_in(device: Device, checkout: Path, bash: str = "bash", **extra: str) -> subprocess.CompletedProcess:
    """Source env.sh of CHECKOUT with the defaults of dev.env and the state directory (the
    fixture's environment sets both) and print what it chose."""
    env = device.env(**extra)
    for name in ("UNOS_DEV_ENV_FILE", "UNOS_DEV_STATE_DIR"):
        if name not in extra:
            del env[name]
    script = '. ./env.sh || exit 9; printf "%s\\n" "$UNOS_DEV_ENV_FILE" "$UNOS_DEV_HOST" "$UNOS_DEV_STATE_DIR"'
    return subprocess.run([bash, "-c", script], cwd=checkout / "scripts" / "dev", env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60)


def put_dev_env(device: Device, path: Path, host: str, mode: int = 0o600) -> None:
    path.write_text(device.env_file.read_text().replace("UNOS_DEV_HOST=10.0.0.9\n", f"UNOS_DEV_HOST={host}\n"))
    os.chmod(path, mode)


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


def mentions(text: str, path: Path) -> bool:
    """Whether a message names PATH (as given, or with its symlinks resolved, as git gives it)."""
    return str(path) in text or os.path.realpath(path) in text


@pytest.mark.parametrize("bash", bashes())
def test_a_linked_worktree_uses_the_main_checkouts_dev_env(device, worktree, bash):
    main, linked = worktree
    put_dev_env(device, main / "dev.env", "10.0.0.20")
    result = env_sh_in(device, linked, bash=bash)
    assert result.returncode == 0, result.stderr
    env_file, host, state = result.stdout.splitlines()
    assert same_path(env_file, main / "dev.env") and host == "10.0.0.20"
    # One record of the one device: the rollback copies and backups of every worktree go to the
    # main checkout's tmp/dev.
    assert same_path(state, main / "tmp" / "dev")
    assert same_path(env_sh_in(device, main, bash=bash).stdout.splitlines()[2], main / "tmp" / "dev")
    # The git variables that a hook exports do not lead it to another repository.
    other = device.tmp / "other"
    other.mkdir()
    git(other, "init", "-q")
    hooked = env_sh_in(device, linked, bash=bash, GIT_DIR=str(other / ".git"), GIT_INDEX_FILE=str(other / ".git" / "index"))
    assert hooked.returncode == 0 and same_path(hooked.stdout.splitlines()[0], main / "dev.env"), hooked.stderr


def test_a_worktrees_own_dev_env_and_unos_dev_env_file_win(device, worktree):
    main, linked = worktree
    put_dev_env(device, main / "dev.env", "10.0.0.20")
    put_dev_env(device, linked / "dev.env", "10.0.0.21")
    own = env_sh_in(device, linked)
    assert same_path(own.stdout.splitlines()[0], linked / "dev.env") and own.stdout.splitlines()[1] == "10.0.0.21"
    chosen = env_sh_in(device, linked, UNOS_DEV_ENV_FILE=str(device.env_file))
    assert chosen.stdout.splitlines()[:2] == [str(device.env_file), "10.0.0.9"], chosen.stderr
    # A file named by UNOS_DEV_ENV_FILE is not replaced by another one when it is missing.
    missing = env_sh_in(device, linked, UNOS_DEV_ENV_FILE=str(device.tmp / "missing.env"))
    assert missing.returncode == 9 and str(device.tmp / "missing.env") in missing.stderr
    assert "main checkout" not in missing.stderr


def test_the_main_checkouts_dev_env_is_held_to_the_same_rules(device, worktree):
    main, linked = worktree
    put_dev_env(device, main / "dev.env", "10.0.0.20", mode=0o644)
    result = env_sh_in(device, linked)
    assert result.returncode == 9 and "mode 644" in result.stderr and mentions(result.stderr, main / "dev.env")
    for value in device.secrets:
        assert value not in result.stdout + result.stderr


def test_without_any_dev_env_a_worktree_is_told_to_create_it_in_the_main_checkout(device, worktree):
    main, linked = worktree
    result = env_sh_in(device, linked)
    assert result.returncode == 9
    assert mentions(result.stderr, linked / "dev.env") and mentions(result.stderr, main / "dev.env")
    assert "Create it in the main checkout" in result.stderr and "UNOS_DEV_ENV_FILE" in result.stderr
    assert "dev.env.example" in result.stderr and "chmod 600" in result.stderr
    assert "the owner fills in the passwords" in result.stderr
    # The main checkout itself has no main checkout to point to.
    plain = env_sh_in(device, main)
    assert plain.returncode == 9 and mentions(plain.stderr, main / "dev.env") and "main checkout" not in plain.stderr


# --- ssh.sh and askpass.sh ------------------------------------------------------------------


def test_askpass_answers_only_the_device_password_prompt(device):
    askpass = DEV / "askpass.sh"
    ok = device.run(askpass, "root@10.0.0.9's password: ")
    assert ok.returncode == 0 and ok.stdout == device.ssh_password + "\n"
    for prompt in ("Enter passphrase for key '/home/u/.ssh/id_ed25519': ", "root@10.0.0.10's password: ",
                   "admin@10.0.0.9's password: ", "Are you sure you want to continue connecting (yes/no/[fingerprint])? ",
                   "(root@10.0.0.9) Password: ", ""):
        refused = device.run(askpass, prompt)
        assert refused.returncode == 1 and refused.stdout == "", prompt
        assert device.ssh_password not in refused.stderr


def test_ssh_sh_puts_no_secret_on_the_command_line_or_in_the_environment(device):
    result = device.run(DEV / "ssh.sh", "echo hello", input="data on stdin")
    assert result.returncode == 0, result.stderr
    argv = (device.log / "argv").read_text().rstrip("\n").split("\0")
    env = dict(item.split("=", 1) for item in (device.log / "env").read_text().rstrip("\n").split("\0"))
    assert argv[-3:] == ["--", "root@10.0.0.9", "echo hello"]
    assert "BatchMode=yes" not in argv and "StrictHostKeyChecking=accept-new" in argv
    assert os.path.realpath(env["SSH_ASKPASS"]) == os.path.realpath(DEV / "askpass.sh")
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    for value in device.secrets:
        assert not any(value in a for a in argv)
        assert not any(value in v for v in env.values())
    # ssh gets the password from askpass.sh, and standard input passes through.
    assert (device.log / "askpass").read_text() == device.ssh_password + "\n"
    assert (device.log / "stdin").read_text() == "data on stdin"


def test_ssh_sh_uses_keys_only_without_a_password(device):
    device.write_env(ssh_password="", extra=f"UNOS_DEV_SSH_KEY={device.tmp}/id_test\n")
    assert device.run(DEV / "ssh.sh", "true").returncode == 0
    argv = (device.log / "argv").read_text().rstrip("\n").split("\0")
    env = (device.log / "env").read_text()
    assert "BatchMode=yes" in argv and "IdentitiesOnly=yes" in argv and f"{device.tmp}/id_test" in argv
    assert "SSH_ASKPASS" not in env


def test_sd_sh_measures_and_keeps_the_status_of_the_command(device):
    sectors = device.run(DEV / "sd.sh", "sectors", FAKE_SSH_MODE="sectors")
    assert sectors.stdout == "1000\n"
    ok = device.run(DEV / "sd.sh", "measure", "a step", "--", "true", FAKE_SSH_MODE="sectors",
                    UNOS_DEV_SD_LOG=str(device.tmp / "sd.log"))
    assert ok.returncode == 0 and "SD writes a step: 0.049 MiB (100 sectors)" in ok.stderr
    assert (device.tmp / "sd.log").read_text().split()[1:] == ["a_step", "100"]
    failed = device.run(DEV / "sd.sh", "measure", "x", "--", "sh", "-c", "exit 7", FAKE_SSH_MODE="sectors")
    assert failed.returncode == 7 and "SD writes x:" in failed.stderr
    assert device.run(DEV / "sd.sh", "measure", "x", "true").returncode == 2


def test_sd_sh_measure_gives_the_command_the_callers_standard_input(device):
    """The sector reads before and after it read none (the device-checks skill pipes a body into
    scripts/dev/api.py ... - under sd.sh measure)."""
    result = device.run(DEV / "sd.sh", "measure", "a body", "--", "sh", "-c", "cat", input="the body\n",
                        FAKE_SSH_MODE="sectors")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "the body\n"
    assert "SD writes a body: 0.049 MiB (100 sectors)" in result.stderr


# --- the device lock ------------------------------------------------------------------------


@pytest.fixture
def local_device(device):
    """The remote side of the scripts runs here: stubs for unshare and mount (and flock)."""
    device.stub("unshare", STUB_UNSHARE)
    device.stub("mount", STUB_MOUNT)
    if shutil.which("flock") is None:
        device.stub("flock", STUB_FLOCK)
    if shutil.which("timeout") is None:
        device.stub("timeout", "#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
    return device


def lock_is_free(path: Path) -> bool:
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(f, fcntl.LOCK_UN)
        return True


def wait_for(predicate, seconds: float = 15) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def test_the_lock_is_taken_recorded_and_released(local_device):
    owner = Path(str(local_device.lock) + ".owner")
    result = local_device.bash('. ./env.sh || exit 9; dev_lock "test step"; cat "$UNOS_DEV_LOCK.owner"; '
                               'echo "held=$UNOS_DEV_LOCK_HELD"', FAKE_SSH_MODE="local")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("test step (") and "held=" in result.stdout
    assert wait_for(lambda: not owner.exists()) and lock_is_free(local_device.lock)


def test_a_second_holder_waits_and_gives_up(local_device):
    first = subprocess.Popen(
        # wait, unlike a foreground sleep, returns at once on SIGTERM, so the traps run.
        ["bash", "-c", ('. ./env.sh || exit 9; dev_lock "first step"; echo held; sleep 30 & '
                        'dev_on_exit "kill $!"; wait')],
        cwd=DEV, env=local_device.env(FAKE_SSH_MODE="local"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True,
    )
    try:
        assert first.stdout.readline() == "held\n"
        assert not lock_is_free(local_device.lock)
        second = local_device.bash('. ./env.sh || exit 9; dev_lock "second step"; echo held',
                                   FAKE_SSH_MODE="local", UNOS_DEV_LOCK_WAIT="1")
        assert second.returncode == 1 and "held" not in second.stdout
        assert "held by first step (" in second.stderr and "could not take the device lock" in second.stderr
    finally:
        first.terminate()
        first.wait(timeout=30)
    assert wait_for(lambda: lock_is_free(local_device.lock))


@pytest.mark.parametrize("sig", ["HUP", "TERM"], ids=lambda s: f"SIG{s}")
def test_a_signal_to_the_holders_process_group_keeps_the_lock_until_the_holder_ends(local_device, sig):
    """A closed terminal (SIGHUP) or a TERM sent to the holder's process group must not release
    the lock while the holder still works: scenario.sh waits for its check to clean up. The
    session that holds the lock ignores both, and the holder's exit releases it. (On the device
    the lock's own commands never get the workstation's signal; here they run in the same group,
    so timeout is the plain stand-in.) The holder's work says held once it ignores the signals, and
    goes on until the test creates the file GO, not for a fixed time: a busy machine (parallel runs)
    could otherwise use that time up, or let the signal come before the work's own trap."""
    device = local_device
    device.stub("timeout", "#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
    go = local_device.lock.parent / "go"
    holder = subprocess.Popen(
        ["bash", "-c", ('. ./env.sh || exit 9; dev_lock "holder"; trap "stop=1" HUP TERM; '
                        'bash -c "trap \\"\\" HUP TERM; echo held; until [ -e \\"\\$GO\\" ]; do sleep 0.05; done"; '
                        'echo done')],
        cwd=DEV, env=local_device.env(FAKE_SSH_MODE="local", GO=str(go)), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    try:
        assert holder.stdout.readline() == "held\n"
        os.killpg(holder.pid, getattr(__import__("signal"), "SIG" + sig))
        time.sleep(0.5)
        assert not lock_is_free(local_device.lock), "the lock went with the signal"
        go.touch()
        out, err = holder.communicate(timeout=30)
        assert out == "done\n", err
    finally:
        if holder.poll() is None:
            os.killpg(holder.pid, 9)
            holder.wait(timeout=30)
    assert wait_for(lambda: lock_is_free(local_device.lock))


# --- check.sh -------------------------------------------------------------------------------

# docker for check.sh's script on the device: three containers, the two of UNOS running and
# healthy, an app whose log rotation comes from the host's daemon.json. It knows the templates
# check.sh uses and fails on any other, so a changed template needs this stub changed too.
STUB_DOCKER = textwrap.dedent(
    """\
    #!@PYTHON@
    import json, os, sys
    containers = {
        "1a2b": ("unos-supervisor", {"Type": "json-file", "Config": {}}),
        "3c4d": ("unos-webui", {"Type": "json-file", "Config": {}}),
        "5e6f": ("transmission", {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}),
    }
    def js(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    args = sys.argv[1:]
    if args[0] == "ps":
        for cid, (name, _) in containers.items():
            print(cid if "-aq" in args else f"{name}  Up 2 hours (healthy)  deploy-{name}:latest")
    elif args[0] == "inspect" and args[1] == "-f":
        for ref in args[3:]:
            found = [c for cid, c in containers.items() if ref in (cid, c[0])]
            if not found:
                sys.exit(f"Error: No such object: {ref}")
            name, log = found[0]
            out = args[2]
            for template, value in (("{{.Name}}", "/" + name), ("{{.HostConfig.LogConfig.Type}}", log["Type"]),
                                    ("{{json .HostConfig.LogConfig.Config}}", js(log["Config"])),
                                    ("{{json .HostConfig.LogConfig}}", js(log)),                                     ("{{.State.Status}}", os.environ.get("FAKE_DOCKER_STATUS", "running")),
                                    ("{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}", "healthy"),
                                    ("{{.RestartCount}}", "0")):
                out = out.replace(template, value)
            if "{{" in out:
                sys.exit(f"the docker stub does not know the template {out!r}")
            print(out)
    elif args[0] == "logs":
        print(os.environ.get("FAKE_DOCKER_LOG", "INFO started"))
    elif args[0] == "images":
        if "-q" not in args:
            print("deploy-supervisor:latest  sha256:1  2 days ago  300MB")
    elif args[0] == "stats" and args[1:] == ["--no-stream", "--format", "{{.Name}} {{.CPUPerc}}"]:
        # FAKE_DOCKER_STATS: one reading, or several joined by "---": each call prints the next, the
        # last one again after that (the counter is a file, FAKE_DOCKER_STATS_COUNTER).
        readings = os.environ.get("FAKE_DOCKER_STATS", "unos-supervisor 2.00%\\nunos-webui 0.10%\\ntransmission 50.00%").split("---")
        counter = os.environ.get("FAKE_DOCKER_STATS_COUNTER")
        n = int(open(counter).read()) if counter and os.path.exists(counter) else 0
        if counter:
            open(counter, "w").write(str(n + 1))
        print(readings[min(n, len(readings) - 1)])
    else:
        sys.exit(f"the docker stub does not know {args}")
    """
)


# systemctl for check.sh: is-active and is-enabled of a unit answer FAKE_<UNIT>_ACTIVE and
# FAKE_<UNIT>_ENABLED (the unit's name in capitals, "-" as "_"); by default every unit is active
# and enabled but nmbd, which is inactive and disabled, as deploy/install.sh leaves it (ADR-0275).
# FAKE_<UNIT>_ENABLED=none answers as for a unit that is not installed: nothing, and exit 1.
STUB_SYSTEMCTL = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    unit=${2%.service}
    var=FAKE_$(printf "%s" "$unit" | tr "a-z-" "A-Z_")
    case $1 in
        is-active) default=active; [ "$unit" != nmbd ] || default=inactive
                   eval "state=\\${${var}_ACTIVE:-$default}"; echo "$state"; [ "$state" = active ] ;;
        is-enabled) default=enabled; [ "$unit" != nmbd ] || default=disabled
                    eval "state=\\${${var}_ENABLED:-$default}"; [ "$state" != none ] || exit 1
                    echo "$state"; [ "$state" = enabled ] ;;
        *) echo "the systemctl stub does not know: $*" >&2; exit 2 ;;
    esac
    """
)


def test_check_sh_shows_every_containers_log_configuration(local_device):
    """The rotation of the containers' json-file logs is set in the host's daemon.json, as log
    options (ADR-0178): check.sh prints the driver and the options of every container, also the
    apps', redacted like the rest of its output. The device's host name is not part of the
    report. Its script runs here, with stubs."""
    device = local_device
    device.stub("docker", STUB_DOCKER)
    device.stub("systemctl", STUB_SYSTEMCTL)
    device.stub("nft", STUB_NFT_LIST)
    device.stub("python3", "#!/usr/bin/env bash\necho 200\n")  # the API's health check
    device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    device.stub("sleep", "#!/usr/bin/env bash\nexit 0\n")  # between the readings of docker stats
    host_name = "box-" + secrets.token_hex(4)
    device.stub("hostname", f"#!/usr/bin/env bash\necho {host_name}\n")
    result = device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable,
                        FAKE_DOCKER_LOG=f"ERROR login as admin with {device.admin_password} failed")
    out = result.stdout
    assert "== device" in out and host_name not in out + result.stderr
    assert "== container logs" in out, out + result.stderr
    section = out.split("== container logs", 1)[1].split("\n== ", 1)[0].splitlines()[1:]
    assert section == [
        'transmission: json-file {"max-file":"3","max-size":"10m"}',
        "unos-supervisor: json-file {}",
        "unos-webui: json-file {}",
    ], out + result.stderr
    assert "unos-supervisor: running healthy restarts=0" in out and "unos-webui: running healthy restarts=0" in out
    assert "== inbound IPv6 filter\nunos-ipv6-inbound.service: active, table ip6 unos_ipv6_inbound: loaded\n" in out
    # Samba and network discovery (ADR-0275): smbd, wsdd2 and avahi-daemon run, nmbd neither runs
    # nor starts at boot.
    assert ("== samba and network discovery\nsmbd: active\nwsdd2: active\navahi-daemon: active\n"
            "nmbd: inactive, disabled\n") in out, out
    # The writes since boot (ADR-0214), whatever the machine's uptime and disk: linux-tests.sh
    # runs this test on the device too.
    assert re.search(r"^mmcblk1 \([^)]*\): \d+\.\d MiB written since boot, \d+\.\d hours ago: \d+\.\d\d MiB per hour$",
                     out, re.M), out + result.stderr
    assert "check: OK" in out and result.returncode == 0, out + result.stderr
    # The output passes through api.py's redaction.
    assert "ERROR login as admin with <redacted> failed" in out and device.admin_password not in out
    assert "SD writes check.sh: " in result.stderr


# nft list table: FAKE_NFT_TABLE=missing answers as nft does for a table that is not loaded.
STUB_NFT_LIST = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    [ "$*" = "list table ip6 unos_ipv6_inbound" ] || { echo "the nft stub does not know: $*" >&2; exit 2; }
    [ "${FAKE_NFT_TABLE:-loaded}" = loaded ] || { echo "Error: No such file or directory" >&2; exit 1; }
    echo "table ip6 unos_ipv6_inbound {"
    echo "}"
    """
)


@pytest.mark.parametrize("unit, table", [("inactive", "loaded"), ("active", "missing"), ("failed", "missing")])
def test_check_sh_reports_a_missing_ipv6_filter_as_a_problem(local_device, unit, table):
    """The inbound IPv6 filter fails open (ADR-0301): a unit that failed at boot, or a table
    deleted by hand, leaves every service open over IPv6. check.sh says so and exits 1."""
    device = local_device
    device.stub("docker", STUB_DOCKER)
    device.stub("systemctl", textwrap.dedent(
        """\
        #!/usr/bin/env bash
        case $* in
            "is-active unos-ipv6-inbound.service") echo "$FAKE_UNIT"; [ "$FAKE_UNIT" = active ] ;;
            "is-active nmbd") echo inactive; exit 3 ;;
            "is-enabled nmbd") echo disabled; exit 1 ;;
            *) echo active ;;
        esac
        """))
    device.stub("nft", STUB_NFT_LIST)
    device.stub("python3", "#!/usr/bin/env bash\necho 200\n")
    device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    device.stub("sleep", "#!/usr/bin/env bash\nexit 0\n")  # between the readings of docker stats
    result = device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable,
                        FAKE_UNIT=unit, FAKE_NFT_TABLE=table)
    out = result.stdout
    assert f"unos-ipv6-inbound.service: {unit}, table ip6 unos_ipv6_inbound: {table}" in out, out + result.stderr
    assert "- the inbound IPv6 filter is not loaded (scripts/dev/ipv6-filter.sh status)" in out, out
    assert "check: problems" in out and result.returncode == 1


@pytest.mark.parametrize("env, problem", [
    ({"FAKE_SMBD_ACTIVE": "inactive"}, "smbd is inactive"),
    ({"FAKE_WSDD2_ACTIVE": "failed"}, "wsdd2 is failed"),
    ({"FAKE_AVAHI_DAEMON_ACTIVE": "inactive"}, "avahi-daemon is inactive"),
    ({"FAKE_NMBD_ACTIVE": "active"}, "nmbd is active, while NetBIOS is off (ADR-0275)"),
    ({"FAKE_NMBD_ACTIVE": "activating"}, "nmbd is activating, while NetBIOS is off (ADR-0275)"),
    ({"FAKE_NMBD_ENABLED": "enabled"}, "nmbd is enabled, while NetBIOS is off (ADR-0275)"),
    ({"FAKE_NMBD_ENABLED": "enabled-runtime"}, "nmbd is enabled-runtime, while NetBIOS is off (ADR-0275)"),
])
def test_check_sh_reports_samba_and_network_discovery_problems(local_device, env, problem):
    """Network discovery needs smbd, wsdd2 (Windows) and avahi-daemon (macOS), and NetBIOS is off,
    so nmbd neither runs nor starts at boot (ADR-0275): check.sh says what is wrong and exits 1."""
    device = local_device
    device.stub("docker", STUB_DOCKER)
    device.stub("systemctl", STUB_SYSTEMCTL)
    device.stub("nft", STUB_NFT_LIST)
    device.stub("python3", "#!/usr/bin/env bash\necho 200\n")
    device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    device.stub("sleep", "#!/usr/bin/env bash\nexit 0\n")  # between the readings of docker stats
    result = device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable, **env)
    out = result.stdout
    assert f"- {problem}" in out and "check: problems" in out and result.returncode == 1, out + result.stderr


def _thermal_zones(folder: Path, zones: list[tuple]) -> Path:
    """A /sys/class/thermal in ``folder``: zones of (type, degrees, [(trip type, degrees), ...]) and,
    as a fourth item, the cooling devices bound to its passive trips (cdevN, a link to
    cooling_deviceM, and cdevN_trip_point, as the kernel has them): a type, bound to the zone's
    passive trips in turn, or (type, trip index). Without it a zone with a passive trip has the
    CPU's clock (cpufreq-cpu0) bound to its first one; a fifth item is the zone's governor."""
    devices = folder / "devices"
    for n, (kind, degrees, trips, *extra) in enumerate(zones):
        cooling, policy = extra[:1], extra[1:]
        zone = folder / f"thermal_zone{n}"
        zone.mkdir(parents=True)
        (zone / "type").write_text(f"{kind}\n")
        (zone / "temp").write_text(f"{degrees * 1000}\n")
        for t, (trip, trip_degrees) in enumerate(trips):
            (zone / f"trip_point_{t}_type").write_text(f"{trip}\n")
            (zone / f"trip_point_{t}_temp").write_text(f"{trip_degrees * 1000}\n")
        passive = [t for t, (trip, _) in enumerate(trips) if trip == "passive"]
        bound = cooling[0] if cooling else (["cpufreq-cpu0"] if passive else [])
        for c, device_type in enumerate(bound):
            device_type, at = device_type if isinstance(device_type, tuple) else (
                device_type, passive[min(c, len(passive) - 1)])
            device = devices / f"cooling_device{n}_{c}"
            device.mkdir(parents=True)
            (device / "type").write_text(f"{device_type}\n")
            (zone / f"cdev{c}").symlink_to(device)
            (zone / f"cdev{c}_trip_point").write_text(f"{at}\n")
        (zone / "policy").write_text(f"{policy[0] if policy else 'step_wise'}\n")
    return folder


# The thermal zones of a SpacemiT K1 (BPI-F3) under Armbian's current-spacemit kernel.
K1_TRIPS = [("passive", 75), ("passive", 85), ("hot", 95), ("hot", 105), ("critical", 115)]


def _run_check(device, tmp_path, zones, **env):
    device.stub("docker", STUB_DOCKER)
    device.stub("systemctl", STUB_SYSTEMCTL)
    device.stub("nft", STUB_NFT_LIST)
    device.stub("python3", "#!/usr/bin/env bash\necho 200\n")
    device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    device.stub("sleep", "#!/usr/bin/env bash\nexit 0\n")
    thermal = _thermal_zones(tmp_path / f"thermal{len(list(tmp_path.glob('thermal*')))}", zones)
    return device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable,
                      UNOS_CHECK_THERMAL_DIR=str(thermal), **env)


def test_check_sh_shows_the_containers_cpu_and_the_temperatures(local_device, tmp_path):
    """Nobody saw a BPI-F3 run at 71 C without a fan, 4 degrees below the point where its kernel
    slows the CPU down, nor UNOS's supervisor take half a core while a dashboard was open: check.sh
    shows each container's CPU, averaged over five readings, and each thermal zone (ADR-0421)."""
    zones = [("cluster0_thermal", 53, K1_TRIPS), ("gpu_thermal", 52, [("active", 40)])]
    result = _run_check(local_device, tmp_path, zones)
    out = result.stdout
    section = out.split("== load and temperature", 1)[1].split("\n== ", 1)[0]
    assert "transmission: 50.0% of a core" in section, out + result.stderr
    assert "unos-supervisor: 2.0% of a core" in section and "unos-webui: 0.1% of a core" in section
    assert "cluster0_thermal: 53 C, first passive trip 75 C" in section
    assert "gpu_thermal: 52 C" in section
    assert "check: OK" in out and result.returncode == 0, out + result.stderr


def test_check_sh_reports_unos_over_its_cpu_budget(local_device, tmp_path):
    """UNOS's containers stay within 15% of a core on average, also with a dashboard open
    (ADR-0421); the supervisor took 25 to 64% before."""
    stats = "unos-supervisor 64.00%\nunos-webui 0.10%\nfrigate 190.00%"
    result = _run_check(local_device, tmp_path, [("cluster0_thermal", 53, K1_TRIPS)], FAKE_DOCKER_STATS=stats)
    out = result.stdout
    assert "- unos-supervisor used 64.0% of a core on average, more than its budget of 15% (ADR-0421)" in out, out
    assert "frigate" not in out.split("check: problems", 1)[1]
    assert result.returncode == 1


def test_check_sh_reports_a_board_near_its_throttling_point(local_device, tmp_path):
    result = _run_check(local_device, tmp_path, [("cluster0_thermal", 71, K1_TRIPS)])
    out = result.stdout
    assert ("- cluster0_thermal is at 71 C, within 5 C of its first passive trip (75 C), where the "
            "kernel slows a processor down: the board needs cooling or less load") in out, out + result.stderr
    assert result.returncode == 1


def test_check_sh_averages_the_five_readings_of_docker_stats(local_device, tmp_path):
    """One reading is a spike: the budget is on the average of five (ADR-0421). A supervisor at 40%
    once and 1% four times averages 8.8%, within the budget; one at 20% in four readings of five
    averages 16.2%."""
    zones = [("cluster0_thermal", 53, K1_TRIPS)]
    one = {"FAKE_DOCKER_STATS": "---".join(f"unos-supervisor {v}%\nunos-webui 0.10%" for v in (40, 1, 1, 1, 1)),
           "FAKE_DOCKER_STATS_COUNTER": str(tmp_path / "n1")}
    result = _run_check(local_device, tmp_path, zones, **one)
    assert "unos-supervisor: 8.8% of a core" in result.stdout and result.returncode == 0, result.stdout
    four = {"FAKE_DOCKER_STATS": "---".join(f"unos-supervisor {v}%\nunos-webui 0.10%" for v in (20, 20, 20, 20, 1)),
            "FAKE_DOCKER_STATS_COUNTER": str(tmp_path / "n2")}
    result = _run_check(local_device, tmp_path, zones, **four)
    assert "unos-supervisor used 16.2% of a core on average" in result.stdout and result.returncode == 1, result.stdout


@pytest.mark.parametrize("cpu, problem", [("15.00", False), ("15.10", True)])
def test_check_sh_budget_is_15_percent_of_a_core_for_each_container_of_unos(local_device, tmp_path, cpu, problem):
    zones = [("cluster0_thermal", 53, K1_TRIPS)]
    for container, other in (("unos-supervisor", "unos-webui"), ("unos-webui", "unos-supervisor")):
        result = _run_check(local_device, tmp_path, zones,
                            FAKE_DOCKER_STATS=f"{container} {cpu}%\n{other} 1.00%")
        assert ("more than its budget of 15%" in result.stdout) is problem, result.stdout
        assert (f"- {container} used" in result.stdout) is problem and result.returncode == int(problem)


def test_check_sh_does_not_pass_a_unos_container_that_docker_stats_does_not_show(local_device, tmp_path):
    """A running container without a reading, or one that reads "--", has not been measured: that is
    a problem, not an OK."""
    zones = [("cluster0_thermal", 53, K1_TRIPS)]
    for stats in ("unos-webui 0.10%\ntransmission 50.00%", "unos-supervisor --\nunos-webui 0.10%"):
        result = _run_check(local_device, tmp_path, zones, FAKE_DOCKER_STATS=stats)
        assert ("- unos-supervisor has no CPU reading from docker stats, so its budget of 15% of a core "
                "is unchecked (ADR-0421)") in result.stdout, result.stdout
        assert result.returncode == 1


@pytest.mark.parametrize("degrees, problem", [(69, False), (70, True)])
def test_check_sh_margin_is_5_degrees_below_the_first_passive_trip(local_device, tmp_path, degrees, problem):
    result = _run_check(local_device, tmp_path, [("cluster0_thermal", degrees, K1_TRIPS)])
    assert ("within 5 C of its first passive trip (75 C)" in result.stdout) is problem, result.stdout
    assert result.returncode == int(problem)


def test_check_sh_takes_the_lowest_passive_trip_and_ignores_the_others(local_device, tmp_path):
    """Only a passive trip is where the kernel slows the CPU down; the hot and critical ones, and
    the higher passive one, are not the margin's (a critical trip has a margin of its own)."""
    trips = [("hot", 60), ("passive", 85), ("passive", 75), ("critical", 125)]
    result = _run_check(local_device, tmp_path, [("cluster0_thermal", 66, trips)])
    assert "cluster0_thermal: 66 C, first passive trip 75 C" in result.stdout and result.returncode == 0


def test_check_sh_shows_a_zone_that_cannot_be_read_and_checks_the_others(local_device, tmp_path):
    zones = [("cluster0_thermal", 53, K1_TRIPS), ("gpu_thermal", 52, [])]
    thermal = _thermal_zones(tmp_path / "thermal", zones)
    (thermal / "thermal_zone0" / "temp").write_text("")
    (thermal / "thermal_zone1" / "temp").write_text("71000\n")
    (thermal / "thermal_zone1" / "trip_point_0_type").write_text("passive\n")
    (thermal / "thermal_zone1" / "trip_point_0_temp").write_text("75000\n")
    cpufreq = thermal / "devices" / "cooling_device_cpu"
    cpufreq.mkdir(parents=True)
    (cpufreq / "type").write_text("cpufreq-cpu0\n")
    (thermal / "thermal_zone1" / "cdev0").symlink_to(cpufreq)
    (thermal / "thermal_zone1" / "cdev0_trip_point").write_text("0\n")
    local_device.stub("docker", STUB_DOCKER)
    local_device.stub("systemctl", STUB_SYSTEMCTL)
    local_device.stub("nft", STUB_NFT_LIST)
    local_device.stub("python3", "#!/usr/bin/env bash\necho 200\n")
    local_device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    local_device.stub("sleep", "#!/usr/bin/env bash\nexit 0\n")
    result = local_device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable,
                              UNOS_CHECK_THERMAL_DIR=str(thermal))
    assert "cluster0_thermal: temperature unreadable" in result.stdout, result.stdout + result.stderr
    assert "- gpu_thermal is at 71 C, within 5 C" in result.stdout and result.returncode == 1


def test_check_sh_a_container_that_does_not_run_is_not_a_missing_reading(local_device, tmp_path):
    """The container check says that it does not run; the CPU section adds no second problem."""
    result = _run_check(local_device, tmp_path, [("cluster0_thermal", 53, K1_TRIPS)],
                        FAKE_DOCKER_STATUS="exited", FAKE_DOCKER_STATS="transmission 5.00%")
    assert "no CPU reading" not in result.stdout, result.stdout
    assert "- unos-supervisor: exited healthy restarts=0" in result.stdout and result.returncode == 1


@pytest.mark.parametrize("temp", ["-", "abc", "-5000", "50000"])
@pytest.mark.parametrize("trip", ["", "-", "x"])
def test_check_sh_unreadable_values_of_a_zone_never_end_the_check_early(local_device, tmp_path, temp, trip):
    """An empty or garbled temp or trip file once aborted the script's arithmetic: the output ended
    without "check: OK" or "check: problems"."""
    thermal = _thermal_zones(tmp_path / "thermal", [("cluster0_thermal", 53, [("passive", 75)])])
    zone = thermal / "thermal_zone0"
    (zone / "temp").write_text(temp)
    (zone / "trip_point_0_temp").write_text(trip)
    for stub, body in (("docker", STUB_DOCKER), ("systemctl", STUB_SYSTEMCTL), ("nft", STUB_NFT_LIST),
                       ("python3", "#!/usr/bin/env bash\necho 200\n"), ("sync", "#!/usr/bin/env bash\nexit 0\n"),
                       ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
        local_device.stub(stub, body)
    result = local_device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable,
                              UNOS_CHECK_THERMAL_DIR=str(thermal))
    assert "check: OK" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0
    assert "cluster0_thermal: " in result.stdout


def test_check_sh_reads_load_and_heat_without_writing_anything():
    """The reading is /proc, /sys and docker stats --no-stream: no redirection into a file, no tool
    that makes one, so that the check adds nothing to the device's system disk (ADR-0214, ADR-0421)."""
    script = (DEV / "check.sh").read_text()
    section = script.split('echo "== load and temperature"', 1)[1].split('echo "== result"', 1)[0]
    assert "docker stats --no-stream" in section
    for word in ("tee", "touch", "mkdir", "mktemp", "dd ", "sync", "write", "echo >", "cat >", "docker run", "docker exec"):
        assert word not in section.replace("// ", ""), word
    # Every redirection in it goes to /dev/null.
    shell = "\n".join(line for line in section.splitlines() if "awk -v a=" not in line)  # a > b in awk
    redirections = re.findall(r"\d?>>?\s*([^\s)\"]+)", shell)
    assert redirections and all(r == "/dev/null" for r in redirections), redirections


# The thermal zones of a Rockchip RK3588 (BPI-W3, ArmSoM W3) under Armbian's vendor kernel 6.1,
# as the dev device has them: only soc-thermal has passive trips; the zones of the CPU's clusters,
# the GPU and the NPU have a critical trip alone (ADR-0376).
RK3588_SOC_COOLING = [("devfreq-fb000000.gpu", 1), ("cpufreq-cpu0", 1), ("cpufreq-cpu4", 1), ("cpufreq-cpu6", 1)]
RK3588_ZONES = [
    ("soc-thermal", 42, [("passive", 75), ("passive", 85), ("critical", 115)], RK3588_SOC_COOLING),
    *[(zone, 42, [("critical", 115)]) for zone in
      ("bigcore0-thermal", "bigcore1-thermal", "littlecore-thermal", "center-thermal", "gpu-thermal", "npu-thermal")],
]


def test_check_sh_reads_the_zones_of_an_rk3588(local_device, tmp_path):
    """On the BPI-W3 soc-thermal carries the passive trip at 75 C that the check measures against;
    a zone with a critical trip alone is shown without one and is never a problem."""
    result = _run_check(local_device, tmp_path, RK3588_ZONES)
    out = result.stdout
    section = out.split("== load and temperature", 1)[1].split("\n== ", 1)[0]
    assert "soc-thermal: 42 C, first passive trip 75 C" in section, out + result.stderr
    lines = section.splitlines()
    assert "bigcore0-thermal: 42 C" in lines and "npu-thermal: 42 C" in lines
    assert "check: OK" in out and result.returncode == 0, out + result.stderr


def test_check_sh_reports_an_rk3588_near_its_throttling_point(local_device, tmp_path):
    zones = [("soc-thermal", 70, RK3588_ZONES[0][2], RK3588_SOC_COOLING), ("bigcore0-thermal", 72, [("critical", 115)])]
    result = _run_check(local_device, tmp_path, zones)
    out = result.stdout
    assert ("- soc-thermal is at 70 C, within 5 C of its first passive trip (75 C), where the "
            "kernel slows a processor down: the board needs cooling or less load") in out, out + result.stderr
    assert "bigcore0-thermal is at" not in out
    assert result.returncode == 1


# The thermal zones of the owner's BPI-F3 (SpacemiT K1, kernel 6.18.45-current-spacemit) as it had
# them on 2026-11-19: top_thermal's passive trips drive the fan alone, gpu_thermal's and
# cluster1_thermal's are bound to nothing, and only cluster0_thermal slows the CPU's clock, at 75 C.
K1_F3_ZONES = [
    ("top_thermal", 59, [("passive", 40), ("passive", 55), ("passive", 70), ("passive", 85)],
     ["pwm-fan", "pwm-fan", "pwm-fan", "pwm-fan"]),
    ("gpu_thermal", 59, [("passive", 40)], []),
    ("cluster0_thermal", 53, K1_TRIPS, ["cpufreq-cpu0"]),
    ("cluster1_thermal", 52, [("passive", 75), ("passive", 85), ("critical", 115)], []),
]


def test_check_sh_leaves_out_the_trips_of_zones_that_slow_no_processor(local_device, tmp_path):
    """On the BPI-F3 check.sh warned of top_thermal and gpu_thermal at 40 to 64 C, within 5 C of
    their first passive trip at 40 C; that trip turns the fan up (top_thermal) or is bound to
    nothing (gpu_thermal), and the kernel slows no processor there (ADR-0421). Such a zone is shown
    without a trip and is never a problem; cluster0_thermal keeps its margin below 75 C."""
    result = _run_check(local_device, tmp_path, K1_F3_ZONES)
    out = result.stdout
    section = out.split("== load and temperature", 1)[1].split("\n== ", 1)[0]
    lines = section.splitlines()
    assert "top_thermal: 59 C, no trip that slows a processor" in lines, out + result.stderr
    assert "gpu_thermal: 59 C, no trip that slows a processor" in lines
    assert "cluster1_thermal: 52 C, no trip that slows a processor" in lines
    assert "cluster0_thermal: 53 C, first passive trip 75 C" in lines
    assert "check: OK" in out and result.returncode == 0, out + result.stderr


def test_check_sh_still_reports_the_k1_near_its_throttling_point(local_device, tmp_path):
    zones = [*K1_F3_ZONES[:2], ("cluster0_thermal", 71, K1_TRIPS, ["cpufreq-cpu0"])]
    result = _run_check(local_device, tmp_path, zones)
    out = result.stdout
    assert "- cluster0_thermal is at 71 C, within 5 C of its first passive trip (75 C)" in out, out + result.stderr
    assert "top_thermal is at" not in out and "gpu_thermal is at" not in out
    assert result.returncode == 1


@pytest.mark.parametrize("bound, problem", [
    (["pwm-fan", "cpufreq-cpu0"], True),   # a fan and the CPU's clock: the zone slows the CPU
    (["devfreq-fb000000.gpu"], True),      # the GPU's clock is a processor's too
    ([""], True),                          # a type that cannot be read: rather a warning than none
    (["gpio-fan"], False),
    ([], False),
])
def test_check_sh_counts_a_zone_that_binds_any_processor(local_device, tmp_path, bound, problem):
    """A zone counts with every passive trip of it once it binds a cooling device other than a fan,
    as the RK3588's soc-thermal, whose CPU and GPU are bound at 85 C behind a first passive trip
    of 75 C, does: the check stays as careful as it was there."""
    result = _run_check(local_device, tmp_path, [("soc-thermal", 71, [("passive", 75), ("passive", 85)], bound)])
    assert ("- soc-thermal is at 71 C, within 5 C of its first passive trip (75 C)" in result.stdout) is problem, \
        result.stdout + result.stderr
    assert result.returncode == int(problem)


def test_check_sh_counts_a_zone_that_a_daemon_governs(local_device, tmp_path):
    """Under the governor user_space a passive trip without a cooling device asks a daemon to slow
    the processors down: the zone counts."""
    zones = [("soc-thermal", 71, [("passive", 75)], [], "user_space")]
    result = _run_check(local_device, tmp_path, zones)
    assert "- soc-thermal is at 71 C, within 5 C of its first passive trip (75 C)" in result.stdout, \
        result.stdout + result.stderr
    assert result.returncode == 1


@pytest.mark.parametrize("zone, degrees, problem", [
    (K1_F3_ZONES[3], 110, True),     # cluster1_thermal: bound to nothing, watched against its critical trip
    (K1_F3_ZONES[3], 109, False),
    (("bigcore0-thermal", 110, [("critical", 115)]), 110, True),   # an RK3588 zone with a critical trip alone
    (("cluster0_thermal", 111, K1_TRIPS, ["cpufreq-cpu0"]), 111, True),
])
def test_check_sh_warns_before_every_zones_critical_trip(local_device, tmp_path, zone, degrees, problem):
    """A zone that slows no processor is not left unwatched up to the kernel's power-off: every zone
    is a problem within 5 C of its critical trip (ADR-0421)."""
    result = _run_check(local_device, tmp_path, [(zone[0], degrees, *zone[2:])])
    expected = f"- {zone[0]} is at {degrees} C, within 5 C of its critical trip (115 C), where the kernel powers the board off"
    assert (expected in result.stdout) is problem, result.stdout + result.stderr
    assert result.returncode == int(problem)


@pytest.mark.parametrize("enabled", ["none", "masked", "static"])
def test_check_sh_takes_an_nmbd_that_cannot_start(local_device, enabled):
    """An nmbd that is not installed, masked or static (never started by itself) is no problem."""
    device = local_device
    device.stub("docker", STUB_DOCKER)
    device.stub("systemctl", STUB_SYSTEMCTL)
    device.stub("nft", STUB_NFT_LIST)
    device.stub("python3", "#!/usr/bin/env bash\necho 200\n")
    device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    device.stub("sleep", "#!/usr/bin/env bash\nexit 0\n")  # between the readings of docker stats
    result = device.run(DEV / "check.sh", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=sys.executable, FAKE_NMBD_ENABLED=enabled)
    out = result.stdout
    shown = "unknown" if enabled == "none" else enabled
    assert f"nmbd: inactive, {shown}\n" in out and "check: OK" in out and result.returncode == 0, out + result.stderr


# --- deploy's tree comparison end to end ---------------------------------------------------


def git(path: Path, *args: str) -> None:
    """git in the fixture repository PATH, without the user's identity, signing or hooks, and
    without an outer git's GIT_* variables."""
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args], check=True,
                   env=without_git_variables())


def git_init(path: Path) -> None:
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "fixture")


@pytest.fixture
def trees(local_device):
    """A repository with copies of the scripts (tree.py takes its root from where it is) and a
    "device checkout", both in tmp_path; the stub ssh runs the remote commands here."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    repo, checkout = local_device.tmp / "repo", local_device.tmp / "device"
    for name in ("tree.py", "env.sh", "ssh.sh", "askpass.sh", "deploy.sh"):
        (repo / "scripts" / "dev").mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEV / name, repo / "scripts" / "dev" / name)
    ignore = "*.env\nscripts/\ntmp/\n"
    make_tree(repo, {".gitignore": ignore, "supervisor/src/main.py": "new\n", "docs/x.md": "doc\n",
                     "deploy/update.sh": ("x", "#!/bin/sh\n"), "added.txt": "added\n"})
    make_tree(checkout, {".gitignore": ignore, "supervisor/src/main.py": "old\n", "docs/x.md": "doc\n",
                         "deploy/update.sh": "#!/bin/sh\n", "stale.txt": "stale\n", "deploy/.env": "JWT_SECRET=keep\n"})
    git_init(repo)
    git_init(checkout)
    return local_device, repo, checkout


def tree_cli(trees, *args: str) -> subprocess.CompletedProcess:
    device, repo, checkout = trees
    return subprocess.run([sys.executable, str(repo / "scripts" / "dev" / "tree.py"), *args], cwd=repo,
                          env=device.env(FAKE_SSH_MODE="local", UNOS_DEV_REPO=str(checkout)),
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)


def test_status_sync_and_rollback_end_to_end(trees):
    device, repo, checkout = trees
    status = tree_cli(trees, "status")
    assert status.returncode == 0, status.stderr
    lines = status.stdout.splitlines()
    assert lines[1].startswith("runtime files: 2 differ (supervisor 1, webui 0, deploy 1)")
    assert [line.split() for line in lines[2:4]] == [["mode", "deploy/update.sh"], ["changed", "supervisor/src/main.py"]]
    assert [line.split()[-1] for line in lines[5:]] == ["added.txt", "stale.txt"]
    assert "deploy/.env" not in status.stdout
    assert tree_cli(trees, "verify", "supervisor").returncode == 1
    assert tree_cli(trees, "verify", "webui").returncode == 0

    dry = tree_cli(trees, "sync", "--dry-run")
    assert dry.returncode == 0 and "write 3 files" in dry.stdout and "delete 1 files" in dry.stdout
    assert (checkout / "supervisor/src/main.py").read_text() == "old\n" and not (device.tmp / "state").exists()

    sync = tree_cli(trees, "sync")
    assert sync.returncode == 0, sync.stderr
    assert sync.stdout.rstrip().endswith("the device checkout now equals the working tree")
    assert (checkout / "supervisor/src/main.py").read_text() == "new\n" and (checkout / "added.txt").exists()
    assert os.access(checkout / "deploy/update.sh", os.X_OK) and not (checkout / "stale.txt").exists()
    assert (checkout / "deploy/.env").read_text() == "JWT_SECRET=keep\n", "an ignored file was touched"
    saved = device.tmp / "state" / "rollback"
    stamp = (saved / "latest").read_text().strip()
    assert oct((saved / stamp / "device-before.tar").stat().st_mode & 0o777) == "0o600"
    assert tree_cli(trees, "verify", "supervisor", "webui", "deploy").returncode == 0
    again = tree_cli(trees, "status")
    assert "runtime files (supervisor, webui, deploy): equal" in again.stdout and "other files: equal" in again.stdout

    plan = tree_cli(trees, "rollback", "--dry-run")
    assert plan.returncode == 0 and "restore 3 files, delete 1 files" in plan.stdout
    assert (checkout / "supervisor/src/main.py").read_text() == "new\n"
    rollback = tree_cli(trees, "rollback")
    assert rollback.returncode == 0, rollback.stderr
    assert (checkout / "supervisor/src/main.py").read_text() == "old\n" and (checkout / "stale.txt").read_text() == "stale\n"
    assert not os.access(checkout / "deploy/update.sh", os.X_OK) and not (checkout / "added.txt").exists()
    assert (checkout / "deploy/.env").read_text() == "JWT_SECRET=keep\n"
    assert (saved / (stamp + ".applied")).is_dir() and not (saved / "latest").exists()
    assert "no sync to roll back" in tree_cli(trees, "rollback").stdout


def test_deploy_sh_status_and_dry_run_hold_the_lock_and_report(trees):
    """deploy.sh's own wiring: the lock is taken and released, the step's SD report is printed
    (here "unknown": the stand-in device has no /sys/block/mmcblk1), the output is tree.py's."""
    device, repo, checkout = trees
    env = device.env(FAKE_SSH_MODE="local", UNOS_DEV_REPO=str(checkout), UNOS_DEV_PYTHON=sys.executable)
    for argv, expected in ((["status"], "runtime files: 2 differ"), (["sync", "--dry-run"], "dry run: nothing was changed")):
        result = subprocess.run([str(repo / "scripts" / "dev" / "deploy.sh"), *argv], cwd=repo, env=env,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
        assert f"SD writes deploy {' '.join(argv)}: unknown" in result.stderr
        assert wait_for(lambda: lock_is_free(device.lock))
    assert (checkout / "supervisor/src/main.py").read_text() == "old\n"


# --- deploy.sh backup -----------------------------------------------------------------------

# docker for the backup's scripts on the device: the compose project, the unos-data volume
# (FAKE_VOLUME) and the images' ids; it fails on any other call.
STUB_DOCKER_BACKUP = textwrap.dedent(
    """\
    #!@PYTHON@
    import json, os, sys
    args = sys.argv[1:]
    if args == ["compose", "config", "--format", "json"]:
        print(json.dumps({"name": "deploy"}))
    elif args == ["volume", "inspect", "-f", "{{.Mountpoint}}", "deploy_unos-data"]:
        print(os.environ["FAKE_VOLUME"])
    elif args[:4] == ["image", "inspect", "-f", "{{.Id}}"] and len(args) == 5:
        print("sha256:" + args[4].replace(":", "-"))
    else:
        sys.exit(f"the docker stub does not know {args}")
    """
)
# GNU tar as the backup calls it on the device (macOS's bsdtar has no --transform), with
# FAKE_ROOT standing for /: tar -c -f - [--transform s,PATTERN,TEXT,] -C DIR NAME... -C DIR
# NAME... It writes the archive to standard output and logs its process id and its parent's.
STUB_GNU_TAR = textwrap.dedent(
    """\
    #!@PYTHON@
    import os, re, sys, tarfile
    args = sys.argv[1:]
    with open(os.path.join(os.environ["FAKE_SSH_LOG"], "tar"), "a") as log:
        log.write(f"{os.getpid()} {os.getppid()} {' '.join(args)}\\n")
    if args[:3] != ["-c", "-f", "-"]:
        sys.exit(f"the tar stub knows only tar -c -f -, not {args}")
    args, transform, base = args[3:], None, None
    if args[:1] == ["--transform"]:
        _, pattern, text, _ = args[1].split(",")
        transform, args = (pattern, text), args[2:]
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
        while args:
            if args[0] == "-C":
                base = os.environ["FAKE_ROOT"] if args[1] == "/" else args[1]
                args = args[2:]
                continue
            name = args.pop(0)
            top = os.path.join(base, name)
            found = [(top, "./" if name == "." else name)]
            for folder, dirs, files in os.walk(top):
                dirs.sort()
                for entry in sorted(dirs + files):
                    path = os.path.join(folder, entry)
                    found.append((path, os.path.join(name, os.path.relpath(path, top))))
            for path, arcname in found:
                if transform:
                    arcname = re.sub(transform[0], transform[1], arcname)
                tar.add(path, arcname=arcname, recursive=False)
    """
)
# mount that logs its arguments and the shell that ran it (DEV_RO_ROOT's remount).
STUB_MOUNT_LOGGED = textwrap.dedent(
    """\
    #!@PYTHON@
    import os, sys
    with open(os.path.join(os.environ["FAKE_SSH_LOG"], "mount"), "a") as log:
        log.write(f"{os.getppid()} {' '.join(sys.argv[1:])}\\n")
    """
)


def test_deploy_sh_backup_is_private_quiet_and_keeps_the_newest_three(trees):
    """deploy.sh backup (also the first part of up) copies the device's fstab, smb.conf and
    unos-data volume, with its JWT secret, here (ADR-0223). Its scripts for the device run here
    with stubs. The backup directory is 0700 (and so is tmp/dev/backup), its files are 0600, and
    nothing of the archive reaches the output; the device's side ran with its root filesystem
    read-only. tmp/dev/backup keeps the newest 3 backups: the one just made and the two newest
    others by name, even one dated in the future (a clock that was ahead), so that a run never
    deletes its own backup. What an interrupted backup left is deleted; other files stay."""
    device, repo, checkout = trees
    root, volume = device.tmp / "root", device.tmp / "volume"
    markers = {name: f"{name}-{secrets.token_hex(6)}" for name in ("fstab", "smb", "jwt", "db")}
    contents = {
        "etc/fstab": f"LABEL=data /mnt/pools/data btrfs defaults 0 0 # {markers['fstab']}\n",
        "etc/samba/smb.conf": f"[global]\n# {markers['smb']}\n",
        "unos-data/jwt_secret": markers["jwt"] + "\n",
        "unos-data/unos.db": markers["db"],
    }
    make_tree(root, {name: text for name, text in contents.items() if name.startswith("etc/")})
    make_tree(volume, {name.split("/", 1)[1]: text for name, text in contents.items() if name.startswith("unos-data/")})
    device.stub("docker", STUB_DOCKER_BACKUP)
    device.stub("tar", STUB_GNU_TAR)
    device.stub("mount", STUB_MOUNT_LOGGED)
    device.stub("python3", '#!/bin/sh\nexec @PYTHON@ "$@"\n')
    device.stub("sync", "#!/bin/sh\nexit 0\n")
    base = device.tmp / "state" / "backup"
    older = ["20250101T000000Z", "20250102T000000Z", "20250103T000000Z", "20991231T235959Z"]
    make_tree(base, {**{f"{name}/device-state.tar": "old\n" for name in older},
                     ".20250104T000000Z.partial/device-state.tar": "half\n", "notes.txt": "mine\n",
                     "latest": "20250103T000000Z\n"})
    base.chmod(0o755)
    env = device.env(FAKE_SSH_MODE="local", UNOS_DEV_REPO=str(checkout), UNOS_DEV_PYTHON=sys.executable,
                     FAKE_ROOT=str(root), FAKE_VOLUME=str(volume))

    def backup() -> tuple[subprocess.CompletedProcess, str]:
        result = subprocess.run([str(repo / "scripts" / "dev" / "deploy.sh"), "backup"], cwd=repo, env=env,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert wait_for(lambda: lock_is_free(device.lock))
        for marker in markers.values():
            assert marker not in result.stdout + result.stderr
        return result, (base / "latest").read_text().strip()

    first, stamp = backup()
    made = base / stamp
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp) and stamp not in older
    assert f"saved the device's fstab, smb.conf and unos-data volume in {made}" in " ".join(first.stdout.split())
    assert "SD writes deploy backup: unknown" in first.stderr
    for path, mode in ((base, 0o700), (made, 0o700), (made / "device-state.tar", 0o600), (made / "meta.txt", 0o600)):
        assert oct(stat.S_IMODE(path.stat().st_mode)) == oct(mode), path
    with tarfile.open(made / "device-state.tar") as tar:
        for name, text in contents.items():
            assert tar.extractfile(name).read().decode() == text, name
    meta = (made / "meta.txt").read_text()
    assert "device checkout HEAD: " in meta and "image deploy-supervisor:latest: sha256:" in meta
    # tar ran in a shell that had remounted / read-only first (DEV_RO_ROOT).
    read_only_shells = {line.split(" ", 1)[0] for line in (device.log / "mount").read_text().splitlines()
                        if line.split(" ", 1)[1] == "-o remount,bind,ro,noatime /"}
    tar_pid, tar_parent = (device.log / "tar").read_text().split()[:2]
    assert read_only_shells & {tar_pid, tar_parent}
    assert {p.name for p in base.iterdir()} == {"20250103T000000Z", "20991231T235959Z", stamp, "latest", "notes.txt"}
    assert "older backups deleted: 2" in first.stdout

    second, stamp2 = backup()
    assert stamp2 > stamp and (base / stamp2 / "device-state.tar").stat().st_size > 0
    assert {p.name for p in base.iterdir()} == {stamp, "20991231T235959Z", stamp2, "latest", "notes.txt"}
    assert "older backups deleted: 1" in second.stdout


def compose_seconds(duration: str) -> int:
    """A compose duration of hours, minutes and seconds ("1m30s") in seconds."""
    parts = re.findall(r"(\d+)([hms])", duration)
    assert parts and "".join(number + unit for number, unit in parts) == duration, duration
    return sum(int(number) * {"h": 3600, "m": 60, "s": 1}[unit] for number, unit in parts)


def test_deploy_up_waits_until_docker_has_decided_the_health():
    """deploy.sh up gives up after UNOS_DEV_UP_TIMEOUT: never before Docker has decided whether a
    container of deploy/docker-compose.yml is healthy, which takes up to start_period +
    (retries + 1) x (interval + timeout) (ADR-0338; the same budget as SUPERVISOR_START_TIMEOUT
    of the install scripts in test_deploy_scripts.py)."""
    yaml = pytest.importorskip("yaml")
    services = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.yml").read_text())["services"]
    decided = {}
    for name, service in services.items():
        check = service["healthcheck"]
        decided[name] = compose_seconds(check["start_period"]) + (check["retries"] + 1) * (
            compose_seconds(check["interval"]) + compose_seconds(check["timeout"]))
    default = re.search(r'^: "\$\{UNOS_DEV_UP_TIMEOUT:=(\d+)\}"$', (DEV / "deploy.sh").read_text(), flags=re.M)
    assert default, "no default UNOS_DEV_UP_TIMEOUT in deploy.sh"
    assert int(default.group(1)) > max(decided.values()), decided
    # Its usage text says so with the current numbers.
    usage = subprocess.run([str(DEV / "deploy.sh"), "--help"], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    assert f"default {default.group(1)}, above the supervisor's {decided['supervisor']} s" in " ".join(usage.stdout.split())


# --- api.py ---------------------------------------------------------------------------------

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2lnbmF0dXJlLXZhbHVl"


def test_redact_hides_secret_values_and_keeps_the_rest():
    known = ["hunter22-known"]
    data = {
        "access_token": JWT,
        "token_type": "bearer",
        "password_changed_at": "2026-11-07",
        "totp_enabled": True,
        "requires_totp": False,
        "hostname": "box",
        "app": {"env": ["TRANSMISSION_PASS=s3cret-value", "PUID=1000"], "PASS": "abc123", "USER": "tx"},
        "secrets": {"api_key": "k-1", "count": 2, "nested": ["x", "y"]},
        "log": "connected with hunter22-known; password=plain1 token: tok-2",
        "camelToken": "ct-3",
        "empty_password": "",
        # A Discord or Slack webhook URL holds its token (GET /notifications/configs); recovery
        # codes come as "codes"; a URL may carry a user and password, or a token, before its host.
        "discord_config": {"webhook_url": "https://discord.com/api/webhooks/1/wh-tok-4", "username": "UNOS"},
        "codes": ["rc-5a", "rc-5b"],
        "repo": "https://user:" + "url-pw-6" + "@example.org/x.git",
        "mirror": "https://url-tok-8" + "@example.org/y.git",
        "page": "https://example.org/a@b",
    }
    out = api.redact(data, known)
    flat = json.dumps(out)
    for leaked in (JWT, "s3cret-value", "abc123", "k-1", '"x"', "hunter22-known", "plain1", "tok-2", "ct-3",
                   "wh-tok-4", "rc-5a", "rc-5b", "url-pw-6", "url-tok-8"):
        assert leaked not in flat
    assert out["token_type"] == "bearer" and out["password_changed_at"] == "2026-11-07"
    assert out["totp_enabled"] is True and out["requires_totp"] is False and out["hostname"] == "box"
    assert out["app"]["env"] == ["TRANSMISSION_PASS=<redacted>", "PUID=1000"] and out["app"]["USER"] == "tx"
    assert out["secrets"]["count"] == 2 and out["empty_password"] == ""
    assert out["discord_config"]["username"] == "UNOS" and out["codes"] == ["<redacted>", "<redacted>"]
    assert out["repo"] == "https://<redacted>@example.org/x.git" and out["page"] == "https://example.org/a@b"


def test_redact_text_for_bodies_that_are_not_json():
    text = (f"Authorization: Bearer {JWT}\nUNOS_DEV_SSH_PASSWORD=pw-1 ok\nbypass mode: on\npassed: 3\n"
            "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/wh-tok-7\n"
            "clone https://user:" + "url-pw-9" + "@example.org/z.git ok\n")
    out = api.redact_text(text, ["pw-1"])
    assert JWT not in out and "pw-1" not in out and "wh-tok-7" not in out and "url-pw-9" not in out
    assert "bypass mode: on" in out and "passed: 3" in out
    assert "clone https://<redacted>@example.org/z.git ok" in out


def test_secret_values_follow_env_sh_names():
    settings = {"UNOS_DEV_HOST": "10.0.0.9", "UNOS_DEV_SSH_PASSWORD": "abcd", "TRANSMISSION_PASS": "longer-one",
                "UNOS_DEV_ADMIN_PASSWORD": "x", "SOME_TOKEN": "tok-tok"}
    assert api.secret_values(settings) == ["longer-one", "tok-tok", "abcd"]


@pytest.mark.parametrize("path", ["system/info", "/system/info?x=1", "/a/../b", "/a b", "/x#y"])
def test_api_path_refuses_what_is_not_an_api_path(path):
    with pytest.raises(ValueError):
        api.api_path(path)


def test_api_path_accepts_both_forms():
    assert api.api_path("/system/info") == api.api_path("/api/v1/system/info") == "/system/info"


def relay_reply(device: Device, status: int, body: object) -> None:
    (device.log / "reply").write_text(json.dumps({"status": status, "body": json.dumps(body)}))


def test_api_cli_sends_secrets_on_stdin_and_prints_them_redacted(device):
    relay_reply(device, 200, {"hostname": "box", "access_token": JWT, "env": [f"TRANSMISSION_PASS={device.tx_password}"],
                              "note": f"made with {device.admin_password}"})
    result = device.run(DEV / "api.py", "GET", "/system/info", "--param", "limit=5")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("HTTP 200\n") and '"hostname": "box"' in result.stdout
    for value in device.secrets + [JWT]:
        assert value not in result.stdout + result.stderr
    argv = (device.log / "argv").read_text()
    for value in device.secrets:
        assert value not in argv
    request = json.loads((device.log / "stdin").read_text())
    assert request["method"] == "GET" and request["path"] == "/system/info" and request["params"] == {"limit": ["5"]}
    assert request["login"] == {"username": "admin", "password": device.admin_password}
    assert request["cache"] == "/dev/shm/unos-dev-api.token" and request["auth"] is True
    assert request["base"] == "https://127.0.0.1/api/v1"


def test_api_cli_reads_a_json_body_and_reports_http_errors(device):
    relay_reply(device, 404, {"detail": "Not Found"})
    result = device.run(DEV / "api.py", "POST", "/shares/smb", "-", input='{"name": "x"}')
    assert result.returncode == 1 and result.stdout.startswith("HTTP 404")
    request = json.loads((device.log / "stdin").read_text())
    assert request["body"] == {"name": "x"} and request["has_body"] is True
    bad = device.run(DEV / "api.py", "POST", "/shares/smb", "-", input="not json")
    assert bad.returncode == 2


def test_api_cli_timeout_reaches_the_request(device):
    """A GET that waits for the end of an app's install job (ADR-0384) answers after the default
    150 s: --timeout gives the relay's request longer."""
    relay_reply(device, 200, {})
    result = device.run(DEV / "api.py", "--timeout", "3600", "GET", "/apps/installed")
    assert result.returncode == 0, result.stderr
    assert json.loads((device.log / "stdin").read_text())["timeout"] == 3600
    (device.log / "stdin").unlink()  # the stub ssh appends each run's standard input
    result = device.run(DEV / "api.py", "GET", "/apps/installed")
    assert result.returncode == 0, result.stderr
    assert json.loads((device.log / "stdin").read_text())["timeout"] == 150


@pytest.mark.parametrize("value", ["0", "-5", "ten"])
def test_api_cli_refuses_a_timeout_that_is_not_a_positive_number(device, value):
    result = device.run(DEV / "api.py", "--timeout", value, "GET", "/apps/installed")
    assert result.returncode == 2 and "--timeout" in result.stderr
    assert not (device.log / "argv").exists()


def test_api_cli_reports_relay_errors_redacted(device):
    (device.log / "reply").write_text(json.dumps({"error": f"login with {device.admin_password} failed"}))
    result = device.run(DEV / "api.py", "GET", "/system/info")
    assert result.returncode == 2 and "failed" in result.stderr
    assert device.admin_password not in result.stderr


def test_api_redact_filter(device):
    result = device.run(DEV / "api.py", "--redact", input=f"line with {device.ssh_password}\nplain line\n")
    assert result.returncode == 0
    assert result.stdout == "line with <redacted>\nplain line\n"


def test_api_cli_refuses_a_dev_env_with_a_wrong_mode(device):
    device.write_env(mode=0o644)
    result = device.run(DEV / "api.py", "GET", "/system/info")
    assert result.returncode == 2 and "chmod 600" in result.stderr
    assert not (device.log / "argv").exists()


def test_api_cli_forget_token_asks_the_relay_to_delete_the_cache(device):
    relay_reply(device, 200, {})
    result = device.run(DEV / "api.py", "--forget-token")
    assert result.returncode == 0 and result.stdout == "token deleted on the device\n", result.stderr
    # The request names the cache and carries no login.
    request = json.loads((device.log / "stdin").read_text())
    assert request == {"forget": True, "base": "https://127.0.0.1/api/v1", "cache": "/dev/shm/unos-dev-api.token",
                       "timeout": 150}


# --- api.py's relay, run here ---------------------------------------------------------------


class FakeApi:
    """The UNOS API as the relay meets it on the device, over plain HTTP on 127.0.0.1. POST
    /auth/login with the password of a user of `users` (the administrator's) hands out a new token (token-1, token-2, ...);
    any other request answers 200 when it carries a token of `valid` (and `accept` is true), else
    401. Every request's method, path and Authorization header are recorded."""

    def __init__(self, password: str) -> None:
        self.password = password
        self.users = {"admin": password}
        self.logins = 0
        self.valid: set[str] = set()
        self.accept = True
        self.requests: list[tuple[str, str, str | None]] = []
        self.bodies: list[tuple[str | None, bytes]] = []  # the Content-Type and body of each request
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, body: object) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def serve(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                bearer = self.headers.get("Authorization")
                fake.requests.append((self.command, self.path, bearer))
                fake.bodies.append((self.headers.get("Content-Type"), body))
                if (self.command, self.path) == ("POST", "/api/v1/auth/login"):
                    login = json.loads(body)
                    if set(login) != {"username", "password"} or fake.users.get(login["username"]) != login["password"]:
                        return self.reply(401, {"detail": "Invalid credentials"})
                    fake.logins += 1
                    fake.valid.add(f"token-{fake.logins}")
                    return self.reply(200, {"access_token": f"token-{fake.logins}", "token_type": "bearer"})
                if fake.accept and bearer in {f"Bearer {token}" for token in fake.valid}:
                    return self.reply(200, {"path": self.path})
                return self.reply(401, {"detail": "Not authenticated"})

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = serve

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tokens_sent(self) -> list[str | None]:
        """The Authorization headers of the requests other than the logins, in order."""
        return [bearer for _, path, bearer in self.requests if path != "/api/v1/auth/login"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ssh.sh's place for DeviceApi: runs the relay's command line here (unshare, mount: the stubs;
# python3: this interpreter), in an environment whose proxy does not answer: the relay must not
# use one.
RUN_HERE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    export PATH="@BIN@:$PATH" http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 \\
        HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 no_proxy= NO_PROXY=
    exec bash -c "$1"
    """
)


@pytest.fixture
def relay(local_device):
    """(DeviceApi, FakeApi, token cache): the relay of api.py runs here against a FakeApi, with
    its token cache in tmp_path instead of the device's /dev/shm."""
    device = local_device
    device.stub("python3", '#!/bin/sh\nexec @PYTHON@ "$@"\n')
    runner = device.bin / "run-here"
    runner.write_text(RUN_HERE.replace("@BIN@", str(device.bin)))
    runner.chmod(0o755)
    fake = FakeApi(device.admin_password)
    cache = device.tmp / "shm" / "unos-dev-api.token"
    cache.parent.mkdir()
    settings = {"UNOS_DEV_ADMIN_USER": "admin", "UNOS_DEV_ADMIN_PASSWORD": device.admin_password}
    client = api.DeviceApi(settings=settings, timeout=10, ssh=runner, base_url=fake.url, token_cache=str(cache))
    yield client, fake, cache
    fake.close()


def test_the_relay_keeps_the_token_private_and_signs_in_only_when_needed(relay):
    """Every login writes the system disk (ADR-0222): a first call signs in once and keeps the token
    in a file of mode 600, through no temporary file left behind; the next call reuses it; a 401
    brings exactly one new login, also when the new token is refused too; a request without a
    login sends no token; --forget-token deletes the cache. The password goes only in the
    login's body."""
    client, fake, cache = relay
    assert client.get("/system/info").status_code == 200
    assert fake.logins == 1 and cache.read_text() == "token-1"
    assert stat.S_ISREG(cache.lstat().st_mode) and oct(stat.S_IMODE(cache.lstat().st_mode)) == "0o600"
    assert [p.name for p in cache.parent.iterdir()] == [cache.name]

    assert client.get("/system/info").status_code == 200
    assert fake.logins == 1 and fake.tokens_sent() == ["Bearer token-1", "Bearer token-1"]

    fake.valid.clear()  # the API refuses the kept token (a new JWT secret, an expired token)
    assert client.get("/system/info").status_code == 200
    assert fake.logins == 2 and cache.read_text() == "token-2"
    assert fake.tokens_sent()[-2:] == ["Bearer token-1", "Bearer token-2"]

    fake.accept = False  # refused again after the new login: no second one, the 401 is the answer
    assert client.get("/system/info").status_code == 401 and fake.logins == 3

    fake.accept = True
    asked = len(fake.requests)
    assert client.request("GET", "/system/info", auth=False).status_code == 401
    assert fake.requests[asked:] == [("GET", "/api/v1/system/info", None)]

    client.forget_token()
    assert not cache.exists()
    client.forget_token()  # nothing left to delete
    assert not any(fake.password in path for _, path, _ in fake.requests)


def test_as_user_signs_in_as_that_user_with_a_token_cache_of_its_own(relay):
    """The scenario checks' temporary user signs in through the same relay with its own password
    and keeps its token in a cache of its own, apart from the administrator's; forget_token
    deletes only that one, and dev.env's secrets stay redacted in its errors."""
    client, fake, cache = relay
    fake.users["unoscheck1a2b3c"] = "pw-of-the-check-user"
    own = cache.parent / "unos-scenario-unoscheck1a2b3c.token"
    mine = client.as_user("unoscheck1a2b3c", "pw-of-the-check-user", str(own))
    assert client.get("/system/info").status_code == 200
    assert mine.get("/auth/me").status_code == 200
    assert fake.logins == 2 and cache.read_text() == "token-1" and own.read_text() == "token-2"
    assert oct(stat.S_IMODE(own.lstat().st_mode)) == "0o600"
    assert fake.tokens_sent() == ["Bearer token-1", "Bearer token-2"]
    logins = [json.loads(body) for (method, path, _), (_, body) in zip(fake.requests, fake.bodies)
              if path == "/api/v1/auth/login"]
    assert [login["username"] for login in logins] == ["admin", "unoscheck1a2b3c"]
    assert set(client.secrets) < set(mine.secrets) and "pw-of-the-check-user" in mine.secrets
    assert (mine.timeout, mine.ssh, mine.base_url) == (client.timeout, client.ssh, client.base_url)
    mine.forget_token()
    assert not own.exists() and cache.read_text() == "token-1"
    fake.users["unoscheck1a2b3c"] = "another"
    with pytest.raises(api.ApiError, match="login as unoscheck1a2b3c failed: HTTP 401"):
        mine.get("/auth/me")


@pytest.mark.parametrize("planted", ["mode 644", "symlink", "fifo"])
def test_the_relay_never_reads_a_cache_that_is_not_its_own_private_file(relay, planted):
    """/dev/shm is shared by every user of the device: a token cache that is not a regular file
    of the relay's user (root there) with mode 600 is never read, whatever valid token it
    holds. The relay signs in and replaces it with a file of its own; a symlink's target stays
    as it was, and a FIFO does not make it wait. (A file of another owner needs root to set up;
    the same check covers it.)"""
    client, fake, cache = relay
    token = "token-planted"
    fake.valid.add(token)
    target = cache.parent.parent / "elsewhere"
    if planted == "mode 644":
        cache.write_text(token)
        cache.chmod(0o644)
    elif planted == "symlink":
        target.write_text(token)
        target.chmod(0o600)
        cache.symlink_to(target)
    else:
        os.mkfifo(cache, 0o600)
    assert client.get("/system/info").status_code == 200
    assert fake.logins == 1 and fake.tokens_sent() == ["Bearer token-1"]
    assert stat.S_ISREG(cache.lstat().st_mode) and oct(stat.S_IMODE(cache.lstat().st_mode)) == "0o600"
    assert cache.read_text() == "token-1"
    if planted == "symlink":
        assert target.read_text() == token


def test_the_relay_uploads_a_file_as_a_form_also_after_a_new_login(relay):
    """upload() sends the file's bytes as the form field "file" of multipart/form-data, as the
    web UI does (the scenario checks upload this way, ADR-0304); a 401 brings one new login and
    the same form again."""
    client, fake, cache = relay
    content = b"bytes \x00\xff\r\n--not-a-boundary\n"
    for login in (1, 2):
        answer = client.upload("/files/upload", {"path": "/mnt/pools/data/x y"}, "a.txt", content)
        assert answer.status_code == 200 and fake.logins == login
        method, path, bearer = fake.requests[-1]
        assert (method, path) == ("POST", "/api/v1/files/upload?path=%2Fmnt%2Fpools%2Fdata%2Fx+y")
        ctype, body = fake.bodies[-1]
        assert ctype.startswith("multipart/form-data; boundary=unos-")
        boundary = ctype.split("boundary=", 1)[1]
        assert body == (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
                        "Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        assert boundary.encode() not in content
        fake.valid.clear()  # the next upload meets a 401 first
    assert [b for t, b in fake.bodies if t and t.startswith("multipart/")][-2] == fake.bodies[-1][1]
    for bad in ("../a", "a/b", 'a"b', "a\nb", ""):
        with pytest.raises(ValueError):
            client.upload("/files/upload", {"path": "/x"}, bad, b"")


def test_the_relay_writes_the_token_through_no_link(relay):
    """The new token goes to a new file with a name nobody can guess (mkstemp), renamed over the
    cache: a link planted next to the cache, here at the fixed name an earlier version used,
    is not written through."""
    client, fake, cache = relay
    target = cache.parent.parent / "elsewhere"
    target.write_text("")
    (cache.parent / (cache.name + ".new")).symlink_to(target)
    assert client.get("/system/info").status_code == 200
    assert target.read_text() == "" and stat.S_ISREG(cache.lstat().st_mode) and cache.read_text() == "token-1"


def test_the_relay_calls_only_the_api_of_the_device_itself(relay):
    """The relay sends the administrator's login and token only to a loopback address."""
    client, fake, cache = relay
    for url in ("https://10.0.0.9/api/v1", "https://127.0.0.1.example.org/api/v1", "file:///etc/api/v1"):
        client.base_url = url
        with pytest.raises(api.ApiError, match="only the API of the device itself"):
            client.get("/system/info")
    assert fake.requests == [] and not cache.exists()


# --- tree.py --------------------------------------------------------------------------------


def make_tree(root: Path, files: dict[str, object]) -> None:
    """files: path -> text, ("x", text) for an executable, ("link", target) for a symlink."""
    for path, spec in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(spec, tuple) and spec[0] == "link":
            full.symlink_to(spec[1])
        elif isinstance(spec, tuple):
            full.write_text(spec[1])
            full.chmod(0o755)
        else:
            full.write_text(spec)
            full.chmod(0o644)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_manifest_records_type_executable_bit_and_hash(tmp_path):
    make_tree(tmp_path, {"a.txt": "alpha\n", "bin/run.sh": ("x", "#!/bin/sh\n"), "link": ("link", "a.txt")})
    m = tree.manifest(tmp_path, ["a.txt", "bin/run.sh", "link", "deleted.txt"])
    assert m == {
        "a.txt": {"kind": "file", "x": False, "size": 6, "sha256": sha("alpha\n")},
        "bin/run.sh": {"kind": "file", "x": True, "size": 10, "sha256": sha("#!/bin/sh\n")},
        "link": {"kind": "link", "x": False, "size": 5, "sha256": sha("a.txt")},
    }


def test_compare_finds_every_kind_of_difference(tmp_path):
    here, there = tmp_path / "here", tmp_path / "there"
    common = {"same.txt": "same\n", "docs/x.md": "x\n"}
    make_tree(here, {**common, "changed.py": "new\n", "mode.sh": ("x", "s\n"), "type": "file\n", "new.txt": "n\n"})
    make_tree(there, {**common, "changed.py": "old\n", "mode.sh": "s\n", "type": ("link", "same.txt"), "gone.txt": "g\n"})
    names = ["same.txt", "docs/x.md", "changed.py", "mode.sh", "type", "new.txt", "gone.txt"]
    diff = tree.compare(tree.manifest(here, names), tree.manifest(there, names))
    assert diff.changed == ["changed.py", "type"] and diff.mode == ["mode.sh"]
    assert diff.only_local == ["new.txt"] and diff.only_remote == ["gone.txt"]
    assert [how for how, _ in diff.items()] == ["changed", "only on device", "mode", "only here", "changed"]
    assert not tree.compare(tree.manifest(here, common), tree.manifest(there, common))


@pytest.mark.parametrize(
    "path, expected",
    [("supervisor/src/main.py", "supervisor"), ("supervisor/src/shares/smbconf.py", "supervisor"),
     ("supervisor/requirements.txt", "supervisor"), ("supervisor/Dockerfile", "supervisor"),
     ("supervisor/requirements-dev.txt", None), ("supervisor/tests/test_smbconf.py", None),
     ("supervisor/Dockerfile.test", None), ("webui/src/routes/+page.svelte", "webui"),
     ("webui/static/favicon.png", "webui"), ("webui/package-lock.json", "webui"), ("webui/Caddyfile", "webui"),
     ("webui/tests/api.spec.ts", None), ("webui/playwright.config.ts", None), ("deploy/docker-compose.yml", "deploy"),
     ("deploy/update.sh", "deploy"), ("deploy/docker-compose.test.yml", None), ("deploy/AGENTS.md", None),
     ("docs/adr/0001-x.md", None), ("scripts/dev/deploy.sh", None), ("AGENTS.md", None)],
)
def test_runtime_components(path, expected):
    assert tree.component(path) == expected


def test_status_separates_runtime_files_from_the_rest():
    local = {"supervisor/src/main.py": {"kind": "file", "x": False, "size": 1, "sha256": "a"},
             "docs/x.md": {"kind": "file", "x": False, "size": 1, "sha256": "b"}}
    remote = {**local, "docs/x.md": {"kind": "file", "x": False, "size": 1, "sha256": "c"}}
    lines = tree.status_lines(tree.compare(local, remote), "/here", "root@h:/opt/unos", (2, 2))
    assert lines[1] == "runtime files (supervisor, webui, deploy): equal"
    assert lines[2] == "other files (docs, tests, tooling): 1 differ" and lines[3].split() == ["changed", "docs/x.md"]
    remote["supervisor/src/main.py"] = {"kind": "file", "x": True, "size": 1, "sha256": "a"}
    lines = tree.status_lines(tree.compare(local, remote), "/here", "root@h:/opt/unos", (2, 2))
    assert lines[1].startswith("runtime files: 1 differ (supervisor 1, webui 0, deploy 0)")
    assert lines[2].split() == ["mode", "supervisor/src/main.py"]


def test_git_files_lists_what_git_tracks_or_would_add(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    env = without_git_variables()
    subprocess.run(git + ["init", "-q"], check=True, env=env)
    make_tree(tmp_path, {".gitignore": "*.env\nbuild/\n", "tracked.txt": "t\n", "gone.txt": "g\n",
                         "untracked.txt": "u\n", "dev.env": "SECRET=1\n", "build/out.js": "o\n"})
    subprocess.run(git + ["add", ".gitignore", "tracked.txt", "gone.txt"], check=True, env=env)
    subprocess.run(git + ["commit", "-q", "-m", "x"], check=True, env=env)
    (tmp_path / "gone.txt").unlink()
    assert tree.git_files(tmp_path) == [".gitignore", "gone.txt", "tracked.txt", "untracked.txt"]
    assert sorted(tree.manifest(tmp_path)) == [".gitignore", "tracked.txt", "untracked.txt"]


def test_git_files_lists_the_checkout_despite_a_hooks_git_variables(tmp_path, monkeypatch):
    """git exports GIT_DIR, GIT_INDEX_FILE and more to its hooks and to `git rebase -x`: tree.py
    still lists the files of the checkout it was given, the ones status and sync compare."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    here, other = tmp_path / "here", tmp_path / "other"
    make_tree(here, {"mine.txt": "m\n"})
    make_tree(other, {"theirs.txt": "t\n"})
    git_init(here)
    git_init(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert tree.git_files(here) == ["mine.txt"]


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", ".git/config", "a//b", "a/../b", "dir/", "", "a\nb"])
def test_sync_refuses_unsafe_paths(path):
    with pytest.raises(tree.TreeError):
        tree.check_paths([path])
    tree.check_paths(["supervisor/src/main.py", ".github/workflows/test.yml", "webui/.dockerignore"])


def test_make_tar_writes_root_owned_files_with_plain_modes(tmp_path):
    make_tree(tmp_path, {"a.txt": "alpha\n", "run.sh": ("x", "#!/bin/sh\n"), "link": ("link", "a.txt")})
    (tmp_path / "a.txt").chmod(0o600)
    import io

    with tarfile.open(fileobj=io.BytesIO(tree.make_tar(tmp_path, ["a.txt", "run.sh", "link"]))) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == {"a.txt", "run.sh", "link"}
        assert all(m.uid == 0 and m.gid == 0 and m.uname == "root" for m in members.values())
        assert members["a.txt"].mode == 0o644 and members["run.sh"].mode == 0o755
        assert members["link"].issym() and members["link"].linkname == "a.txt"
        assert tar.extractfile("a.txt").read() == b"alpha\n"


def test_the_device_is_read_with_a_read_only_root():
    dev = tree.Device(host="root@h", repo="/opt/my unos", state_dir=Path("/tmp/x"), ssh=Path("/bin/false"))
    command = dev.read_only("python3 -I -B - manifest .")
    assert command.startswith("unshare --mount --propagation private sh -c ")
    assert "mount -o remount,bind,ro,noatime / && cd \"$1\" && python3 -I -B - manifest ." in command
    assert command.endswith(" sh '/opt/my unos'")


def test_requirement_pins_and_the_rootfs_listing(tmp_path):
    req = tmp_path / "requirements-dev.txt"
    req.write_text("# tools\n-r requirements.txt\npytest==9.2.0\npytest-asyncio==1.4.1.post1  # async\n"
                   "uvicorn[standard]==0.54.0\nPyYAML==6.0.3\n")
    assert tree.pins(req) == {"pytest": "9.2.0", "pytest-asyncio": "1.4.1.post1", "uvicorn": "0.54.0", "pyyaml": "6.0.3"}
    python, dists = tree.rootfs_dists("python3.11/site-packages/fastapi-0.162.0.dist-info\n"
                                      "python3.11/site-packages/typing_extensions-4.15.0.dist-info\nmachine=aarch64\n")
    assert python == "3.11" and dists == {"fastapi": "0.162.0", "typing-extensions": "4.15.0"}
    env = tree.target_environment("3.11", "aarch64")
    assert tree.requirement_name("colorama>=0.4; sys_platform == 'win32'", env) is None
    assert tree.requirement_name("pytest-cov; extra == 'testing'", env) is None
    assert tree.requirement_name("Pygments>=2.7.2", env) == "pygments"


# --- the static scan ------------------------------------------------------------------------

# SSH_ASKPASS names the helper program, not a secret.
SHELL_SECRET_EXPANSION = re.compile(r"\$\{?(?!SSH_ASKPASS)[A-Za-z0-9_]*(PASS|TOKEN|SECRET|API_KEY|PRIVATE_KEY)")
SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output", "system", "execv", "execvp", "execve", "spawnv"}


def command_line_problems(shell_scripts, python_scripts) -> list[str]:
    """Places where a script would put a secret on a command line (see the test below)."""
    problems = []
    for script in shell_scripts:
        for n, line in enumerate(Path(script).read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            where = f"{Path(script).name}:{n}"
            if SHELL_SECRET_EXPANSION.search(line):
                problems.append(f"{where}: expands a secret's variable")
            exported = re.findall(r"\bexport\s+([^#;&|]*)", line)
            names = [n.split("=", 1)[0] for group in exported for n in group.split()]
            if any(SECRET_NAME.search(n) and not n.startswith("SSH_ASKPASS") for n in names):
                problems.append(f"{where}: exports a secret")
    for script in list(shell_scripts) + list(python_scripts):
        text = Path(script).read_text()
        if "sshpass" in text or re.search(r"curl\s[^\n]*\s(-u|--user)\s", text) or re.search(r"--password[= ]", text):
            problems.append(f"{Path(script).name}: sshpass, curl -u or --password")
    for script in python_scripts:
        for node in ast.walk(ast.parse(Path(script).read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name not in SUBPROCESS_CALLS:
                continue
            argv = list(node.args[:1]) + [k.value for k in node.keywords if k.arg == "args"]
            for part in argv:
                for sub in ast.walk(part):
                    ident = sub.id if isinstance(sub, ast.Name) else sub.attr if isinstance(sub, ast.Attribute) else ""
                    if re.search(r"pass|secret|token|cred", ident, re.IGNORECASE):
                        problems.append(f"{Path(script).name}:{node.lineno}: {ident} on a command line")
    return problems


def test_no_script_puts_a_secret_on_a_command_line():
    """Secrets reach other programs only through a pipe: askpass.sh to ssh, standard input to
    the relay. In the shell scripts no secret's variable is ever expanded or exported (the
    values are read by dev_secret into a pipe); in the Python scripts no command line given
    to subprocess or os mentions a password, token or secret; nothing uses sshpass, curl -u
    or --password."""
    assert command_line_problems(SHELL_SCRIPTS, PYTHON_SCRIPTS) == []


def test_the_scan_finds_secrets_on_command_lines(tmp_path):
    bad_shell = tmp_path / "bad.sh"
    bad_shell.write_text(
        "#!/usr/bin/env bash\n"
        "# a comment with $UNOS_DEV_SSH_PASSWORD is fine\n"
        'ssh -o "x=$UNOS_DEV_SSH_PASSWORD" host\n'
        'curl -u "admin:${UNOS_DEV_ADMIN_PASSWORD}" https://h\n'
        "export TRANSMISSION_PASS\n"
        "export SSH_ASKPASS SSH_ASKPASS_REQUIRE\n"
        "sshpass -f file ssh host\n"
    )
    bad_python = tmp_path / "bad.py"
    bad_python.write_text(
        "import os, subprocess\n"
        "subprocess.run(['curl', '--data', password])\n"
        "subprocess.run(args=['x', self.token])\n"
        "subprocess.run(['ok'], input=password)\n"
        "os.system('mysql --password=' + secret)\n"
    )
    problems = command_line_problems([bad_shell], [bad_python])
    assert problems == [
        "bad.sh:3: expands a secret's variable",
        "bad.sh:4: expands a secret's variable",
        "bad.sh:5: exports a secret",
        "bad.sh: sshpass, curl -u or --password",
        "bad.py: sshpass, curl -u or --password",
        "bad.py:2: password on a command line",
        "bad.py:3: token on a command line",
        "bad.py:5: secret on a command line",
    ]


def test_no_device_password_is_written_into_the_repository_tooling():
    """dev.env.example and the skills hold no value for a secret."""
    skills = REPO_ROOT / ".agents" / "skills"
    for path in (REPO_ROOT / "dev.env.example", skills / "deploy-device" / "SKILL.md", skills / "device-checks" / "SKILL.md"):
        for name, value in re.findall(r"^([A-Z_][A-Z0-9_]*)=(\S*)", path.read_text(), flags=re.M):
            if SECRET_NAME.search(name):
                assert value == "", f"{path}: {name} has a value"


# --- the skills -----------------------------------------------------------------------------


SKILLS = REPO_ROOT / ".agents" / "skills"


def front_matter(text: str) -> dict[str, str]:
    front = re.match(r"---\n(.*?)\n---\n", text, flags=re.S)
    assert front, "front matter"
    return dict(line.split(": ", 1) for line in front.group(1).splitlines())


@pytest.mark.parametrize("name", ["deploy-device", "device-checks"])
def test_skill_and_its_claude_symlink(name):
    skill = SKILLS / name / "SKILL.md"
    text = skill.read_text()
    fields = front_matter(text)
    assert fields["name"] == name and len(fields["description"]) > 40
    for script in re.findall(r"scripts/dev/[\w./-]+", text):
        assert (REPO_ROOT / script.rstrip(".")).exists(), script
    link = REPO_ROOT / ".claude" / "skills" / name
    assert link.is_symlink() and os.readlink(link) == f"../../.agents/skills/{name}"
    assert (link / "SKILL.md").read_text() == text


def test_only_the_owner_starts_a_deploy():
    """A deploy replaces what runs on the device and writes its system disk: the model never loads
    the deploy-device skill on its own, and the skill says so (AGENTS.md: the owner asks)."""
    text = (SKILLS / "deploy-device" / "SKILL.md").read_text()
    fields = front_matter(text)
    assert fields.get("disable-model-invocation") == "true"
    assert fields["description"].endswith("Use only when the owner has asked for a deploy in this conversation.")


def scripts_calling(pattern: str) -> list[str]:
    """The scripts of scripts/dev (env.sh, which defines the helpers, aside) whose code, not
    their comments, matches PATTERN."""
    found = []
    for script in SHELL_SCRIPTS:
        code = "\n".join(line for line in script.read_text().splitlines() if not line.lstrip().startswith("#"))
        if script.name != "env.sh" and re.search(pattern, code, flags=re.M):
            found.append(script.relative_to(DEV).as_posix())
    return sorted(found)


def scripts_named(text: str, phrase: str) -> list[str]:
    """The scripts (`name.sh`, `name.py`) that the one sentence of TEXT with PHRASE names."""
    flat = " ".join(text.split())
    found = [s for s in re.split(r"(?<=[.;:])\s+", flat) if phrase in s]
    assert len(found) == 1, (phrase, found)
    return sorted(set(re.findall(r"`([\w-]+\.(?:sh|py))", found[0])))


def test_the_device_checks_skill_says_which_scripts_lock_and_report_sd_writes():
    """What the skill (like AGENTS.md and ADR-0210) says about the scripts is what they do: a
    script that starts or stops taking the device lock or reporting its SD writes fails here
    until the skill says so too."""
    locking = scripts_calling(r"^\s*dev_lock\b")
    reporting = scripts_calling(r"\bdev_sd_report(_from)?\b")
    assert locking == ["deploy.sh", "ipv6-filter.sh", "linux-tests.sh", "scenario.sh"]
    assert reporting == ["check.sh", "deploy.sh", "ipv6-filter.sh", "linux-tests.sh", "scenario.sh", "sd.sh"]
    text = (SKILLS / "device-checks" / "SKILL.md").read_text()
    assert scripts_named(text, "take the device lock") == locking
    assert scripts_named(text, "print `SD writes") == reporting


def test_the_device_skills_measure_every_other_command_and_run_testparm_in_one_process():
    """ssh.sh, api.py and the testparm shim print no SD writes of their own: every step that runs
    them does so under sd.sh measure, so that the report has a number for each check. pytest runs
    through make test, in one process (TEST_WORKERS=0): one testparm at a time on the device."""
    text = (SKILLS / "device-checks" / "SKILL.md").read_text()
    steps = text.split("## Steps", 1)[1].split("## Gotchas", 1)[0]
    # The command lines of the steps (code spans with arguments), not the names in the prose.
    commands = [span for span in re.findall(r"`([^`]+)`", steps) if " " in span and span.startswith(("scripts/dev/", "PATH="))]
    unmeasured = [c for c in commands if re.search(r"scripts/dev/(ssh\.sh|api\.py|bin)\b", c) and "scripts/dev/sd.sh measure " not in c]
    assert len(commands) >= 4 and not unmeasured, commands
    assert any("make test TESTS=tests/test_smbconf_testparm.py TEST_WORKERS=0" in c for c in commands), commands
    for name in ("device-checks", "deploy-device"):
        skill = (SKILLS / name / "SKILL.md").read_text()
        assert not re.search(r"-m pytest|\bpytest tests/", skill), name


def test_the_supervisor_build_gets_the_version_of_the_workstations_tree():
    """The device's checkout is synced file by file, without git: deploy.sh passes git describe of this tree (ADR-0234)."""
    text = (DEV / "deploy.sh").read_text()

    assert "describe --tags --always --dirty" in text
    assert 'dev_remote "$REMOTE_BUILD_SUPERVISOR" "$UNOS_DEV_REPO" "$version"' in text
    assert "UNOS_VERSION=${2:-unknown} env -u JWT_SECRET docker compose build supervisor" in text
    # Only a plain version reaches the device's shell.
    assert "*[!A-Za-z0-9._+-]*) version=unknown" in text


# docker for deploy.sh build-supervisor on the device: the compose project, the images named in
# FAKE_IMAGES (separated by spaces), the build cache's records in FAKE_BUILD_CACHE (the text of
# `docker buildx du --verbose`), the build and the checks of its result. It logs every call to
# FAKE_SSH_LOG/docker and fails on any other.
STUB_DOCKER_BUILD = textwrap.dedent(
    """\
    #!@PYTHON@
    import json, os, sys
    args = sys.argv[1:]
    with open(os.path.join(os.environ["FAKE_SSH_LOG"], "docker"), "a") as log:
        log.write(" ".join(args) + "\\n")
    if args == ["compose", "config", "--format", "json"]:
        print(json.dumps({"name": "deploy"}))
    elif args == ["buildx", "du", "--verbose"]:
        sys.stdout.write(os.environ.get("FAKE_BUILD_CACHE", ""))
    elif args[:2] == ["image", "inspect"] and len(args) == 3:
        sys.exit(0 if args[2] in os.environ.get("FAKE_IMAGES", "").split() else 1)
    elif args == ["compose", "build", "supervisor"]:
        print("built")
    elif args == ["tag", "deploy-supervisor:latest", "deploy-supervisor:rollback"]:
        pass
    elif args == ["compose", "config", "--quiet"]:
        pass
    elif args[:3] == ["image", "inspect", "-f"] and args[4:] == ["deploy-supervisor:latest"]:
        print("PYTHONDONTWRITEBYTECODE=1" if ".Config.Env" in args[3] else "deploy-supervisor:latest sha256:1")
    else:
        sys.exit(f"the docker stub does not know {args}")
    """
)


def test_deploy_sh_build_supervisor_says_whether_its_pinned_base_is_on_the_device(trees):
    """supervisor/Dockerfile pins its base by digest (ADR-0305). Before the build,
    build-supervisor says whether the device has that base: as an image, or as the layers that
    BuildKit pulled into its build cache, which `docker buildx du --verbose` describes as "pulled
    from <name>:<tag>@<digest>". When it has neither, the build pulls the base and runs apt-get and
    pip again, hundreds of MB on the system disk (ADR-0214), unless the image the digest names for the
    device's platform is one the cache holds under another digest: the note says both. A base
    without a whole digest is refused before anything on the device changes."""
    device, repo, checkout = trees
    pinned = "python:3.11-slim@sha256:" + "6" * 64
    other = "python:3.11-slim@sha256:" + "0" * 64
    device.stub("docker", STUB_DOCKER_BUILD)
    device.stub("python3", '#!/bin/sh\nexec @PYTHON@ "$@"\n')

    def cache(*bases: str) -> str:
        return "".join(f"ID:           layer{n}\nDescription:  pulled from docker.io/library/{base}\n"
                       f"Type:         regular\n\n" for n, base in enumerate(bases))

    def build(dockerfile: str, **fake: str) -> subprocess.CompletedProcess:
        for root in (repo, checkout):
            make_tree(root, {"supervisor/Dockerfile": dockerfile, "supervisor/src/main.py": "same\n"})
        (device.log / "docker").unlink(missing_ok=True)
        env = device.env(FAKE_SSH_MODE="local", UNOS_DEV_REPO=str(checkout), UNOS_DEV_PYTHON=sys.executable, **fake)
        result = subprocess.run([str(repo / "scripts" / "dev" / "deploy.sh"), "build-supervisor"], cwd=repo,
                                env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        assert wait_for(lambda: lock_is_free(device.lock))
        return result

    def calls() -> list[str]:
        return (device.log / "docker").read_text().splitlines()

    for fake in ({"FAKE_BUILD_CACHE": cache(other, pinned)}, {"FAKE_IMAGES": pinned}):
        result = build(f"FROM {pinned}\nWORKDIR /app\n", **fake)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"base {pinned}: on the device" in result.stdout, fake
        assert "note:" not in result.stderr and "compose build supervisor" in calls(), fake

    result = build(f"FROM {pinned}\nWORKDIR /app\n", FAKE_BUILD_CACHE=cache(other), FAKE_IMAGES="deploy-supervisor:latest")
    assert result.returncode == 0, result.stdout + result.stderr
    note = " ".join(result.stderr.split())
    assert f"note: no layers of the build cache come from the base {pinned}" in note
    assert "pulls it and runs apt-get and pip again" in note and "unless" in note
    made = calls()
    assert made.index("tag deploy-supervisor:latest deploy-supervisor:rollback") < made.index("compose build supervisor")

    for unpinned in ("python:3.11-slim", "python:3.11-slim@sha256:", "python:3.11-slim@sha256:" + "6" * 63):
        result = build(f"FROM {unpinned}\nWORKDIR /app\n", FAKE_BUILD_CACHE=cache(pinned),
                       FAKE_IMAGES="deploy-supervisor:latest")
        assert result.returncode == 1, result.stdout + result.stderr
        assert f"names the base {unpinned} without a digest" in result.stderr
        assert not [call for call in calls() if call.startswith(("tag ", "compose build"))], unpinned


# --- the inbound IPv6 filter of the device (ADR-0301) ---------------------------------------

FILTER_RULES = DEV / "device" / "ipv6-inbound.nft"
FILTER_UNIT = DEV / "device" / "unos-ipv6-inbound.service"


def nft_statements(path: Path) -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def test_the_ipv6_filter_admits_only_the_lan_and_the_bittorrent_port():
    # IPv6 only, in a table of its own that the file replaces in one transaction (IPv4 and
    # Docker's iptables-nft tables stay as they are). Input and forward drop what the chain admit
    # does not accept; the one port open to every source is BitTorrent's. The whole list, in
    # order: an added accept or set element fails here.
    assert nft_statements(FILTER_RULES) == [
        "table ip6 unos_ipv6_inbound", "delete table ip6 unos_ipv6_inbound", "table ip6 unos_ipv6_inbound {",
        "set lan {", "type ipv6_addr", "flags interval", "elements = { fe80::/10, fc00::/7 }", "}",
        "chain admit {",
        "ct state vmap { established : accept, related : accept, invalid : drop }",
        "meta l4proto ipv6-icmp accept",
        "ip6 saddr @lan accept",
        "meta l4proto { tcp, udp } th dport 51413 accept",
        "}",
        "chain input {", "type filter hook input priority filter; policy drop;", 'iif "lo" accept', "jump admit", "}",
        "chain forward {", "type filter hook forward priority filter; policy drop;", "jump admit", "}",
        "}",
    ]


def test_the_ipv6_filter_unit_loads_before_the_network_and_removes_only_its_table():
    unit = FILTER_UNIT.read_text()
    # network-pre.target is passive: without Wants=, Before= would order against nothing.
    for line in ("DefaultDependencies=no", "Wants=network-pre.target", "Before=network-pre.target shutdown.target",
                 "Conflicts=shutdown.target", "Type=oneshot", "RemainAfterExit=yes",
                 "ExecStart=/usr/sbin/nft -f /etc/unos-dev/ipv6-inbound.nft",
                 "ExecReload=/usr/sbin/nft -f /etc/unos-dev/ipv6-inbound.nft",
                 "ExecStop=-/usr/sbin/nft delete table ip6 unos_ipv6_inbound", "WantedBy=sysinit.target"):
        assert line in unit.splitlines(), line
    assert "flush" not in unit
    # deploy/uninstall.sh deletes /etc/unos; the filter's file lives apart from the installation.
    assert "/etc/unos/" not in unit and "/etc/unos/" not in (DEV / "ipv6-filter.sh").read_text()


# The device side of ipv6-filter.sh install, run here (FAKE_SSH_MODE=local) below the root
# UNOS_DEV_FILTER_ROOT: ip answers FAKE_IP_ROUTES and FAKE_IP_ADDRS; nft and systemctl log their
# arguments to FAKE_LOG, nft -c keeps a copy of the file it checked and fails with
# FAKE_NFT_CHECK=1, and systemctl keeps the unit's enabled state in FAKE_ENABLED.
STUB_IP = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    # A write to a closed pipe ends ip, as SIGPIPE does on the device (here SIGPIPE may be ignored).
    lines() { printf "%s" "$1" | while IFS= read -r line; do echo "$line" || exit 141; [ -z "${FAKE_IP_PAUSE:-}" ] || sleep "$FAKE_IP_PAUSE"; done; }
    case $* in
        "-6 -o route show") lines "${FAKE_IP_ROUTES-}" ;;
        "-6 -o addr show scope global") lines "${FAKE_IP_ADDRS-}" ;;
        *) echo "the ip stub does not know: $*" >&2; exit 2 ;;
    esac
    """
)
# FAKE_TABLE exists while the table is loaded: the unit's start or reload loads it, its stop and
# nft delete table delete it.
STUB_NFT_CHECK = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo "nft $*" >> "$FAKE_LOG"
    case $* in
        "-c -f "*) cp "$3" "$FAKE_CHECKED"; exit "${FAKE_NFT_CHECK:-0}" ;;
        "delete table ip6 unos_ipv6_inbound" | "list table ip6 unos_ipv6_inbound")
            [ -e "$FAKE_TABLE" ] || { echo "Error: No such file or directory" >&2; exit 1; }
            [ "$1" = list ] || rm -f "$FAKE_TABLE" ;;
        *) echo "the nft stub does not know: $*" >&2; exit 2 ;;
    esac
    """
)
STUB_SYSTEMCTL_UNIT = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo "systemctl $*" >> "$FAKE_LOG"
    case $1 in
        is-enabled) if [ -e "$FAKE_ENABLED" ]; then echo enabled; else echo disabled; exit 1; fi ;;
        enable) touch "$FAKE_ENABLED" ;;
        disable) rm -f "$FAKE_ENABLED"; [ "$2" != --now ] || rm -f "$FAKE_TABLE" ;;
        reload-or-restart) touch "$FAKE_TABLE" ;;
        is-active) echo active ;;
    esac
    """
)
ROUTES = (
    "2001:db8:1::/64 dev eth0 proto ra metric 100 expires 86000sec pref medium\n"
    "fd00:1::/64 dev eth0 proto ra metric 100 pref medium\n"
    "2001:2::c633:6425 nhid 5 via fe80::1 dev eth0 metric 100 pref low\n"
    "2001:db8:9::/48 via fe80::1 dev eth0 proto ra metric 100 pref medium\n"
    "2001:db8:7::/48 proto static metric 1024 pref medium \\\tnexthop via fe80::1 dev eth0 weight 1 "
    "\\\tnexthop via fe80::2 dev eth0 weight 1 \n"
    "3fff:5::/64 dev wg0 proto kernel metric 256 pref medium\n"
    "unreachable 2001:db8:dead::/64 dev lo metric 1024 pref medium\n"
    "default nhid 5 via fe80::1 dev eth0 proto ra metric 100 pref medium\n"
    "fe80::/64 dev eth0 proto kernel metric 256 pref medium\n"
)
ADDRS = "2: eth0    inet6 2001:db8:1::5/64 scope global dynamic mngtmpaddr\n"


@pytest.fixture
def filter_device(local_device):
    device = local_device
    device.stub("ip", STUB_IP)
    device.stub("nft", STUB_NFT_CHECK)
    device.stub("systemctl", STUB_SYSTEMCTL_UNIT)
    device.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    root = device.tmp / "root"
    (root / "dev" / "shm").mkdir(parents=True)
    device.root = root
    return device


def run_filter(device, step: str, **extra: str) -> subprocess.CompletedProcess:
    env = {"FAKE_SSH_MODE": "local", "UNOS_DEV_FILTER_ROOT": str(device.root), "FAKE_LOG": str(device.tmp / "calls"),
           "FAKE_CHECKED": str(device.tmp / "checked.nft"), "FAKE_ENABLED": str(device.tmp / "enabled"),
           "FAKE_TABLE": str(device.tmp / "table"), "FAKE_IP_ROUTES": ROUTES, "FAKE_IP_ADDRS": ADDRS, **extra}
    (device.tmp / "calls").write_text("")
    return device.run(DEV / "ipv6-filter.sh", step, **env)


def install_filter(device, **extra: str) -> subprocess.CompletedProcess:
    return run_filter(device, "install", **extra)


def filter_calls(device) -> list[str]:
    """The calls of nft and systemctl that change something (not the is-* and nft -c ones)."""
    calls = (device.tmp / "calls").read_text().splitlines()
    return [c for c in calls if not c.startswith(("systemctl is-", "nft -c "))]


def test_ipv6_filter_sh_install_adds_the_on_link_global_prefixes_checks_and_loads(filter_device):
    device = filter_device
    result = install_filter(device)
    assert result.returncode == 0, result.stdout + result.stderr
    rules = (device.root / "etc" / "unos-dev" / "ipv6-inbound.nft").read_text()
    # The repository's file, then the on-link global prefixes, whatever the route's origin: not
    # unique-local ones (fc00::/7 is in the set), routes through a gateway (multipath ones
    # included), host routes or others.
    assert rules == FILTER_RULES.read_text() + (
        "\n# The global prefixes of the LAN: on-link routes of the device at install time.\n"
        "add element ip6 unos_ipv6_inbound lan { 2001:db8:1::/64, 3fff:5::/64 }\n")
    assert "global LAN prefixes: 2001:db8:1::/64, 3fff:5::/64" in result.stdout
    assert (device.root / "etc" / "systemd" / "system" / "unos-ipv6-inbound.service").read_text() == FILTER_UNIT.read_text()
    # nft checked that very file, in RAM, before it reached /etc.
    calls = (device.tmp / "calls").read_text().splitlines()
    checked = [c for c in calls if c.startswith("nft -c -f ")]
    assert len(checked) == 1 and checked[0].startswith(f"nft -c -f {device.root}/dev/shm/")
    assert (device.tmp / "checked.nft").read_text() == rules
    assert filter_calls(device) == [
        "systemctl daemon-reload", "systemctl enable --quiet unos-ipv6-inbound.service",
        "systemctl reload-or-restart unos-ipv6-inbound.service"]
    assert list((device.root / "dev" / "shm").iterdir()) == []
    assert "unos-ipv6-inbound.service: enabled, active" in result.stdout


def test_ipv6_filter_sh_install_again_writes_nothing_that_did_not_change(filter_device):
    device = filter_device
    assert install_filter(device).returncode == 0
    files = [device.root / "etc" / "unos-dev" / "ipv6-inbound.nft",
             device.root / "etc" / "systemd" / "system" / "unos-ipv6-inbound.service"]
    before = [(f.stat().st_ino, f.stat().st_mtime_ns) for f in files]
    result = install_filter(device)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [(f.stat().st_ino, f.stat().st_mtime_ns) for f in files] == before
    assert filter_calls(device) == ["systemctl reload-or-restart unos-ipv6-inbound.service"]
    assert "unchanged" in result.stdout


def test_ipv6_filter_sh_install_refuses_global_addresses_without_an_on_link_prefix(filter_device):
    """A static or DHCPv6 address, or a prefix announced off-link, gives the device global
    addresses but no on-link route for them: a filter without the LAN's prefix would drop the
    LAN hosts that use them, so install changes nothing. ip writes one address at a time, as
    iproute2 does, so a reader that stops at the first match must not lose it to SIGPIPE."""
    device = filter_device
    routes = "default nhid 5 via fe80::1 dev eth0 proto ra metric 100 pref medium\n"
    addrs = ADDRS + "2: eth0    inet6 fd00:1::5/64 scope global dynamic mngtmpaddr\n"
    result = install_filter(device, FAKE_IP_ROUTES=routes, FAKE_IP_ADDRS=addrs, FAKE_IP_PAUSE="0.2")
    assert result.returncode == 1
    assert "no on-link route for the global addresses" in result.stderr
    assert not (device.root / "etc").exists() and not (device.tmp / "calls").read_text()


def test_ipv6_filter_sh_install_without_global_addresses_needs_no_prefix(filter_device):
    device = filter_device
    result = install_filter(device, FAKE_IP_ROUTES="fd00:1::/64 dev eth0 proto ra metric 100\n", FAKE_IP_ADDRS="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (device.root / "etc" / "unos-dev" / "ipv6-inbound.nft").read_text() == FILTER_RULES.read_text()
    assert "global LAN prefixes: none" in result.stdout


def test_ipv6_filter_sh_install_changes_nothing_when_nft_refuses_the_file(filter_device):
    device = filter_device
    result = install_filter(device, FAKE_NFT_CHECK="1")
    assert result.returncode == 1
    assert "nft refused the ruleset: nothing changed" in result.stderr
    assert not (device.root / "etc").exists()
    assert list((device.root / "dev" / "shm").iterdir()) == []
    assert not [c for c in (device.tmp / "calls").read_text().splitlines() if c.startswith("systemctl ")]


def test_ipv6_filter_sh_install_with_a_new_prefix_rewrites_only_the_ruleset(filter_device):
    device = filter_device
    assert install_filter(device).returncode == 0
    unit = device.root / "etc" / "systemd" / "system" / "unos-ipv6-inbound.service"
    before = (unit.stat().st_ino, unit.stat().st_mtime_ns)
    result = install_filter(device, FAKE_IP_ROUTES=ROUTES.replace("2001:db8:1::/64", "2001:db8:2::/64"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (device.root / "etc" / "unos-dev" / "ipv6-inbound.nft").read_text().endswith(
        "add element ip6 unos_ipv6_inbound lan { 2001:db8:2::/64, 3fff:5::/64 }\n")
    assert (unit.stat().st_ino, unit.stat().st_mtime_ns) == before
    assert filter_calls(device) == ["systemctl reload-or-restart unos-ipv6-inbound.service"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_ipv6_filter_sh_install_stops_when_a_file_cannot_be_written(filter_device):
    """put() runs where set -e does not apply: a failed write must still end the run before the
    reload, and not pass for an unchanged file."""
    device = filter_device
    assert install_filter(device).returncode == 0
    rules = device.root / "etc" / "unos-dev" / "ipv6-inbound.nft"
    before = rules.read_text()
    rules.parent.chmod(0o555)
    try:
        result = install_filter(device, FAKE_IP_ROUTES=ROUTES.replace("2001:db8:1::/64", "2001:db8:2::/64"))
    finally:
        rules.parent.chmod(0o755)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"could not write {rules}" in result.stderr
    assert rules.read_text() == before and not (rules.parent / "ipv6-inbound.nft.new").exists()
    assert filter_calls(device) == []
    assert list((device.root / "dev" / "shm").iterdir()) == []


def test_ipv6_filter_sh_remove_stops_the_unit_and_leaves_nothing(filter_device):
    device = filter_device
    assert install_filter(device).returncode == 0
    result = run_filter(device, "remove")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "the inbound IPv6 filter is removed" in result.stdout
    # The unit's stop deletes the table; nothing flushes the ruleset, which holds Docker's rules.
    assert filter_calls(device) == [
        "systemctl disable --now --quiet unos-ipv6-inbound.service", "systemctl daemon-reload",
        "systemctl reset-failed unos-ipv6-inbound.service", "nft delete table ip6 unos_ipv6_inbound",
        "nft list table ip6 unos_ipv6_inbound"]
    assert not (device.root / "etc" / "unos-dev").exists()
    assert not (device.root / "etc" / "systemd" / "system" / "unos-ipv6-inbound.service").exists()


def test_ipv6_filter_sh_remove_fails_while_the_table_stays_loaded(filter_device):
    device = filter_device
    assert install_filter(device).returncode == 0
    # A stop that left the table, and a delete that fails: remove must not report success.
    device.stub("systemctl", STUB_SYSTEMCTL_UNIT.replace('[ "$2" != --now ] || rm -f "$FAKE_TABLE"', ":"))
    device.stub("nft", STUB_NFT_CHECK.replace('[ "$1" = list ] || rm -f "$FAKE_TABLE"', '[ "$1" = list ] || exit 1'))
    result = run_filter(device, "remove")
    assert result.returncode == 1
    assert "the inbound IPv6 filter is not removed" in result.stderr


def test_ipv6_filter_sh_refuses_unknown_steps_and_extra_words_without_contacting_anything(device):
    # "remove --help" must not remove the filter, nor "install --dry-run" install it.
    for argv in (["bogus"], [], ["status", "x"], ["install", "--dry-run"], ["remove", "--help"]):
        result = device.run(DEV / "ipv6-filter.sh", *argv)
        assert result.returncode == 2, argv
    assert not any(device.log.iterdir())


def test_ipv6_filter_sh_locks_and_measures_install_and_remove_only():
    main = (DEV / "ipv6-filter.sh").read_text().split("\n# --- main\n", 1)[1]
    assert main.count('dev_lock "ipv6-filter.sh ') == 2 and main.count('dev_sd_report "ipv6-filter ') == 2
    assert 'status) dev_remote_ro "$REMOTE_STATUS"' in main


# --- immich-ml-probe.py ---------------------------------------------------------------------

PROBE = DEV / "immich-ml-probe.py"
# What the stand-in machine learning answers, in the shapes of Immich's /predict: an embedding is
# a JSON text of numbers (orjson's), faces and OCR are lists.
EMBEDDING = json.dumps([0.0123456 + n / 1e6 for n in range(512)])
IMAGE_ANSWER = {
    "clip": EMBEDDING,
    "facial-recognition": [],
    "ocr": {"box": [[288.0, 288.0, 1048.0, 288.0, 1048.0, 344.0, 288.0, 344.0]], "boxScore": [0.97],
            "text": ["IMMICH ON THE NPU", "UNOS PROBE\x1b[2J 2026"], "textScore": [0.99, 0.98]},
    "imageHeight": 720,
    "imageWidth": 1280,
}


class FakeMachineLearning:
    """Immich's machine learning as the probe meets it, over HTTP on 127.0.0.1: /ping answers pong,
    /predict the answer of its entries (IMAGE_ANSWER, or CLIP's textual embedding), or `fail`'s
    (status, detail) for a request with an image. Every request's method, path, Content-Type and
    body are recorded."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str | None, bytes]] = []
        self.fail: tuple[int, str] | None = None
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, data: bytes, kind: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def serve(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.requests.append((self.command, self.path, self.headers.get("Content-Type"), body))
                if (self.command, self.path) == ("GET", "/ping"):
                    return self.reply(200, b"pong", "text/plain")
                fields = probe_fields(self.headers.get("Content-Type"), body)
                if "image" in fields and fake.fail:
                    return self.reply(fake.fail[0], json.dumps({"detail": fake.fail[1]}).encode())
                answer = IMAGE_ANSWER if "image" in fields else {"clip": EMBEDDING}
                return self.reply(200, json.dumps(answer).encode())

            do_GET = do_POST = serve

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def predictions(self) -> list[dict[str, object]]:
        return [probe_fields(kind, body) for method, path, kind, body in self.requests if path == "/predict"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def probe_fields(kind: str | None, body: bytes) -> dict[str, object]:
    """The fields of a multipart/form-data body: name -> text, or (filename, Content-Type, bytes)
    for a file."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + (kind or "").encode() + b"\r\n\r\n" + body)
    assert message.is_multipart(), kind
    fields: dict[str, object] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        filename = part.get_filename()
        fields[name] = (filename, part.get_content_type(), data) if filename else data.decode()
    return fields


@pytest.fixture
def machine_learning():
    fake = FakeMachineLearning()
    yield fake
    fake.close()


def run_probe(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """The probe as the device runs it, `python3 -I -B -` with the script on standard input, in an
    empty working folder and HOME, with TMPDIR in tmp_path and a proxy that does not answer."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(work), "TMPDIR": str(tmp_path / "tmp"),
           "http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""}
    return subprocess.run([sys.executable, "-I", "-B", "-", *args], input=PROBE.read_text(), cwd=work, env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_probe_asks_for_immich_s_models_and_prints_status_timings_and_shapes(tmp_path, machine_learning):
    """scripts/dev/immich-ml-probe.py, run as `python3 -I -B -` in Immich's machine-learning
    container (ADR-0183, ADR-0382): /ping, then a picture of 1280x720 for CLIP's visual embedding,
    faces and OCR, and the text "a photo of a dog" for CLIP's textual embedding, each once and
    then --runs times, in the shapes of Immich's server's requests; it prints the status, the time
    of the first call and the median of the others and the shape of the answer, with the text
    that OCR read, never an embedding's numbers; it writes nothing."""
    result = run_probe(tmp_path, "--port", str(machine_learning.port), "--runs", "3")

    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert f"immich-ml-probe: http://127.0.0.1:{machine_learning.port} answers /ping" in out
    assert re.search(r"^a picture of 1280x720 \(\d+ bytes of PNG\): CLIP visual, faces, OCR:\n"
                     r"  first call: status 200 in \d+\.\d\d s \(it loads the models, and downloads them the first time\)\n"
                     r"  3 more calls: median \d+\.\d{3} s \(fastest \d+\.\d{3} s, slowest \d+\.\d{3} s\), all status 200\n"
                     r'  answer: \{"clip": an embedding of 512 numbers, "facial-recognition": list of 0, '
                     r'"ocr": \{"box": list of 1 \[list of 8 \[float, \.\.\.\], \.\.\.\], "boxScore": list of 1 \[float, \.\.\.\], '
                     r'"text": list of 2 \[text of 17 characters, \.\.\.\], "textScore": list of 2 \[float, \.\.\.\]\}, '
                     r'"imageHeight": int, "imageWidth": int\}\n'
                     r"  OCR read: 'IMMICH ON THE NPU', 'UNOS PROBE \[2J 2026'\n", out, re.M), out
    assert re.search(r"^the text 'a photo of a dog': CLIP textual:\n  first call: status 200 in .*\n  3 more calls: "
                     r'.*all status 200\n  answer: \{"clip": an embedding of 512 numbers\}\n', out, re.M), out
    # No number of the embedding is printed (0.0123456 and up): a timing such as "0.012 s" may be.
    assert "0.01234" not in out and "\x1b" not in out
    assert [(m, p) for m, p, *_ in machine_learning.requests] == [("GET", "/ping")] + [("POST", "/predict")] * 8

    predictions = machine_learning.predictions()
    assert [json.loads(p["entries"]) for p in predictions] == [
        {"clip": {"visual": {"modelName": "ViT-B-32__openai"}},
         "facial-recognition": {"detection": {"modelName": "buffalo_l"}, "recognition": {"modelName": "buffalo_l"}},
         "ocr": {"detection": {"modelName": "PP-OCRv5_mobile"}, "recognition": {"modelName": "PP-OCRv5_mobile"}}},
    ] * 4 + [{"clip": {"textual": {"modelName": "ViT-B-32__openai", "options": {}}}}] * 4
    assert [sorted(p) for p in predictions] == [["entries", "image"]] * 4 + [["entries", "text"]] * 4
    assert {p["text"] for p in predictions[4:]} == {"a photo of a dog"}
    filename, kind, png = predictions[0]["image"]
    assert (filename, kind) == ("probe.png", "image/png")
    assert all(p["image"] == predictions[0]["image"] for p in predictions[:4])
    # A PNG of 1280x720, RGB, with dark text on a light background.
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    assert struct.unpack(">IIBBBBB", png[16:29]) == (1280, 720, 8, 2, 0, 0, 0)
    idat = png.index(b"IDAT")
    raw = zlib.decompress(png[idat + 4:idat + 4 + struct.unpack(">I", png[idat - 4:idat])[0]])
    assert len(raw) == 720 * (1 + 1280 * 3) and all(raw[y * (1 + 3840)] == 0 for y in range(720))
    rows = [raw[y * 3841 + 1:(y + 1) * 3841] for y in range(720)]
    dark = sum(row[x:x + 3] == bytes((20, 24, 28)) for row in rows for x in range(0, 3840, 3))
    assert 20_000 < dark < 200_000
    # Nothing written: no bytecode, no temporary file, nothing in HOME or the working folder.
    assert [p for p in tmp_path.rglob("*") if p.name not in ("work", "tmp")] == []


def test_the_probe_asks_for_the_clip_model_and_language_it_is_given(tmp_path, machine_learning):
    """--clip-model names the CLIP model that Immich's settings chose (the dev device's Smart
    Search uses nllb-clip-base-siglip__v1), for the picture's and the text's requests; --language
    goes into the text's options, as Immich's server sends a search's language, and --text sets
    the text. The faces' and OCR's models stay Immich's defaults."""
    result = run_probe(tmp_path, "--port", str(machine_learning.port), "--runs", "1",
                       "--clip-model", "nllb-clip-base-siglip__v1", "--language", "ru", "--text", "фото собаки")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the text 'фото собаки': CLIP textual:" in result.stdout
    predictions = machine_learning.predictions()
    entries = [json.loads(p["entries"]) for p in predictions]
    assert [e["clip"] for e in entries] == (
        [{"visual": {"modelName": "nllb-clip-base-siglip__v1"}}] * 2
        + [{"textual": {"modelName": "nllb-clip-base-siglip__v1", "options": {"language": "ru"}}}] * 2)
    assert entries[0]["facial-recognition"] == {"detection": {"modelName": "buffalo_l"},
                                                "recognition": {"modelName": "buffalo_l"}}
    assert entries[0]["ocr"] == {"detection": {"modelName": "PP-OCRv5_mobile"},
                                 "recognition": {"modelName": "PP-OCRv5_mobile"}}
    assert {p["text"] for p in predictions[2:]} == {"фото собаки"}


def test_the_probe_reports_a_refusal_and_goes_on_with_the_text(tmp_path, machine_learning):
    """A request that the machine learning refuses: its status and detail (without control
    characters), no further calls of it, the text's request still, and exit status 1."""
    machine_learning.fail = (500, "Failed to load model 'buffalo_l'\x1b[0m")

    result = run_probe(tmp_path, "--port", str(machine_learning.port), "--runs", "2")

    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(r"^  first call: status 500 after \d+\.\d\d s: \{\"detail\": \"Failed to load model 'buffalo_l'"
                     r"\\u001b\[0m\"\}\n", result.stdout, re.M), result.stdout
    assert "\x1b" not in result.stdout
    assert [sorted(p) for p in machine_learning.predictions()] == [["entries", "image"]] + [["entries", "text"]] * 3


def test_the_probe_stops_when_the_machine_learning_does_not_answer(tmp_path):
    with socket_bound() as port:
        result = run_probe(tmp_path, "--port", str(port), "--runs", "1")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith(f"immich-ml-probe: the machine learning does not answer /ping on port {port} "
                                    "(127.0.0.1: ")


@pytest.mark.parametrize("args", [["--runs", "-1"], ["--runs", "ten"], ["--port", "x"], ["--clip-model", ""],
                                  ["--clip-model", "../x"], ["--language", "r u"]])
def test_the_probe_refuses_a_wrong_argument_before_any_call(tmp_path, machine_learning, args):
    result = run_probe(tmp_path, "--port", str(machine_learning.port), *args)
    assert result.returncode == 2
    assert machine_learning.requests == []


@contextlib.contextmanager
def socket_bound():
    """A port of 127.0.0.1 that is bound and not listening: a connection is refused."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()

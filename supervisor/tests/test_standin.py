"""The local E2E stand-in (scripts/standin/, ADR-0215) keeps to its directory and serves what Caddy serves.

* scripts/standin/run_supervisor.py redirects every hard-coded /data path of
  supervisor/src into the stand-in's state directory: a child process
  configures the stand-in, imports every module of src and looks for a /data
  path left in their globals and in the settings. A /data path inside a
  function cannot be redirected; the only ones are the read-only probes listed
  in READ_ONLY_PROBES.
* The stand-in cannot reach this machine's Docker, and drops the caller's
  UNOS_* and DOCKER_* variables.
* The security headers of scripts/standin/front.mjs equal those of the
  Caddyfile it stands in for (webui/Caddyfile, and its copy deploy/Caddyfile).
* The specs never run against the device: node loads webui/playwright.config.ts,
  and with it webui/tests/target.ts, over a table of targets, and it refuses
  every BASE_URL and HTTP_URL whose host is not local; a copy of
  scripts/standin/e2e.sh refuses one before it starts anything; and no script
  of webui/package.json starts Playwright (the specs run through e2e.sh and
  scripts/test-cycle.sh, which set the target and E2E_STAND_IN=1).
* playwright.config.ts's default target (webui/tests/target.ts) is where
  scripts/standin/e2e.sh serves the stand-in. Before the specs, e2e.sh creates
  the administrator they sign in as (E2E_ADMIN of webui/tests/fixtures.ts)
  through the /setup page: a run of webui/tests/first-run.spec.ts alone, with
  E2E_FIRST_RUN=1, which is never retried (ADR-0261). A copy of e2e.sh whose
  stand-in, curl and Playwright are fakes shows the order of the two runs.
* The specs skip only on the conditions listed in SKIP_CONDITIONS, never on
  the version of the supervisor they run against (ADR-0207), and every skip
  gives its reason: a skip on E2E_STAND_IN says what the stand-in lacks, then
  that the compose test stack takes it too (TEST_STACK_TOO of target.ts).
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STANDIN = REPO / "scripts" / "standin"
SRC = REPO / "supervisor" / "src"
SPECS = REPO / "webui" / "tests"
CONFIG = REPO / "webui" / "playwright.config.ts"

DATA_PATH = re.compile(r"^/data(/|$)")

# The skip of a feature that the stand-in lacks (ADR-0215).
STAND_IN_SKIP = "process.env.E2E_STAND_IN === '1'"

# The conditions a spec may skip on (test.skip, in its test.describe, test.step
# and TestInfo forms too), each with why. A condition is something a local
# target lacks or must keep; never the version of the supervisor it runs
# (ADR-0207: no compatibility skips) and never a known failure (ADR-0215):
# test.fixme and test.fail are refused on any condition. A new kind of skip is
# added here, with its reason.
SKIP_CONDITIONS = {
    # A feature the stand-in lacks: the reason starts with "The stand-in" and names it, and
    # ends with TEST_STACK_TOO (webui/tests/target.ts): the compose test stack takes it too.
    STAND_IN_SKIP,
    # Needs a target with E2E_STAND_IN=1 (the stand-in, or the test stack): no Samba for the
    # SMB activation to change, the seeded share.
    "process.env.E2E_STAND_IN !== '1'",
    # A restore replaces the database and smb.conf of the target: only on this machine, or
    # with E2E_ALLOW_RESTORE=1 (backup-restore.spec.ts).
    "!RESTORE_ALLOWED",
    # The plain-HTTP address of the target is unknown: HTTP_URL is not set (https.spec.ts).
    "!httpUrl",
    # The IPv6 connection hints need a stand-in on this machine that answers on [::1] (shares.spec.ts).
    "!['localhost', '127.0.0.1'].includes(base.hostname)",
    "!reachable",
    # An earlier run on the same stand-in dismissed the import notes of the seeded share (shares.spec.ts).
    "notes.length === 0",
    # No folder of the pools directory takes a new one: the test stack has none (files.spec.ts).
    "scratch === null",
    # The supervisor's emergency command line runs against the stand-in's data on this machine
    # (scripts/standin/emergency.py, E2E_STANDIN_STATE of e2e.sh); the test stack's supervisor is
    # in a container (emergency-reset.spec.ts).
    "!process.env.E2E_STANDIN_STATE",
}

# /data literals inside functions of supervisor/src, which no runner can
# redirect: (file below src, function, literal). Each one is only read where it
# exists; the stand-in has no /data, so the handler reports it missing.
READ_ONLY_PROBES = {
    # GET /api/v1/health/full
    ("main.py", "full_health_check", "/data/unos.db"),
    ("main.py", "full_health_check", "/data/raid_status.json"),
    ("main.py", "full_health_check", "/data/ups_config.json"),
    # GET /api/v1/health/prometheus
    ("main.py", "prometheus_metrics", "/data/raid_status.json"),
}

# Runs in a child process, so that the redirected globals and the cached
# settings never reach the other tests. argv: the state directory.
CHILD = r"""
import importlib, json, os, pkgutil, sys
from pathlib import Path, PurePath

sys.path.insert(0, os.environ["STANDIN_DIR"])
import run_supervisor as standin

state = Path(sys.argv[1]).resolve()
standin.prepare(state, fresh=True)
data = standin.configure(state)

import src
for info in pkgutil.walk_packages(src.__path__, "src."):
    importlib.import_module(info.name)

left = []

def visit(where, value, depth=0):
    if isinstance(value, (str, PurePath)):
        text = str(value)
        if text == "/data" or text.startswith("/data/"):
            left.append(f"{where} = {text!r}")
    elif depth < 3 and isinstance(value, dict):
        for key, item in value.items():
            visit(f"{where}[{key!r}]", key, depth + 1)
            visit(f"{where}[{key!r}]", item, depth + 1)
    elif depth < 3 and isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            visit(f"{where}[{index}]", item, depth + 1)

for name, module in sorted(sys.modules.items()):
    if name == "src" or name.startswith("src."):
        for attr, value in list(vars(module).items()):
            visit(f"{name}.{attr}", value)

redirected = {}
for module_name, names in standin.DATA_GLOBALS.items():
    for name in names:
        redirected[f"{module_name}.{name}"] = str(getattr(importlib.import_module(module_name), name))
for module_name, files in standin.HOST_FILE_GLOBALS.items():
    for name in files:
        redirected[f"{module_name}.{name}"] = str(getattr(importlib.import_module(module_name), name))

from src.config import get_settings
settings = get_settings()
print(json.dumps({
    "data": str(data),
    "left": left,
    "redirected": redirected,
    "mountinfo": importlib.import_module("src.files.router").MOUNTINFO,
    "settings": {
        name: getattr(settings, name)
        for name in ("db_path", "smb_conf_path", "nfs_exports_path", "pools_base_path",
                     "app_catalog_path", "docker_socket", "debug_mode")
    },
    "env": {name: os.environ.get(name) for name in (
        "DOCKER_HOST", "DOCKER_TLS_VERIFY", "UNOS_HOST_EXEC", "UNOS_DISABLE_BACKGROUND_TASKS",
        "UNOS_INSTALL_DIR", "UNOS_DEBUG_MODE", "container", "TMPDIR")},
    "login_limit": importlib.import_module("src.auth.router").limiter.enabled,
    "power_off": importlib.import_module("src.host").runs_locally(),
    "gitignore": (state / ".gitignore").read_text(),
    "seeded": (data / "smb.conf").read_text(),
}))
"""


def _load_runner():
    spec = importlib.util.spec_from_file_location("standin_run_supervisor", STANDIN / "run_supervisor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inside(path: str, folder: Path) -> bool:
    return Path(path).resolve().is_relative_to(folder.resolve())


@pytest.fixture(scope="module")
def configured(tmp_path_factory):
    """The stand-in configured in a child process, started from the caller's UNOS_* and DOCKER_* variables."""
    state = tmp_path_factory.mktemp("standin") / "state"
    env = dict(os.environ)
    env.update(
        {
            "STANDIN_DIR": str(STANDIN),
            "PYTHONDONTWRITEBYTECODE": "1",
            # The caller's environment must not leak into the stand-in.
            "UNOS_SMB_CONF_PATH": "/etc/samba/smb.conf",
            "UNOS_DEBUG_MODE": "true",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "DOCKER_TLS_VERIFY": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(state)],
        cwd=state.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    report["state"] = state.resolve()
    return report


class TestDataPaths:
    def test_no_data_path_is_left_in_the_supervisors_modules(self, configured):
        assert configured["left"] == [], (
            "add these globals to DATA_GLOBALS (or HOST_FILE_GLOBALS) in scripts/standin/run_supervisor.py"
        )

    def test_redirected_globals_point_into_the_data_directory(self, configured):
        data = Path(configured["data"])
        assert configured["redirected"], "DATA_GLOBALS and HOST_FILE_GLOBALS are empty"
        outside = {name: value for name, value in configured["redirected"].items() if not _inside(value, data)}
        assert outside == {}

    def test_host_files_are_the_stand_ins_own(self, configured):
        data = Path(configured["data"])
        redirected = configured["redirected"]
        assert Path(redirected["src.storage.pools.FSTAB_PATH"]) == data / "fstab"
        assert _inside(redirected["src.updates.updater.BACKUP_DIR"], data)
        # The real mount table where the machine has one (read only), else an empty one.
        mountinfo = configured["mountinfo"]
        assert mountinfo == "/proc/self/mountinfo" or _inside(mountinfo, data)

    def test_settings_point_into_the_state_directory(self, configured):
        settings, state = configured["settings"], configured["state"]
        paths = ("db_path", "smb_conf_path", "nfs_exports_path", "pools_base_path", "app_catalog_path", "docker_socket")
        for name in paths:
            assert _inside(settings[name], state), (name, settings[name])
        assert settings["debug_mode"] is False

    def test_data_literals_inside_functions_are_only_the_read_only_probes(self):
        found = set()
        unexpected = []
        for path in sorted(SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and DATA_PATH.match(node.value)):
                    continue
                parent = parents.get(node)
                if isinstance(parent, ast.keyword) and parent.arg == "container":
                    continue  # the container side of an app's volume
                function, ancestor = None, node
                while ancestor in parents and function is None:
                    ancestor = parents[ancestor]
                    if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        function = ancestor.name
                    elif isinstance(ancestor, ast.Lambda):
                        function = "<lambda>"
                if function is None:
                    continue  # module or class level: the child process checks it
                key = (path.relative_to(SRC).as_posix(), function, node.value)
                found.add(key)
                if key not in READ_ONLY_PROBES:
                    unexpected.append(f"{key[0]}:{node.lineno} in {function}(): {node.value!r}")
        assert unexpected == [], (
            "a /data path inside a function cannot be redirected by scripts/standin/run_supervisor.py: "
            "use a setting or a module global instead"
        )
        assert READ_ONLY_PROBES <= found, f"stale READ_ONLY_PROBES entries: {sorted(READ_ONLY_PROBES - found)}"


class TestIsolation:
    def test_docker_is_out_of_reach(self, configured):
        env, data = configured["env"], Path(configured["data"])
        socket = configured["settings"]["docker_socket"]
        assert env["DOCKER_HOST"] == f"unix://{socket}"
        assert _inside(socket, data) and not Path(socket).exists()
        assert env["DOCKER_TLS_VERIFY"] is None

    def test_the_callers_variables_are_dropped(self, configured):
        assert configured["env"]["UNOS_DEBUG_MODE"] is None
        assert _inside(configured["settings"]["smb_conf_path"], configured["state"])

    def test_host_commands_run_here_without_background_work_or_power_actions(self, configured):
        env, state = configured["env"], configured["state"]
        assert env["UNOS_HOST_EXEC"] == "local"
        assert env["UNOS_DISABLE_BACKGROUND_TASKS"] == "1"
        # With host commands on this machine, the power API refuses every power action: no
        # reboot, shutdown, suspend or Wake-on-LAN change of this machine (ADR-0233).
        assert configured["power_off"] is True
        # The UPS monitor shuts nothing down inside a container (system.ups-monitoring).
        assert env["container"] == "docker"
        assert _inside(env["UNOS_INSTALL_DIR"], state) and not Path(env["UNOS_INSTALL_DIR"]).exists()
        assert _inside(env["TMPDIR"], state)

    def test_login_rate_limit_is_off(self, configured):
        assert configured["login_limit"] is False

    def test_state_directory_ignores_itself_and_seeds_the_e2e_share(self, configured):
        assert configured["gitignore"] == "*\n"
        seeded = configured["seeded"]
        assert "[e2e_seed]" in seeded
        assert f"path = {configured['state'] / 'pools' / 'testpool' / 'e2e-seed'}" in seeded

    def test_fresh_refuses_a_directory_that_is_not_the_stand_ins(self, tmp_path):
        runner = _load_runner()
        (tmp_path / "data").mkdir()
        (tmp_path / "notes.txt").write_text("not the stand-in's")
        with pytest.raises(SystemExit, match="not a stand-in state directory"):
            runner.prepare(tmp_path, fresh=True)
        assert (tmp_path / "notes.txt").exists() and (tmp_path / "data").is_dir()

    def test_fresh_empties_the_data_of_a_stand_in_directory(self, tmp_path):
        runner = _load_runner()
        runner.prepare(tmp_path, fresh=True)
        (tmp_path / "data" / "unos.db").write_text("old")
        (tmp_path / "tls").mkdir()
        (tmp_path / "tls" / "cert.pem").write_text("kept")
        runner.prepare(tmp_path, fresh=True)
        assert not (tmp_path / "data" / "unos.db").exists()
        assert (tmp_path / "tls" / "cert.pem").read_text() == "kept"

    def test_refuses_to_run_as_root(self, monkeypatch, tmp_path):
        runner = _load_runner()
        monkeypatch.setattr(runner.os, "geteuid", lambda: 0, raising=False)
        with pytest.raises(SystemExit, match="refusing to run as root"):
            runner.main(["--state-dir", str(tmp_path / "state")])
        assert not (tmp_path / "state").exists()


def _caddy_headers(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"^\s*header \{\n(.*?)^\s*\}", text, re.S | re.M)
    assert len(blocks) == 1, f"{path}: expected one header block, found {len(blocks)}"
    headers: dict[str, str] = {}
    lines = [line.strip() for line in blocks[0].splitlines()]
    assert "defer" in lines, f"{path}: the headers must also replace those of proxied answers"
    for line in lines:
        if not line or line.startswith("#") or line == "defer":
            continue
        match = re.fullmatch(r'([A-Za-z-]+)\s+"([^"\\]*)"', line)
        assert match, f"{path}: unexpected header line {line!r}"
        headers[match[1].lower()] = match[2]
    return headers


def _front_headers() -> dict[str, str]:
    text = (STANDIN / "front.mjs").read_text(encoding="utf-8")
    match = re.search(r"^const SECURITY_HEADERS = \{\n(.*?)\n\};", text, re.S | re.M)
    assert match, "front.mjs: no SECURITY_HEADERS block"
    headers: dict[str, str] = {}
    for line in match[1].splitlines():
        entry = re.fullmatch(r"""\s*'([A-Za-z-]+)':\s*(["'])([^"'\\]*(?:(?!\2)["'][^"'\\]*)*)\2,?""", line)
        assert entry, f"front.mjs: unexpected SECURITY_HEADERS line {line!r}"
        headers[entry[1].lower()] = entry[3]
    return headers


class TestFrontHeaders:
    def test_front_sends_the_security_headers_of_the_caddyfile(self):
        caddy = _caddy_headers(REPO / "webui" / "Caddyfile")
        assert "content-security-policy" in caddy and "strict-transport-security" not in caddy
        assert _front_headers() == caddy

    def test_deploy_caddyfile_has_the_same_headers(self):
        assert _caddy_headers(REPO / "deploy" / "Caddyfile") == _caddy_headers(REPO / "webui" / "Caddyfile")


# Loads webui/playwright.config.ts as Playwright does, without Playwright: a
# stub stands in for @playwright/test, and a relative import without an
# extension gets ".ts", as Playwright's loader resolves it. node runs the
# TypeScript of the configuration and of tests/target.ts as it is. Prints the
# configuration's base URL, or the message of the error it throws (exit 3). With
# the argument "projects" and file names under webui/ after it: each project's
# name, its retries (null: the configuration's) and which of those files it
# runs, by its testMatch and testIgnore (regular expressions, tested on the
# absolute path as Playwright tests them; no testMatch: Playwright's default,
# which every *.spec.ts matches).
PROBE = r"""
import { registerHooks } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STUB =
  'data:text/javascript,export const devices = { "Desktop Chrome": {} }; ' +
  'export const defineConfig = (config) => config;';
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@playwright/test') return { url: STUB, shortCircuit: true };
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      const withoutExtension = /^\.\.?\//.test(specifier) && !/\.[cm]?[jt]s$/.test(specifier);
      if (withoutExtension) return nextResolve(`${specifier}.ts`, context);
      throw error;
    }
  },
});

try {
  const { default: config } = await import(pathToFileURL(process.argv[2]).href);
  if (process.argv[3] === 'projects') {
    const absolute = (file) => path.join(path.dirname(process.argv[2]), file);
    const matches = (pattern, file) => pattern instanceof RegExp && pattern.test(absolute(file));
    const runs = (project, file) =>
      (project.testMatch === undefined || matches(project.testMatch, file)) && !matches(project.testIgnore, file);
    const files = process.argv.slice(4);
    process.stdout.write(JSON.stringify(config.projects.map((project) => ({
      name: project.name,
      retries: project.retries ?? null,
      runs: files.filter((file) => runs(project, file)),
    }))));
  } else {
    process.stdout.write(JSON.stringify({ baseURL: config.use.baseURL }));
  }
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String(error?.message ?? error) }));
  process.exitCode = 3;
}
"""

# (BASE_URL, HTTP_URL, the base URL the configuration then uses) that
# playwright.config.ts accepts. None: not set; "stand-in": where e2e.sh serves it.
ACCEPTED_TARGETS = [
    (None, None, "stand-in"),
    ("", None, "stand-in"),
    ("https://localhost:46443/", "http://localhost:46080", "https://localhost:46443"),
    # The compose test stack from this machine, and from its test-runner.
    ("https://127.0.0.1:3443", "http://127.0.0.1:3080", "https://127.0.0.1:3443"),
    ("https://[::1]:3443", "http://[::1]:3080", "https://[::1]:3443"),
    ("https://webui", "http://webui", "https://webui"),
]

# (BASE_URL, HTTP_URL, the variable that playwright.config.ts refuses).
REFUSED_TARGETS = [
    ("https://10.1.1.103", None, "BASE_URL"),  # the dev device
    ("https://nas.local", None, "BASE_URL"),
    ("https://localhost.evil.example", None, "BASE_URL"),  # a host name that starts with localhost
    ("https://127.0.0.1@10.1.1.103", None, "BASE_URL"),  # user info: the host is the device
    ("localhost:46443", None, "BASE_URL"),  # no scheme: no host
    ("not a url", None, "BASE_URL"),
    (None, "http://10.1.1.103", "HTTP_URL"),
    ("https://localhost:3443", "http://nas.local", "HTTP_URL"),
]

# What scripts/standin/e2e.sh refuses (exit 2) and lets through. The stand-in
# runs on this machine, so e2e.sh refuses webui, the test stack's name, too.
E2E_SH_REFUSED = [
    ("BASE_URL", "https://10.1.1.103"),
    ("HTTP_URL", "http://10.1.1.103"),
    ("BASE_URL", "https://127.0.0.1@10.1.1.103"),
    ("BASE_URL", "https://localhost.evil.example"),
    ("BASE_URL", "https://webui"),
    ("BASE_URL", "not a url"),
]
E2E_SH_ACCEPTED = [
    ("BASE_URL", "https://localhost:46443"),
    ("BASE_URL", "https://127.0.0.1:46443"),
    ("HTTP_URL", "http://[::1]:46080"),
]


def _e2e_sh_default(variable: str) -> str:
    """The default that scripts/standin/e2e.sh gives its port ``variable``."""
    e2e = (STANDIN / "e2e.sh").read_text(encoding="utf-8")
    default = re.search(rf"^\w+=\$\{{{variable}:-(\d+)\}}$", e2e, re.M)
    assert default, f"scripts/standin/e2e.sh: no default for {variable}"
    return default[1]


def _node() -> str:
    """The node on PATH; skips the test without one."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed (Node.js 22, webui/package.json)")
    return node


@pytest.fixture(scope="module")
def load_config(tmp_path_factory):
    """Loads webui/playwright.config.ts with node: (exit code, what PROBE printed) for a BASE_URL and HTTP_URL."""
    node = _node()
    version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    numbers = re.match(r"v(\d+)\.(\d+)\.", version)
    major, minor = (int(numbers[1]), int(numbers[2])) if numbers else (0, 0)
    # From 22.18 and 23.6 on, node runs TypeScript as it is and has module.registerHooks.
    if (major, minor) < (22, 18) or (major == 23 and minor < 6):
        pytest.skip(f"node {version or '(no version)'} cannot run TypeScript as it is: Node.js 22.18 or later can")
    probe = tmp_path_factory.mktemp("config") / "probe.mjs"
    probe.write_text(PROBE, encoding="utf-8")

    def load(
        base_url: str | None, http_url: str | None, *, env: dict[str, str] | None = None, args: tuple[str, ...] = ()
    ) -> tuple[int, dict]:
        env = {"PATH": os.environ.get("PATH", os.defpath), "NODE_NO_WARNINGS": "1", **(env or {})}
        for name, value in (("BASE_URL", base_url), ("HTTP_URL", http_url)):
            if value is not None:
                env[name] = value
        result = subprocess.run(
            [node, str(probe), str(CONFIG), *args], env=env, capture_output=True, text=True, timeout=60
        )
        assert result.stdout, result.stderr[-3000:]
        return result.returncode, json.loads(result.stdout)

    return load


def _run_e2e_sh_copy(tmp_path: Path, name: str, value: str) -> subprocess.CompletedProcess:
    """A copy of scripts/standin/e2e.sh, alone in a checkout in tmp_path, run with ``name=value``.

    node parses the URL; npm, openssl, python and curl are commands that do nothing. If the
    copy got past its checks, it would find no webui/ to build or serve.
    """
    node = _node()
    script = tmp_path / "scripts" / "standin" / "e2e.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(STANDIN / "e2e.sh", script)
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "curl").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (tools / "curl").chmod(0o755)
    env = {
        "PATH": f"{tools}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "NODE": node,
        "NPM": "false",
        "OPENSSL": "false",
        "PYTHON": "false",
        name: value,
    }
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run([bash, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)


class TestTargets:
    """The specs run only against local targets, never against the device (ADR-0215)."""

    @pytest.mark.parametrize(("base_url", "http_url", "expected"), ACCEPTED_TARGETS)
    def test_config_accepts_a_local_target(self, load_config, base_url, http_url, expected):
        if expected == "stand-in":
            expected = f"https://localhost:{_e2e_sh_default('E2E_HTTPS_PORT')}"
        assert load_config(base_url, http_url) == (0, {"baseURL": expected})

    @pytest.mark.parametrize(("base_url", "http_url", "refused"), REFUSED_TARGETS)
    def test_config_refuses_a_target_that_is_not_local(self, load_config, base_url, http_url, refused):
        code, report = load_config(base_url, http_url)
        value = base_url if refused == "BASE_URL" else http_url
        assert code == 3 and "error" in report, report
        message = report["error"]
        assert message.startswith(f"{refused}={value}"), message
        assert "never against the device" in message or message == f"{refused}={value} is not a URL", message

    @pytest.mark.parametrize(("name", "value"), E2E_SH_REFUSED)
    def test_e2e_sh_refuses_a_target_off_this_machine_before_it_starts_anything(self, tmp_path, name, value):
        result = _run_e2e_sh_copy(tmp_path, name, value)
        assert result.returncode == 2, result.stderr
        assert result.stderr.startswith(f"e2e.sh: refusing {name}={value}: "), result.stderr
        assert "never against the device" in result.stderr
        assert result.stdout == ""
        assert not (tmp_path / ".standin").exists()

    @pytest.mark.parametrize(("name", "value"), E2E_SH_ACCEPTED)
    def test_e2e_sh_lets_a_target_on_this_machine_through(self, tmp_path, name, value):
        # Past the refusal, the copy stops at its next check: there is no webui/ next to it.
        result = _run_e2e_sh_copy(tmp_path, name, value)
        assert "refusing" not in result.stderr
        assert result.returncode == 1 and "webui/node_modules has no Playwright" in result.stderr, result.stderr
        assert not (tmp_path / ".standin").exists()

    def test_no_npm_script_runs_the_specs(self):
        # Such a script started Playwright without the stand-in, or against the test stack without
        # E2E_STAND_IN=1. The specs run through scripts/standin/e2e.sh (make e2e-local) and
        # scripts/test-cycle.sh, which set the target and E2E_STAND_IN=1.
        scripts = json.loads((REPO / "webui" / "package.json").read_text(encoding="utf-8"))["scripts"]
        assert {name: command for name, command in scripts.items() if re.search(r"\bplaywright\b", command)} == {}


# The files Playwright may load from webui/tests: specs and the modules they import, in every folder.
SPEC_SUFFIXES = {".ts", ".mts", ".cts", ".tsx", ".js", ".mjs", ".cjs", ".jsx"}


def _spec_files(folder: Path = SPECS) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in SPEC_SUFFIXES)


STRING = re.compile(r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`""", re.S)

TYPESCRIPT = REPO / "webui" / "node_modules" / "typescript"

# Run by node with the path of TYPESCRIPT and the files: prints as JSON every use of the names skip,
# fixme, fail and expectedStatus that TypeScript's parser finds in them, and their syntax errors. It
# reports each one that the code writes out: a member of any object (test, test.describe,
# test.step, a TestInfo under any name, test.info(), an alias) through ., ?. or brackets, with
# parentheses, a non-null assertion or a cast around the object or the key, called (with its
# condition and reason; also test.fail.only) or not; a destructured name; a key expectedStatus; an
# import alias; and any other string of the name alone, an annotation's type among them. A name
# that the code builds, or reads from data, is not seen.
SKIP_SCAN = r"""
const [typescript, ...files] = process.argv.slice(1);
const ts = require(typescript);
// The methods of a skip, a fixme and a fail, and the field of a TestInfo that test.fail sets.
const METHODS = new Set(['skip', 'fixme', 'fail']);
const EXPECTED_STATUS = 'expectedStatus';
const NAMES = new Set([...METHODS, EXPECTED_STATUS]);
const options = { allowJs: true, noLib: true, noResolve: true, noEmit: true, types: [] };
// With the parent of every node set, which the parser alone leaves out.
const program = ts.createProgram(files, options, ts.createCompilerHost(options, true));
// Parentheses, a non-null assertion and casts, which leave the value inside as it is.
const WRAPPERS = [ts.isParenthesizedExpression, ts.isNonNullExpression, ts.isAsExpression,
  ts.isTypeAssertionExpression, ts.isSatisfiesExpression];
const inner = (node) => {
  while (node && WRAPPERS.some((is) => is(node))) node = node.expression;
  return node;
};
const outer = (node) => {
  while (node.parent && WRAPPERS.some((is) => is(node.parent)) && node.parent.expression === node) node = node.parent;
  return node;
};
const isString = (node) => !!node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node));
// The strings that are the name of a member, a binding or a property: reported there, if at all.
const named = new Set();
// The name of a member, a binding or a property: written out, quoted, or a computed string.
const nameOf = (name) => {
  if (!name) return undefined;
  const node = ts.isComputedPropertyName(name) ? inner(name.expression) : name;
  if (isString(node)) named.add(node);
  return node && (ts.isIdentifier(node) || isString(node)) ? node.text : undefined;
};
// Whether an object literal is the target of a destructuring assignment, also inside another one.
const assigned = (node) => {
  for (;;) {
    node = outer(node);
    const parent = node.parent;
    if (ts.isBinaryExpression(parent)) return parent.left === node && parent.operatorToken.kind === ts.SyntaxKind.EqualsToken;
    if (ts.isForOfStatement(parent) || ts.isForInStatement(parent)) return parent.initializer === node;
    if (ts.isPropertyAssignment(parent) && parent.initializer === node) node = parent.parent;
    else if (ts.isArrayLiteralExpression(parent)) node = parent;
    else if (ts.isSpreadElement(parent)) node = parent.parent;
    else return false;
  }
};
const found = [];
const problems = [];
for (const file of files) {
  const source = program.getSourceFile(file);
  if (!source) {
    problems.push(`${file}: not read`);
    continue;
  }
  for (const diagnostic of program.getSyntacticDiagnostics(source)) {
    const { line } = source.getLineAndCharacterOfPosition(diagnostic.start ?? 0);
    problems.push(`${file}:${line + 1}: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')}`);
  }
  const add = (node, method, condition, reason = null) => {
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
    found.push({ file, line: line + 1, method, condition, reason });
  };
  const visit = (node) => {
    let member;
    if (ts.isPropertyAccessExpression(node)) member = nameOf(node.name);
    else if (ts.isElementAccessExpression(node)) {
      const key = inner(node.argumentExpression);
      if (isString(key)) {
        named.add(key);
        member = key.text;
      }
    }
    if (member === EXPECTED_STATUS) {
      add(node, member, '(a member)');
    } else if (METHODS.has(member)) {
      // A call, also through .only; anything else is a reference.
      let use = outer(node);
      const only = use.parent;
      if (ts.isPropertyAccessExpression(only) && only.expression === use && nameOf(only.name) === 'only') use = outer(only);
      const call = use.parent;
      if (ts.isCallExpression(call) && call.expression === use) {
        const [condition, reason] = call.arguments;
        add(call, member, condition ? condition.getText(source).split(/\s+/).join(' ') : '',
          reason ? reason.getText(source) : null);
      } else {
        add(node, member, '(a reference)');
      }
    } else if (ts.isBindingElement(node) && ts.isObjectBindingPattern(node.parent)) {
      const bound = nameOf(node.propertyName ?? node.name);
      if (NAMES.has(bound)) add(node, bound, '(a destructured name)');
    } else if ((ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node))
               && ts.isObjectLiteralExpression(node.parent)) {
      const key = nameOf(node.name);
      if (NAMES.has(key) && assigned(node.parent)) add(node, key, '(a destructured name)');
      else if (key === EXPECTED_STATUS) add(node, key, '(a property)');
    } else if (ts.isImportEqualsDeclaration(node) && ts.isEntityName(node.moduleReference)) {
      // import alias = test.skip
      for (let name = node.moduleReference; name; name = ts.isQualifiedName(name) ? name.left : undefined) {
        const segment = ts.isQualifiedName(name) ? name.right : name;
        if (NAMES.has(segment.text)) {
          add(node, segment.text, '(an import alias)');
          break;
        }
      }
    } else if (isString(node) && NAMES.has(node.text) && !named.has(node)) {
      // Any other string of a name alone: an annotation's type, or a name that the code passes on.
      const value = outer(node);
      const holder = value.parent;
      const annotation = (ts.isPropertyAssignment(holder) || ts.isPropertyDeclaration(holder))
        && holder.initializer === value && nameOf(holder.name) === 'type';
      add(node, node.text, annotation ? '(an annotation)' : '(a string)');
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}
process.stdout.write(JSON.stringify({ found, problems }));
"""


# The conditions that SKIP_SCAN gives a use of the names that is not a call; none is a condition
# of TypeScript.
NOT_A_CALL = {"(a reference)", "(a destructured name)", "(a property)", "(a member)", "(an import alias)",
              "(an annotation)", "(a string)"}


@functools.cache
def _scan(folder: Path) -> tuple[dict, ...]:
    """What SKIP_SCAN finds in the files of ``folder`` that Playwright may load. Skips the test
    without node or without TypeScript in webui/node_modules (make setup installs it)."""
    node = _node()
    if not (TYPESCRIPT / "package.json").is_file():
        pytest.skip("webui/node_modules has no typescript: make setup installs it")
    files = [str(path) for path in _spec_files(folder)]
    result = subprocess.run(
        [node, "-e", SKIP_SCAN, str(TYPESCRIPT), *files], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-3000:]
    scan = json.loads(result.stdout)
    assert scan["problems"] == [], "TypeScript cannot parse these files, so their skips are not known"
    return tuple(scan["found"])


def _skip_calls(folder: Path = SPECS):
    """(spec, line, method, condition, reason expression, spec text) of every skip, fixme and fail
    of Playwright that SKIP_SCAN finds in ``folder``. The condition of a call is the text of its
    first argument; that of a reference, a destructured name and an annotation says which it is,
    such as "(a reference)"."""
    texts: dict[Path, str] = {}
    for item in _scan(folder):
        spec = Path(item["file"])
        if spec not in texts:
            texts[spec] = spec.read_text(encoding="utf-8")
        yield spec, item["line"], item["method"], item["condition"], item["reason"], texts[spec]


def _unexpected_skips(folder: Path = SPECS) -> list[str]:
    """What test_specs_skip_only_on_the_listed_conditions refuses: a skip on a condition that
    SKIP_CONDITIONS does not list (every use of the names that is not a call, such as a reference
    or a string, included), and every test.fixme, test.fail and expectedStatus, the marks of a
    known failure (ADR-0215)."""
    unexpected = []
    for spec, line, method, condition, _reason, _text in _skip_calls(folder):
        where = f"{spec.relative_to(folder).as_posix()}:{line}"
        if method == "expectedStatus":
            unexpected.append(f"{where}: expectedStatus {condition}: a known failure (ADR-0215)")
        elif method in ("fixme", "fail"):
            unexpected.append(f"{where}: test.{method} on {condition or '(no condition)'}: a known failure (ADR-0215)")
        elif condition not in SKIP_CONDITIONS:
            unexpected.append(f"{where}: {condition or '(no condition)'}")
    return unexpected


def _missing_reasons(folder: Path = SPECS) -> list[str]:
    """What test_every_skip_gives_its_reason refuses: a skip without a reason (string literals and
    string constants joined by +), and a stand-in skip whose reason does not start with what the
    stand-in lacks or does not end with TEST_STACK_TOO of webui/tests/target.ts."""
    test_stack_too = _string_value("TEST_STACK_TOO", (SPECS / "target.ts").read_text(encoding="utf-8"))
    assert "compose test stack" in test_stack_too, "webui/tests/target.ts: no TEST_STACK_TOO"
    missing = []
    for spec, line, _method, condition, reason, text in _skip_calls(folder):
        if condition in NOT_A_CALL:
            continue  # test_specs_skip_only_on_the_listed_conditions refuses it
        where = f"{spec.relative_to(folder).as_posix()}:{line}"
        value = _string_value(reason, text)
        if not value.strip():
            missing.append(f"{where}: no reason (string literals and string constants joined by +)")
        elif condition == STAND_IN_SKIP:
            lacks = value.removeprefix("The stand-in ")
            if lacks == value or not lacks.removesuffix(test_stack_too).strip(" ;,."):
                missing.append(f"{where}: {value!r} does not start with what the stand-in lacks")
            elif not lacks.endswith(test_stack_too):
                missing.append(
                    f"{where}: {value!r} does not end with TEST_STACK_TOO of webui/tests/target.ts "
                    "(the compose test stack takes the skip too)"
                )
    return missing


NAME = re.compile(r"[A-Za-z_$][\w$]*")


def _constant(name: str, text: str) -> tuple[str, str] | None:
    """(expression, text of its file) of the constant ``name``: the spec's own, else an export of webui/tests."""
    own = re.search(rf"^\s*(?:export\s+)?const {re.escape(name)}\s*=\s*(.*?);\s*$", text, re.M | re.S)
    if own:
        return own[1], text
    for path in _spec_files():
        other = path.read_text(encoding="utf-8")
        exported = re.search(rf"^export const {re.escape(name)}\s*=\s*(.*?);\s*$", other, re.M | re.S)
        if exported:
            return exported[1], other
    return None


def _string_value(expression: str | None, text: str, depth: int = 0) -> str:
    """The text of a reason: string literals and string constants joined by +; "" for anything else.

    A constant is one of the spec (``text``), or one that a file of webui/tests exports, such as
    TEST_STACK_TOO of target.ts.
    """
    if not expression or depth > 4:
        return ""
    value, i, operand = "", 0, True
    while i < len(expression):
        if expression[i].isspace():
            i += 1
        elif not operand:
            if expression[i] != "+":
                return ""  # not a concatenation
            i, operand = i + 1, True
        elif literal := STRING.match(expression, i):
            value += next(group for group in literal.groups() if group is not None)
            i, operand = literal.end(), False
        elif name := NAME.match(expression, i):
            constant = _constant(name[0], text)
            part = _string_value(*constant, depth + 1) if constant else ""
            if not part:
                return ""
            value += part
            i, operand = name.end(), False
        else:
            return ""
    return "" if operand else value


def _free_ports(count: int) -> list[int]:
    """Ports on 127.0.0.1 that nothing listens on (each was free a moment ago)."""
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _run_e2e_sh_with_fakes(
    tmp_path: Path, *args: str, first_run: int = 0, specs: int = 0, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run a copy of scripts/standin/e2e.sh with ``args`` in a checkout in tmp_path, with fakes.

    The supervisor and front.mjs only wait to be stopped, curl answers that the stand-in is up
    and has no account yet, and the build is the existing one (E2E_SKIP_BUILD=1). Playwright
    writes each run as a line "E2E_FIRST_RUN=<value> <arguments>" and exits with ``first_run``
    for the first-run setup (E2E_FIRST_RUN=1) and with ``specs`` otherwise. Returns the result
    and those lines.
    """
    node = _node()
    standin = tmp_path / "scripts" / "standin"
    standin.mkdir(parents=True)
    shutil.copy2(STANDIN / "e2e.sh", standin / "e2e.sh")
    (standin / "front.mjs").write_text("setTimeout(() => {}, 600_000);\n", encoding="utf-8")
    build = tmp_path / "webui" / "build"
    build.mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    tls = tmp_path / ".standin" / "tls"
    tls.mkdir(parents=True)
    for name in ("cert.pem", "key.pem"):
        (tls / name).write_text("not checked: OPENSSL is true\n", encoding="utf-8")
    runs = tmp_path / "playwright-runs.txt"
    fakes = {
        "bin/curl": """case "$*" in *"/api/v1/auth/status"*) echo '{"setup_required": true}' ;; esac\n""",
        "bin/python": '[ "$1" = -c ] && exit 0\nexec sleep 600\n',
        "webui/node_modules/.bin/playwright": (
            f'echo "E2E_FIRST_RUN=${{E2E_FIRST_RUN:-}} $*" >> "{runs}"\n'
            f'[ "${{E2E_FIRST_RUN:-}}" = 1 ] && exit {first_run}\nexit {specs}\n'
        ),
    }
    for name, body in fakes.items():
        fake = tmp_path / name
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        fake.chmod(0o755)
    https_port, http_port, backend_port = _free_ports(3)
    environment = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "NODE": node,
        "NPM": "false",
        "OPENSSL": "true",
        "PYTHON": str(tmp_path / "bin" / "python"),
        "E2E_SKIP_BUILD": "1",
        "E2E_HTTPS_PORT": str(https_port),
        "E2E_HTTP_PORT": str(http_port),
        "E2E_BACKEND_PORT": str(backend_port),
        **(env or {}),
    }
    bash = shutil.which("bash") or "/bin/bash"
    result = subprocess.run(
        [bash, str(standin / "e2e.sh"), *args], cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=60,
    )
    return result, runs.read_text(encoding="utf-8").splitlines() if runs.exists() else []


class TestSpecsAndRunner:
    def test_default_target_is_where_e2e_sh_serves_the_stand_in(self):
        target = (REPO / "webui" / "tests" / "target.ts").read_text(encoding="utf-8")
        url = re.search(r"STAND_IN_URL = 'https://localhost:(\d+)'", target)
        assert url and url[1] == _e2e_sh_default("E2E_HTTPS_PORT")
        assert int(_e2e_sh_default("E2E_BACKEND_PORT")) == _load_runner().DEFAULT_PORT

    def test_config_uses_the_checked_target(self):
        config = (REPO / "webui" / "playwright.config.ts").read_text(encoding="utf-8")
        assert re.search(r"^const baseURL = targetUrl\(\);$", config, re.M)
        assert "10.1.1.103" not in config
        target = (REPO / "webui" / "tests" / "target.ts").read_text(encoding="utf-8")
        hosts = re.search(r"LOCAL_HOSTS: readonly string\[\] = \[(.*?)\];", target)
        assert hosts and re.findall(r"'([^']*)'", hosts[1]) == ["localhost", "127.0.0.1", "[::1]", "webui"]

    def test_specs_skip_only_on_the_listed_conditions(self):
        found = {condition for _spec, _line, _method, condition, _reason, _text in _skip_calls()}
        unexpected = _unexpected_skips()
        assert unexpected == [], (
            "a skip on a condition that SKIP_CONDITIONS does not list: specs skip for what a local target "
            "lacks, never for an older supervisor (ADR-0207) or a known failure (ADR-0215)"
        )
        assert SKIP_CONDITIONS <= found, f"stale SKIP_CONDITIONS entries: {sorted(SKIP_CONDITIONS - found)}"

    def test_every_skip_form_in_every_spec_file_is_seen(self, tmp_path):
        """Every use of the names skip, fixme, fail and expectedStatus that a file of webui/tests
        and its folders writes out is seen and refused, but a skip on a listed condition
        (testing.no-compat-skips, ADR-0317): a member of any object, also inside casts, called or
        not, a destructured name, an import alias and a string of the name alone, an annotation's
        type among them. One that the checks above missed would pass them unseen. TypeScript's
        parser reads the files, so neither a comment nor a division or a regular expression literal
        that a heuristic might misread hides what follows it."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.spec.ts").write_text(
            "test('x', async ({}, info) => {\n  info.skip(smbStatus === 404, 'old');\n});\n"
            "test.step.skip('a step', async () => {});\n"
            "test.describe.fixme(old === true, 'old');\n"
            "if (url.startsWith('https://')) test.skip(viaUrl, 'r'); // a comment\n"
            "test.skip (spaced, 'r');\n"
            "test?.skip(optional, 'r');\n"
            "test['fixme'](bracketed, 'r');\n"
            "test.fail.only(failing, 'r');\n"
            "const skipLater = test.skip;\n"
            "test('annotated', { annotation: { type: 'skip', description: 'r' } }, async () => {});\n",
            encoding="utf-8")
        (tmp_path / "sub" / "b.spec.mts").write_text("test.fail(version < 2, 'old');\n", encoding="utf-8")
        (tmp_path / "c.spec.js").write_text("// test.skip(commented, 'no')\n/* test.skip(blocked, 'no') */\n"
                                            "test.info().skip(x, 'y');\n", encoding="utf-8")
        (tmp_path / "d.spec.ts").write_text(
            "const { skip } = test;\nskip(destructured, 'r');\n"
            "({ fixme: later } = test);\n"
            "test[`skip`](templated, 'r');\n"
            "test?.['skip'](optionalBracket, 'r');\n"
            "(test).skip(parenthesized, 'r');\n"
            "(test.skip as typeof test.skip)(cast, 'r');\n"
            "test.skip.call(test, viaCall, 'r');\n"
            "test('t', { annotation: { type: `fixme` } }, async () => {});\n"
            "const options = { skip: true, type: 'other' };\n"
            "const half = n! / 2 + '/';\nconst u = 'https://x'; test.skip(afterDivision, 'r');\n"
            "if (ok) /'/.test(s); test.skip(afterRegex, 'r');\n",
            encoding="utf-8")
        (tmp_path / "e.spec.ts").write_text(
            "test['skip' as const](castKey, 'r');\n"
            "test['fixme'!](nonNullKey, 'r');\n"
            "test[<const>'skip'](angleKey, 'r');\n"
            "test['skip' satisfies string](satisfiesKey, 'r');\n"
            "test?.['fixme' as const](optionalCastKey, 'r');\n"
            "const { ['skip' as const]: s } = test;\n"
            "test('t', { annotation: { type: 'skip' as const } }, async () => {});\n"
            "class Old { type = 'fail'; }\n"
            "import alias = test.skip;\n"
            "export import nested = test.describe.fixme;\n"
            "test('u', async ({}, info) => { info.expectedStatus = 'failed'; });\n"
            "Object.assign(testInfo, { expectedStatus: 'failed' });\n"
            "Reflect.get(test, 'skip')(viaReflect, 'r');\n"
            "test[old ? 'skip' : 'only'](ternary, 'r');\n",
            encoding="utf-8")
        found = sorted((spec.relative_to(tmp_path).as_posix(), method, condition)
                       for spec, _line, method, condition, *_ in _skip_calls(tmp_path))
        assert found == [
            ("a.spec.ts", "fail", "failing"), ("a.spec.ts", "fixme", "bracketed"), ("a.spec.ts", "fixme", "old === true"),
            ("a.spec.ts", "skip", "'a step'"), ("a.spec.ts", "skip", "(a reference)"), ("a.spec.ts", "skip", "(an annotation)"),
            ("a.spec.ts", "skip", "optional"), ("a.spec.ts", "skip", "smbStatus === 404"), ("a.spec.ts", "skip", "spaced"),
            ("a.spec.ts", "skip", "viaUrl"), ("c.spec.js", "skip", "x"),
            ("d.spec.ts", "fixme", "(a destructured name)"), ("d.spec.ts", "fixme", "(an annotation)"),
            ("d.spec.ts", "skip", "(a destructured name)"), ("d.spec.ts", "skip", "(a reference)"),
            ("d.spec.ts", "skip", "afterDivision"), ("d.spec.ts", "skip", "afterRegex"), ("d.spec.ts", "skip", "cast"),
            ("d.spec.ts", "skip", "optionalBracket"), ("d.spec.ts", "skip", "parenthesized"),
            ("d.spec.ts", "skip", "templated"),
            ("e.spec.ts", "expectedStatus", "(a member)"), ("e.spec.ts", "expectedStatus", "(a property)"),
            ("e.spec.ts", "fail", "(an annotation)"), ("e.spec.ts", "fixme", "(an import alias)"),
            ("e.spec.ts", "fixme", "nonNullKey"), ("e.spec.ts", "fixme", "optionalCastKey"),
            ("e.spec.ts", "skip", "(a destructured name)"), ("e.spec.ts", "skip", "(a string)"),
            ("e.spec.ts", "skip", "(a string)"), ("e.spec.ts", "skip", "(an annotation)"),
            ("e.spec.ts", "skip", "(an import alias)"), ("e.spec.ts", "skip", "angleKey"),
            ("e.spec.ts", "skip", "castKey"), ("e.spec.ts", "skip", "satisfiesKey"),
            ("sub/b.spec.mts", "fail", "version < 2"),
        ]
        assert len(_unexpected_skips(tmp_path)) == len(found)

    def test_an_expected_failure_is_refused_on_any_condition(self, tmp_path):
        """test.fixme, test.fail and the expectedStatus that test.fail sets mark a known failure,
        which ADR-0215 rules out, also on the stand-in's condition."""
        (tmp_path / "a.spec.ts").write_text(
            "test.fail(process.env.E2E_STAND_IN === '1', 'The stand-in has no disks' + TEST_STACK_TOO);\n"
            "test.fixme(process.env.E2E_STAND_IN === '1', 'The stand-in has no disks' + TEST_STACK_TOO);\n"
            "test('x', async ({}, info) => {\n  info.expectedStatus = 'failed';\n});\n",
            encoding="utf-8")
        assert _unexpected_skips(tmp_path) == [
            "a.spec.ts:1: test.fail on process.env.E2E_STAND_IN === '1': a known failure (ADR-0215)",
            "a.spec.ts:2: test.fixme on process.env.E2E_STAND_IN === '1': a known failure (ADR-0215)",
            "a.spec.ts:4: expectedStatus (a member): a known failure (ADR-0215)",
        ]

    def test_a_file_that_typescript_cannot_parse_fails_the_check(self, tmp_path):
        """Its skips are not known, so it fails the check instead of passing unseen, even when the
        skips that can be read are on listed conditions."""
        (tmp_path / "a.spec.ts").write_text(
            "test.skip(!httpUrl, 'set HTTP_URL to the plain-HTTP address');\nconst = 1;\n", encoding="utf-8")
        with pytest.raises(AssertionError, match="TypeScript cannot parse"):
            list(_skip_calls(tmp_path))

    def test_every_skip_gives_its_reason(self):
        assert any(call[3] == STAND_IN_SKIP for call in _skip_calls()), "no skip on E2E_STAND_IN found"
        assert _missing_reasons() == []

    def test_a_skip_without_its_reason_is_refused(self, tmp_path):
        """No reason, a stand-in skip that does not start with what the stand-in lacks, and one that
        does not end with TEST_STACK_TOO."""
        (tmp_path / "a.spec.ts").write_text(
            "test.skip(!httpUrl);\n"
            "test.skip(process.env.E2E_STAND_IN === '1', 'No disks here' + TEST_STACK_TOO);\n"
            "test.skip(process.env.E2E_STAND_IN === '1', 'The stand-in has no disks');\n"
            "test.skip(process.env.E2E_STAND_IN === '1', 'The stand-in has no disks' + TEST_STACK_TOO);\n",
            encoding="utf-8")
        missing = _missing_reasons(tmp_path)
        assert [problem.split(":")[:2] for problem in missing] == [["a.spec.ts", "1"], ["a.spec.ts", "2"],
                                                                   ["a.spec.ts", "3"]]
        assert "no reason" in missing[0]
        assert "does not start with what the stand-in lacks" in missing[1]
        assert "does not end with TEST_STACK_TOO" in missing[2]

    def test_the_first_run_spec_creates_the_administrator_the_specs_sign_in_as(self):
        fixtures = (SPECS / "fixtures.ts").read_text(encoding="utf-8")
        admin = re.search(
            r"^export const E2E_ADMIN = \{ username: '([^'\\]+)', password: '([^'\\]+)' \} as const;$", fixtures, re.M
        )
        assert admin, "webui/tests/fixtures.ts: no E2E_ADMIN"
        # Through the form of /setup, as the owner does after installing (ADR-0261).
        first_run = (SPECS / "first-run.spec.ts").read_text(encoding="utf-8")
        assert re.search(r"^import \{ test, expect, E2E_ADMIN \} from './fixtures';$", first_run, re.M)
        for label, value in (("'Username'", "username"), ("'Password', { exact: true }", "password"),
                             ("'Confirm Password'", "password")):
            assert f"getByLabel({label}).fill(E2E_ADMIN.{value})" in first_run, label
        # e2e.sh creates the account through that spec alone: it checks that the new stand-in has
        # none, sends no setup request of its own, runs the spec with E2E_FIRST_RUN=1, stops when
        # it fails, and only then runs the specs, without E2E_FIRST_RUN.
        e2e = (STANDIN / "e2e.sh").read_text(encoding="utf-8")
        assert "auth/setup" not in e2e
        fresh = e2e.index("/api/v1/auth/status\" | grep -q '\"setup_required\": *true'")
        first_run = re.search(r"^if ! E2E_FIRST_RUN=1 E2E_STAND_IN=1 .*\\\n.*playwright\" test --reporter=list .*; then$",
                              e2e, re.M)
        assert first_run, "scripts/standin/e2e.sh: no first-run setup before the specs"
        specs = e2e.index('"$webui/node_modules/.bin/playwright" test "$@"')
        assert fresh < first_run.start() < specs
        assert 'die "creating the test administrator through the /setup page failed' in e2e[first_run.end():specs]
        assert re.search(r"^unset E2E_FIRST_RUN$", e2e, re.M)
        # The specs take the password from E2E_ADMIN: one copy to change.
        copies = [
            spec.name
            for spec in _spec_files()
            if spec.name != "fixtures.ts" and admin[2] in spec.read_text(encoding="utf-8")
        ]
        assert copies == []

    def test_e2e_sh_sets_up_the_new_stand_in_then_runs_the_specs(self, tmp_path):
        # A caller's E2E_FIRST_RUN is dropped: only the first-run setup has it.
        result, runs = _run_e2e_sh_with_fakes(tmp_path, "tests/auth.spec.ts", specs=3, env={"E2E_FIRST_RUN": "1"})

        assert runs == ["E2E_FIRST_RUN=1 test --reporter=list", "E2E_FIRST_RUN= test tests/auth.spec.ts"]
        assert result.returncode == 3, result.stderr
        assert result.stdout.splitlines()[-1].startswith("== Playwright exit code 3 "), result.stdout

    def test_e2e_sh_stops_when_the_first_run_setup_fails(self, tmp_path):
        result, runs = _run_e2e_sh_with_fakes(tmp_path, first_run=1)

        assert runs == ["E2E_FIRST_RUN=1 test --reporter=list"]
        assert result.returncode == 1
        assert "e2e.sh: creating the test administrator through the /setup page failed" in result.stderr

    @pytest.mark.parametrize("spec", ["tests/first-run.spec.ts", "first-run.spec.ts"])
    def test_the_first_run_spec_alone_is_the_first_run_setup(self, tmp_path, spec):
        # The run of the specs ignores that file: there it would find no test, and fail.
        result, runs = _run_e2e_sh_with_fakes(tmp_path, spec, specs=1)

        assert runs == ["E2E_FIRST_RUN=1 test --reporter=list"]
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[-1] == (
            "== Playwright exit code 0 (tests/first-run.spec.ts alone; logs: .standin/logs)"
        )

    def test_the_first_run_setup_is_a_run_of_its_own_and_never_retried(self, load_config):
        files = ("tests/first-run.spec.ts", "tests/auth.spec.ts")

        first_run = load_config(None, None, env={"E2E_FIRST_RUN": "1", "CI": "true"}, args=("projects", *files))
        specs = load_config(None, None, args=("projects", *files))

        # An installation is set up once: a retry would find the account and fail for that.
        assert first_run == (0, [{"name": "first-run", "retries": 0, "runs": ["tests/first-run.spec.ts"]}])
        # The specs, also on the compose test stack, which creates the account through the API.
        assert specs == (0, [{"name": "chromium", "retries": None, "runs": ["tests/auth.spec.ts"]}])

    def test_no_spec_falls_back_to_the_device(self):
        offenders = [
            spec.name
            for spec in _spec_files()
            if re.search(r"\b10\.1\.1\.\d+\b|process\.env\.BASE_URL\s*\|\|", spec.read_text(encoding="utf-8"))
            and spec.name != "target.ts"
        ]
        assert offenders == []

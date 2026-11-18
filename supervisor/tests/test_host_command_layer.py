"""The system, security, logs, NFS, network and power routers reach the host only through src/host.py
(ADR-0414).

A process that one of their modules starts itself runs in the supervisor's container, not on
the host (ADR-0020), and nsenter of its own skips the namespaces and checks of host.py. The
modules are parsed, not imported: the test fails for a call that starts a process, for a name
imported from subprocess that starts one, and for the text "nsenter". subprocess's exceptions
(TimeoutExpired, SubprocessError) stay allowed: host.py raises them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# The modules of the routers and what they call: the system, security, logs and NFS routers, and
# those of the network and power, whose actions the entry names too.
MODULES = sorted(
    [
        *(SRC / "system").glob("*.py"),
        *(SRC / "security").glob("*.py"),
        *(SRC / "logs").glob("*.py"),
        SRC / "shares" / "nfs.py",
        SRC / "shares" / "exports.py",
        *(SRC / "network").glob("*.py"),
        *(SRC / "power").glob("*.py"),
    ]
)

# What a module may still start itself, with the reason. The UPS monitor's shutdown runs only
# outside a container, where the supervisor runs on the host itself (ADR-0233): its move to the
# host is the feature-list entry system.ups-monitoring.
ALLOWED = {
    "power/router.py": ["subprocess.Popen"],
}

# Everything of these modules that starts a process.
STARTERS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "os": {
        "system", "popen", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "posix_spawn", "posix_spawnp", "fork", "forkpty",
    },
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "pty": {"spawn", "fork"},
}


def host_commands_outside_host_py(source: str) -> list[str]:
    """Each place in ``source`` that starts a process or names nsenter: "line: what"."""
    tree = ast.parse(source)
    found: list[str] = []
    # The names under which the modules of STARTERS are imported: "import subprocess as sp".
    aliases = {name: name for name in STARTERS}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in STARTERS:
                    aliases[alias.asname or alias.name] = alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            module = aliases[node.value.id]
            if node.attr in STARTERS[module]:
                found.append(f"{node.lineno}: {module}.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module in STARTERS:
            for alias in node.names:
                if alias.name in STARTERS[node.module] or alias.name == "*":
                    found.append(f"{node.lineno}: from {node.module} import {alias.name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "nsenter" in node.value:
            found.append(f"{node.lineno}: the text nsenter")
    return sorted(found, key=lambda text: int(text.split(":")[0]))


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(SRC)))
def test_the_module_runs_no_command_outside_host_py(path):
    found = [text.split(": ", 1)[1] for text in host_commands_outside_host_py(path.read_text())]
    assert found == ALLOWED.get(str(path.relative_to(SRC)), [])


def test_the_modules_are_there():
    """A module that moved would leave the test without its subject."""
    names = {str(path.relative_to(SRC)) for path in MODULES}
    assert {"system/router.py", "security/router.py", "security/firewall.py", "logs/router.py",
            "shares/nfs.py", "shares/exports.py"} <= names


class TestTheCheck:
    """The check finds every way these modules could start a process."""

    @pytest.mark.parametrize(
        "source, what",
        [
            ("import subprocess\nsubprocess.run(['ufw', 'status'])\n", "subprocess.run"),
            ("import subprocess\nsubprocess.Popen(['ufw'])\n", "subprocess.Popen"),
            ("import subprocess\nf = subprocess.check_output\n", "subprocess.check_output"),
            ("from subprocess import run\n", "from subprocess import run"),
            ("from subprocess import *\n", "from subprocess import *"),
            ("import os\nos.system('ufw enable')\n", "os.system"),
            ("import os\nos.execvp('ufw', ['ufw'])\n", "os.execvp"),
            ("import asyncio\nasyncio.create_subprocess_exec('ufw')\n", "asyncio.create_subprocess_exec"),
            ("import functools, subprocess\nfunctools.partial(subprocess.run, ['openssl'])\n", "subprocess.run"),
            ("cmd = ['nsenter', '--target', '1', '--net', 'ip']\n", "the text nsenter"),
            ("import subprocess as sp\nsp.run(['ufw'])\n", "subprocess.run"),
        ],
    )
    def test_it_finds(self, source, what):
        assert [found.split(": ", 1)[1] for found in host_commands_outside_host_py(source)] == [what]

    def test_it_lets_subprocess_errors_through(self):
        source = (
            "import subprocess\n"
            "try:\n    pass\n"
            "except (subprocess.TimeoutExpired, subprocess.SubprocessError):\n    pass\n"
            "from subprocess import TimeoutExpired\n"
        )
        assert host_commands_outside_host_py(source) == []


@pytest.mark.parametrize("method, path", [
    ("get", "/api/v1/system/network"),
    ("get", "/api/v1/system/pcie"),
    ("get", "/api/v1/logs/files"),
    ("get", "/api/v1/logs/file/syslog"),
    ("delete", "/api/v1/logs/clear/journal"),
])
def test_the_routes_that_read_the_container_are_gone(method, path):
    """GET /system/network ran nsenter of its own, /system/pcie an lspci the container lacks, and
    /logs/files and /logs/file/{filename} read the container's /var/log; DELETE /logs/clear did
    nothing. No page called them (ADR-0414, ADR-0207)."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from src.auth.utils import verify_token
    from src.main import app

    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=1, username="admin", is_admin=True)
    try:
        assert getattr(TestClient(app), method)(path).status_code == 404
    finally:
        app.dependency_overrides.pop(verify_token, None)

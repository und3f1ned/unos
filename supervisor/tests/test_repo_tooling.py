"""The repository's own tooling: the Makefile (ADR-0216), scripts/setup.sh (ADR-0217), the git
hooks and scripts/check-commit-msg.py (ADR-0209), .claude/settings.json with its Stop hook
(ADR-0211, ADR-0216), the project skills (ADR-0205), the CI workflows, Dependabot and the triage
templates (ADR-0206, ADR-0218), .gitignore, and the scan of the repository's files for secrets
(ADR-0212).

Nothing here touches the real checkout beyond reading it: the setup script, the Stop hook and the
commit checker run in temporary repositories, with stub tools where they would use the network,
and git never gets the GIT_* variables of an outer git (a test runs the suite's git steps with
GIT_DIR and GIT_INDEX_FILE pointing at a repository that must stay unchanged).
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TARGETS = ("help", "setup", "check", "check-fast", "lint", "test", "test-fast", "smoke", "frontend", "e2e-local",
           "audit")
TEST_FAST = ("tests/test_adrs.py", "tests/test_feature_list.py", "tests/test_agent_guard.py",
             "tests/test_repo_tooling.py", "tests/test_dev_scripts.py", "tests/test_standin.py")
# make smoke: the startup and /api/v1/health, the first-run setup, a login, anonymous requests.
SMOKE = ("tests/test_lifespan.py", "tests/test_auth.py::TestFirstRunSetup", "tests/test_authz.py::TestRealClientAddress",
         "tests/test_authz.py::TestRouteMatrix::test_anonymous_requests_are_rejected")
TOOLS = ("PYTHON=/opt/py/bin/python", "RUFF=/opt/lint/bin/ruff", "PIP_AUDIT=pip-audit", "NPM=npm")


def without_git_variables(environ) -> dict:
    """A copy of an environment without GIT_DIR, GIT_INDEX_FILE and the other GIT_* variables
    that git exports to its hooks and to git rebase -x: inherited, they would send the git
    commands of the fixtures below into the developer's repository (conftest.py removes them
    from os.environ as well)."""
    return {key: value for key, value in environ.items() if not key.startswith("GIT_")}


def isolated_git_env(home: Path) -> dict:
    """An environment in which git ignores the user's and the system's configuration and the
    GIT_* variables of an outer git."""
    home.mkdir(parents=True, exist_ok=True)
    return dict(without_git_variables(os.environ), HOME=str(home), GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.org",
                GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.org")


# The variables of an outer make (when the suite runs under make test) and of the Makefile
# itself must not reach the make runs below, nor those of an outer git.
MAKE_ENV = {key: value for key, value in without_git_variables(os.environ).items()
            if key not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "PYTHON", "RUFF", "PIP_AUDIT",
                           "NPM", "TESTS", "PYTEST_ARGS", "E2E_ARGS", "TEST_LOCK")}


def make(*args, cwd=REPO_ROOT, check=True):
    return subprocess.run(["make", "--no-print-directory", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=120, check=check, env=MAKE_ENV)


# --- Makefile -------------------------------------------------------------------------------


def test_makefile_has_the_targets_and_declares_them_phony():
    text = MAKEFILE.read_text()
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", text, re.M))
    phony = set(" ".join(re.findall(r"^\.PHONY:(.*)$", text, re.M)).split())

    assert set(TARGETS) <= targets
    assert phony == targets


def test_make_help_lists_every_target():
    lines = make("help").stdout.splitlines()

    assert [target for target in TARGETS if not any(line.startswith(f"  {target} ") for line in lines)] == []
    assert any(line.startswith("Variables: PYTHON=") for line in lines)


def test_help_is_the_default_target():
    assert make().stdout == make("help").stdout


@pytest.mark.parametrize("target, commands", [
    ("setup", ["scripts/setup.sh"]),
    ("lint", ["/opt/lint/bin/ruff check --select F,E9 supervisor scripts .claude/hooks", "bash -n",
              "deploy scripts .claude/hooks", "/opt/py/bin/python -m json.tool",
              "feature-list.json .claude/settings.json"]),
    ("test", ["cd supervisor &&", "/opt/py/bin/python -m pytest tests/ -q --tb=short "
              "--ignore=tests/mocks --ignore=tests/fixtures"]),
    ("test-fast", ["cd supervisor &&", "/opt/py/bin/python -m pytest " + " ".join(TEST_FAST)]),
    ("smoke", ["cd supervisor &&", "/opt/py/bin/python -m pytest " + " ".join(SMOKE)]),
    ("frontend", ["cd webui && npm run check", "cd webui && npm run build"]),
    ("e2e-local", ["scripts/standin/e2e.sh"]),
    ("audit", ["pip-audit -r supervisor/requirements.txt", "cd webui && npm audit --omit=dev --audit-level=high"]),
])
def test_targets_run_the_agreed_commands(target, commands):
    dry_run = make("-n", target, *TOOLS).stdout

    assert [command for command in commands if command not in dry_run] == []


def test_check_runs_lint_test_and_frontend_and_check_fast_the_fast_part():
    check, fast = make("-n", "check", *TOOLS).stdout, make("-n", "check-fast", *TOOLS).stdout
    lint, test, test_fast, frontend = (make("-n", target, *TOOLS).stdout for target in
                                       ("lint", "test", "test-fast", "frontend"))

    assert lint in check and test in check and frontend in check and test_fast not in check
    assert lint in fast and test_fast in fast and frontend not in fast and "npm" not in fast
    assert "test_lifespan.py" not in check + fast  # make smoke is a part of make test only


def test_the_smoke_tests_exist():
    missing = []
    for test in SMOKE:
        path, *names = test.split("::")
        tree = ast.parse((REPO_ROOT / "supervisor" / path).read_text())
        scope = tree.body
        for name in names:
            found = [node for node in scope if getattr(node, "name", None) == name]
            if not found:
                missing.append(test)
                break
            scope = found[0].body
    assert missing == []
    assert " ".join(SMOKE) in make("-n", "smoke", *TOOLS).stdout


def test_a_tool_given_as_a_relative_path_still_works_in_supervisor():
    dry_run = make("-n", "test", "PYTHON=supervisor/.venv/bin/python").stdout

    assert f"{REPO_ROOT}/supervisor/.venv/bin/python -m pytest" in dry_run


def test_the_test_targets_wait_for_the_machine_wide_lock():
    locked = make("-n", "test", *TOOLS).stdout
    unlocked = make("-n", "test", *TOOLS, "TEST_LOCK=").stdout

    assert re.search(r"(flock -w 1800|/usr/bin/lockf -k -t 1800) /tmp/unos-backend-tests\.lock "
                     r"/opt/py/bin/python -m pytest", locked)
    assert "unos-backend-tests.lock" not in unlocked


def test_the_defaults_are_the_environments_of_make_setup(tmp_path):
    shutil.copy(MAKEFILE, tmp_path / "Makefile")
    plain = make("-n", "test", "lint", cwd=tmp_path).stdout
    for tool in ("supervisor/.venv/bin/python", ".venv-lint/bin/ruff"):
        (tmp_path / tool).parent.mkdir(parents=True)
        (tmp_path / tool).write_text("#!/bin/sh\n")
    installed = make("-n", "test", "lint", cwd=tmp_path).stdout

    assert "python3 -m pytest" in plain and "\nruff check" in "\n" + plain
    assert f"{tmp_path.resolve()}/supervisor/.venv/bin/python -m pytest" in installed
    assert ".venv-lint/bin/ruff check --select F,E9 supervisor scripts .claude/hooks" in installed


def test_check_runs_every_part_even_when_one_fails(tmp_path):
    shutil.copy(MAKEFILE, tmp_path / "Makefile")
    for directory in ("supervisor", "webui"):
        (tmp_path / directory).mkdir()
    # lint fails at once (ruff is `false`); the stand-ins for python and npm print their arguments.
    result = make("check", "RUFF=false", "PYTHON=echo", "NPM=echo", "TEST_LOCK=", cwd=tmp_path, check=False)

    assert result.returncode != 0
    assert "make check failed: lint" in result.stderr
    assert "-m pytest tests/" in result.stdout  # test ran
    assert "run check" in result.stdout and "run build" in result.stdout  # frontend ran
    # Each part starts with the line that .claude/hooks/stop-check.sh looks for.
    assert [line for line in result.stdout.splitlines() if line.startswith("== make check: ")] == [
        "== make check: lint", "== make check: test", "== make check: frontend"]


def test_lint_and_frontend_run_every_part_even_when_one_fails(tmp_path):
    shutil.copy(MAKEFILE, tmp_path / "Makefile")
    (tmp_path / "webui").mkdir()
    (tmp_path / "feature-list.json").write_text("{not json\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "good.sh").write_text("#!/bin/sh\necho ok\n")
    (tmp_path / "scripts" / "bad.sh").write_text("#!/bin/sh\nif then\n")
    npm = tmp_path / "npm"  # svelte-check fails, the build passes
    npm.write_text('#!/bin/sh\necho "npm $*"\n[ "$2" != check ]\n')
    npm.chmod(0o755)

    lint = make("lint", "RUFF=false", f"PYTHON={sys.executable}", cwd=tmp_path, check=False)
    frontend = make("frontend", f"NPM={npm}", cwd=tmp_path, check=False)

    assert lint.returncode != 0
    assert "make lint failed: lint-ruff lint-shell lint-json" in lint.stderr
    assert "bash -n failed: scripts/bad.sh" in lint.stderr and "not valid JSON: feature-list.json" in lint.stderr
    assert frontend.returncode != 0
    assert "make frontend failed: frontend-check" in frontend.stderr
    assert "npm run build" in frontend.stdout  # the build ran after the failed check


# --- .claude/settings.json, the guard and the Stop hook --------------------------------------

SETTINGS = REPO_ROOT / ".claude" / "settings.json"
HOOKS = REPO_ROOT / ".claude" / "hooks"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _module("guard", HOOKS / "guard.py")


def test_settings_turn_auto_memory_off_deny_dev_env_and_desktop_commander_and_run_the_hooks():
    settings = json.loads(SETTINGS.read_text())
    guard, stop = settings["hooks"]["PreToolUse"], settings["hooks"]["Stop"]

    assert settings["autoMemoryEnabled"] is False
    # ./ is the session's directory. A session in a linked worktree uses the main checkout's dev.env
    # and device backups (tmp/dev/backup, ADR-0223) by a path outside it: // starts at the
    # filesystem's root, so these rules cover every dev.env and every backup, wherever they are.
    # Desktop Commander's shell and file tools would get around the guard and the Read rules.
    assert {"Read(./dev.env)", "Read(./**/dev.env)", "Read(//**/dev.env)", "Read(//**/tmp/dev/backup/**)",
            "mcp__plugin_desktop-commander_desktop-commander"} <= set(settings["permissions"]["deny"])
    assert set(settings["hooks"]) == {"PreToolUse", "Stop"}
    assert [entry["matcher"] for entry in guard] == ["Bash|mcp__terminal__run_in_terminal"]
    # The guard checks the commands of exactly the tools that the matcher sends it.
    assert sorted(guard[0]["matcher"].split("|")) == sorted(GUARD.COMMAND_TOOLS)
    assert guard[0]["hooks"] == [{"type": "command",
                                  "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py"'}]
    assert stop == [{"hooks": [{"type": "command", "command": '"$CLAUDE_PROJECT_DIR/.claude/hooks/stop-check.sh"',
                                "timeout": 600}]}]
    for script in ("guard.py", "stop-check.sh"):
        assert (HOOKS / script).is_file() and os.access(HOOKS / script, os.X_OK), script


def test_the_hook_commands_run_the_hooks(tmp_path):
    settings = json.loads(SETTINGS.read_text())
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "hooks").symlink_to(HOOKS)
    env = dict(MAKE_ENV, CLAUDE_PROJECT_DIR=str(project))

    def run(entry, payload):
        return subprocess.run(["sh", "-c", settings["hooks"][entry][0]["hooks"][0]["command"]], cwd=tmp_path,
                              input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)

    blocked = run("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
                                 "cwd": str(tmp_path)})
    stopped = run("Stop", {"hook_event_name": "Stop", "stop_hook_active": True})

    assert blocked.returncode == 2
    assert blocked.stderr.startswith("Blocked by .claude/hooks/guard.py (push-main): ")
    assert (project / "tmp" / "guard" / "denials.log").read_text().split()[1:] == ["push-main", "Bash"]
    assert (stopped.returncode, stopped.stdout, stopped.stderr) == (0, "", "")


STOP_CHECK = HOOKS / "stop-check.sh"
CHECK_FAST = "make --no-print-directory check-fast"


@pytest.fixture
def stop_hook(tmp_path):
    """A repository in which .claude/hooks/stop-check.sh runs as Claude Code runs it, with stubs
    for make (it prints the file make-output and exits with make-status), ruff and Python."""
    repo, stubs = tmp_path / "repo", tmp_path / "stubs"
    repo.mkdir()
    stubs.mkdir()
    env = dict(MAKE_ENV, **{key: value for key, value in isolated_git_env(tmp_path / "home").items()
                            if key.startswith(("GIT_", "HOME"))})

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, timeout=30)

    git("init", "-q")
    (repo / ".gitignore").write_text("tmp/\n")
    (repo / "README").write_text("x\n")
    git("add", ".")
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "chore: start")
    stub_bodies = {
        "make": 'echo "make $*" >> "$STUB_LOG"\nif [ -n "${STUB_REAL_MAKE:-}" ]; then exec "$STUB_REAL_MAKE" "$@"; fi\n'
                'cat "$STUB_DIR/make-output" 2> /dev/null\nexit "$(cat "$STUB_DIR/make-status" 2> /dev/null || echo 0)"\n',
        # A Python with pytest (make setup's) whose tests pass; anything else is the real Python.
        "python": 'case "$1 $2" in\n  "-c import pytest") exit "${STUB_NO_PYTEST:-0}" ;;\n'
                  '  "-m pytest") echo "pytest stub: 3 passed"; exit 0 ;;\nesac\nexec "$REAL_PYTHON" "$@"\n',
        "ruff": 'echo "supervisor/src/x.py:1:8: F401 [*] os imported but unused"\nexit "${STUB_RUFF_STATUS:-0}"\n',
    }
    for name, body in stub_bodies.items():
        (stubs / name).write_text("#!/bin/sh\n" + body)
        (stubs / name).chmod(0o755)
    real_make = shutil.which("make")
    env.update(PATH=f"{stubs}:{os.environ['PATH']}", STUB_DIR=str(stubs), STUB_LOG=str(tmp_path / "log"),
               PYTHON=str(stubs / "python"), RUFF=str(stubs / "ruff"), REAL_PYTHON=sys.executable,
               CLAUDE_PROJECT_DIR=str(repo), TEST_LOCK="")

    def run(payload=None, **extra):
        (tmp_path / "log").write_text("")
        result = subprocess.run([str(STOP_CHECK)], input=json.dumps(payload or {"hook_event_name": "Stop",
                                                                               "stop_hook_active": False}),
                                cwd=repo, env=dict(env, **extra), capture_output=True, text=True, timeout=120)
        return result, (tmp_path / "log").read_text().splitlines()

    def answer(output, status):
        (stubs / "make-output").write_text(output)
        (stubs / "make-status").write_text(f"{status}\n")

    return SimpleNamespace(repo=repo, run=run, answer=answer, real_make=real_make)


def test_the_stop_hook_runs_check_fast_once_for_each_state_of_the_tree(stop_hook):
    repo = stop_hook.repo

    first, calls = stop_hook.run()
    assert (first.returncode, calls) == (0, [CHECK_FAST]), first.stdout + first.stderr
    assert stop_hook.run()[1] == []  # nothing changed since the green run

    for change in (lambda: (repo / "README").write_text("y\n"),  # a tracked file
                   lambda: (repo / "new.py").write_text("a\n"),  # a new untracked file
                   lambda: (repo / "new.py").write_text("b\n")):  # the content of an untracked file
        change()
        assert stop_hook.run()[1] == [CHECK_FAST]
        assert stop_hook.run()[1] == []
    (repo / "tmp" / "other").mkdir(parents=True, exist_ok=True)  # git ignores tmp/
    (repo / "tmp" / "other" / "x.txt").write_text("x\n")
    assert stop_hook.run()[1] == []


def test_a_failing_check_sends_the_agent_back_with_the_failing_part(stop_hook):
    stop_hook.answer("== make check-fast: lint\n== make lint: lint-ruff\nF401 [*] os imported but unused\n"
                     "make lint failed: lint-ruff\n== make check-fast: test-fast\npytest: 5 passed\n"
                     "make check-fast failed: lint\n", 2)

    result, calls = stop_hook.run()
    log = (stop_hook.repo / "tmp" / "stop-check" / "check-fast.log").read_text()

    assert (result.returncode, calls) == (2, [CHECK_FAST])
    assert result.stderr.startswith("stop-check.sh: make check-fast failed in: lint. Fix it before you finish")
    assert "--- the last lines of make lint:\n== make lint: lint-ruff\nF401 [*] os imported but unused" in result.stderr
    assert "5 passed" not in result.stderr and "5 passed" in log
    assert "tmp/stop-check/check-fast.log" in result.stderr
    # No green run yet: the next stop checks again, and after a pass the hook is quiet.
    stop_hook.answer("make check-fast passed: lint test-fast\n", 0)
    assert stop_hook.run()[0].returncode == 0
    assert stop_hook.run()[1] == []


def test_a_failure_without_parts_shows_the_end_of_the_output(stop_hook):
    stop_hook.answer("Makefile:3: *** missing separator.  Stop.\n", 2)

    result, _ = stop_hook.run()

    assert result.returncode == 2
    assert result.stderr.endswith("Makefile:3: *** missing separator.  Stop.\n")


def test_the_stop_hook_reads_the_parts_of_the_real_makefile(stop_hook):
    shutil.copy(MAKEFILE, stop_hook.repo / "Makefile")
    (stop_hook.repo / "supervisor").mkdir()
    (stop_hook.repo / "feature-list.json").write_text("{}\n")
    (stop_hook.repo / ".claude").mkdir()
    (stop_hook.repo / ".claude" / "settings.json").write_text("{}\n")

    result, _ = stop_hook.run(STUB_REAL_MAKE=stop_hook.real_make, STUB_RUFF_STATUS="1")

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stderr.startswith("stop-check.sh: make check-fast failed in: lint.")
    assert "F401 [*] os imported but unused" in result.stderr
    assert "pytest stub" not in result.stderr  # test-fast passed: not shown


def test_a_session_that_went_on_once_because_of_the_hook_may_stop(stop_hook):
    stop_hook.answer("make check-fast failed: lint\n", 2)

    result, calls = stop_hook.run({"hook_event_name": "Stop", "stop_hook_active": True})

    assert (result.returncode, calls, result.stdout, result.stderr) == (0, [], "", "")


def _warning(result) -> str:
    """The systemMessage of the Stop hook's JSON answer: one line, which Claude Code shows the user."""
    assert result.stdout.count("\n") == 1 and result.stderr == "", result.stdout + result.stderr
    answer = json.loads(result.stdout)
    assert set(answer) == {"systemMessage"}
    return answer["systemMessage"]


@pytest.mark.parametrize("extra, missing", [
    ({"STUB_NO_PYTEST": "1"}, "pytest"),
    ({"RUFF": "/nonexistent/ruff"}, "ruff"),
    ({"PYTHON": '/no "such"\\python'}, 'pytest (/no "such"\\python)'),  # quotes and a backslash: still JSON
])
def test_without_the_tools_the_session_stops_with_a_warning(stop_hook, extra, missing):
    result, calls = stop_hook.run(**extra)
    warning = _warning(result)

    assert (result.returncode, calls) == (0, [])
    assert f"missing: {missing}" in warning and "make setup" in warning and "did not run" in warning


def test_outside_a_work_tree_the_session_stops_with_a_warning(stop_hook, tmp_path):
    (tmp_path / "plain").mkdir()

    result, calls = stop_hook.run(CLAUDE_PROJECT_DIR=str(tmp_path / "plain"),
                                  GIT_CEILING_DIRECTORIES=str(tmp_path))

    assert (result.returncode, calls) == (0, [])
    assert _warning(result) == "stop-check.sh: not a git work tree; make check-fast did not run."


# --- git hooks -------------------------------------------------------------------------------


@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg"])
def test_hooks_are_executable_shell_scripts(hook):
    path = REPO_ROOT / "scripts" / "git-hooks" / hook

    assert path.stat().st_mode & stat.S_IXUSR
    assert path.read_text().startswith("#!/bin/sh\n")
    subprocess.run(["sh", "-n", str(path)], check=True, timeout=30)


def test_pre_commit_runs_make_check_fast_at_the_top_of_the_work_tree(tmp_path):
    stub = tmp_path / "make"
    stub.write_text('#!/bin/sh\necho "$(pwd) $*" > "$STUB_LOG"\n')
    stub.chmod(0o755)

    subprocess.run([str(REPO_ROOT / "scripts" / "git-hooks" / "pre-commit")], cwd=REPO_ROOT / "docs",
                   env=dict(without_git_variables(os.environ), MAKE=str(stub), STUB_LOG=str(tmp_path / "log")),
                   check=True, timeout=60)

    assert (tmp_path / "log").read_text() == f"{REPO_ROOT} check-fast\n"


def test_pre_commit_keeps_gits_variables_from_make_check_fast(tmp_path):
    # git exports GIT_DIR to the hooks of a linked worktree and GIT_INDEX_FILE (its index.lock)
    # during git commit -a: inherited, they would point the git commands of make check-fast's
    # tests at the repository being committed to.
    main, linked = tmp_path / "main", tmp_path / "linked"
    main.mkdir()
    env = isolated_git_env(tmp_path / "home")
    for args in (["init", "-q"], ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "chore: start"],
                 ["worktree", "add", "-q", "-b", "linked", str(linked)]):
        subprocess.run(["git", *args], cwd=main, env=env, check=True, capture_output=True, timeout=30)
    git_dir = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=linked, env=env, check=True,
                             capture_output=True, text=True, timeout=30).stdout.strip()
    stub = tmp_path / "make"
    stub.write_text('#!/bin/sh\n{ pwd; env | sed -n "s/^\\(GIT_[A-Z_]*\\)=.*/\\1/p"; echo "make $*"; } > "$STUB_LOG"\n')
    stub.chmod(0o755)
    hook_env = dict(env, MAKE=str(stub), STUB_LOG=str(tmp_path / "log"), GIT_DIR=git_dir,
                    GIT_INDEX_FILE=os.path.join(git_dir, "index.lock"), GIT_WORK_TREE=str(linked), GIT_PREFIX="")

    subprocess.run([str(REPO_ROOT / "scripts" / "git-hooks" / "pre-commit")], cwd=linked, env=hook_env, check=True,
                   timeout=60)
    top, *variables, made = (tmp_path / "log").read_text().splitlines()

    assert (Path(top).resolve(), made) == (linked.resolve(), "make check-fast")
    assert "GIT_CONFIG_GLOBAL" in variables  # the stub saw the environment
    assert {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX"} & set(variables) == set()


# The git steps of the suite's fixtures: the places of the guard's cases, the Stop hook's
# repository, setup's sandbox with a linked worktree, a commit history, the .gitignore check and
# update.sh's clone of a local origin.
GIT_STEPS = (
    "tests/test_agent_guard.py::test_the_terminal_tool_runs_in_its_own_cwd",
    "tests/test_repo_tooling.py::test_the_stop_hook_runs_check_fast_once_for_each_state_of_the_tree",
    "tests/test_repo_tooling.py::test_setup_in_a_linked_worktree_uses_the_main_checkouts_dev_env",
    "tests/test_repo_tooling.py::test_a_conforming_range_passes",
    "tests/test_repo_tooling.py::test_gitignore_covers_the_local_environments_and_a_linked_node_modules",
    "tests/test_deploy_scripts.py::TestUpdateScript::test_builds_while_running_then_switches_and_prunes_after_health",
)


def _files_with_digests(root: Path) -> dict:
    """Every file below root, the git directory included, with the SHA-256 of its content."""
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()}


def test_the_suites_git_steps_leave_an_outer_repository_alone(tmp_path):
    # git exports GIT_DIR to the commands of git rebase -x in a linked worktree, and GIT_INDEX_FILE
    # to the pre-commit hook: make check-fast and make test may run with both pointing at a real
    # repository. conftest.py removes them, and the fixtures build their environments without them.
    outer = tmp_path / "outer"
    outer.mkdir()
    env = isolated_git_env(tmp_path / "home")
    (outer / "README").write_text("the outer repository\n")
    for args in (["init", "-q", "-b", "main"], ["add", "README"],
                 ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "chore: start"]):
        subprocess.run(["git", *args], cwd=outer, env=env, check=True, capture_output=True, timeout=30)
    before = _files_with_digests(outer)

    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *GIT_STEPS],
                            cwd=REPO_ROOT / "supervisor", capture_output=True, text=True, timeout=600,
                            env=dict(os.environ, GIT_DIR=str(outer / ".git"), GIT_INDEX_FILE=str(outer / ".git" / "index"),
                                     PYTHONDONTWRITEBYTECODE="1"))

    assert _files_with_digests(outer) == before, "the suite's git commands changed the outer repository"
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert f"{len(GIT_STEPS)} passed" in result.stdout


@pytest.mark.parametrize("message, code", [("feat(shares): add per-user access\n", 0), ("Add things\n", 1)])
def test_commit_msg_runs_the_checker_on_the_message_file(tmp_path, message, code):
    (tmp_path / "COMMIT_EDITMSG").write_text(message)

    result = subprocess.run([str(REPO_ROOT / "scripts" / "git-hooks" / "commit-msg"), str(tmp_path / "COMMIT_EDITMSG")],
                            capture_output=True, text=True, timeout=60)

    assert result.returncode == code
    assert ("ADR-0209" in result.stderr) == bool(code)


# --- scripts/check-commit-msg.py ---------------------------------------------------------


def _checker():
    spec = importlib.util.spec_from_file_location("check_commit_msg", REPO_ROOT / "scripts" / "check-commit-msg.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _checker()
FEATURES = {"shares.per-user-access"}
GOOD_MESSAGES = [
    "feat: add per-user access to SMB shares",
    "fix(shares): keep the setgid bit\n\nThe folder lost it.\n\nFeature: shares.per-user-access\n"
    "Co-Authored-By: Someone <someone@example.org>",
    "docs(adr)!: renumber the conversion ADRs",
    "chore(webui/tests,scripts): move the stand-in skips",
    "revert: feat: add per-user access",
    "test: cover the guard's heredocs",
    "feat: " + "a" * 66,  # 72 characters
    "Merge branch 'main' into feature",
    "Merge pull request #21 from und3f1ned/phase0-hardening",
    "fixup! feat: add per-user access to SMB shares",
    "feat: add x\n# Please enter the commit message for your changes.\n"
    "# ------------------------ >8 ------------------------\ndiff --git a/x b/x\n+Add things.",
    "\n\nperf: blank lines around the message are not part of it\n\n",
    # Dependabot's messages, with the prefixes of .github/dependabot.yml.
    "build(deps): bump the pip group across 1 directory with 3 updates\n\nBumps the pip group.\n\n"
    "Signed-off-by: dependabot[bot] <support@github.com>",
    "build(deps-dev): bump svelte-check from 4.3.4 to 4.3.5 in /webui",
    "ci(deps): bump actions/checkout from 4 to 5 in the actions group",
]
BAD_MESSAGES = [
    ("Add SMB shares", "type(scope)!: subject"),
    ("feature: add x", "type(scope)!: subject"),
    ("FEAT: add x", "type(scope)!: subject"),
    ("feat : add x", "type(scope)!: subject"),
    ("WIP", "type(scope)!: subject"),
    ("feat:add x", "one space after the colon"),
    ("feat:  add x", "one space after the colon"),
    ("feat:", "one space after the colon"),
    ("feat(): add x", "the scope"),
    ("feat(Shares): add x", "the scope"),
    ("feat: add x.", "ends with a period"),
    ("feat: " + "a" * 67, "73 characters"),
    ("feat: add x\nno blank line before the body", "blank line"),
    ("", "empty"),
    ("# only a comment\n", "empty"),
    ("feat: x\n\nFeature: Not An Id", "feature-list id"),
    ("feat: x\n\nFeature: shares.no-such-entry", "not an id of feature-list.json"),
    ("feat: \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c", "English"),
]


@pytest.mark.parametrize("message", GOOD_MESSAGES)
def test_good_messages_pass(message):
    assert CHECKER.problems(message, FEATURES) == []


@pytest.mark.parametrize("message, problem", BAD_MESSAGES)
def test_bad_messages_name_the_problem(message, problem):
    found = CHECKER.problems(message, FEATURES)

    assert len(found) == 1 and problem in found[0], found


def test_autosquash_commits_fail_the_range_check_only():
    assert CHECKER.problems("fixup! feat: x", FEATURES) == []
    assert "squash" in CHECKER.problems("fixup! feat: x", FEATURES, autosquash_ok=False)[0]


def test_the_message_file_mode(tmp_path):
    script = REPO_ROOT / "scripts" / "check-commit-msg.py"
    features = tmp_path / "feature-list.json"
    features.write_text(json.dumps({"features": [{"id": "shares.per-user-access"}]}))
    (tmp_path / "good").write_text("fix: x\n\nFeature: shares.per-user-access\n")
    (tmp_path / "unknown").write_text("fix: x\n\nFeature: shares.unknown\n")

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=60)

    assert run(str(tmp_path / "good"), "--feature-list", str(features)).returncode == 0
    unknown = run(str(tmp_path / "unknown"), "--feature-list", str(features))
    assert unknown.returncode == 1 and "shares.unknown" in unknown.stderr
    assert run(str(tmp_path / "missing")).returncode == 2
    assert run().returncode == 2


@pytest.fixture
def history(tmp_path):
    """A repository whose first commits predate the checker, then conforming and failing ones."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = isolated_git_env(tmp_path / "home")

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True,
                              timeout=30).stdout.strip()

    def commit(message, path, content):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
        git("add", path)
        git("-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    shas = {"first": commit("Initial import", "README", "x\n")}
    shas["older"] = commit("update stuff", "README", "y\n")
    shas["checker"] = commit("chore: add the commit message checker", "scripts/check-commit-msg.py",
                             (REPO_ROOT / "scripts" / "check-commit-msg.py").read_text())
    shas["feature"] = commit("feat(shares): add per-user access\n\nFeature: shares.per-user-access",
                             "feature-list.json", json.dumps({"features": [{"id": "shares.per-user-access"}]}))
    shas["bad"] = commit("Bad message without a type", "a.txt", "a\n")
    git("checkout", "-q", "-b", "side")
    shas["side"] = commit("fix: on the side branch", "b.txt", "b\n")
    git("checkout", "-q", "main")
    git("-c", "commit.gpgsign=false", "merge", "-q", "--no-ff", "--no-edit", "side")
    shas["fixup"] = commit("fixup! feat(shares): add per-user access", "c.txt", "c\n")
    shas["unknown"] = commit("feat: x\n\nFeature: shares.unknown", "d.txt", "d\n")
    return repo, env, shas


def _check_range(repo, env, revisions):
    return subprocess.run([sys.executable, str(repo / "scripts" / "check-commit-msg.py"), "--range", revisions],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=60)


def test_the_range_checks_only_commits_that_have_the_checker(history):
    repo, env, shas = history

    result = _check_range(repo, env, f"{shas['first']}..HEAD")

    assert result.returncode == 1
    failing = [line.split()[0] for line in result.stdout.splitlines() if re.match(r"[0-9a-f]{10} ", line)]
    assert failing == [shas["bad"][:10], shas["fixup"][:10], shas["unknown"][:10]]
    assert "3 of 6 commits" in result.stdout  # checker, feature, bad, side, fixup, unknown
    assert "(2 older or merge commits skipped)" in result.stdout
    assert "squash" in result.stdout and "shares.unknown" in result.stdout
    # Pushed commits are never rewritten (ADR-0208): the advice is the owner's squash merge.
    assert "rebase -i" not in result.stdout and "ask the owner to squash-merge the pull request" in result.stdout


def test_a_conforming_range_passes(history):
    repo, env, shas = history

    result = _check_range(repo, env, f"{shas['older']}..{shas['feature']}")

    assert result.returncode == 0, result.stdout
    assert "2 commits follow the commit convention" in result.stdout


def test_a_range_before_the_checker_has_nothing_to_check(history):
    repo, env, shas = history

    result = _check_range(repo, env, f"{shas['first']}..{shas['older']}")

    assert result.returncode == 0
    assert "0 commits follow the commit convention (1 older or merge commits skipped)" in result.stdout


@pytest.mark.parametrize("revisions", ["HEAD", "no-such-revision..HEAD"])
def test_a_bad_range_is_a_usage_error(history, revisions):
    repo, env, _ = history

    assert _check_range(repo, env, revisions).returncode == 2


# --- scripts/setup.sh --------------------------------------------------------------------


def test_setup_check_reports_and_changes_nothing():
    def state():
        hooks = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=REPO_ROOT, capture_output=True,
                               text=True, timeout=30).stdout
        return hooks, [(REPO_ROOT / path).exists() for path in ("supervisor/.venv", ".venv-lint", "dev.env")]

    before = state()
    result = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup.sh"), "--check"], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=120)

    assert result.returncode in (0, 1), result.stderr
    assert result.stdout.startswith("setup.sh: prerequisites\n")
    assert re.search(r"^setup\.sh --check: ", result.stdout, re.M)
    assert state() == before


STUBS = {
    "python3.11": r"""
        echo "python3.11 $*" >> "$STUB_LOG"
        case "$1" in
            -c) echo 3.11 ;;
            -m) if [ "$2" = venv ]; then
                    for dir; do :; done
                    mkdir -p "$dir/bin"
                    cp "$STUB_DIR/venv-python" "$dir/bin/python"
                fi ;;
        esac""",
    "venv-python": r"""
        echo "$0 $*" >> "$STUB_LOG"
        case "$1" in -c) echo 3.11 ;; esac""",
    "node": r"""
        echo "node $*" >> "$STUB_LOG"
        case "$1" in --version) echo v22.11.0 ;; esac""",
    "npm": r"""
        echo "npm $*" >> "$STUB_LOG"
        case "$1" in
            --version) echo 10.9.0 ;;
            ci) mkdir -p node_modules/.bin && cp "$STUB_DIR/playwright" node_modules/.bin/playwright ;;
        esac""",
    "playwright": r"""
        echo "playwright $*" >> "$STUB_LOG"
        if [ "$1 $2" = "install --dry-run" ]; then
            echo "browser: chromium version 1"
            echo "  Install location:    $STUB_DIR/browsers/chromium"
        elif [ "$1" = install ]; then
            mkdir -p "$STUB_DIR/browsers/chromium"
        fi""",
    "make": r"""
        echo "make $*" >> "$STUB_LOG"
        case "$1" in
            --version) echo "GNU Make 4.4.1" ;;
            "${STUB_FAILING_TARGET:-}") exit 2 ;;
        esac""",
}
SYSTEM_TOOLS = ("bash", "sh", "cat", "cut", "head", "sed", "stat", "id", "uname", "chmod", "cp", "mkdir",
                "dirname", "rm", "grep", "tr", "ls", "env", "git", "sha256sum", "shasum")
FAKE_SECRET = "fake-secret-of-the-setup-test"


def installs(calls):
    """The calls of the stubs that install something (the network steps) or run the baseline."""
    return [call for call in calls if "-m venv" in call or "-m pip" in call or call.startswith("npm ci")
            or call == "playwright install chromium" or call in ("make check-fast", "make smoke")]


@pytest.fixture
def sandbox(tmp_path):
    """A checkout with the files setup.sh reads, and PATH with stubs instead of the network tools."""
    repo, stubs, tools = tmp_path / "repo", tmp_path / "stubs", tmp_path / "tools"
    files = {
        "scripts/setup.sh": (REPO_ROOT / "scripts" / "setup.sh").read_text(),
        "supervisor/requirements.txt": "fastapi==0.1\n",
        "supervisor/requirements-dev.txt": "-r requirements.txt\npytest==9.2.0\n",
        ".github/lint-requirements.txt": "ruff==0.17.3 \\\n    --hash=sha256:" + "0" * 64 + "\n",
        "webui/package.json": json.dumps({"engines": {"node": ">=22"}}, indent=2) + "\n",
        "webui/package-lock.json": "{}\n",
        "dev.env.example": "UNOS_DEV_SSH_PASSWORD=\n",
    }
    for path, content in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
    env = isolated_git_env(tmp_path / "home")
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True, timeout=30)
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text("#!/bin/sh\n" + textwrap.dedent(body).strip() + "\nexit 0\n")
        (stubs / name).chmod(0o755)
    tools.mkdir()
    for tool in SYSTEM_TOOLS:
        found = shutil.which(tool)
        if found:
            (tools / tool).symlink_to(found)
    env.update(PATH=f"{stubs}:{tools}", STUB_DIR=str(stubs), STUB_LOG=str(tmp_path / "log"))
    env.pop("PYTHON311", None)

    def run(*args, path=None, **extra):
        (tmp_path / "log").write_text("")
        result = subprocess.run([str(tools / "bash"), str(repo / "scripts" / "setup.sh"), *args], cwd=tmp_path,
                                env=dict(env, PATH=path or env["PATH"], **extra), capture_output=True, text=True,
                                timeout=120)
        return result, (tmp_path / "log").read_text().splitlines()

    return repo, run, stubs, tools


def test_setup_bootstraps_a_checkout_and_a_rerun_changes_nothing(sandbox):
    repo, run, _, _ = sandbox

    checked, calls = run("--check")
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "missing  supervisor/.venv" in checked.stdout and "missing  dev.env" in checked.stdout
    assert not (repo / "supervisor/.venv").exists() and not (repo / "dev.env").exists()
    assert installs(calls) == []

    first, calls = run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert "supervisor/.venv/bin/python -m pip install --disable-pip-version-check -q " \
           "-r supervisor/requirements-dev.txt" in calls
    assert ".venv-lint/bin/python -m pip install --disable-pip-version-check -q --require-hashes " \
           "-r .github/lint-requirements.txt" in calls
    assert "npm ci --no-audit --no-fund" in calls
    assert "playwright install chromium" in calls
    assert calls[-2:] == ["make check-fast", "make smoke"]  # the baseline comes last
    assert first.stdout.rstrip().endswith("setup.sh: baseline green. make help lists the targets.")
    hooks = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo, capture_output=True, text=True,
                           env=isolated_git_env(repo.parent / "home"), timeout=30).stdout
    assert hooks == "scripts/git-hooks\n"
    assert (repo / "dev.env").read_text() == (repo / "dev.env.example").read_text()
    assert stat.S_IMODE((repo / "dev.env").stat().st_mode) == 0o600

    again, calls = run()
    assert again.returncode == 0, again.stdout + again.stderr
    assert installs(calls) == ["make check-fast", "make smoke"]

    checked, _ = run("--check")
    assert checked.returncode == 0, checked.stdout
    assert "setup.sh --check: this checkout is set up." in checked.stdout


def test_setup_installs_again_when_a_requirement_file_changes(sandbox):
    repo, run, _, _ = sandbox
    assert run()[0].returncode == 0
    # requirements-dev.txt includes requirements.txt: a change there counts too.
    with open(repo / "supervisor/requirements.txt", "a") as handle:
        handle.write("uvicorn==0.1\n")

    checked, _ = run("--check")
    result, calls = run()

    assert "missing  supervisor/.venv is not up to date" in checked.stdout
    assert result.returncode == 0
    assert [call for call in calls if "-m pip install" in call] == [
        "supervisor/.venv/bin/python -m pip install --disable-pip-version-check -q -r supervisor/requirements-dev.txt"
    ]


@pytest.mark.parametrize("target, calls", [("check-fast", ["make check-fast"]),
                                           ("smoke", ["make check-fast", "make smoke"])])
def test_a_red_baseline_is_named(sandbox, target, calls):
    _, run, _, _ = sandbox

    result, made = run(STUB_FAILING_TARGET=target)

    assert result.returncode == 1
    assert f"setup.sh: baseline red: make {target} failed before any change" in result.stderr
    assert [call for call in made if call in ("make check-fast", "make smoke")] == calls


def test_setup_keeps_an_existing_dev_env_and_never_shows_it(sandbox):
    repo, run, _, _ = sandbox
    (repo / "dev.env").write_text(f"UNOS_DEV_SSH_PASSWORD={FAKE_SECRET}\n")
    (repo / "dev.env").chmod(0o644)

    checked, _ = run("--check")
    result, _ = run()

    assert "dev.env has mode 644, not 600" in checked.stdout
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "dev.env").read_text() == f"UNOS_DEV_SSH_PASSWORD={FAKE_SECRET}\n"
    assert stat.S_IMODE((repo / "dev.env").stat().st_mode) == 0o600
    assert FAKE_SECRET not in checked.stdout + checked.stderr + result.stdout + result.stderr


def test_setup_in_a_linked_worktree_uses_the_main_checkouts_dev_env(sandbox, tmp_path):
    # scripts/dev/env.sh prefers a worktree's own dev.env, so a copy of the example there
    # would hide the main checkout's settings (ADR-0210, ADR-0212).
    repo, _, stubs, tools = sandbox
    git_env = isolated_git_env(tmp_path / "home")
    for args in (["add", "-A"], ["commit", "-q", "-m", "chore: sandbox"],
                 ["worktree", "add", "-q", "-b", "feature", str(tmp_path / "worktree")]):
        subprocess.run(["git", *args], cwd=repo, env=git_env, check=True, capture_output=True, timeout=30)
    worktree = tmp_path / "worktree"
    (repo / "dev.env").write_text(f"UNOS_DEV_SSH_PASSWORD={FAKE_SECRET}\n")
    (repo / "dev.env").chmod(0o600)
    env = dict(git_env, PATH=f"{stubs}:{tools}", STUB_DIR=str(stubs), STUB_LOG=str(tmp_path / "log"))
    env.pop("PYTHON311", None)

    # --check before the setup misses the environments (exit 1) but not dev.env; then setup and
    # --check pass.
    for args, code in ((["--check"], 1), ([], 0), (["--check"], 0)):
        (tmp_path / "log").write_text("")
        result = subprocess.run([str(tools / "bash"), str(worktree / "scripts" / "setup.sh"), *args],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)

        assert result.returncode == code, result.stdout + result.stderr
        assert "dev.env: this linked worktree uses the main checkout's" in result.stdout, \
            result.stdout + result.stderr
        assert "missing  dev.env" not in result.stdout
        assert not (worktree / "dev.env").exists()
        assert FAKE_SECRET not in result.stdout + result.stderr
    assert (repo / "dev.env").read_text() == f"UNOS_DEV_SSH_PASSWORD={FAKE_SECRET}\n"
    assert stat.S_IMODE((repo / "dev.env").stat().st_mode) == 0o600


def test_setup_stops_before_changing_anything_when_a_prerequisite_is_missing(sandbox):
    repo, run, stubs, tools = sandbox
    without_node = stubs.parent / "without-node"
    without_node.mkdir()
    for stub in stubs.iterdir():
        if stub.name != "node":
            (without_node / stub.name).symlink_to(stub)

    result, calls = run(path=f"{without_node}:{tools}")

    assert result.returncode == 1
    assert "missing  Node.js 22 or later" in result.stdout
    assert "nothing was changed" in result.stderr
    assert not (repo / "supervisor/.venv").exists() and not (repo / "dev.env").exists()
    assert installs(calls) == []


def test_setup_usage(sandbox):
    _, run, _, _ = sandbox

    usage, _ = run("--help")
    unknown, _ = run("--fast")

    assert usage.returncode == 0 and "scripts/setup.sh --check" in usage.stdout
    assert unknown.returncode == 2


# --- CI, Dependabot and the templates ------------------------------------------------------


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _on(workflow):
    # YAML 1.1 reads the key `on` as True.
    return workflow.get("on", workflow.get(True))


def test_ci_has_the_commits_job_for_pull_requests():
    job = _workflow("test.yml")["jobs"]["commits"]
    checkout = job["steps"][0]
    run = [step for step in job["steps"] if "run" in step]

    assert job["if"] == "github.event_name == 'pull_request'"
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    assert [step["run"] for step in run] == ['python3 scripts/check-commit-msg.py --range "$BASE_SHA..$HEAD_SHA"']
    assert run[0]["env"] == {"BASE_SHA": "${{ github.event.pull_request.base.sha }}",
                             "HEAD_SHA": "${{ github.event.pull_request.head.sha }}"}


def test_unit_tests_check_out_the_history_for_the_ledger_comparison():
    workflow = _workflow("test.yml")
    job = workflow["jobs"]["test"]
    checkout = job["steps"][0]
    environments = [workflow.get("env", {}), job.get("env", {})] + [step.get("env", {}) for step in job["steps"]]

    # tests/test_feature_list.py compares the ledger with the one at the merge base with
    # origin/main, which a shallow checkout lacks.
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    # Not before main has a feature-list.json: the comparison would fail instead of skipping.
    assert [env for env in environments if "FEATURE_LIST_BASE" in env] == []


def test_ci_jobs_run_the_targets_of_make_check():
    jobs = _workflow("test.yml")["jobs"]
    runs = {name: [step["run"] for step in job["steps"] if "run" in step] for name, job in jobs.items()}

    assert runs["test"][-1] == "make test"
    assert "make lint" in runs["lint"]
    assert "make frontend" in runs["frontend"]
    assert "lint test frontend" in MAKEFILE.read_text().split("\ncheck:", 1)[1].split("\n", 2)[1]


@pytest.mark.parametrize("name", ["test.yml", "audit.yml"])
def test_workflows_read_only_with_timeouts(name):
    workflow = _workflow(name)

    assert workflow["permissions"] == {"contents": "read"}
    assert [job for job, spec in workflow["jobs"].items() if not spec.get("timeout-minutes")] == []
    checkouts = [step for spec in workflow["jobs"].values() for step in spec["steps"]
                 if step.get("uses", "").startswith("actions/checkout@")]
    assert checkouts and all(step["with"]["persist-credentials"] is False for step in checkouts)


def test_the_audit_workflow_runs_weekly_and_on_demand_the_commands_of_make_audit():
    workflow = _workflow("audit.yml")
    trigger = _on(workflow)
    runs = [step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step]
    audits = [run for run in runs if "audit" in run and not run.startswith("pip install")]
    dry_run = make("-n", "audit", "PIP_AUDIT=pip-audit", "NPM=npm").stdout.splitlines()

    assert "workflow_dispatch" in trigger
    assert re.fullmatch(r"\d+ \d+ \* \* [0-6]", trigger["schedule"][0]["cron"])
    assert audits == ["pip-audit -r supervisor/requirements.txt", "npm audit --omit=dev --audit-level=high"]
    assert dry_run == ["pip-audit -r supervisor/requirements.txt", "cd webui && npm audit --omit=dev --audit-level=high"]


def test_no_workflow_merges_anything():
    texts = [path.read_text() for path in WORKFLOWS.glob("*.yml")]

    assert not [text for text in texts if "gh pr merge" in text or "fetch-metadata" in text]
    assert all(_workflow(path.name)["permissions"] == {"contents": "read"} for path in WORKFLOWS.glob("*.yml"))


def test_dependabot_proposes_weekly_grouped_updates_with_conventional_prefixes():
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    updates = {update["package-ecosystem"]: update for update in config["updates"]}

    assert config["version"] == 2
    assert set(updates) == {"pip", "npm", "github-actions", "docker"}
    for update in updates.values():
        assert update["schedule"]["interval"] == "weekly"
        assert update["cooldown"]["default-days"] >= 7
        assert update["commit-message"]["prefix"] in CHECKER.TYPES
        assert update["groups"]
    assert updates["pip"]["directory"] == "/supervisor" and updates["npm"]["directory"] == "/webui"


def test_dependabot_leaves_major_npm_versions_and_playwright_alone():
    """Majors of the web UI's toolchain are features; Playwright moves with its image by hand (ADR-0231)."""
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    updates = {update["package-ecosystem"]: update for update in config["updates"]}
    npm_ignored = {rule["dependency-name"]: rule.get("update-types") for rule in updates["npm"]["ignore"]}
    docker_ignored = {rule["dependency-name"]: rule.get("update-types") for rule in updates["docker"]["ignore"]}

    assert npm_ignored["*"] == ["version-update:semver-major"]
    assert npm_ignored["@playwright/test"] is None  # every version
    # Dependabot names an image without its registry: mcr.microsoft.com/playwright is "playwright".
    assert docker_ignored["playwright"] is None
    assert not [name for name in docker_ignored if "/" in name]


def test_issue_templates_start_in_triage():
    labels = {}
    for template in ("bug", "enhancement"):
        text = (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / f"{template}.md").read_text()
        front_matter = yaml.safe_load(text.split("---\n")[1])
        labels[template] = front_matter["labels"]
    config_text = (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text()
    config = yaml.safe_load(config_text)

    assert labels == {"bug": ["bug", "needs-triage"], "enhancement": ["enhancement", "needs-triage"]}
    assert config["blank_issues_enabled"] is False
    for label in ("needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"):
        assert re.search(rf"^#\s+{label}\s", config_text, re.M), label


def test_the_pull_request_template_asks_for_the_gate():
    text = (REPO_ROOT / ".github" / "pull_request_template.md").read_text()

    assert "make check" in text and "make e2e-local" in text and "Feature:" in text
    # The review in a fresh context, and the one definition of a host-level change.
    assert "ADR-0017" in text and "host-level change (as `GLOSSARY.md` defines it)" in text
    assert "docs/TESTING.md" in text


# --- the project skills ---------------------------------------------------------------------

SKILLS = REPO_ROOT / ".agents" / "skills"


def test_every_skill_has_its_front_matter_and_a_relative_claude_link():
    names = sorted(path.name for path in SKILLS.iterdir() if path.is_dir())
    problems = []
    for name in names:
        text = (SKILLS / name / "SKILL.md").read_text()
        meta = yaml.safe_load(text.split("---\n")[1]) if text.startswith("---\n") else None
        if not isinstance(meta, dict) or meta.get("name") != name:
            problems.append(f"{name}: the front matter's name is not {name}")
        elif not isinstance(meta.get("description"), str) or not meta["description"].strip():
            problems.append(f"{name}: no description")
        link = REPO_ROOT / ".claude" / "skills" / name
        if not link.is_symlink() or os.readlink(link) != f"../../.agents/skills/{name}" \
                or link.resolve() != (SKILLS / name).resolve():
            problems.append(f"{name}: .claude/skills/{name} is not the relative link ../../.agents/skills/{name}")

    assert len(names) >= 6
    assert problems == []
    assert sorted(path.name for path in (REPO_ROOT / ".claude" / "skills").iterdir()) == names


def test_every_agents_md_is_imported_by_a_claude_md_beside_it():
    # Claude Code reads CLAUDE.md, nested ones when it works in their folder, and no AGENTS.md in a
    # project that has a CLAUDE.md (ADR-0202): a missing import would drop that memory silently.
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True, timeout=30).stdout.split("\n")
    memory = sorted(name for name in listed if name == "AGENTS.md" or name.endswith("/AGENTS.md"))
    imports = sorted(name for name in listed if name == "CLAUDE.md" or name.endswith("/CLAUDE.md"))

    assert memory == ["AGENTS.md", "deploy/AGENTS.md", "supervisor/AGENTS.md", "webui/AGENTS.md"]
    assert imports == [name[: -len("AGENTS.md")] + "CLAUDE.md" for name in memory]
    assert {name: (REPO_ROOT / name).read_text() for name in imports} == {name: "@AGENTS.md\n" for name in imports}


# --- secrets in the repository's text files ------------------------------------------------

# A secret that reaches the repository stays in its history (ADR-0212). The scan reads the text
# files of the working tree, tracked and untracked (never dev.env or another env file, which git
# must not list at all), and looks for the shapes in which secrets get written down. A value
# counts when it is a literal of four or more characters (a quoted one may hold spaces, as a
# passphrase does): not a variable, a substitution, a placeholder (<...>, {...}, YOUR_..., the word
# for what it stands for: password, secret, token, value), a YAML or JSON scalar such as true or 8,
# an escape sequence, dots or stars. Which names hold a secret is the guard's rule (_is_secret_name).
SECRET_VALUE = r"(?P<value>'[^'\n]*'|\"[^\"\n]*\"|[^\s'\"`,;|&)\]}>]+)"
SECRET_SHAPES = (
    ("sshpass with a password", re.compile(r"\bsshpass\b[^\n]*?(?<![\w-])-p[ \t]*" + SECRET_VALUE)),
    ("a password option with a value", re.compile(
        r"--(?:[a-z0-9]+-)*(?:password|passwd|pass|passphrase|token|secret)=" + SECRET_VALUE)),
    ("a password in a URL", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"`]*:(?P<value>[^\s/@'\"`]+)@")),
    ("curl -u user:password", re.compile(
        r"\bcurl\b[^\n]*?\s(?:-[A-Za-z]*u|--(?:proxy-)?user)(?:[ \t]+|=)?['\"]?[^\s'\"@:]*:(?P<value>[^\s'\"@]+)")),
    ("an Authorization header", re.compile(
        r"(?i)\bauthorization\b[\"']?[ \t]*[:=,][ \t]*[\"']?(?:bearer|basic|token)[ \t]+(?P<value>[\w.~+/=-]+)")),
    ("a JWT", re.compile(r"(?<![\w-])(?P<value>eyJ[\w-]{8,}\.eyJ[\w-]{2,}\.[\w-]{8,})")),
    ("a secret assignment", re.compile(r"(?<![\w.$^-])(?P<name>[A-Z_][A-Z0-9_]*)=" + SECRET_VALUE)),
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
)
# NAME: value in YAML and JSON files (compose and workflow env blocks, request bodies), the key
# quoted or not. Only there: in prose, "JWT_SECRET: leave it unset" is no mapping.
SECRET_MAPPING = ("a secret in a mapping", re.compile(
    r"(?:^|[\s{,\[])[\"']?(?P<name>[A-Za-z_][\w.-]*)[\"']?[ \t]*:[ \t]+"
    r"(?P<value>'[^'\n]*'|\"[^\"\n]*\"|[^\s'\"`,;|&)\]}>#]+)"))
MAPPING_FILES = (".yml", ".yaml", ".json")
# Words that stand for a secret instead of being one: scheme://user:password@host, sshpass -p secret.
PLACEHOLDER_WORDS = {"password", "passwd", "passphrase", "pass", "secret", "token", "value"}
NOT_LITERALS = {"true", "false", "null", "none", "yes", "no", "on", "off"}  # YAML and JSON scalars
# Literal values of those shapes that are test data, never a credential, and the files that hold
# them. A value anywhere else is a finding, and an entry that a file no longer needs fails the scan.
ALLOWED_SECRET_VALUES = {
    # Commands that the guard must block (an Authorization header: a token and admin:pw), and the
    # scan's own cases.
    "hunter2": {"supervisor/tests/test_agent_guard.py", "supervisor/tests/test_repo_tooling.py"},
    "ghp_abc123": {"supervisor/tests/test_agent_guard.py"},
    "ghp_x": {"supervisor/tests/test_agent_guard.py"},
    "eyJhbGciOiJIUzI1NiJ9.e30.x": {"supervisor/tests/test_agent_guard.py"},
    "YWRtaW46cHc=": {"supervisor/tests/test_agent_guard.py"},
    # An ignored file that deploy.sh sync must leave alone; the redaction of scripts/dev/api.py's
    # output and a token that it redacts.
    "keep": {"supervisor/tests/test_dev_scripts.py"},
    "s3cret-value": {"supervisor/tests/test_dev_scripts.py"},
    "eyJhbGciOiJIUzI1NiJ9." "eyJzdWIiOiJhZG1pbiJ9." "c2lnbmF0dXJlLXZhbHVl": {"supervisor/tests/test_dev_scripts.py"},
    # The E2E administrator of the compose test stack (docs/TESTING.md).
    "unos-e2e-pass": {"deploy/docker-compose.test.yml"},
}
# The passwords that CLAUDE.md once published, the dev device's SSH password and the UNOS
# administrator's, by their SHA-256: they stay in git history until the owner rotates them
# (ADR-0212, ADR-0024) and must never come back into a file. Every token of six or more
# characters of a scanned file (a run of letters and digits, or a word between spaces, quotes and
# delimiters) is hashed and compared. The values are kept nowhere.
PUBLISHED_PASSWORD_DIGESTS = frozenset({
    "9b91d0f8083826018d3e53df7f8151b2375b02e8d2eecf748182f53953594ca8",
    "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9",
})
PASSWORD_TOKENS = (re.compile(r"[A-Za-z0-9]{6,}"), re.compile(r"[^\s'\"`=:,;()\[\]{}<>|&]{6,}"))


def _literal(value: str):
    """The literal of a matched value when it can be a secret (see above), else None."""
    quoted = len(value) > 1 and value[0] == value[-1] and value[0] in "'\""
    value = re.sub(r"(\\+[nrt0]?)+$", "", value.strip("'\""))  # "NAME=value\n" in a string of code
    if re.search(r"\s", value):
        # A quoted passphrase counts; quoted prose ("your password", "the token") does not.
        if not quoted or re.search(r"(?i)\b(?:your|the|a|password|passwd|passphrase|secret|token|value)\b", value):
            return None
    elif not re.fullmatch(r"[^,;()'\"`]{4,}", value):
        return None
    if len(value) < 4 or value.lower().rstrip(".:!?") in PLACEHOLDER_WORDS | NOT_LITERALS or re.fullmatch(
            r"[-+]?\d+(\.\d+)?", value):
        return None
    if re.match(r"(?i)your[_-]", value) or value[0] in "$<{%*\\&!" or set(value) <= set(".*xX_-…"):
        return None
    return value


def secret_shapes(text: str, name: str = "", used: set = None) -> list:
    """(line number, shape) of every secret-shaped literal of a text; the value is never returned.
    `name` is the file's path in the repository: the mapping shape applies to YAML and JSON files,
    and a value that ALLOWED_SECRET_VALUES allows in that file is left out and added to `used`."""
    shapes = SECRET_SHAPES + ((SECRET_MAPPING,) if name.endswith(MAPPING_FILES) else ())
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for shape, pattern in shapes:
            for match in pattern.finditer(line):
                if "value" not in pattern.groupindex:
                    found.append((number, shape))  # a private key: no value to judge
                    continue
                if "name" in pattern.groupindex and not GUARD._is_secret_name(match["name"]):
                    continue
                literal = _literal(match["value"])
                if literal is None:
                    continue
                if name in ALLOWED_SECRET_VALUES.get(literal, ()):
                    if used is not None:
                        used.add((literal, name))
                    continue
                found.append((number, shape))
    return found


def published_passwords(text: str, digests=PUBLISHED_PASSWORD_DIGESTS) -> list:
    """The numbers of the lines of a text that hold a token whose SHA-256 is one of `digests`."""
    tokens = {match.group() for pattern in PASSWORD_TOKENS for match in pattern.finditer(text)}
    hits = {token for token in tokens if hashlib.sha256(token.encode()).hexdigest() in digests}
    if not hits:
        return []
    return [number for number, line in enumerate(text.splitlines(), 1)
            if hits & {match.group() for pattern in PASSWORD_TOKENS for match in pattern.finditer(line)}]


def _is_env_file(name: str) -> bool:
    """dev.env, another *.env, or .env.local and the like: files that hold secrets and stay out of git."""
    base = name.rsplit("/", 1)[-1]
    return base.endswith(".env") or base.startswith(".env.")


@functools.lru_cache(maxsize=1)
def _listed_files() -> tuple:
    """The working tree's files as git lists them, tracked and untracked but not ignored: what
    git add -A would commit."""
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO_ROOT,
                            capture_output=True, timeout=60)
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    return tuple(sorted(set(listed.stdout.decode("utf-8", "replace").split("\0")) - {""}))


def _repository_text_files():
    """(path, text) of the text files of the working tree: tracked and untracked, not ignored."""
    for name in _listed_files():
        if _is_env_file(name):
            continue  # dev.env and every other env file are never read
        path = REPO_ROOT / name
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" not in data[:8192]:
            yield name, data.decode("utf-8", "replace")


def test_git_lists_no_env_file():
    # git's own list, before any file is read: a force-added dev.env would put the device's
    # passwords into the history, and an env file is never scanned, as it is never read.
    names = _listed_files()

    assert "Makefile" in names and "dev.env.example" in names
    assert [name for name in names if _is_env_file(name)] == [], "env files stay out of git (ADR-0212)"


def test_no_secret_shapes_in_the_repository():
    files = dict(_repository_text_files())
    used = set()
    found = [f"{name}:{line}: {shape}" for name, text in files.items() for line, shape in secret_shapes(text, name, used)]
    stale = sorted({(value, name) for value, names in ALLOWED_SECRET_VALUES.items() for name in names} - used)

    assert len(files) > 100 and "Makefile" in files
    assert found == [], "secrets belong in dev.env (ADR-0212); test values in ALLOWED_SECRET_VALUES"
    assert stale == [], "ALLOWED_SECRET_VALUES allows values that these files no longer hold: remove them"


def test_no_published_password_in_the_repository():
    found = [f"{name}:{line}" for name, text in _repository_text_files() for line in published_passwords(text)]

    assert found == [], "a password that CLAUDE.md once published (ADR-0212): remove it without showing it"


def test_published_passwords_are_found_by_their_digest():
    digests = {hashlib.sha256(word.encode()).hexdigest() for word in ("Kq7-w9Published", "Zr4nqx")}
    text = ("no secret here\n"
            "ssh" + "pass -p 'Kq7-w9Published' ssh root@host\n"
            '{"password": "Zr4nqx"}\n'
            "Zr4nqxy and xZr4nqx are other words\n"
            "https://admin:Zr4nqx" + "@host/ and host-Zr4nqx\n")

    assert published_passwords(text, digests) == [2, 3, 5]
    assert len(PUBLISHED_PASSWORD_DIGESTS) == 2
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in PUBLISHED_PASSWORD_DIGESTS)


@pytest.mark.parametrize("line, shape", [
    # The secret-shaped lines are put together here, so that this file holds none of them.
    ("ssh" + "pass -p wq7-Kp2 ssh root@10.1.1.103", "sshpass with a password"),
    ("ssh" + "pass -e -pwq7-Kp2 ssh root@10.1.1.103", "sshpass with a password"),
    ("mysql --pass" + "word=wq7-Kp2", "a password option with a value"),
    ("gh auth login --with-tok" + "en=wq7-Kp2", "a password option with a value"),
    ("git clone https://user:wq7-Kp2" + "@example.org/x.git", "a password in a URL"),
    ("git clone https://:wq7-Kp2" + "@example.org/x.git", "a password in a URL"),
    ("cu" + "rl -sku admin:wq7-Kp2 https://localhost/api/v1/auth/me", "curl -u user:password"),
    ("cu" + "rl --user=admin:wq7-Kp2 https://localhost/", "curl -u user:password"),
    ("curl -H 'Authori" + "zation: Bearer wq7Kp2tokenvalue' https://localhost/", "an Authorization header"),
    ('{"Authori' + 'zation": "Basic YWRtaW46d3E3LUtwMg=="}', "an Authorization header"),
    ("token = 'eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiJ1In0.wq7Kp2xyzSIG'", "a JWT"),
    ("UNOS_DEV_ADMIN_PASS" + "WORD='wq7-Kp2'", "a secret assignment"),
    ("export TRANSMISSION_PA" + "SS=wq7-Kp2", "a secret assignment"),
    ("OPENAI_API_" + "KEY=wq7-Kp2 TS_AUTHKEY=", "a secret assignment"),
    ("TS_AUTH" + "KEY=tskey-auth-wq7Kp2", "a secret assignment"),
    ("UNOS_DEV_SSH_PASS" + "WORD='wq7 Kp2 horse battery'", "a secret assignment"),
    ("-----BEGIN OPENSSH PRIV" + "ATE KEY-----", "a private key"),
    ("-----BEGIN PGP PRIV" + "ATE KEY BLOCK-----", "a private key"),
])
def test_the_secret_scan_finds_each_shape(line, shape):
    assert secret_shapes(f"text\n{line}\n") == [(2, shape)]


@pytest.mark.parametrize("line, name", [
    ("    UNOS_DEV_ADMIN_PASS" + "WORD: wq7-Kp2", "deploy/docker-compose.yml"),
    ("      - TRANSMISSION_PA" + "SS: 'wq7-Kp2'", ".github/workflows/x.yml"),
    ('{"user": "admin", "pass' + 'word": "wq7-Kp2"}', "webui/tests/fixtures/x.json"),
    ('  "api' + 'Key": "wq7-Kp2"', "x.json"),
])
def test_the_secret_scan_finds_mappings_in_yaml_and_json_only(line, name):
    assert secret_shapes(f"text\n{line}\n", name) == [(2, "a secret in a mapping")]
    assert secret_shapes(f"text\n{line}\n", "docs/x.md") == []


@pytest.mark.parametrize("line", [
    'sshpass -p "$UNOS_DEV_SSH_PASSWORD" ssh root@10.1.1.103',
    "secrets on command lines (`sshpass -p`, `curl -u user:password`, `--password=...`)",
    "docker login --password-stdin < token.txt",
    "UNOS_DEV_SSH_PASSWORD=",
    "UNOS_DEV_ADMIN_PASSWORD=$(cat /dev/shm/x) ADMIN_TOKEN=${TOKEN:-}",
    "SSH_ASKPASS=scripts/dev/askpass.sh TOKEN_FILE=/tmp/token PASSWORD_MIN_LENGTH=12",
    "TS_AUTHKEY= API_KEY_FILE=/run/secrets/key AWS_ACCESS_KEY_ID=AKIAEXAMPLE",
    "git clone https://user:${GITHUB_TOKEN}@github.com/x/y.git",
    "--password=<the password> --token=... scheme://user:password@host",
    "API_PASS=abc TOKEN=\"the token you made\" SECRET='<your secret>'",
    'curl -u admin https://localhost/ && curl -u "admin:${UNOS_DEV_ADMIN_PASSWORD}" https://h',
    'curl -H "Authorization: Bearer $TOKEN" -H "Authorization: Bearer YOUR_API_TOKEN"',
    "-----BEGIN CERTIFICATE-----",
    "-----BEGIN PUBLIC KEY-----",
])
def test_the_secret_scan_leaves_variables_and_placeholders_alone(line):
    assert secret_shapes(line) == []


@pytest.mark.parametrize("line", [
    "  UNOS_JWT_SECRET: ${UNOS_JWT_SECRET:-}",
    '"secret": true, "PASS": {"required": true}, "password_min_length": 8',
    "      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    "  password: ''",
    "  TOKEN_FILE: /run/secrets/token",
    "  password: !vault |",
])
def test_the_mapping_shape_leaves_variables_and_scalars_alone(line):
    assert secret_shapes(line, "deploy/x.yml") == []


def test_a_test_value_passes_only_in_the_files_that_hold_it():
    line = "TRANSMISSION_PASS=hunter2"
    used = set()

    assert secret_shapes(line, "supervisor/tests/test_repo_tooling.py", used) == []
    assert used == {("hunter2", "supervisor/tests/test_repo_tooling.py")}
    assert secret_shapes(line, "docs/USER_GUIDE.md") == [(1, "a secret assignment")]


# --- .gitignore ------------------------------------------------------------------------------


def test_gitignore_covers_the_local_environments_and_a_linked_node_modules(tmp_path):
    lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    repo = tmp_path / "repo"
    (repo / "webui").mkdir(parents=True)
    shutil.copy(REPO_ROOT / ".gitignore", repo / ".gitignore")
    env = isolated_git_env(tmp_path / "home")
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True, timeout=30)
    (tmp_path / "modules").mkdir()
    (repo / "webui" / "node_modules").symlink_to(tmp_path / "modules")
    for path in ("supervisor/.venv/bin/python", ".venv-lint/bin/ruff", ".standin/data/unos.db", "dev.env",
                 "tmp/guard/denials.log", "tmp/stop-check/green", "tmp/dev/backup/x/unos-data.tar",
                 ".claude/settings.local.json"):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("x\n")

    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, env=env,
                            capture_output=True, text=True, check=True, timeout=30).stdout

    assert {".standin/", "supervisor/.venv/", ".venv-lint/", "node_modules"} <= set(lines)
    assert status == "?? .gitignore\n"

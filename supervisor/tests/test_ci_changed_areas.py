"""scripts/ci/changed_areas.py, the job Changed paths of .github/workflows/test.yml (ADR-0467): which of
the CI workflow's jobs a pull request or a push to main needs.

Tested here, offline: the classification of changed files into documentation and the ledger, the web UI
and code; the files that a pull request and a push change, read from real git repositories (a rename
lists both paths, and a push without a usable `before` lists nothing, which runs every job); the record
of the commits that a pull request's run tested; and the rule that a push to main which merges a pull
request whose successful run tested the same tree runs none of Unit Tests, Lint and Frontend, against a
fake of GitHub's API. The structure of the workflow is tested in test_repo_tooling.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "scripts" / "ci" / "changed_areas.py"


def _load():
    spec = importlib.util.spec_from_file_location("changed_areas", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


areas = _load()
Areas = areas.Areas
EVERYTHING = Areas(code=True, lint=True, webui=True, images=True, docs_only=False)
DOCS = Areas(code=False, lint=False, webui=False, images=False, docs_only=True)
ZERO = "0" * 40
REPOSITORY = "owner/unos"


def _areas(*names):
    """Areas with the named ones true and docs_only false."""
    return Areas(**{name: name in names for name in Areas._fields})


# --- The classification -------------------------------------------------------------------------


DOCUMENTS = ["README.md", "PROGRESS.md", "CHANGELOG.md", "docs/TESTING.md", "docs/adr/0001-record-decisions.md",
             "docs/research/diagram.png", "feature-list.json", "LICENSE", ".agents/skills/adr/SKILL.md",
             "supervisor/AGENTS.md", ".github/pull_request_template.md"]


def test_documents_and_the_ledger_alone_are_docs_only():
    assert areas.classify(DOCUMENTS) == DOCS
    for path in DOCUMENTS:
        assert areas.classify([path]) == DOCS, path


def test_a_web_ui_file_runs_the_unit_tests_the_frontend_and_the_build_not_lint():
    # make lint and the audit read nothing below webui/; the backend tests read its package files and
    # its Dockerfile, and the web UI's image copies the folder.
    assert areas.classify(["webui/src/routes/+page.svelte"]) == _areas("code", "webui", "images")
    assert areas.classify(["webui/package-lock.json", "README.md"]) == _areas("code", "webui", "images")


def test_a_document_of_the_web_ui_runs_the_frontend_the_build_and_the_docs_job():
    # Everything below webui/ is the frontend's: make frontend and the image's build read the folder.
    assert areas.classify(["webui/AGENTS.md"]) == Areas(code=False, lint=False, webui=True, images=True,
                                                         docs_only=True)


@pytest.mark.parametrize("path", [".github/workflows/test.yml", "scripts/ci/changed_areas.py"])
def test_what_decides_the_jobs_runs_every_job(path):
    assert areas.classify([path]) == EVERYTHING
    assert areas.classify(["README.md", path]) == EVERYTHING


def test_the_makefile_runs_the_unit_tests_lint_and_the_frontend():
    # It holds the recipes of make test, make lint and make frontend.
    assert areas.classify(["Makefile"]) == _areas("code", "lint", "webui")


@pytest.mark.parametrize("path", ["deploy/docker-compose.yml", "supervisor/Dockerfile", "supervisor/requirements.txt",
                                  "supervisor/.dockerignore"])
def test_what_the_images_build_from_runs_the_build_check(path):
    assert areas.classify([path]) == _areas("code", "lint", "images")


def test_a_python_or_shell_file_runs_the_unit_tests_and_lint_only():
    assert areas.classify(["supervisor/src/main.py"]) == _areas("code", "lint")
    assert areas.classify(["scripts/dev/api.py", "deploy/install.sh", ".claude/settings.json"]) == _areas("code", "lint")


def test_a_mix_of_documents_and_code_is_code():
    assert areas.classify(["README.md", "docs/TESTING.md", "supervisor/src/main.py"]) == _areas("code", "lint")
    assert areas.classify(["feature-list.json", "webui/src/app.css"]) == _areas("code", "webui", "images")


@pytest.mark.parametrize("paths", [[], None])
def test_no_list_or_an_empty_one_runs_every_job(paths):
    # None: the files could not be listed. An empty list tells nothing either: any doubt runs everything.
    assert areas.classify(paths) == EVERYTHING


@pytest.mark.parametrize("path", ["docs.py", "docsx/a.md.py", "supervisor/docs/a.txt", "LICENSE.txt",
                                  "feature-list.json.orig", "README.markdown", "notes.MD", "webuix/a.ts",
                                  "supervisor/src/apps/catalog.py", ".github/workflows/btrfs.yml",
                                  "supervisor/Dockerfile.test", "deploy/docker-compose.test.yml"])
def test_near_misses_are_code(path):
    assert areas.classify([path]) == _areas("code", "lint"), path


# --- The changed files, from real git repositories --------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HOME=str(repo.parent / "home"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.org", GIT_COMMITTER_NAME="Test",
               GIT_COMMITTER_EMAIL="test@example.org")
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60, env=env,
                            check=True)
    return result.stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str | None]) -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            _git(repo, "rm", "-q", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """main: a first commit with code and a document."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _commit(path, "first", {"supervisor/src/app.py": "print(1)\n", "README.md": "UNOS\n"})
    return path


def test_a_push_lists_the_files_since_before(repo):
    before = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "docs", {"README.md": "UNOS, a NAS\n", "docs/TESTING.md": "tests\n"})
    sha = _commit(repo, "ledger", {"feature-list.json": "{}\n"})

    files = areas.changed_files("push", {"before": before, "after": sha}, sha, cwd=repo)

    assert sorted(files) == ["README.md", "docs/TESTING.md", "feature-list.json"]
    assert areas.classify(files) == DOCS


def test_a_rename_lists_both_paths(repo):
    # git diff names only the new path of a rename by default: code moved into docs/ would look like a
    # change of documentation alone.
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    _git(repo, "mv", "supervisor/src/app.py", "docs/app.md")
    _git(repo, "commit", "-q", "-m", "move")
    sha = _git(repo, "rev-parse", "HEAD")

    files = areas.changed_files("push", {"before": before}, sha, cwd=repo)

    assert sorted(files) == ["docs/app.md", "supervisor/src/app.py"]
    assert areas.classify(files) == _areas("code", "lint")


def test_odd_file_names_are_listed_as_they_are(repo):
    before = _git(repo, "rev-parse", "HEAD")
    sha = _commit(repo, "odd", {"docs/a b.md": "x\n", "docs/über.md": "x\n", "docs/new\nline.md": "x\n"})

    assert sorted(areas.changed_files("push", {"before": before}, sha, cwd=repo)) == [
        "docs/a b.md", "docs/new\nline.md", "docs/über.md"]


@pytest.mark.parametrize("event", [
    {},  # no before
    {"before": None},
    {"before": ""},
    {"before": ZERO},  # a new branch
    {"before": "f" * 40},  # not in the repository
    {"before": "HEAD~1"},  # not a commit id
    {"before": "@first", "forced": True},  # a force push
    {"before": "@sibling"},  # not an ancestor: history rewritten
])
def test_a_push_without_a_usable_before_lists_nothing(repo, event):
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "other")
    sibling = _commit(repo, "sibling", {"docs/x.md": "x\n"})
    _git(repo, "checkout", "-q", "main")
    sha = _commit(repo, "docs", {"README.md": "UNOS, a NAS\n"})
    event = {key: {"@first": first, "@sibling": sibling}.get(value, value) for key, value in event.items()}

    assert areas.changed_files("push", event, sha, cwd=repo) is None


def test_a_pull_request_lists_its_own_changes_since_the_merge_base(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, "feature", {"docs/TESTING.md": "tests\n"})
    _git(repo, "checkout", "-q", "main")
    moved = _commit(repo, "main moves on", {"supervisor/src/other.py": "x = 1\n"})
    event = {"pull_request": {"base": {"sha": moved}, "head": {"sha": head}}}

    # The base moved on after the branch started: its own change is not the pull request's.
    assert areas.changed_files("pull_request", event, "unused", cwd=repo) == ["docs/TESTING.md"]
    event["pull_request"]["base"]["sha"] = base
    assert areas.changed_files("pull_request", event, "unused", cwd=repo) == ["docs/TESTING.md"]


@pytest.mark.parametrize("event", [
    {"pull_request": {"base": {"sha": "f" * 40}, "head": {"sha": "@head"}}},  # not in the checkout
    {"pull_request": {"base": {"sha": "@head"}, "head": {"sha": "-x"}}},  # not a commit id
    {"pull_request": {"base": {}, "head": {"sha": "@head"}}},
    {},
])
def test_a_pull_request_whose_diff_fails_lists_nothing(repo, event):
    head = _git(repo, "rev-parse", "HEAD")
    text = json.dumps(event).replace("@head", head)

    assert areas.changed_files("pull_request", json.loads(text), head, cwd=repo) is None


@pytest.mark.parametrize("name", ["workflow_dispatch", "schedule", "merge_group"])
def test_other_events_list_nothing(repo, name):
    head = _git(repo, "rev-parse", "HEAD")

    assert areas.changed_files(name, {"before": head}, head, cwd=repo) is None


# --- The record of what a pull request's run tested -------------------------------------------------


def _merge(repo: Path, branch: str, message: str = "merge") -> str:
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "-m", message, branch)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def merged(repo):
    """main at base, a branch at head that changes a document, and HEAD at their merge, as GitHub's test
    merge of a pull request is checked out and as the merge button makes it."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, "feature", {"supervisor/src/app.py": "print(2)\n"})
    _git(repo, "checkout", "-q", "main")
    merge = _merge(repo, "feature")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    return repo, base, head, merge, tree


def test_a_pull_request_run_records_the_base_head_and_tree_it_tested(merged):
    repo, base, head, _, tree = merged

    assert areas.tested_commits(cwd=repo) == (base, head, tree)
    assert areas.record_line(base, head, tree) == f"tested base={base} head={head} tree={tree}"


def test_no_record_off_a_merge_commit(repo):
    assert areas.tested_commits(cwd=repo) is None


def test_records_are_read_from_a_job_log(merged):
    _, base, head, _, tree = merged
    record = areas.record_line(base, head, tree)
    log = (f"\ufeff2026-11-23T07:00:36.1Z ##[group]Run python3 scripts/ci/changed_areas.py areas\n"
           f"2026-11-23T07:00:37.2Z {record}\r\n"
           f"2026-11-23T07:00:37.3Z   changed: {areas.record_line('a' * 40, head, tree)}\n"
           f"2026-11-23T07:00:37.4Z tested base={base[:12]} head={head} tree={tree}\n"
           f"2026-11-23T07:00:37.5Z {record} and more\n")

    # Only a line that is the record alone, after the log's time stamp; ids in full.
    assert areas.read_records(log) == {(base, head, tree)}
    assert areas.read_records(f"{record}\n") == {(base, head, tree)}
    assert areas.read_records("") == set()


# --- The runs before a push, against a fake of GitHub's API ---------------------------------------


def _run(run_id, event, head, **fields):
    return {"id": run_id, "event": event, "status": "completed", "conclusion": "success", "head_sha": head,
            "head_branch": "main" if event == "push" else "feature", "path": ".github/workflows/test.yml",
            "head_repository": {"full_name": REPOSITORY}, "pull_requests": [],
            "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}", **fields}


class FakeAPI:
    """GitHub's answers: the runs of test.yml by event and head commit (`runs`), the jobs of each run
    (`jobs`, by run id; by default its Changed paths has the id run id + 1000) and the log of each job
    (`logs`, by job id). At first: a passed push run 10 of base, and a passed pull request run 42 whose
    Changed paths recorded base, head and tree. Each call is logged."""

    def __init__(self, base, head, tree):
        self.base, self.head, self.tree = base, head, tree
        self.runs = {("push", base): [_run(10, "push", base)], ("pull_request", head): [_run(42, "pull_request", head)]}
        self.jobs = {}
        self.logs = {1042: self.log(base, head, tree)}
        self.calls = []
        self.error = None

    @staticmethod
    def log(*records):
        lines = ["2026-11-23T07:00:36.1Z ##[group]Run python3 scripts/ci/changed_areas.py areas"]
        lines += [f"2026-11-23T07:00:37.2Z {areas.record_line(*records[i:i + 3])}" for i in range(0, len(records), 3)]
        return "\n".join(lines) + "\n"

    def get(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        runs = re.fullmatch(rf"/repos/{REPOSITORY}/actions/workflows/test\.yml/runs\?event=(\w+)&head_sha=(\w+)"
                            r"&per_page=100", path)
        if runs:
            found = self.runs.get((runs[1], runs[2]), [])
            return json.dumps({"total_count": len(found), "workflow_runs": found}).encode()
        jobs = re.fullmatch(rf"/repos/{REPOSITORY}/actions/runs/(\d+)/jobs\?per_page=100", path)
        if jobs:
            run_id = int(jobs[1])
            found = self.jobs.get(run_id, [{"id": run_id + 999, "name": "Unit Tests", "conclusion": "success"},
                                           {"id": run_id + 1000, "name": "Changed paths", "conclusion": "success"}])
            return json.dumps({"total_count": len(found), "jobs": found}).encode()
        logs = re.fullmatch(rf"/repos/{REPOSITORY}/actions/jobs/(\d+)/logs", path)
        if logs and int(logs[1]) in self.logs:
            return self.logs[int(logs[1])].encode()
        raise AssertionError(path)


def _runs_path(event, head):
    return f"/repos/{REPOSITORY}/actions/workflows/test.yml/runs?event={event}&head_sha={head}&per_page=100"


def test_the_push_run_of_before_passed(merged):
    _, base, head, _, tree = merged
    api = FakeAPI(base, head, tree)

    passed, why = areas.before_passed(base, "main", REPOSITORY, api.get)

    assert passed is True, why
    assert "run 10" in why
    assert api.calls == [_runs_path("push", base)]


@pytest.mark.parametrize("runs", [
    [],  # no run: [skip ci], or the workflow was off
    [_run(10, "push", "@base", conclusion="failure")],
    [_run(10, "push", "@base", status="in_progress", conclusion=None)],
    [_run(10, "push", "@base", conclusion="cancelled")],
    [_run(10, "push", "@base"), _run(11, "push", "@base", conclusion="failure")],  # the newest decides
    [_run(10, "push", "@base", head_branch="other")],
    [_run(10, "pull_request", "@base")],
    [_run(10, "push", "e" * 40)],
    [_run(10, "push", "@base", path=".github/workflows/btrfs.yml")],
    [_run(10, "push", "@base", head_repository={"full_name": "fork/unos"})],
    [_run("10", "push", "@base")],
])
def test_a_push_run_of_before_that_did_not_pass_is_doubt(merged, runs):
    _, base, head, _, tree = merged
    api = FakeAPI(base, head, tree)
    api.runs[("push", base)] = json.loads(json.dumps(runs).replace("@base", base))

    passed, why = areas.before_passed(base, "main", REPOSITORY, api.get)

    assert passed is False, why


def test_an_older_failed_run_of_before_does_not_count_against_a_newer_pass(merged):
    _, base, head, _, tree = merged
    api = FakeAPI(base, head, tree)
    api.runs[("push", base)] = [_run(11, "push", base), _run(10, "push", base, conclusion="failure")]

    assert areas.before_passed(base, "main", REPOSITORY, api.get)[0] is True


@pytest.mark.parametrize("before", ["", ZERO, "HEAD", None])
def test_no_before_asks_github_nothing(merged, before):
    _, base, head, _, tree = merged
    api = FakeAPI(base, head, tree)

    assert areas.before_passed(before, "main", REPOSITORY, api.get)[0] is False
    assert api.calls == []


# --- The merge of a tested pull request, against a fake of GitHub's API -----------------------------


def test_a_merge_that_a_pull_request_run_tested_runs_no_check_again(merged):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)

    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)

    assert tested is True, why
    assert "run 42" in why and tree in why
    # The pull request runs of this workflow whose head is the merge's second parent, the jobs of the
    # run, the log of its Changed paths.
    assert api.calls == [_runs_path("pull_request", head), f"/repos/{REPOSITORY}/actions/runs/42/jobs?per_page=100",
                         f"/repos/{REPOSITORY}/actions/jobs/1042/logs"]


@pytest.mark.parametrize("change", [
    {"conclusion": "failure"},
    {"conclusion": None, "status": "in_progress"},
    {"conclusion": "cancelled"},
    {"event": "push"},
    {"head_sha": "e" * 40},
    {"path": ".github/workflows/btrfs.yml"},
    {"head_repository": {"full_name": "fork/unos"}},
    {"head_repository": None},
    {"id": "42/../43"},
])
def test_a_run_that_did_not_pass_or_is_not_the_pull_requests_is_doubt(merged, change):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    api.runs[("pull_request", head)][0].update(change)

    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why
    assert not [call for call in api.calls if "../" in call]


@pytest.mark.parametrize("jobs", [
    [{"id": 1042, "name": "Changed paths", "conclusion": "failure"}],
    [{"id": 1042, "name": "Changed paths", "conclusion": "skipped"}],
    [{"id": 1042, "name": "Unit Tests", "conclusion": "success"}],
    [{"id": "1042?x", "name": "Changed paths", "conclusion": "success"}],
    [{"id": 1042, "name": "Changed paths", "conclusion": "success"},
     {"id": 1043, "name": "Changed paths", "conclusion": "success"}],
    [],
])
def test_a_run_without_one_passed_changed_paths_job_is_doubt(merged, jobs):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    api.jobs[42] = jobs

    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why
    assert not [call for call in api.calls if "?x" in call]


@pytest.mark.parametrize("records", [
    (),
    ("@other", "@head", "@tree"),
    ("@base", "@other", "@tree"),
    ("@base", "@head", "@other"),
    ("@base", "@head", "@tree", "@other", "@head", "@tree"),  # two records
])
def test_a_log_without_exactly_the_record_of_this_merge_is_doubt(merged, records):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    values = {"@base": base, "@head": head, "@tree": tree, "@other": "c" * 40}
    api.logs[1042] = FakeAPI.log(*[values[name] for name in records])

    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why


def test_the_newest_run_that_recorded_this_merge_decides(merged):
    # A run of the same head on another base does not count; the newest run with this merge's record
    # does, whatever an older one says: a failure after a pass (a flaky test) is doubt.
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    other = "c" * 40
    api.runs[("pull_request", head)] = [_run(41, "pull_request", head, conclusion="failure"),
                                        _run(43, "pull_request", head, conclusion="failure"),
                                        _run(42, "pull_request", head)]
    api.logs.update({1041: FakeAPI.log(base, head, tree), 1043: FakeAPI.log(other, head, tree)})

    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)

    assert tested is True and "run 42" in why, why
    assert [call for call in api.calls if call.endswith("/logs")] == [
        f"/repos/{REPOSITORY}/actions/jobs/1043/logs", f"/repos/{REPOSITORY}/actions/jobs/1042/logs"]

    api.runs[("pull_request", head)][1] = _run(44, "pull_request", head, conclusion="failure")
    api.logs[1044] = FakeAPI.log(base, head, tree)
    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)
    assert tested is False and "run 44" in why, why


def test_a_merge_whose_tree_is_not_the_tested_one_is_doubt(merged):
    # The run tested GitHub's merge of the same two commits; this merge commit holds more (a local merge
    # with a change of its own, say).
    repo, base, head, _, tree = merged
    (repo / "supervisor/src/app.py").write_text("print(3)\n")
    _git(repo, "commit", "-q", "--amend", "-a", "--no-edit")
    merge = _git(repo, "rev-parse", "HEAD")
    api = FakeAPI(base, head, tree)

    tested, why = areas.tested_merge(merge, base, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why
    assert len(api.calls) == 3


def test_a_merge_onto_another_base_than_the_tested_one_is_doubt(merged):
    # main moved on after the run: the run's record names the old base.
    repo, base, head, _, _ = merged
    _git(repo, "reset", "-q", "--hard", base)
    moved = _commit(repo, "main moves on", {"docs/x.md": "x\n"})
    merge = _merge(repo, "feature")
    api = FakeAPI(base, head, _git(repo, "rev-parse", "HEAD^{tree}"))

    tested, why = areas.tested_merge(merge, moved, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why


@pytest.mark.parametrize("case", ["not a merge", "before is not the first parent", "unknown commit"])
def test_a_push_that_is_not_one_merge_asks_github_nothing(merged, case):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    sha, before = {"not a merge": (head, base), "before is not the first parent": (merge, head),
                   "unknown commit": ("f" * 40, base)}[case]

    tested, why = areas.tested_merge(sha, before, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why
    assert api.calls == []


def test_a_merge_with_more_than_two_parents_asks_github_nothing(merged):
    repo, base, head, _, tree = merged
    _git(repo, "reset", "-q", "--hard", base)
    _git(repo, "checkout", "-q", "-b", "third")
    _commit(repo, "third", {"docs/y.md": "y\n"})
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "feature", "third")
    api = FakeAPI(base, head, tree)

    tested, why = areas.tested_merge(_git(repo, "rev-parse", "HEAD"), base, REPOSITORY, api.get, cwd=repo)

    assert tested is False, why
    assert api.calls == []


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self.body if self.body is not None else b"x" * size


def test_the_token_goes_to_githubs_api_only_never_along_a_redirect(monkeypatch):
    # The job log is a redirect to a storage host: the request that urllib makes for the redirect must
    # carry no Authorization header.
    seen = []

    def urlopen(request, timeout):
        seen.append((request, timeout))
        return _Response(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    get = areas.api_get("the-token", "https://api.github.com")

    assert get("/repos/owner/unos/actions/jobs/7/logs") == b"{}"
    (request, timeout), = seen
    assert request.full_url == "https://api.github.com/repos/owner/unos/actions/jobs/7/logs"
    assert request.get_header("Authorization") == "Bearer the-token" and 0 < timeout <= 60
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://storage.example/log?sig=1")
    assert redirected.full_url == "https://storage.example/log?sig=1"
    assert redirected.get_header("Authorization") is None
    assert "the-token" not in json.dumps([dict(redirected.header_items())])
    for url in ("http://api.github.com", "file:///etc", ""):
        with pytest.raises(ValueError):
            areas.api_get("the-token", url)


def test_an_answer_too_large_is_refused(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: _Response(None))

    with pytest.raises(ValueError):
        areas.api_get("the-token", "https://api.github.com")("/x")


# --- The two steps of the job, as the workflow runs them ----------------------------------------------


def _environment(tmp_path, event_name, event, sha, **extra):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    output = tmp_path / "output"
    output.write_text("")
    env = {"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event_path), "GITHUB_SHA": sha,
           "GITHUB_OUTPUT": str(output), "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_API_URL": "https://api.github.com",
           "GITHUB_REF_NAME": "main", **extra}
    return env, output


def _outputs(output: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def _expected(found):
    return {name: str(value).lower() for name, value in found._asdict().items()}


def _main(monkeypatch, repo, env, *args):
    for key in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_OUTPUT", "GITHUB_REPOSITORY",
                "GITHUB_API_URL", "GITHUB_REF_NAME", "GH_TOKEN", "BEFORE_PASSED"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(repo)
    return areas.main(list(args))


def test_the_areas_of_a_docs_push_after_a_passed_run(repo, tmp_path, monkeypatch, capsys):
    before = _git(repo, "rev-parse", "HEAD")
    sha = _commit(repo, "docs", {"README.md": "UNOS, a NAS\n"})
    env, output = _environment(tmp_path, "push", {"before": before}, sha, BEFORE_PASSED="true")

    assert _main(monkeypatch, repo, env, "areas") == 0

    assert _outputs(output) == _expected(DOCS)
    out = capsys.readouterr().out
    assert '  changed: "README.md"' in out and "tested base=" not in out
    # The names are listed while the runner takes no commands.
    stop = re.search(r"^::stop-commands::([0-9a-f]{32})$", out, re.M)
    assert stop and out.index(stop[0]) < out.index("changed:") < out.index(f"::{stop[1]}::")


@pytest.mark.parametrize("passed", ["false", "", None])
def test_a_push_after_a_run_that_did_not_pass_runs_every_job(repo, tmp_path, monkeypatch, capsys, passed):
    # main is red, or its last run was not found: a change of documents alone must not turn it green.
    before = _git(repo, "rev-parse", "HEAD")
    sha = _commit(repo, "docs", {"README.md": "UNOS, a NAS\n"})
    env, output = _environment(tmp_path, "push", {"before": before}, sha,
                               **({} if passed is None else {"BEFORE_PASSED": passed}))

    assert _main(monkeypatch, repo, env, "areas") == 0

    assert _outputs(output) == _expected(EVERYTHING)
    assert "did not pass" in capsys.readouterr().out


def test_the_areas_of_a_push_without_before_are_everything(repo, tmp_path, monkeypatch):
    sha = _git(repo, "rev-parse", "HEAD")
    env, output = _environment(tmp_path, "push", {"before": ZERO}, sha, BEFORE_PASSED="true")

    assert _main(monkeypatch, repo, env, "areas") == 0
    assert _outputs(output) == _expected(EVERYTHING)


def test_a_pull_request_run_prints_its_record(merged, tmp_path, monkeypatch, capsys):
    repo, base, head, merge, tree = merged
    event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    env, output = _environment(tmp_path, "pull_request", event, merge)

    assert _main(monkeypatch, repo, env, "areas") == 0

    out = capsys.readouterr().out
    assert areas.read_records(out) == {(base, head, tree)}
    # A pull request does not depend on main's last run.
    assert _outputs(output) == _expected(_areas("code", "lint"))

    # Not when the checkout's second parent is not the pull request's head.
    event["pull_request"]["head"]["sha"] = base
    env, output = _environment(tmp_path, "pull_request", event, merge)
    assert _main(monkeypatch, repo, env, "areas") == 0
    assert areas.read_records(capsys.readouterr().out) == set()


def test_the_runs_step_writes_what_it_found(merged, tmp_path, monkeypatch, capsys):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    tokens = []
    monkeypatch.setattr(areas, "api_get", lambda token, url: tokens.append((token, url)) or api.get)
    env, output = _environment(tmp_path, "push", {"before": base}, merge, GH_TOKEN="the-token")

    assert _main(monkeypatch, repo, env, "runs") == 0

    assert _outputs(output) == {"passed": "true", "tested": "true"}
    assert tokens == [("the-token", "https://api.github.com")]
    out = capsys.readouterr().out
    assert "the-token" not in out and "run 10" in out and "run 42" in out


def test_a_tested_merge_onto_a_red_main_is_not_taken(merged, tmp_path, monkeypatch):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    api.runs[("push", base)][0]["conclusion"] = "failure"
    monkeypatch.setattr(areas, "api_get", lambda token, url: api.get)
    env, output = _environment(tmp_path, "push", {"before": base}, merge, GH_TOKEN="t")

    assert _main(monkeypatch, repo, env, "runs") == 0

    assert _outputs(output) == {"passed": "false", "tested": "false"}
    assert api.calls == [_runs_path("push", base)]


@pytest.mark.parametrize("case", ["api error", "bad answer", "error after the first answer", "no token",
                                  "pull request"])
def test_the_runs_step_answers_false_on_any_doubt(merged, tmp_path, monkeypatch, case):
    repo, base, head, merge, tree = merged
    api = FakeAPI(base, head, tree)
    if case == "api error":
        api.error = OSError("HTTP Error 403: Resource not accessible by integration")
    if case == "bad answer":
        api.get = lambda path: b"<html>"
    get = api.get
    if case == "error after the first answer":
        # main's run passed; the answer about the pull request's jobs is not a JSON object.
        get = lambda path: b"[]" if "/jobs" in path else api.get(path)  # noqa: E731
    monkeypatch.setattr(areas, "api_get", lambda token, url: get)
    name, event = ("pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}) \
        if case == "pull request" else ("push", {"before": base})
    env, output = _environment(tmp_path, name, event, merge, **({} if case == "no token" else {"GH_TOKEN": "t"}))

    assert _main(monkeypatch, repo, env, "runs") == 0

    assert _outputs(output) == {"passed": "false", "tested": "false"}
    if case in ("no token", "pull request"):
        assert api.calls == []


def test_the_script_runs_as_the_workflow_runs_it(repo, tmp_path):
    # python3 scripts/ci/changed_areas.py areas, from the root of the checkout, in a Python of its own.
    before = _git(repo, "rev-parse", "HEAD")
    sha = _commit(repo, "web", {"webui/src/app.css": "a {}\n"})
    env, output = _environment(tmp_path, "push", {"before": before}, sha, BEFORE_PASSED="true")
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GITHUB_"))}

    result = subprocess.run([sys.executable, "-I", str(MODULE), "areas"], cwd=repo, env={**environment, **env},
                            capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert _outputs(output) == _expected(_areas("code", "webui", "images"))
    other = subprocess.run([sys.executable, "-I", str(MODULE), "other"], cwd=repo, env=environment,
                           capture_output=True, text=True, timeout=60)
    assert other.returncode == 2 and "unknown" in other.stderr

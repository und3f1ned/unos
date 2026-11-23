#!/usr/bin/env python3
"""Which jobs of the CI workflow a run needs (ADR-0467): the job Changed paths of
.github/workflows/test.yml runs this script, and the other jobs read its outputs.

    changed_areas.py runs    on a push, with the job's token in GH_TOKEN: writes the outputs passed
                             (the run of the workflow for the push's `before` passed) and tested (the
                             push is the merge commit of a pull request whose run of the workflow
                             tested the same parents and tree, and passed)
    changed_areas.py areas   writes the outputs code, lint, webui, images and docs_only; on a pull
                             request it first prints the record of the commits that its run tests

The files that changed: for a pull request `git diff <base>...<head>`, for a push
`git diff <before> <sha>`, both with --no-renames, so that a rename names both paths. A missing or
zero `before`, a force push, a `before` that is no ancestor of the pushed commit, a failed diff, an
empty list and any other event mean that everything changed; so does a push whose `before` has no
passed run, so that a red main stays red. A change of the workflow or of this script runs every job.

    docs_only  every file is documentation or the ledger: *.md anywhere, docs/**, feature-list.json,
               LICENSE. Docs and Ledger runs; Unit Tests run unless it is true.
    code       not docs_only.
    lint       a file that is no document and not below webui/: what make lint and the audit read.
               Lint and Audit runs unless it is false.
    webui      a file below webui/, or the Makefile (make frontend's recipe). Frontend Checks run
               unless it is false.
    images     a file below webui/ or one of IMAGE_FILES, which the images' builds read. Build Check
               runs on main unless it is false.

A run of a pull request checks out GitHub's merge of the pull request's head into its base and prints
`tested base=<first parent> head=<second parent> tree=<tree>` for it. On a push whose commit has
exactly two parents, the first of them the push's `before`, `runs` asks GitHub's API for the
pull_request runs of this workflow whose head is the second parent, newest first, and reads the log of
each one's Changed paths: the newest run whose log holds exactly the record of this merge's parents
and tree decides, and only a completed, successful one makes tested true. Anything else, or any error,
is doubt: false. The token goes to GitHub's API only, never along a redirect (a job's log is a
redirect to a storage host).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import urllib.request
from typing import Callable, NamedTuple

ZERO = "0" * 40
COMMIT = re.compile(r"[0-9a-f]{40}")
WORKFLOW = "test.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW
JOB_NAME = "Changed paths"
# The files that decide the jobs: a change of them runs every job.
DECIDERS = (WORKFLOW_PATH, "scripts/ci/changed_areas.py")
# What Build Check's `docker compose build` reads besides webui/, which the web UI's image copies whole.
IMAGE_FILES = ("deploy/docker-compose.yml", "supervisor/Dockerfile", "supervisor/requirements.txt",
               "supervisor/.dockerignore")
# A record alone on a line of a job's log, after the log's time stamp (and the byte order mark of the
# log's first line).
RECORD = re.compile(r"^\ufeff?(?:\S+ )?tested base=([0-9a-f]{40}) head=([0-9a-f]{40}) tree=([0-9a-f]{40})\r?$",
                    re.M)
# The largest answer read from the API: a job's log is a few kB.
MAX_ANSWER = 8 * 1024 * 1024


class Areas(NamedTuple):
    code: bool
    lint: bool
    webui: bool
    images: bool
    docs_only: bool


EVERYTHING = Areas(code=True, lint=True, webui=True, images=True, docs_only=False)


def is_document(path: str) -> bool:
    return path.endswith(".md") or path.startswith("docs/") or path in ("feature-list.json", "LICENSE")


def classify(paths: list[str] | None) -> Areas:
    """The areas of the changed files; None (not listed), none at all or a decider: everything."""
    if not paths or any(path in DECIDERS for path in paths):
        return EVERYTHING
    docs_only = all(is_document(path) for path in paths)
    return Areas(
        code=not docs_only,
        lint=any(not is_document(path) and not path.startswith("webui/") for path in paths),
        webui=any(path.startswith("webui/") or path == "Makefile" for path in paths),
        images=any(path.startswith("webui/") or path in IMAGE_FILES for path in paths),
        docs_only=docs_only,
    )


def _git(args: list[str], cwd: str = ".") -> str | None:
    """git's output, or None when it failed."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    return result.stdout if result.returncode == 0 else None


def _commit(value: object) -> str | None:
    return value if isinstance(value, str) and COMMIT.fullmatch(value) and value != ZERO else None


def _diff(old: str, new: str, cwd: str, three_dots: bool = False) -> list[str] | None:
    span = [f"{old}...{new}"] if three_dots else [old, new]
    out = _git(["diff", "--name-only", "--no-renames", "--no-ext-diff", "-z", *span, "--"], cwd)
    return None if out is None else [path for path in out.split("\0") if path]


def changed_files(event_name: str, event: dict, sha: str, cwd: str = ".") -> list[str] | None:
    """The files that the pull request or the push changed, or None when they cannot be told."""
    if event_name == "pull_request":
        pull_request = event.get("pull_request") or {}
        base = _commit((pull_request.get("base") or {}).get("sha"))
        head = _commit((pull_request.get("head") or {}).get("sha"))
        return _diff(base, head, cwd, three_dots=True) if base and head else None
    if event_name == "push":
        before, after = _commit(event.get("before")), _commit(sha)
        if not before or not after or event.get("forced"):
            return None
        if _git(["merge-base", "--is-ancestor", before, after], cwd) is None:
            return None
        return _diff(before, after, cwd)
    return None


def tested_commits(cwd: str = ".") -> tuple[str, str, str] | None:
    """The first and second parent and the tree of the checked-out merge commit, or None."""
    out = _git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd)
    tree = _git(["rev-parse", "HEAD^{tree}"], cwd)
    ids = out.split() if out else []
    if len(ids) != 3 or not tree:
        return None
    return ids[1], ids[2], tree.strip()


def record_line(base: str, head: str, tree: str) -> str:
    return f"tested base={base} head={head} tree={tree}"


def read_records(log: str) -> set[tuple[str, str, str]]:
    return {(base, head, tree) for base, head, tree in RECORD.findall(log)}


def api_get(token: str, api_url: str) -> Callable[[str], bytes]:
    """A GET of GitHub's REST API with the token, which urllib sends to that host alone."""
    if not api_url.startswith("https://"):
        raise ValueError(f"not an https URL of GitHub's API: {api_url!r}")

    def get(path: str) -> bytes:
        request = urllib.request.Request(api_url + path, headers={  # noqa: S310 - https only, checked above
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "unos-ci"})
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https only, checked above
            body = response.read(MAX_ANSWER + 1)
        if len(body) > MAX_ANSWER:
            raise ValueError(f"an answer of more than {MAX_ANSWER} bytes: {path}")
        return body

    return get


def _json(get: Callable[[str], bytes], path: str) -> dict:
    answer = json.loads(get(path))
    if not isinstance(answer, dict):
        raise ValueError(f"not a JSON object: {path}")
    return answer


def _runs(get: Callable[[str], bytes], repository: str, event: str, head: str) -> list[dict]:
    """The runs of this workflow for the event and head commit, from this repository, newest first."""
    answer = _json(get, f"/repos/{repository}/actions/workflows/{WORKFLOW}/runs?event={event}&head_sha={head}"
                        "&per_page=100")
    runs = [run for run in answer.get("workflow_runs") or []
            if isinstance(run, dict) and type(run.get("id")) is int and run.get("event") == event
            and run.get("head_sha") == head and run.get("path") == WORKFLOW_PATH
            and (run.get("head_repository") or {}).get("full_name") == repository]
    return sorted(runs, key=lambda run: run["id"], reverse=True)


def _passed(run: dict) -> bool:
    return run.get("status") == "completed" and run.get("conclusion") == "success"


def before_passed(before: str, branch: str, repository: str, get: Callable[[str], bytes]) -> tuple[bool, str]:
    """Whether the newest run of the workflow for the push of `before` to the branch passed, and why."""
    if not _commit(before):
        return False, f"no commit before this push ({before!r})"
    runs = [run for run in _runs(get, repository, "push", before) if run.get("head_branch") == branch]
    if not runs:
        return False, f"no run of the workflow for the push of {before} to {branch}"
    run = runs[0]
    state = f"run {run['id']} for {before} ({run.get('html_url')}): {run.get('status')}, {run.get('conclusion')}"
    return _passed(run), state


def tested_merge(sha: str, before: str, repository: str, get: Callable[[str], bytes],
                 cwd: str = ".") -> tuple[bool, str]:
    """Whether the push of `sha` is the merge commit of a pull request whose newest run of the workflow
    that tested its parents and its tree passed, and why."""
    out = _git(["rev-list", "--parents", "-n", "1", sha], cwd) if _commit(sha) else None
    ids = out.split() if out else []
    if len(ids) != 3:
        return False, f"{sha} is not a merge commit of two parents"
    first, second = ids[1], ids[2]
    if before != first:
        return False, f"the push brought more than the merge: before {before}, first parent {first}"
    tree = (_git(["rev-parse", f"{sha}^{{tree}}"], cwd) or "").strip()
    if not COMMIT.fullmatch(tree):
        return False, f"no tree for {sha}"
    for run in _runs(get, repository, "pull_request", second):
        jobs = _json(get, f"/repos/{repository}/actions/runs/{run['id']}/jobs?per_page=100").get("jobs") or []
        changes = [job for job in jobs if isinstance(job, dict) and job.get("name") == JOB_NAME]
        if len(changes) != 1 or changes[0].get("conclusion") != "success" or type(changes[0].get("id")) is not int:
            continue
        log = get(f"/repos/{repository}/actions/jobs/{changes[0]['id']}/logs").decode("utf-8", "replace")
        if read_records(log) != {(first, second, tree)}:
            continue
        state = f"run {run['id']} of the pull request ({run.get('html_url')}) tested base {first}, head {second} " \
                f"and tree {tree}"
        if _passed(run):
            return True, f"{state}, which this merge holds, and passed"
        return False, f"{state}: {run.get('status')}, {run.get('conclusion')}"
    return False, f"no run of a pull request recorded base {first}, head {second} and tree {tree}"


def _write_outputs(values: dict[str, bool]) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for name, value in values.items():
            output.write(f"{name}={'true' if value else 'false'}\n")


def _event() -> tuple[str, dict]:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as file:
        event = json.load(file)
    return os.environ["GITHUB_EVENT_NAME"], event if isinstance(event, dict) else {}


def _runs_step() -> None:
    event_name, event = _event()
    token = os.environ.get("GH_TOKEN", "")
    passed = tested = False
    if event_name != "push" or not token:
        print("Not a push with the job's token: nothing to ask.")
    else:
        try:
            get = api_get(token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
            before = str(event.get("before") or "")
            repository = os.environ["GITHUB_REPOSITORY"]
            passed, why = before_passed(before, os.environ.get("GITHUB_REF_NAME", ""), repository, get)
            print(f"main before this push: {'passed' if passed else 'not passed'}: {why}")
            if passed:
                tested, why = tested_merge(os.environ["GITHUB_SHA"], before, repository, get)
                print(f"tested merge: {'yes' if tested else 'no'}: {why}")
        except Exception as error:  # any doubt runs every check
            passed = tested = False
            print(f"The question to GitHub failed ({type(error).__name__}: {error}): every job runs.")
    _write_outputs({"passed": passed, "tested": tested})


def _areas_step() -> None:
    event_name, event = _event()
    if event_name == "pull_request":
        commits = tested_commits()
        if commits and commits[1] == ((event.get("pull_request") or {}).get("head") or {}).get("sha"):
            print(record_line(*commits))
    paths = changed_files(event_name, event, os.environ["GITHUB_SHA"])
    if paths is None:
        print(f"The changed files of this {event_name} cannot be listed: every job runs.")
    else:
        print(f"{len(paths)} changed files:")
        # The runner takes no command from the names in between: a file name could look like one.
        resume = secrets.token_hex(16)
        print(f"::stop-commands::{resume}")
        for path in paths:
            print("  changed:", json.dumps(path, ensure_ascii=False))
        print(f"::{resume}::")
    found = classify(paths)
    if event_name == "push" and os.environ.get("BEFORE_PASSED") != "true":
        print("The run of the workflow for the commit before this push did not pass, or was not found: every "
              "job runs.")
        found = EVERYTHING
    print(" ".join(f"{name}={value}" for name, value in found._asdict().items()))
    _write_outputs(found._asdict())


def main(argv: list[str]) -> int:
    steps = {"runs": _runs_step, "areas": _areas_step}
    if len(argv) != 1 or argv[0] not in steps:
        print(f"usage: changed_areas.py {{{','.join(steps)}}}; unknown: {' '.join(argv)}", file=sys.stderr)
        return 2
    steps[argv[0]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

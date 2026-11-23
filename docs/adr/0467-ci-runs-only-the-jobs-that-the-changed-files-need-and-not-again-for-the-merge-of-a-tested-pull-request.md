---
status: accepted
date: 2026-11-23
area: ci
decided-by: agent
supersedes: [ADR-0462]
---

# ADR-0467: CI runs only the jobs that the changed files need, and not again for the merge of a tested pull request

## Context

The repository is private on GitHub's Free plan, whose Actions minutes are limited. Since ADR-0462 the
CI workflow (`.github/workflows/test.yml`) runs every job on every pull request and every push to
`main`, Unit Tests about 20 minutes and Frontend about 3, also for a commit that changes only
`PROGRESS.md`, and again on `main` for the merge commit of a pull request whose run checked the same
tree minutes before. The owner asked to "add the filter, maximum savings" (in translation). The
`pull_requests` list of a workflow run, which names the base of the pull request, is empty once the
pull request is merged (seen on 2026-11-23 for the run of pull request 112), so a push cannot learn
from it what the pull request's run tested.

## Decision

- A first job `changes` (name Changed paths) runs `scripts/ci/changed_areas.py`. The changed files are
  `git diff --name-only --no-renames <base.sha>...<head.sha>` of a pull request and `git diff
  --name-only --no-renames <before> <sha>` of a push (`--no-renames`: a rename names both paths, so code
  moved into `docs/` is still code). A missing or zero `before`, a force push, a `before` that is no
  ancestor of the pushed commit, a failed diff, an empty list, any other event, and a change of
  `.github/workflows/test.yml` or of the script itself mean "everything changed".
- Its outputs and the jobs they skip:
  - `docs_only`: every file is documentation or the ledger (`*.md` anywhere, `docs/**`,
    `feature-list.json`, `LICENSE`). Unit Tests run unless it is true; Docs and Ledger runs when it is.
  - `lint`: a file that is neither a document nor below `webui/`, which nothing of `make lint` or the
    audit reads. Lint and Audit runs unless it is false.
  - `webui`: a file below `webui/`, or the `Makefile` (make frontend's recipe). Frontend Checks run
    unless it is false.
  - `images`: a file below `webui/` (the web UI's image copies the folder), `deploy/docker-compose.yml`,
    `supervisor/Dockerfile`, `supervisor/requirements.txt` or `supervisor/.dockerignore`. Build Check
    runs on `main` unless it is false, after Unit Tests passed or were skipped (`needs: [changes,
    test]`, `!cancelled()`). As before it never runs on a pull request; with the merge rule below
    "after Unit Tests" alone would have left it almost never running.
  - `tested`: the push merges a pull request whose run tested the same tree (below). Unit Tests, Lint,
    Frontend and Docs and Ledger do not run.
  Each job is skipped only by the output named for it (`!cancelled() && <output> != ...`): a failed
  Changed paths, whose outputs are empty, runs them, and a wrong value of one output still runs Unit
  Tests or Docs and Ledger, which run the classifier's tests. Commit Messages and Secret Scan have no
  condition on the changes and no `needs`.
- Docs and Ledger: the supervisor's packages, a checkout with the whole history, `make test-fast`, and
  `make test TESTS='...'` over the backend test files outside `make test-fast` whose code names a
  document of the repository (an `.md` literal, `feature-list.json`, `LICENSE`, or `docs` joined to the
  repository's root). `supervisor/tests/test_repo_tooling.py` searches the test files the same way and
  fails when the job's list differs.
- On a push, the step "Look up the runs that checked main and this merge" asks GitHub's API for the
  newest run of this workflow for the push of `before` to the branch. Unless it completed with success,
  the push runs every job (`passed`), so that a change of documents alone, or a merge, does not turn a
  red `main` green.
- The merge of a tested pull request. The run's `pull_requests[].base.sha` cannot name the tested base
  after the merge (Context), so the pull request's run records what it tests: its Changed paths prints
  `tested base=<first parent> head=<second parent> tree=<tree>` of the checked-out commit, GitHub's
  merge of the head into the base, which every job of the run checks out. On a push after a passed run,
  whose commit has exactly two parents, the first of them `before`, the step lists the `pull_request`
  runs of `test.yml` from this repository whose head is the second parent, newest first, and reads the
  log of each one's Changed paths; the newest whose log holds exactly the record of the merge's two
  parents and tree decides, and only when it completed with success is `tested` true. Anything else,
  an error or an answer that is not as expected included, is doubt and runs everything.
- The token: the job `changes` alone has `permissions: {contents: read, actions: read}`; the workflow
  keeps `contents: read` for every other job. Only the step above, which runs on pushes, gets
  `GH_TOKEN: ${{ github.token }}`; the script sends it to GitHub's API as an unredirected header, so
  that it does not follow the log's redirect to the storage host. Pull request runs never get it. The
  changed files are printed quoted, between `::stop-commands::` and its resume token, so that no file
  name acts as a command to the runner.
- A skipped job reports: GitHub shows a job skipped by its `if` as skipped, and a required status
  check counts a skipped job as passed, so a skipped check never waits. The jobs are skipped by `if`,
  never by `paths` on the trigger, which would leave a required check of a run that never starts
  pending. `main` has no branch protection (ADR-0229); this holds when it gets one. The gate that
  ADR-0229 names stays: every pull request runs the checks that its changes need.
- No new action: Changed paths uses `actions/checkout` at the pin of the other jobs (ADR-0423) and the
  runner's `python3`.
- Tests: `supervisor/tests/test_ci_changed_areas.py` (the classification on file lists, the changed
  files of real git repositories with a rename, odd names and unusable `before`s, the record, the run of
  `before` and the merge rule against a fake of GitHub's API, the token's header on a redirect) and
  `supervisor/tests/test_repo_tooling.py` (each job's `if`, `needs` and permissions, the token's one
  place, the docs job's commands and list).

This supersedes the first rule of ADR-0462, that every job runs on every push to `main` and every pull
request; its other rules stay in force. It also changes when the Build Check of ADR-0190 runs (on pushes
to `main` that change what the images build from), whose other rules stay as ADR-0423 left them.

## Consequences

- A pull request or push of documents and the ledger alone costs Changed paths, Docs and Ledger,
  Commit Messages and Secret Scan instead of every job; a change below `supervisor/` or `scripts/` no
  longer runs Frontend, one below `webui/` no longer runs Lint; the merge push of a pull request whose
  last run passed on the `main` it merges into costs Changed paths and Secret Scan, and Build Check
  when it changes what the images build from.
- A merge onto a `main` that moved on after the pull request's last run, a squash or rebase merge, a
  merge commit with changes of its own, a pull request run from before this ADR (no record), and any
  push after a run of `main` that did not pass, is still running, or was cancelled, run every job.
- Two pushes to `main` in quick succession: the second runs everything while the first's run is not
  done.
- A docs-only change does not run the backend tests that read no document: a document that some
  other code reads at run time must not be given the `.md` suffix or put below `docs/`.
- A change of `scripts/ci/changed_areas.py` also starts the workflow Storage on btrfs, whose paths
  include `scripts/ci/**` (ADR-0464).
- Secret Scan runs again on the merge push of a tested pull request, although its run scanned the
  same content: the scan's rule is that it runs on every run.

## Considered options

- `paths` and `paths-ignore` on the trigger: a run that never starts leaves required checks pending,
  and it cannot tell a merge of a tested pull request.
- A third-party action such as dorny/paths-filter: a new dependency with the job's token, for a diff
  that git does.
- The run's `pull_requests[].base.sha` for the merge rule: empty once the pull request is merged.
- An artifact or an annotation as the record: a new action, or `checks: read` beside `actions: read`.
- Skipping Unit Tests for a change of the web UI alone: the backend tests read files of `webui/` (its
  package files, its Dockerfile, its source).
- Skipping Unit Tests and Lint on `code == 'false'` rather than `docs_only == 'true'`: a classifier that
  wrote false for both would run neither Unit Tests nor Docs and Ledger, so nothing would run its tests.

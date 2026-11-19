---
status: superseded
date: 2026-11-06
area: ci
decided-by: agent
superseded-by: ADR-0423
supersedes: [ADR-0056]
---

# ADR-0190: CI fails on any test, lint, audit, build or check error, with a read-only token

## Context

Superseded by ADR-0423: only in part, the commands of the Lint and Audit job: ruff and pip-audit are installed with `pip install --require-hashes --only-binary=:all: -r .github/lint-requirements.txt`, and `scripts/audit-python.sh` runs pip-audit in place of `pip-audit -r supervisor/requirements.txt`; every other rule here stays in force.

CI ran only pytest, and an x86 image build after a merge. The November 2026 audit counted 179 svelte-check errors, 10 of them in the web UI's source and 3 of those real bugs of the file manager, and Phase 0 still shipped with 7 of them, in files it did not change (ADR-0056). A tool run through npx could be downloaded unpinned, and the build or test code of a dependency could read the workflow's token from the git configuration.

## Decision

`.github/workflows/test.yml` (name CI, on pushes and pull requests to main):

- Unit Tests: `make test` (pytest over `supervisor/tests`) on Python 3.11 with `supervisor/requirements-dev.txt`.
- Lint and Audit (Python): `make lint` (`ruff check --select F,E9 supervisor scripts .claude/hooks` for pyflakes findings and syntax errors in the supervisor and the repository's own tooling, `bash -n` on the shell scripts, and the JSON syntax of `feature-list.json` and `.claude/settings.json`) and `pip-audit -r supervisor/requirements.txt`. ruff 0.17.3 and pip-audit 2.10.1, with every package they depend on, are installed with `pip install --require-hashes -r .github/lint-requirements.txt`.
- Frontend Checks on Node.js 22: `npm ci`, `make frontend` (`npm run check`, that is `svelte-kit sync`, then `svelte-check --threshold error`, svelte-check pinned to 3.8.6 in `webui/package-lock.json` and run from `node_modules/.bin`, not through npx; and `npm run build`) and `npm audit --omit=dev --audit-level=high`.
- Commit Messages, on pull requests: the commit convention over the pull request's commits (ADR-0209).
- Build Check: the images are built with `docker compose build` on pushes to main.
- Every check fails the run when it finds a problem, and each check runs even when another one failed: a step runs unless the run was cancelled or its tools failed to install, and `make lint` and `make frontend` run every part before they fail (ADR-0216).
- The workflow has `permissions: contents: read`, every checkout sets `persist-credentials: false`, and a new push to a pull request cancels the run for the previous one.
- `supervisor/tests/test_deploy_scripts.py` and `supervisor/tests/test_repo_tooling.py` keep the workflow, the pins, the make targets and the documented local commands in step.

## Consequences

- A green run has no failing test, no error of svelte-check, no pyflakes finding, no known vulnerability in the supervisor's runtime dependencies and no high-severity one in the web UI's. The owner merges on it; branch protection that requires it is not set up yet (the feature-list entry `ci.branch-protection`).
- A new advisory can turn CI red without a code change; dependency updates are proposed separately (ADR-0218).
- Not in CI yet: the E2E specs, and btrfs tests on loop devices.
- `make check` runs the same test, lint and frontend steps locally (ADR-0216).

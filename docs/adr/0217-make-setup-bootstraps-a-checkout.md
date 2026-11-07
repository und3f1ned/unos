---
status: accepted
date: 2026-11-07
area: ci
decided-by: agent
---

# ADR-0217: One command, make setup, bootstraps a checkout idempotently

## Context

Setting up a checkout took scattered steps: a Python 3.11 environment with the pinned requirements, the hashed lint tools, `npm ci` and Playwright's browser for the web UI, the git hooks, and the local device settings. The README's development setup did not work on macOS as written, and each agent depended on environments that existed only in session notes (the book's Reproducible Agent Bootstrap pattern).

## Decision

`make setup` runs `scripts/setup.sh`, which is idempotent:

- It first checks the prerequisites (git, make, Python 3.11, Node.js with the npm version of `webui/package.json`) and, when one is missing, says so and stops before it changes anything. `scripts/setup.sh --check` only reports what is missing or outdated; it changes nothing and needs no network.
- It creates the Python 3.11 virtual environment `supervisor/.venv` from `supervisor/requirements-dev.txt`, and the lint tools in `.venv-lint` with the hashes of `.github/lint-requirements.txt`.
- In `webui/` it runs `npm ci` and installs the Chromium of the Playwright version that `webui/package-lock.json` pins.
- It sets `git config core.hooksPath scripts/git-hooks`, so that the pre-commit and commit-msg hooks run (ADR-0209, ADR-0216).
- It creates `dev.env` from `dev.env.example` with mode 600 when it is missing (ADR-0212), except in a linked worktree whose main checkout has one, which the scripts of `scripts/dev/` then use (ADR-0210): a copy in the worktree would win over it and hold no settings (`test_setup_in_a_linked_worktree_uses_the_main_checkouts_dev_env`). An existing `dev.env` only gets mode 600; it is never replaced, read or shown.
- Each environment remembers the SHA-256 of the files it was installed from (`.unos-setup.sha256`), so a rerun installs only what changed.
- It ends with the baseline: `make check-fast`, then `make smoke` (the app starts and serves health, the first-run setup and a login, ADR-0216), and says whether the baseline is green or red, so that a red baseline is known before any change.
- It needs the network, so the owner runs it; agents do not install packages (ADR-0211).
- Tested in `supervisor/tests/test_repo_tooling.py`, with stub tools instead of the network.

## Consequences

- A new checkout or worktree is ready for `make check` after one command, and the make targets find the tools it installed.
- The setup installs only on the workstation; the device gets nothing from it (ADR-0214).
- The secrets in a new `dev.env` stay empty until the owner fills them in; only the scripts of `scripts/dev/` need them.
- CI does not run `make setup`: its jobs install with pip and npm themselves, so the bootstrap is tested only against stub tools (the feature-list entry `ci.bootstrap-in-ci`).

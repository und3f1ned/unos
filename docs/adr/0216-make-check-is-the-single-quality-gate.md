---
status: superseded
date: 2026-11-07
area: ci
decided-by: agent
superseded-by: ADR-0422
---

# ADR-0216: One quality gate: make check runs what CI runs

## Context

Superseded by ADR-0422: only in part, the command of `make audit`, whose Python part is now `scripts/audit-python.sh` in place of `pip-audit -r supervisor/requirements.txt`; every other rule here stays in force.

CI's jobs (ADR-0190) had no single local equivalent: the commands were spread over `docs/TESTING.md`, `scripts/test-cycle.sh` and the session notes of each agent, so local runs and CI could drift apart and an agent could not tell which command proves a change done (the book's Quality Gates pattern). The book's repository uses mise and hk for this, but neither can be installed here, while make exists everywhere. Agents also commit only when the owner asks (ADR-0208), so the pre-commit hook alone does not run the checks before an agent reports a change as done.

## Decision

A root `Makefile` is the single entry point:

| Target | Runs |
| --- | --- |
| `make check` | lint, test and frontend: what CI's Unit Tests, Lint and Frontend jobs run, minus the network audits |
| `make check-fast` | lint and test-fast, for the pre-commit hook (`scripts/git-hooks/pre-commit`) and the Stop hook |
| `make lint` | `ruff check --select F,E9 supervisor scripts .claude/hooks` (the supervisor and the repository's own tooling: the guard, `scripts/dev`, the stand-in, the commit checker), `bash -n` on every shell script of `deploy/`, `scripts/` and `.claude/hooks/`, and the JSON syntax of `feature-list.json` and `.claude/settings.json` |
| `make test` | all backend tests |
| `make test-fast` | the repository-convention tests: `test_adrs.py`, `test_feature_list.py`, `test_agent_guard.py`, `test_repo_tooling.py` (with the secret scan), `test_dev_scripts.py`, `test_standin.py` |
| `make smoke` | a few seconds of existing backend tests that start the app and serve requests: the startup and `/api/v1/health`, the first-run setup, a login through the proxy layer and the refusal of anonymous requests |
| `make frontend` | `npm run check` and `npm run build` in `webui/` |
| `make e2e-local` | `scripts/standin/e2e.sh`, the local stand-in only (ADR-0215) |
| `make audit` | `pip-audit -r supervisor/requirements.txt` and `npm audit --omit=dev --audit-level=high` (needs the network) |
| `make setup` | `scripts/setup.sh` (ADR-0217) |

- `make check`, `make check-fast`, `make lint` and `make frontend` run every part even when one fails and then fail when any did, so that one run reports every problem (ADR-0190).
- `PYTHON` (default `supervisor/.venv/bin/python`, else `python3`), `RUFF` (default `.venv-lint/bin/ruff`, else `ruff`) and `NPM` can be overridden; `make help` lists the targets. The backend targets wait for the machine-wide test lock (ADR-0210).
- The tools are pinned by the repository's own files: `supervisor/requirements-dev.txt`, `.github/lint-requirements.txt` with hashes, `webui/package-lock.json`.
- The secret scan of `supervisor/tests/test_repo_tooling.py` needs no scanner to install (ADR-0212). It works on the files that git lists in the working tree, tracked and untracked but not ignored (`git ls-files --cached --others --exclude-standard`, what `git add -A` would commit):
  - `test_git_lists_no_env_file`: git lists no `dev.env` or other env file at all, tracked or about to be added. Env files are never read.
  - `test_no_secret_shapes_in_the_repository` reads the text files and fails on the shapes in which secrets get written down: `sshpass -p` with a value, `--password=` (or token, secret, passphrase) with a value, `user:password@` in a URL, `curl -u user:password`, an Authorization header with a literal credential, a JWT, a literal assigned to a name that holds a secret (the guard's rule, `_is_secret_name`, ADR-0211), in YAML and JSON files also a `NAME: value` mapping with such a name, and PEM or PGP private keys. Variables and placeholders do not count. `ALLOWED_SECRET_VALUES` names each test value with the files that may hold it, and an entry that no file needs any more fails the scan.
  - `test_no_published_password_in_the_repository` hashes every token of six or more characters and fails on the SHA-256 of a password that CLAUDE.md once published (`PUBLISHED_PASSWORD_DIGESTS`); the test stores only the digests.
- The backend tests never act on the caller's git repository. git exports `GIT_DIR` to the commands of `git rebase -x` in a linked worktree, and `GIT_INDEX_FILE` to the pre-commit hook. So `supervisor/tests/conftest.py` removes every `GIT_*` variable when pytest loads it, before any test module builds an environment; the tests' git helpers build their environments without `GIT_*` keys; and `scripts/git-hooks/pre-commit` unsets `GIT_INDEX_FILE`, `GIT_DIR`, `GIT_WORK_TREE` and `GIT_PREFIX` before `make check-fast`. Tested by `test_the_suites_git_steps_leave_an_outer_repository_alone` (the suite's git steps, run with `GIT_DIR` and `GIT_INDEX_FILE` pointing at a scratch repository, leave it byte for byte unchanged) and `test_pre_commit_keeps_gits_variables_from_make_check_fast`.
- The Stop hook `.claude/hooks/stop-check.sh` (`.claude/settings.json`) runs `make check-fast` when an agent is about to stop. It lets the session stop without a run when its input says `stop_hook_active` (the agent already went back to work once), when this is not a git work tree or a tool is missing (with a warning that Claude Code shows to the user, the `systemMessage` of a JSON answer), or when the working tree is the one of the last green run: a SHA-256 of HEAD, git status, the staged and unstaged diff and the untracked files, kept in the git-ignored `tmp/stop-check/`. When `make check-fast` fails, it exits 2 with the last lines of each failing part, so that the agent goes back to work instead of reporting done.
- `make smoke` is the baseline: of `make setup`, after `make check-fast` (ADR-0217), of the start of a session (`AGENTS.md`) and of a feature pass (the feature-pass skill).
- A change is done only when `make check` passes. CI runs the same targets, and CI is the check the owner merges on; it becomes mandatory once branch protection requires its checks (the feature-list entry `ci.branch-protection`).
- Tested in `supervisor/tests/test_repo_tooling.py` (the targets and their commands, every part running, the hooks, the Stop hook with stubs, CI running the same targets).

## Consequences

- One command to run before calling a change done, named in AGENTS.md, and the Stop hook catches a red `make check-fast` before the agent says it is done.
- The Stop hook adds the time of a `make check-fast` run to the end of a turn that changed the working tree; an unchanged tree costs only the hash.
- CI's jobs and the Makefile must be kept in step whenever either changes.
- E2E and the audits stay outside `make check`: the first needs the stand-in, the second the network.

## Considered options

- mise and hk, as the book's repository uses: they cannot be installed here, and make is available everywhere. Revisit when the toolchain grows.

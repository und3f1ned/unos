---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0210: Parallel agents work in separate worktrees, and one agent at a time uses the device

## Context

The SMB slice and this conversion were built by several agents at once. git isolates only files: shared state such as the backend suite's fixed paths under `/tmp`, the stand-in's ports, the git index and the one dev device can still collide, and parallel device deploys would multiply the writes to its system disk (ADR-0214). Every change must stay attributable and reviewable (the book's Isolated Parallel Work pattern).

## Decision

- Parallel work happens in separate git worktrees, each with one branch and one done criterion. A feature's worktree is made with `git worktree add --no-track -b <id> ../unos-<id> <base>`, where the base is the branch that holds `AGENTS.md`: `origin/main` once the conversion is merged, until then `origin/phase1-broken-flows`. `--no-track` leaves the new branch without an upstream, so a plain `git push` cannot reach the base's branch (ADR-0208). A worktree without `AGENTS.md` or `.claude/settings.json` has no guard and no deny rules: the agent stops and asks the owner.
- Agents of a workflow that shares one checkout edit only the files their brief assigns them and report a change they need elsewhere, with the exact change, instead of making it; they never commit, stash, check out or reset (ADR-0208).
- Shared resources are serialized: one backend test run per machine at a time, also across worktrees, because the suite uses fixed paths under `/tmp` (the test targets of the Makefile wait for the lock `/tmp/unos-backend-tests.lock`, ADR-0216). E2E runs only against a local target, one run at a time (ADR-0215): `scripts/standin/e2e.sh` allows one run per checkout (`.standin/e2e.lock`) and uses fixed default ports, which a second worktree changes with `E2E_HTTPS_PORT`, `E2E_HTTP_PORT` and `E2E_BACKEND_PORT`; the compose test stack of `scripts/test-cycle.sh` has fixed container names and ports, so it runs once per machine (the feature-list entry `testing.compose-stack-per-worktree`).
- The dev device is a single shared resource. Every step of `scripts/dev/deploy.sh` and every run of `scripts/dev/linux-tests.sh` takes the device lock, `/run/unos-dev.lock` on the device (flock, in RAM): it records its holder, waits for another holder up to a limit and is released when the script ends. The read-only `scripts/dev/check.sh` only reports the holder. Implemented in `scripts/dev/env.sh` (`dev_lock`) and tested in `supervisor/tests/test_dev_scripts.py`.
- A linked worktree without a `dev.env` of its own uses the main checkout's, found through git's common directory, and the deploy's backups and rollback copies in `tmp/dev/` are the main checkout's, shared by all its worktrees (`scripts/dev/env.sh`, ADR-0212, ADR-0223; tested in `supervisor/tests/test_dev_scripts.py`, `test_a_linked_worktree_uses_the_main_checkouts_dev_env`). A session in a worktree reaches them by a path outside its own checkout, so the deny rules of `.claude/settings.json` name them by absolute path, and the guard treats the main checkout as holding them (ADR-0211). `make setup` creates no `dev.env` in such a worktree (ADR-0217).
- Nobody installs from the network or works around the workstation's network filter, and nobody prints a secret (ADR-0211, ADR-0212). Decisions already taken are applied, not re-decided.

## Consequences

- One agent's deploy or Linux test run makes the others wait; device work is serialized by design.
- A change outside an agent's files waits for the agent or person who owns them, which costs a round trip but keeps every change attributable.
- The lock lives in RAM, so a reboot of the device releases it.

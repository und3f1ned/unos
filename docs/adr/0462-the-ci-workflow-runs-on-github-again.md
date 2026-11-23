---
status: superseded
date: 2026-11-22
area: ci
decided-by: owner
superseded-by: ADR-0467
supersedes: [ADR-0230]
---

# ADR-0462: The CI workflow runs on GitHub again

## Context

Superseded by ADR-0467: only in part, its first rule: a run no longer runs every job, but those that the changed files need, and a push to `main` that merges a pull request whose run tested the same tree runs none of Unit Tests, Lint, Frontend and Docs and Ledger; every other rule here stays in force.

ADR-0230 turned the workflow CI (`.github/workflows/test.yml`) off on GitHub on 2026-11-07 and
made `make check` on the workstation the only gate. Since then the workflow's jobs have been kept
in step with `make check` by the tests, and ten ledger entries of the area ci (mypy, shellcheck,
ruff's rules, ESLint and Prettier, gitleaks, the ledger's changes, Samba's testparm, the pinned
actions, btrfs on loop devices, branch protection) wait for a run on GitHub. The weekly Audit
workflow, which stayed on, showed on 2026-11-22 that the hosted runner catches what the
workstation does not: ubuntu-latest had moved to Ubuntu 26.04, which the pinned Playwright did not
know (ADR-0461). Asked whether to turn CI on again, the owner answered "turn it on" (in
translation).

## Decision

- The workflow CI is enabled on GitHub again (`gh workflow enable CI`). It runs as ADR-0190
  describes it: on every push to `main` and every pull request against `main`, its Unit Tests,
  Lint and Audit, Frontend Checks, Commit Messages and Secret Scan jobs.
- `make check` stays the gate before a change is done (ADR-0216): the pre-commit hook and the
  Stop hook run its fast part (ADR-0383), and CI checks the same again on a fresh Linux runner,
  where the backend tests that skip on macOS run too. `scripts/dev/linux-tests.sh` stays for a
  check before a push.
- A red job is fixed as any failing check is, test first (ADR-0186); no job is turned off or
  allowed to fail to get green.
- `main` still has no branch protection (ADR-0229): CI reports, it does not block a merge.
- The weekly Audit workflow (ADR-0218) and Dependabot stay as they are.

## Consequences

- Every push to `main` and every pull request uses Actions minutes of the owner's plan, about 15
  to 30 minutes a run.
- The entries of the area ci that wait for a run on GitHub can pass once their job has run.

## Considered options

- Keeping CI off and running its jobs by hand with `gh workflow run`: the workflow has no
  `workflow_dispatch`, and a check that runs only when someone remembers it is no gate.

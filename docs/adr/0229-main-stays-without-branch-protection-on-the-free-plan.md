---
status: accepted
date: 2026-11-07
area: ci
decided-by: owner
---

# ADR-0229: The main branch stays without branch protection on GitHub's Free plan

## Context

The ledger entry `ci.branch-protection` asks that `main` take changes only through pull requests whose required checks passed, and that force pushes to it and its deletion be refused. On 2026-11-07 the API refused both branch protection and rulesets for the repository (HTTP 403, "Upgrade to GitHub Pro or make this repository public to enable this feature"). The repository is private and the account is on the Free plan. The owner chose to leave it as it is rather than buy GitHub Pro or make the repository public.

## Decision

- `main` has no branch protection while the repository is private on the Free plan.
- The gate is kept by other means:
  - the guard blocks agents' pushes to `main` and `master`, force pushes and `--no-verify` (ADR-0211);
  - CI runs Unit Tests, Lint and Audit, Frontend Checks and Commit Messages on every pull request (ADR-0190, ADR-0209);
  - only the owner merges (ADR-0208).
- `ci.branch-protection` stays in the ledger, not passing, for the day the plan or the repository's visibility changes.

## Consequences

- GitHub itself does not stop a merge with failing checks or a direct push to `main`: the owner looks at the checks of a pull request before merging it.
- Changing this needs GitHub Pro (or Team) or a public repository; then the protection of `ci.branch-protection` is a single API call.

## Considered options

- GitHub Pro: branch protection and rulesets for private repositories, for a monthly fee.
- A public repository: free protection, but the code, the issues and the history (including the passwords once published in `CLAUDE.md`, rotated by ADR-0226) become public.

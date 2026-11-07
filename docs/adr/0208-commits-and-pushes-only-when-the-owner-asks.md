---
status: accepted
date: 2026-11-07
area: process
decided-by: owner
supersedes: [ADR-0004]
---

# ADR-0208: Commits and pushes happen only when the owner asks, and the owner opens pull requests

## Context

The old instructions had changes committed on the device and pushed straight to main (ADR-0004). With several agents working in parallel, unrequested commits, branch switches or pushes could damage history or publish unreviewed work, and the owner wants to control main and what gets published. The owner's words on 2026-11-05 and 2026-11-06 were "I'll open the PR myself" and "Commit and push".

## Decision

- Agents commit and push only when the owner asks, on one branch per unit of work (for example `phase0-hardening`, `smtp-verify-tls`, `phase1-broken-flows`), and never to main or master.
- The owner opens and merges the pull requests; an agent drafts the commit message and the pull request text when asked.
- Agents of a parallel workflow never commit, push, stash, check out, reset or switch branches; their changes stay uncommitted, in their own worktree or their own files, for the lead agent to integrate (ADR-0210).
- Commit messages follow ADR-0209; an agent's commits end with a Co-Authored-By trailer.
- Pushes to main or master, merging a pull request with `gh pr merge`, force pushes, deleting remote branches and skipping the git hooks are blocked by the guard (ADR-0211).

## Consequences

- Finished work can wait uncommitted in the working tree until the owner asks; on 2026-11-07, for example, the SMB slice was still uncommitted on `phase1-broken-flows`.
- The owner reviews every change that reaches main.
- The device's checkout only receives deploys and cannot push: its GitHub token was removed on 2026-11-05, and its SSH key is to become a read-only deploy key, an open owner action (ADR-0053, ADR-0024). The guard also blocks a git push on the device (ADR-0211).

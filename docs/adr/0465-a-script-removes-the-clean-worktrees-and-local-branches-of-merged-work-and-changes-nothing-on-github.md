---
status: accepted
date: 2026-11-23
area: process
decided-by: agent
---

# ADR-0465: A script removes the clean worktrees and local branches of merged work, and changes nothing on GitHub

## Context

One feature per worktree and branch (ADR-0210) leaves a worktree and a local branch behind every
merged pull request. The lead removed them by hand at the end of a batch (170 worktrees on
2026-11-19, 33 on 2026-11-22, the notes of `process.prune-merged-branches`), where a slip can lose
uncommitted work. GitHub deletes the head branch of a merged pull request itself since its setting
`delete_branch_on_merge` is on, so only the local side is left to do.

## Decision

- `scripts/prune-merged-branches.py`, which the lead runs at the end of a batch from any checkout,
  first runs `git fetch --prune origin` (`--no-fetch` skips it) and is a dry run unless `--apply`:
  it lists what it would prune, remove and delete, and every worktree it keeps with the reason.
- Merged means an ancestor of `origin/main` (`git merge-base --is-ancestor`): a branch merged by a
  merge commit or a fast-forward, and a branch equal to main. A squashed or rebased pull request
  leaves its branch unmerged for git, so it is kept.
- A new branch, without a commit of its own since it was made, is an ancestor of `origin/main` too
  but is kept with its worktree, as "new, no commits yet": a feature's worktree whose work has not
  started. Its reflog holds only `branch: Created from ...`, or it records no commit (`commit`,
  `cherry-pick`, `revert`, `am`, a `merge` or `pull` that was no fast-forward) and no commit follows
  the branch's fork point, the newest entry of `origin/main`'s reflog that is an ancestor of the
  branch. Without a reflog the fork point alone decides, and a branch git cannot judge is kept. A
  branch that had commits and was merged or fast-forwarded into main counts as merged.
- A merged branch's worktree that is clean (`git status` with the untracked files) is removed with
  `git worktree remove`, never `--force`. A worktree whose folder is gone is kept on the same terms,
  and its record is pruned one by one with the same command, which accepts a missing folder:
  `git worktree prune` would drop every such record, and with a detached one the commits that only
  its HEAD holds.
- Then `git branch -d` deletes the branches merged into `origin/main` that no worktree left has
  checked out, those of the removed and pruned worktrees included. For that one command
  `origin/main` is the branch's upstream (`--config-env`), so git's own check that the branch is
  merged holds whatever HEAD the checkout has. An upstream of the branch's own comes first for
  git: one that is gone after the fetch, as GitHub deletes a merged head, is unset before, and one
  still there is what git checks against.
- Kept, and named with the reason: the main checkout, the checkout the script runs in, a locked
  worktree, one on a detached HEAD (kept on purpose, such as the source of copied venvs), one of
  `main` or of a branch not merged, new or without a commit, one with changes or untracked files,
  one with a handoff in its git-ignored `tmp/handoff`, and one with another worktree inside its
  folder, which `git status` does not see under an ignored path. The branch `main` is never
  deleted.
- Nothing on the remote: no push and no remote branch deleted. GitHub deletes merged heads, and any
  other remote deletion stays the lead's (ADR-0208).
- `supervisor/tests/test_prune_merged_branches.py` checks it on throwaway repositories, with a
  folder as `origin`.

## Consequences

- A feature's worktree survives the run before its first commit, also while other features are
  in progress. A branch whose reflog expired (`gc.reflogExpire`) and that was fast-forwarded into
  main has its tip in `origin/main`'s reflog, so it looks new and stays: it is removed by hand.
- Every git-ignored file of a removed worktree goes with it: its venvs, `node_modules`, `tmp/`,
  `.standin/` and a `dev.env` of its own. Only a handoff keeps it.
- A branch merged into `origin/main` without a worktree is deleted too, as a local bookmark; its
  commits are on `origin/main`.
- A step that git refuses under `--apply` is named on a `failed` line and the run exits 1; the
  branch of a worktree that stays is not deleted.

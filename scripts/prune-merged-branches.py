#!/usr/bin/env python3
"""Remove the local worktrees and branches of work merged into origin/main, at the end of a batch
(ADR-0465, ADR-0210).

    scripts/prune-merged-branches.py [--apply] [--no-fetch]

From any checkout of the repository. It first runs git fetch --prune origin (--no-fetch skips it),
then lists, against origin/main:

    prune   the record of a merged branch's worktree whose folder is gone
    remove  the clean worktree of a branch merged into origin/main
    delete  a local branch merged into origin/main that no worktree left has checked out, those
            of the removed and pruned worktrees included
    keep    every other worktree, with the reason

A dry run by default: it changes nothing but the fetch, and --apply does what it lists.

Merged means an ancestor of origin/main: a branch merged by a merge commit or a fast-forward, and
a branch equal to main. A squashed or rebased pull request leaves its branch unmerged for git:
kept. A new branch, without a commit of its own since it was made, is an ancestor of origin/main
too, and is kept with its worktree: a feature's worktree that has no commit yet. Its reflog says
so: it holds only the entry "branch: Created from ...", or it records no commit (git commit,
cherry-pick, revert, am, or a merge or pull that was no fast-forward) and no commit follows the
branch's fork point: the newest entry of origin/main's reflog that is an ancestor of the branch,
where the branch left origin/main. A branch without a reflog is judged by that fork point alone (a
fast-forwarded one then looks new), and one that git cannot judge is kept.

Kept: the main checkout, the checkout this runs in, a locked worktree, one on a detached HEAD, one
of main or of a branch not merged or new, one with changes or untracked files (git status), one that
holds a handoff in its git-ignored tmp/handoff, and one with another worktree inside its folder.
git worktree remove deletes a worktree's folder with every ignored file in it. A worktree whose
folder is gone is kept on the same terms, its record pruned one by one with git worktree remove:
git worktree prune would drop every such record, and with a detached one the commits that only
its HEAD holds. The branch main is never deleted. Nothing on the remote changes: GitHub deletes
the head branch of a merged pull request itself (delete_branch_on_merge), and remote branches are
the lead's to delete (ADR-0208).

git checks each step once more. git worktree remove, without --force, refuses a worktree with
changes or untracked files. git branch -d refuses a branch that a worktree uses, or that is not
merged into its upstream, or into HEAD without one: for that one command origin/main is the
branch's upstream (--config-env), so the check holds whatever HEAD this checkout has. An upstream
of the branch's own comes first for git: the one GitHub deleted with the merged head (gone after
the fetch) is unset before, and one still there is git's check.

Exit status: 0; 1 when git refused a step of --apply, named on a line that starts with "failed";
2 on a usage error, outside a repository, without origin/main or when the fetch fails, before any
change.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import NamedTuple

REMOTE = "origin"
MAIN = "main"
TARGET = f"refs/remotes/{REMOTE}/{MAIN}"
HANDOFF = os.path.join("tmp", "handoff")
# git branch -d reads the branch's upstream from these, through --config-env.
UPSTREAM_ENV = {"UNOS_PRUNE_REMOTE": REMOTE, "UNOS_PRUNE_MERGE": f"refs/heads/{MAIN}"}
CHANGES = "it has changes or untracked files"
NEW = "new, no commits yet"
CREATED = "branch: Created from "
# The reflog entries of a branch that record a commit of its own; a merge or pull counts unless it
# was a fast-forward.
COMMITS = ("commit", "cherry-pick", "revert", "am", "merge", "pull")


class Stop(Exception):
    pass


class Worktree(NamedTuple):
    path: str
    branch: str | None  # None on a detached HEAD and in a bare repository
    bare: bool
    locked: bool
    prunable: bool

    def label(self) -> str:
        what = f"branch {self.branch}" if self.branch else "bare" if self.bare else "detached"
        return f"worktree {self.path} ({what})"


def git(*args: str, cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env=None if env is None else {**os.environ, **env})


def must(*args: str) -> str:
    result = git(*args)
    if result.returncode != 0:
        raise Stop(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def worktrees() -> list[Worktree]:
    """git worktree list, the main checkout first."""
    trees = []
    for record in must("worktree", "list", "--porcelain", "-z").split("\0\0"):
        fields = dict(field.partition(" ")[::2] for field in record.split("\0") if field)
        if "worktree" not in fields:
            continue
        ref = fields.get("branch", "")
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else None
        trees.append(Worktree(path=fields["worktree"], branch=branch, bare="bare" in fields,
                              locked="locked" in fields, prunable="prunable" in fields))
    if not trees:
        raise Stop("git worktree list named no worktree")
    return trees


def merged(branch: str, target: str) -> bool | None:
    """Whether the branch is an ancestor of target; None when git cannot tell (a branch without a
    commit, a broken ref)."""
    result = git("merge-base", "--is-ancestor", f"refs/heads/{branch}", target)
    return {0: True, 1: False}.get(result.returncode)


def is_new(branch: str) -> bool:
    """Whether the branch has no commit of its own since it was made; True when git cannot tell."""
    log = git("reflog", "show", "--format=%gs", f"refs/heads/{branch}", "--")
    if log.returncode != 0:
        return True
    entries = log.stdout.splitlines()
    if len(entries) == 1 and entries[0].startswith(CREATED):
        return True
    if any(entry.startswith(COMMITS) and "Fast-forward" not in entry for entry in entries):
        return False
    fork = fork_point(branch)
    if fork is None:
        return True
    own = git("rev-list", "--count", f"{fork}..refs/heads/{branch}")
    return own.returncode != 0 or own.stdout.strip() == "0"


def fork_point(branch: str) -> str | None:
    """The newest entry of origin/main's reflog that is an ancestor of the branch. git merge-base
    --fork-point finds none for a branch that origin/main contains, unless its tip is an entry."""
    entries = git("reflog", "show", "--format=%H", TARGET, "--")
    ancestors = git("rev-list", f"refs/heads/{branch}", "--")
    if entries.returncode != 0 or ancestors.returncode != 0:
        return None
    known = set(ancestors.stdout.split())
    return next((sha for sha in entries.stdout.split() if sha in known), None)


def holds_handoff(path: str) -> bool:
    try:
        with os.scandir(os.path.join(path, HANDOFF)) as entries:
            return any(True for _ in entries)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # unreadable, or not a folder: kept, to be looked at


def why_kept(tree: Worktree, target: str, here: str | None, others: list[str]) -> str | None:
    """None for a worktree that may go, else the reason it stays. `others` are the real paths of
    the other worktrees."""
    if tree.locked:
        return "it is locked"
    real = os.path.realpath(tree.path)
    if tree.prunable and os.path.lexists(tree.path):
        return "git lists it as prunable, but its folder is there"
    if here is not None and real == here:
        return "this run's checkout"
    if tree.branch is None:
        return "a detached HEAD"
    if tree.branch == MAIN:
        return f"the branch {MAIN}"
    is_merged = merged(tree.branch, target)
    if is_merged is None:
        return f"git cannot compare its branch with {REMOTE}/{MAIN}"
    if not is_merged:
        return f"not merged into {REMOTE}/{MAIN}"
    if tree.prunable:
        return NEW if is_new(tree.branch) else None
    if any(other.startswith(real + os.sep) for other in others):
        return "another worktree is inside its folder"
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=tree.path,
                 env={"GIT_OPTIONAL_LOCKS": "0"})
    if status.returncode != 0:
        return f"git status failed: {status.stderr.strip()}"
    if status.stdout:
        return CHANGES
    if holds_handoff(tree.path):
        return f"it holds a handoff in {HANDOFF}"
    if is_new(tree.branch):
        return NEW
    return None


class Plan(NamedTuple):
    prune: list[Worktree]
    remove: list[Worktree]
    delete: list[str]
    keep: list[tuple[Worktree, str]]


def make_plan(target: str, here: str | None) -> Plan:
    main, *linked = worktrees()
    paths = [os.path.realpath(tree.path) for tree in (main, *linked)]
    prune, remove, keep = [], [], [(main, "the main checkout")]
    for tree in sorted(linked, key=lambda tree: tree.path):
        real = os.path.realpath(tree.path)
        reason = why_kept(tree, target, here, [path for path in paths if path != real])
        if reason is not None:
            keep.append((tree, reason))
        elif tree.prunable:
            prune.append(tree)
        else:
            remove.append(tree)
    checked_out = {tree.branch for tree, _ in keep if tree.branch}
    branches = must("for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads").splitlines()
    delete = [branch for branch in sorted(branches)
              if branch != MAIN and branch not in checked_out and merged(branch, target) and not is_new(branch)]
    return Plan(prune, remove, delete, keep)


def delete_branch(branch: str) -> subprocess.CompletedProcess:
    upstream = git("for-each-ref", "--format=%(upstream:track,nobracket)", f"refs/heads/{branch}")
    if upstream.stdout.strip() == "gone":
        # GitHub deleted the head with the merge, and the fetch its remote-tracking branch: git
        # branch -d would check against HEAD instead of origin/main.
        unset = git("branch", "--unset-upstream", branch)
        if unset.returncode != 0:
            return unset
    # --config-env, not -c: a branch name may hold "=", and git splits --config-env at the last one.
    return git(f"--config-env=branch.{branch}.remote=UNOS_PRUNE_REMOTE",
               f"--config-env=branch.{branch}.merge=UNOS_PRUNE_MERGE", "branch", "-d", branch, env=UPSTREAM_ENV)


def apply(plan: Plan) -> int:
    failures = 0
    delete = list(plan.delete)
    for verb, past, trees, suffix in (("prune", "pruned", plan.prune, ": its folder is gone"),
                                      ("remove", "removed", plan.remove, "")):
        for tree in trees:
            # Without --force: git refuses a locked worktree, and one with changes or untracked files.
            result = git("worktree", "remove", tree.path)
            if result.returncode == 0:
                print(f"{past} {tree.label()}{suffix}")
            else:
                failures += 1
                print(f"failed to {verb} {tree.label()}: {result.stderr.strip()}")
                delete = [branch for branch in delete if branch != tree.branch]
    for branch in delete:
        result = delete_branch(branch)
        if result.returncode == 0:
            print(f"deleted branch {branch}")
        else:
            failures += 1
            print(f"failed to delete branch {branch}: {result.stderr.strip()}")
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="prune-merged-branches.py",
                                     description="Remove the clean worktrees and the local branches of work merged "
                                                 f"into {REMOTE}/{MAIN}; a dry run unless --apply.")
    parser.add_argument("--apply", action="store_true", help="remove and delete what the dry run lists")
    parser.add_argument("--no-fetch", action="store_true", help=f"skip git fetch --prune {REMOTE}")
    args = parser.parse_args(argv)

    try:
        if git("rev-parse", "--git-dir").returncode != 0:
            raise Stop("not in a git repository")
        if not args.no_fetch:
            fetch = git("fetch", "--prune", REMOTE)
            if fetch.returncode != 0:
                raise Stop(f"git fetch --prune {REMOTE} failed: {fetch.stderr.strip()}")
        target = git("rev-parse", "--verify", "--quiet", f"{TARGET}^{{commit}}").stdout.strip()
        if not target:
            raise Stop(f"{REMOTE}/{MAIN} does not exist: nothing to compare the branches with")
        top = git("rev-parse", "--show-toplevel")
        here = os.path.realpath(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else None
        plan = make_plan(target, here)
    except Stop as stop:
        print(f"prune-merged-branches.py: {stop}", file=sys.stderr)
        return 2

    if args.apply:
        print(f"Against {REMOTE}/{MAIN} {target[:12]}:")
        failures = apply(plan)
    else:
        print(f"Dry run against {REMOTE}/{MAIN} {target[:12]}: --apply does what it lists.")
        for tree in plan.prune:
            print(f"prune {tree.label()}: its folder is gone")
        for tree in plan.remove:
            print(f"remove {tree.label()}")
        for branch in plan.delete:
            print(f"delete branch {branch}")
        failures = 0
    for tree, reason in plan.keep:
        print(f"keep {tree.label()}: {reason}")
    if not (plan.prune or plan.remove or plan.delete):
        print("Nothing to prune, remove or delete.")
    changed = sum(reason == CHANGES for _, reason in plan.keep)
    if changed:
        them = "them" if changed > 1 else "it"
        print(f"{changed} worktree{'s' if changed > 1 else ''} with changes kept: "
              f"commit {them}, or remove {them} by hand")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

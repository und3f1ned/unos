"""scripts/prune-merged-branches.py, which the lead runs at the end of a batch (ADR-0465, ADR-0210):
it removes the clean local worktrees of branches merged into origin/main and deletes their local
branches, and keeps everything else.

Every test builds a throwaway batch: a bare "origin" that stands in for GitHub, a "hub" clone that
merges pull requests on it, and the main checkout with its worktrees. git runs without the user's
and the system's configuration and without the GIT_* variables of an outer git, and nothing
reaches the network: origin is a folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "prune-merged-branches.py"


def _env(home: Path) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@example.org", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
    return env


@dataclass
class Batch:
    root: Path
    env: dict
    origin: Path
    hub: Path
    main: Path

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=self.env,
                                timeout=60)
        assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
        return result.stdout

    def worktree(self, name: str) -> Path:
        return self.root / f"unos-{name}"

    def feature(self, name: str, base: str = "origin/main") -> Path:
        """A feature's worktree as ADR-0210 makes it, with one commit, pushed as its pull request."""
        path = self.worktree(name)
        self.git(self.main, "worktree", "add", "--no-track", "-b", name, str(path), base)
        (path / f"{name}.txt").write_text(f"{name}\n")
        self.git(path, "add", f"{name}.txt")
        self.git(path, "commit", "-m", f"feat: {name}")
        self.git(path, "push", "origin", name)
        return path

    def merge(self, name: str, how: str = "--no-ff") -> None:
        """The pull request of `name` merged on GitHub: by a merge commit, a fast-forward or a squash."""
        self.git(self.hub, "fetch", "origin")
        self.git(self.hub, "merge", how, f"origin/{name}", *(["-m", f"Merge {name}"] if how == "--no-ff" else []))
        if how == "--squash":
            self.git(self.hub, "commit", "-m", f"feat: {name} (squashed)")
        self.git(self.hub, "push", "origin", "main")

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd or self.main, capture_output=True,
                              text=True, env=self.env, timeout=120)

    def branches(self) -> set[str]:
        return set(self.git(self.main, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())

    def worktrees(self) -> set[Path]:
        lines = self.git(self.main, "worktree", "list", "--porcelain").splitlines()
        return {Path(line.split(" ", 1)[1]).resolve() for line in lines if line.startswith("worktree ")}

    def state(self) -> tuple:
        """What a dry run must leave as it was: the worktrees' records, the local and remote-tracking
        branches, and the remote's branches."""
        return (self.git(self.main, "worktree", "list", "--porcelain"),
                self.git(self.main, "for-each-ref", "--format=%(refname) %(objectname)"),
                self.git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)"))


@pytest.fixture
def batch(tmp_path) -> Batch:
    """The end of a batch:

    merged            merged by a merge commit, clean: removed, its branch deleted
    ff                merged by a fast-forward, so equal to origin/main, clean: removed
    equal             made from origin/main after the merges, no commit of its own: new, kept
    fresh             made from an older main commit, no commit of its own: new, kept
    gone              merged, its folder deleted by hand: pruned, its branch deleted
    changed           merged, a tracked file changed: kept
    untracked         merged, an untracked file: kept
    handoff           merged, a handoff in its git-ignored tmp/handoff: kept
    locked            merged, locked with git worktree lock: kept
    open              not merged: kept
    squashed          merged by a squash, so not an ancestor of origin/main: kept
    detached          a worktree detached at a merged commit: kept
    no-worktree       merged, a local branch without a worktree: deleted
    open-no-worktree  not merged, without a worktree: kept
    new-no-worktree   made from an older main commit, no commit of its own, no worktree: kept

    The main checkout is detached at the first commit, so that HEAD there contains none of the
    merges, and its local main is behind origin/main: main is kept all the same.
    """
    root = tmp_path / "work"
    root.mkdir()
    env = _env(tmp_path / "home")
    b = Batch(root=root.resolve(), env=env, origin=root.resolve() / "origin.git", hub=root.resolve() / "hub",
              main=root.resolve() / "repo")
    b.git(root, "init", "--quiet", "--bare", "-b", "main", str(b.origin))
    b.git(root, "clone", "--quiet", str(b.origin), str(b.main))
    (b.main / ".gitignore").write_text("tmp/\n")
    (b.main / "README.md").write_text("UNOS\n")
    b.git(b.main, "add", ".")
    b.git(b.main, "commit", "-m", "chore: start")
    b.git(b.main, "push", "origin", "main")
    b.git(root, "clone", "--quiet", str(b.origin), str(b.hub))

    for name in ("merged", "gone", "changed", "untracked", "handoff", "locked", "no-worktree"):
        b.feature(name)
        b.merge(name)
    b.feature("squashed")
    b.merge("squashed", how="--squash")
    b.feature("open")
    b.git(b.main, "branch", "--no-track", "open-no-worktree", "open")
    b.git(b.main, "fetch", "origin")
    b.feature("ff")
    b.merge("ff", how="--ff-only")
    b.git(b.main, "fetch", "origin")

    b.git(b.main, "worktree", "add", "--no-track", "-b", "equal", str(b.worktree("equal")), "origin/main")
    b.git(b.main, "worktree", "add", "--no-track", "-b", "fresh", str(b.worktree("fresh")), "main")
    b.git(b.main, "branch", "--no-track", "new-no-worktree", "main")
    b.git(b.main, "worktree", "add", "--detach", str(b.worktree("detached")), "origin/main")
    shutil.rmtree(b.worktree("gone"))
    (b.worktree("changed") / "README.md").write_text("changed\n")
    (b.worktree("untracked") / "notes.txt").write_text("not yet added\n")
    (b.worktree("handoff") / "tmp" / "handoff").mkdir(parents=True)
    (b.worktree("handoff") / "tmp" / "handoff" / "2026-11-23-next.md").write_text("# Handoff\n")
    b.git(b.main, "worktree", "lock", str(b.worktree("locked")))
    b.git(b.main, "worktree", "remove", str(b.worktree("no-worktree")))
    b.git(b.main, "checkout", "--quiet", "--detach", "main")
    return b


# In the order of the output: worktrees by path, branches by name.
REMOVED = ("ff", "merged")
KEPT = ("changed", "equal", "fresh", "handoff", "locked", "open", "squashed", "untracked")
DELETED = ("ff", "gone", "merged", "no-worktree")
KEPT_BRANCHES = ("main", "changed", "equal", "fresh", "untracked", "handoff", "locked", "open", "squashed",
                 "open-no-worktree", "new-no-worktree")


def lines(result: subprocess.CompletedProcess) -> list[str]:
    return result.stdout.splitlines()


def test_a_dry_run_lists_what_apply_would_do_and_changes_nothing(batch):
    before = batch.state()

    result = batch.run("--no-fetch")

    assert result.returncode == 0, result.stderr
    out = lines(result)
    assert out[0].startswith("Dry run against origin/main ")
    assert [line for line in out if line.startswith(("remove ", "prune ", "delete "))] == [
        f"prune worktree {batch.worktree('gone')} (branch gone): its folder is gone",
        *(f"remove worktree {batch.worktree(name)} (branch {name})" for name in REMOVED),
        *(f"delete branch {name}" for name in DELETED),
    ]
    assert batch.state() == before
    assert batch.worktree("merged").is_dir()


def test_apply_removes_the_clean_worktrees_of_merged_branches_and_deletes_their_branches(batch):
    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    out = lines(result)
    for name in REMOVED:
        assert f"removed worktree {batch.worktree(name)} (branch {name})" in out
        assert not batch.worktree(name).exists()
    assert f"pruned worktree {batch.worktree('gone')} (branch gone): its folder is gone" in out
    for name in DELETED:
        assert f"deleted branch {name}" in out
    assert batch.branches() == set(KEPT_BRANCHES)
    assert batch.worktrees() == {batch.main, *(batch.worktree(name) for name in (*KEPT, "detached"))}


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
def test_everything_else_is_kept_and_named_with_its_reason(batch, apply):
    result = batch.run(*(["--apply"] if apply else []), "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    kept = [line for line in lines(result) if line.startswith("keep ")]
    assert kept == [
        f"keep worktree {batch.main} (detached): the main checkout",
        f"keep worktree {batch.worktree('changed')} (branch changed): it has changes or untracked files",
        f"keep worktree {batch.worktree('detached')} (detached): a detached HEAD",
        f"keep worktree {batch.worktree('equal')} (branch equal): new, no commits yet",
        f"keep worktree {batch.worktree('fresh')} (branch fresh): new, no commits yet",
        f"keep worktree {batch.worktree('handoff')} (branch handoff): it holds a handoff in tmp/handoff",
        f"keep worktree {batch.worktree('locked')} (branch locked): it is locked",
        f"keep worktree {batch.worktree('open')} (branch open): not merged into origin/main",
        f"keep worktree {batch.worktree('squashed')} (branch squashed): not merged into origin/main",
        f"keep worktree {batch.worktree('untracked')} (branch untracked): it has changes or untracked files",
    ]
    assert lines(result)[-1] == "2 worktrees with changes kept: commit them, or remove them by hand"
    # Their work is where it was.
    assert (batch.worktree("changed") / "README.md").read_text() == "changed\n"
    assert (batch.worktree("untracked") / "notes.txt").read_text() == "not yet added\n"
    assert (batch.worktree("handoff") / "tmp" / "handoff" / "2026-11-23-next.md").is_file()
    assert set(KEPT_BRANCHES) <= batch.branches()


def test_nothing_changes_on_the_remote_nor_in_the_remote_tracking_branches(batch):
    remote = batch.git(batch.origin, "for-each-ref", "--format=%(refname) %(objectname)")
    tracking = batch.git(batch.main, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes")

    assert batch.run("--apply", "--no-fetch").returncode == 0

    assert batch.git(batch.origin, "for-each-ref", "--format=%(refname) %(objectname)") == remote
    assert batch.git(batch.main, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes") == tracking
    assert "refs/heads/merged" in remote  # the remote's merged branches are GitHub's to delete


def test_it_runs_from_a_linked_worktree_whose_head_holds_none_of_the_merges(batch):
    result = batch.run("--apply", "--no-fetch", cwd=batch.worktree("open"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert batch.branches() == set(KEPT_BRANCHES)


def test_the_checkout_it_runs_in_is_kept(batch):
    result = batch.run("--apply", "--no-fetch", cwd=batch.worktree("merged") / ".")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"keep worktree {batch.worktree('merged')} (branch merged): this run's checkout" in lines(result)
    assert batch.worktree("merged").is_dir()
    assert "merged" in batch.branches()
    assert not batch.worktree("ff").exists()


def test_it_fetches_with_prune_first(batch):
    """A pull request merged on GitHub after the last fetch, whose head branch GitHub deleted."""
    batch.feature("late")
    batch.merge("late")
    batch.git(batch.hub, "push", "origin", "--delete", "late")

    stale = batch.run("--no-fetch")
    assert f"keep worktree {batch.worktree('late')} (branch late): not merged into origin/main" in lines(stale)

    result = batch.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"remove worktree {batch.worktree('late')} (branch late)" in lines(result)
    assert "origin/late" not in batch.git(batch.main, "branch", "--remotes")
    assert batch.worktree("late").is_dir()  # a dry run


def test_a_failed_fetch_stops_the_run_before_any_change(batch):
    batch.git(batch.main, "remote", "set-url", "origin", str(batch.root / "nowhere.git"))
    before = batch.state()

    result = batch.run("--apply")

    assert result.returncode == 2
    assert "git fetch --prune origin failed" in result.stderr
    assert result.stdout == ""
    assert batch.state() == before


def test_outside_a_repository_it_stops(tmp_path, batch):
    outside = tmp_path / "outside"
    outside.mkdir()

    result = batch.run("--no-fetch", cwd=outside)

    assert result.returncode == 2
    assert "not in a git repository" in result.stderr


def test_without_origin_main_it_stops(batch):
    batch.git(batch.main, "update-ref", "-d", "refs/remotes/origin/main")

    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 2
    assert "origin/main" in result.stderr
    assert batch.worktree("merged").is_dir()


def test_an_unknown_argument_is_a_usage_error(batch):
    result = batch.run("--force")

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_a_step_that_git_refuses_is_reported_and_fails_the_run(batch):
    """git worktree remove checks the worktree once more: here a change appears after the run's
    own check, as when another session writes to it, through a stub git that changes a file first."""
    stub = batch.root / "bin"
    stub.mkdir()
    real = shutil.which("git", path=batch.env["PATH"])
    readme = batch.worktree("merged") / "README.md"
    (stub / "git").write_text(
        "#!/bin/sh\n"
        f'case "$*" in *"worktree remove"*unos-merged*) echo dirty >> "{readme}";; esac\n'
        f'exec "{real}" "$@"\n'
    )
    (stub / "git").chmod(0o755)
    batch.env["PATH"] = f"{stub}{os.pathsep}{batch.env['PATH']}"

    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 1
    failed = [line for line in lines(result) if line.startswith("failed ")]
    assert len(failed) == 1
    assert failed[0].startswith(f"failed to remove worktree {batch.worktree('merged')} (branch merged): ")
    assert "deleted branch merged" not in lines(result)
    assert batch.worktree("merged").is_dir() and "merged" in batch.branches()
    assert not batch.worktree("ff").exists()


def test_a_worktree_whose_folder_is_gone_is_pruned_only_on_the_terms_of_the_others(batch):
    """git worktree prune would drop every such record: with a detached one, the commits that only
    its HEAD holds would be left to git gc."""
    detached = batch.worktree("gone-detached")
    batch.git(batch.main, "worktree", "add", "--detach", str(detached), "origin/main")
    (detached / "work.txt").write_text("work\n")
    batch.git(detached, "add", "work.txt")
    batch.git(detached, "commit", "-m", "feat: only here")
    commit = batch.git(detached, "rev-parse", "HEAD").strip()
    batch.feature("gone-open")
    shutil.rmtree(detached)
    shutil.rmtree(batch.worktree("gone-open"))

    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"keep worktree {detached} (detached): a detached HEAD" in lines(result)
    assert (f"keep worktree {batch.worktree('gone-open')} (branch gone-open): not merged into origin/main"
            in lines(result))
    assert f"pruned worktree {batch.worktree('gone')} (branch gone): its folder is gone" in lines(result)
    assert {detached, batch.worktree("gone-open")} <= batch.worktrees()
    assert commit in batch.git(batch.main, "worktree", "list", "--porcelain")
    assert "gone-open" in batch.branches() and "gone" not in batch.branches()


def test_a_worktree_with_another_one_inside_its_folder_is_kept(batch):
    """git status of the outer worktree does not see one inside its git-ignored tmp/, and git
    worktree remove would delete it with the outer folder."""
    batch.feature("outer")
    batch.merge("outer")
    batch.git(batch.main, "fetch", "origin")
    inner = batch.worktree("outer") / "tmp" / "inner"
    batch.git(batch.main, "worktree", "add", "--no-track", "-b", "inner", str(inner), "origin/main")
    (inner / "draft.txt").write_text("draft\n")

    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (f"keep worktree {batch.worktree('outer')} (branch outer): another worktree is inside its folder"
            in lines(result))
    assert f"keep worktree {inner} (branch inner): it has changes or untracked files" in lines(result)
    assert (inner / "draft.txt").read_text() == "draft\n"
    assert {"outer", "inner"} <= batch.branches()


def test_a_branch_whose_upstream_github_deleted_with_the_merge_is_deleted(batch):
    """A branch pushed with -u: after the merge GitHub deletes its head and the fetch its
    remote-tracking branch, and git branch -d would compare it with HEAD, which holds no merge."""
    batch.feature("tracked")
    batch.git(batch.worktree("tracked"), "branch", "--set-upstream-to", "origin/tracked")
    batch.merge("tracked")
    batch.git(batch.hub, "push", "origin", "--delete", "tracked")

    result = batch.run("--apply")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"removed worktree {batch.worktree('tracked')} (branch tracked)" in lines(result)
    assert "deleted branch tracked" in lines(result)
    assert "tracked" not in batch.branches()
    assert batch.git(batch.main, "config", "--get-regexp", "^branch\\.").split() == [
        "branch.main.remote", "origin", "branch.main.merge", "refs/heads/main"]


def test_a_branch_without_a_commit_is_kept_and_the_run_goes_on(batch):
    batch.git(batch.main, "worktree", "add", "--orphan", "-b", "empty", str(batch.worktree("empty")))

    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (f"keep worktree {batch.worktree('empty')} (branch empty): git cannot compare its branch with origin/main"
            in lines(result))
    assert batch.worktree("empty").is_dir()
    assert f"removed worktree {batch.worktree('merged')} (branch merged)" in lines(result)


def test_without_a_reflog_a_branch_is_new_when_nothing_follows_its_fork_point(batch):
    """A reflog expires (gc.reflogExpire): then the commits between the branch's fork point from
    origin/main, which origin/main's own reflog gives, and the branch tell."""
    logs = batch.main / ".git" / "logs" / "refs" / "heads"
    (logs / "fresh").unlink()
    (logs / "merged").unlink()

    result = batch.run("--apply", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"keep worktree {batch.worktree('fresh')} (branch fresh): new, no commits yet" in lines(result)
    assert f"removed worktree {batch.worktree('merged')} (branch merged)" in lines(result)
    assert "deleted branch merged" in lines(result)

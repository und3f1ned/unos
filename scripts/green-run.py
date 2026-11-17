#!/usr/bin/env python3
"""The green run: the state of the working tree in which make check or make check-fast last passed
(ADR-0216, ADR-0383).

    scripts/green-run.py state          print the state of the working tree
    scripts/green-run.py check          exit 0 when the tree is in the state recorded as green
    scripts/green-run.py record STATE   record STATE as green, if the tree is still in it
    scripts/green-run.py forget         remove the record

The state is one SHA-256 of HEAD, git status, git diff (staged and not) and the names and
contents of the untracked files that git does not ignore. The record is tmp/stop-check/green of
the work tree that the current directory belongs to (tmp/ is git-ignored). make check and make
check-fast record their state when they pass, and record compares it with the tree once more: a
run during which the tree changed records nothing. The Stop hook (.claude/hooks/stop-check.sh)
and the pre-commit hook (scripts/git-hooks/pre-commit) skip make check-fast for a tree in the
recorded state.

git takes no optional locks here: the hooks run beside other git commands, and git commit -a and
a commit of paths hold the index's lock while the pre-commit hook runs. The diffs are git's own:
an external diff driver or a textconv filter could hide a change. Exit status: 0, 1 when check
finds another state or record finds that the tree changed, 2 outside a git work tree, when a git
command fails (no state then) or on a usage error.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RECORD = Path("tmp") / "stop-check" / "green"
USAGE = "usage: green-run.py state | check | record STATE | forget"


class GitFailed(Exception):
    pass


def git(*args: str, may_fail: bool = False) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True, check=False,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if result.returncode != 0 and not may_fail:
        raise GitFailed(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


DIFF = ("--binary", "--no-ext-diff", "--no-textconv", "--no-color")


def state() -> str:
    digest = hashlib.sha256()
    # A repository without a commit has no HEAD.
    digest.update(git("rev-parse", "--verify", "--quiet", "HEAD", may_fail=True) + b"\0")
    for args in (("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                 ("diff", *DIFF), ("diff", "--cached", *DIFF)):
        digest.update(git(*args) + b"\0")
    for name in git("ls-files", "-z", "--others", "--exclude-standard").split(b"\0"):
        if not name or name.startswith(b"tmp/stop-check/"):
            continue
        digest.update(name + b"\0")
        try:
            if os.path.islink(name):
                digest.update(os.fsencode(os.readlink(name)))
            elif os.path.isfile(name):
                with open(name, "rb") as handle:
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(block)
        except OSError:
            digest.update(b"?")
        digest.update(b"\0")
    return digest.hexdigest()


def recorded() -> str:
    try:
        return RECORD.read_text().strip()
    except OSError:
        return ""


def record(expected: str) -> int:
    if state() != expected:
        print(f"green-run.py: the working tree changed during the run: no green run recorded in {RECORD}",
              file=sys.stderr)
        return 1
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=RECORD.parent, prefix=".green.")
    with os.fdopen(fd, "w") as handle:
        handle.write(expected + "\n")
    os.replace(temporary, RECORD)
    return 0


def main(argv: list[str]) -> int:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if top.returncode != 0:
        print("green-run.py: not a git work tree", file=sys.stderr)
        return 2
    os.chdir(top.stdout.rstrip("\n"))
    try:
        if argv == ["state"]:
            print(state())
            return 0
        if argv == ["check"]:
            return 0 if recorded() == state() else 1
        if len(argv) == 2 and argv[0] == "record" and re.fullmatch(r"[0-9a-f]{64}", argv[1]):
            return record(argv[1])
    except GitFailed as error:
        print(f"green-run.py: {error}", file=sys.stderr)
        return 2
    if argv == ["forget"]:
        RECORD.unlink(missing_ok=True)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

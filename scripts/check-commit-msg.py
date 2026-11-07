#!/usr/bin/env python3
"""Check commit messages against the commit convention of this repository (ADR-0209).

    scripts/check-commit-msg.py FILE           one message, as the commit-msg hook passes it
    scripts/check-commit-msg.py --range A..B   the commits of A..B, as CI's commits job runs it
    options: --feature-list FILE (default: feature-list.json of the checkout)

A message is Conventional Commits in English:

    type(scope)!: subject

    Optional body, after a blank line.

    Feature: <feature-list id>
    Co-Authored-By: Name <email>

- type: feat, fix, docs, test, refactor, perf, build, ci, chore, revert or style; the scope is
  optional (lowercase letters, digits and . _ / - ; several separated by commas); ! marks a
  breaking change.
- The first line (type, scope and subject together) has at most 72 characters and does not end
  with a period.
- A blank line separates it from the body. Trailers such as Co-Authored-By are allowed; a
  `Feature:` trailer names an existing id of feature-list.json.
- English only: no Cyrillic letters (ADR-0200).

Lines starting with '#' and everything below git's scissors line are ignored, as git ignores
them. Merge messages written by git (`Merge branch ...`) pass; `fixup!`, `squash!` and `amend!`
commits pass the hook, but not the range check: squash them before they are pushed, or ask the
owner to squash-merge the pull request (pushed commits are never rewritten, ADR-0208).
In range mode only the commits whose tree contains this script are checked, so commits made
before the convention existed are left alone; merge commits are skipped.

Exit codes: 0 every message passes, 1 a message fails, 2 a usage or git error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TYPES = ("feat", "fix", "docs", "test", "refactor", "perf", "build", "ci", "chore", "revert", "style")
MAX_FIRST_LINE = 72
HEADER = re.compile(
    r"(?P<type>[a-z]+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?(?P<colon>:)?(?P<space> ?)(?P<subject>.*)"
)
SCOPE = re.compile(r"[a-z0-9][a-z0-9._/-]*(?:,[a-z0-9][a-z0-9._/-]*)*")
FEATURE_ID = re.compile(r"[a-z]+\.[a-z0-9]+(?:-[a-z0-9]+)*")
GIT_MERGE = re.compile(r"Merge (branch|branches|remote-tracking branch|tag|commit|pull request) .+")
AUTOSQUASH = re.compile(r"(fixup|squash|amend)! ")
CYRILLIC = re.compile("[\u0400-\u04ff]")
SCISSORS = re.compile(r"^# -+ >8 -+$")
CHECKER_PATH = "scripts/check-commit-msg.py"
EXAMPLE = "fix(shares): keep the setgid bit on new share folders"


def cleaned(message: str) -> list:
    """The lines of a message as git stores it: comments and the scissors part removed,
    trailing whitespace and the blank lines at both ends stripped."""
    lines = []
    for line in message.splitlines():
        if SCISSORS.match(line):
            break
        if line.startswith("#"):
            continue
        lines.append(line.rstrip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def problems(message: str, feature_ids=None, autosquash_ok: bool = True) -> list:
    """What is wrong with a message; empty when it passes. feature_ids: the ids of
    feature-list.json, or None when that file is not available."""
    lines = cleaned(message)
    if not lines:
        return ["the message is empty"]
    first = lines[0]
    if GIT_MERGE.fullmatch(first):
        return []
    found = []
    if AUTOSQUASH.match(first):
        if not autosquash_ok:
            return [f"'{first.split('!')[0]}!' commit: squash it into its commit before it is pushed "
                    "(git rebase --autosquash), or ask the owner to squash-merge the pull request"]
        first = AUTOSQUASH.sub("", first, count=1)
    header = HEADER.fullmatch(first)
    if not header or header.group("type") not in TYPES or not header.group("colon"):
        found.append(f"the first line must be `type(scope)!: subject` with type one of {', '.join(TYPES)}"
                     f" (scope and ! optional), for example `{EXAMPLE}`")
    else:
        scope = header.group("scope")
        if scope is not None and not SCOPE.fullmatch(scope):
            found.append(f"the scope `({scope})` must be lowercase letters, digits and . _ / - "
                         "(several separated by commas)")
        if header.group("space") != " " or not header.group("subject").strip() \
                or header.group("subject") != header.group("subject").lstrip():
            found.append("one space after the colon, then the subject")
        elif first.endswith("."):
            found.append("the subject ends with a period")
    if len(first) > MAX_FIRST_LINE:
        found.append(f"the first line has {len(first)} characters; at most {MAX_FIRST_LINE}")
    if len(lines) > 1 and lines[1]:
        found.append("a blank line must separate the first line from the body")
    for line in lines[1:]:
        trailer = re.fullmatch(r"Feature:\s*(.*)", line)
        if not trailer:
            continue
        value = trailer.group(1).strip()
        if not FEATURE_ID.fullmatch(value):
            found.append(f"`Feature: {value}` must name a feature-list id such as shares.per-user-access")
        elif feature_ids is not None and value not in feature_ids:
            found.append(f"`Feature: {value}` is not an id of feature-list.json")
    if CYRILLIC.search("\n".join(lines)):
        found.append("write the message in English (ADR-0200)")
    return found


def feature_ids_from(text):
    try:
        data = json.loads(text)
        return {feature["id"] for feature in data.get("features", []) if isinstance(feature, dict)}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip() or 'failed'}")
    return result.stdout


def check_range(revisions: str, feature_list) -> int:
    if ".." not in revisions:
        print(f"check-commit-msg: --range needs A..B, not {revisions!r}", file=sys.stderr)
        return 2
    try:
        commits = git("rev-list", "--reverse", "--topo-order", revisions).split()
        if not commits:
            print(f"check-commit-msg: no commits in {revisions}")
            return 0
        listing = "".join(f"{commit}:{CHECKER_PATH}\n" for commit in commits)
        checked = subprocess.run(["git", "cat-file", "--batch-check"], input=listing, capture_output=True,
                                 text=True, check=True).stdout.splitlines()
        details = git("log", "--no-walk=unsorted", "--format=%H%x1f%P%x1f%B%x1e", *commits)
        if feature_list is not None:
            ids = feature_ids_from(Path(feature_list).read_text(encoding="utf-8"))
        else:
            head = revisions.split("..", 1)[1].lstrip(".") or "HEAD"
            show = subprocess.run(["git", "show", f"{head}:feature-list.json"], capture_output=True, text=True)
            ids = feature_ids_from(show.stdout) if show.returncode == 0 else None
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"check-commit-msg: {error}", file=sys.stderr)
        return 2
    has_checker = {commit for commit, line in zip(commits, checked) if not line.endswith(" missing")}
    failures, total, skipped = [], 0, 0
    for record in details.split("\x1e"):
        record = record.lstrip("\n")
        if not record:
            continue
        commit, parents, message = record.split("\x1f", 2)
        if commit not in has_checker:
            skipped += 1  # made before the convention existed
            continue
        if len(parents.split()) > 1:
            skipped += 1  # a merge commit
            continue
        total += 1
        found = problems(message, ids, autosquash_ok=False)
        if found:
            failures.append((commit, cleaned(message)[:1], found))
    for commit, first, found in failures:
        print(f"{commit[:10]} {first[0] if first else '(empty)'}")
        for problem in found:
            print(f"    - {problem}")
    note = f" ({skipped} older or merge commits skipped)" if skipped else ""
    if failures:
        print(f"check-commit-msg: {len(failures)} of {total} commits do not follow the commit convention "
              f"(ADR-0209){note}. Pushed commits are never rewritten (ADR-0208): ask the owner to "
              "squash-merge the pull request with a conforming message. Commits that are not pushed yet "
              "can be reworded first.")
        return 1
    print(f"check-commit-msg: {total} commits follow the commit convention{note}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check commit messages (ADR-0209).")
    parser.add_argument("file", nargs="?", help="the message file (the commit-msg hook's argument)")
    parser.add_argument("--range", dest="revisions", metavar="A..B", help="check the commits of A..B")
    parser.add_argument("--feature-list", metavar="FILE", help="feature-list.json to check Feature: trailers")
    args = parser.parse_args(argv)
    if bool(args.file) == bool(args.revisions):
        parser.error("give a message file or --range A..B")
    if args.revisions:
        return check_range(args.revisions, args.feature_list)
    try:
        message = Path(args.file).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(f"check-commit-msg: {error}", file=sys.stderr)
        return 2
    feature_list = Path(args.feature_list or Path(__file__).resolve().parents[1] / "feature-list.json")
    try:
        ids = feature_ids_from(feature_list.read_text(encoding="utf-8"))
    except OSError:
        ids = None
    found = problems(message, ids)
    if not found:
        return 0
    first = cleaned(message)[:1]
    print("check-commit-msg: the commit message does not follow the commit convention (ADR-0209):",
          file=sys.stderr)
    if first:
        print(f"  first line: {first[0]}", file=sys.stderr)
    for problem in found:
        print(f"  - {problem}", file=sys.stderr)
    print(f"  The message is kept in {args.file}: fix it, then commit again "
          f"(git commit --cleanup=strip -F {args.file}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

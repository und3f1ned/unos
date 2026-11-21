#!/usr/bin/env python3
"""The passwords that CLAUDE.md once published, found by their SHA-256 (ADR-0212, ADR-0439).

The dev device's root password and the UNOS administrator's, both rotated (ADR-0226), are kept
here as digests only, never as values. Every token of six or more characters (a run of letters
and digits, or a word between spaces, quotes and delimiters) is hashed and compared.

- The secret scan of the working tree, `supervisor/tests/test_repo_tooling.py`, uses DIGESTS and
  TOKENS: no file may hold one of the passwords.
- `scripts/secret-scan.sh` runs this file over the history: no commit may add a line that holds
  one, but the commits and paths of ALLOWED, which added them before they were rotated. gitleaks'
  rules do not find a bare password in code or prose, as the old E2E specs held the
  administrator's. A merge is read as git's remerge-diff shows it (what its resolution changed),
  with the merge's temporary objects in a temporary folder, not in the repository.

Usage: published-passwords.py [REPOSITORY]. Prints a finding as its commit, path and line, never
the value; exits 1 for a finding and 2 when git fails.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping

DIGESTS = frozenset({
    "9b91d0f8083826018d3e53df7f8151b2375b02e8d2eecf748182f53953594ca8",
    "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9",
})
TOKENS = (re.compile(r"[A-Za-z0-9]{6,}"), re.compile(r"[^\s'\"`=:,;()\[\]{}<>|&]{6,}"))

# The commits that added them, with the paths: the old CLAUDE.md published both, and the test stack
# and the E2E specs signed in with the administrator's until ADR-0212 gave them a test password of
# their own. Both were rotated on 2026-11-07, so they open nothing (ADR-0226), and the history is not
# rewritten (ADR-0208).
ALLOWED = {
    "da193b8b0c12fc431a2675234ede1c2ebf7611e3": ("CLAUDE.md",),
    "3c83c784f1f19bf772f105d8a9975574e885d51b": ("docs/TESTING.md", "supervisor/tests/fixtures/init-test-data.sh"),
    "5308c1c922840961630f74e8e550094dd90c8340": ("webui/tests/account-smb.spec.ts", "webui/tests/api.spec.ts",
                                                 "webui/tests/shares.spec.ts", "webui/tests/users.spec.ts"),
    "605903cfcc439e30307a0e866918708f4345917a": ("webui/tests/api.spec.ts", "webui/tests/auth.spec.ts",
                                                 "webui/tests/fixtures.ts"),
    "792d40e85ddea8404fc4d33b319a218b89c91dbf": ("deploy/docker-compose.test.yml", "docs/TESTING.md",
                                                 "scripts/test-cycle.sh", "supervisor/tests/test_auth.py",
                                                 "supervisor/tests/test_deploy_scripts.py",
                                                 "webui/tests/roles.spec.ts"),
}


def lines_with(text: str, digests: Iterable[str] = DIGESTS) -> list[int]:
    """The numbers of the lines of a text that hold a token whose SHA-256 is one of `digests`."""
    digests = frozenset(digests)
    hits = {token for token in _tokens(text) if hashlib.sha256(token.encode()).hexdigest() in digests}
    if not hits:
        return []
    return [number for number, line in enumerate(text.splitlines(), 1) if hits & _tokens(line)]


def history(repository: str, digests: Iterable[str] = DIGESTS,
            allowed: Mapping[str, Iterable[str]] = ALLOWED) -> tuple[int, list[str]]:
    """(the commits read, the findings as "commit path:line") of the lines that the commits HEAD
    reaches add, merges by their remerge-diff, but those of `allowed` (commit: paths)."""
    digests, allowed = frozenset(digests), {commit: set(paths) for commit, paths in allowed.items()}
    objects = _git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects").strip()
    with tempfile.TemporaryDirectory() as scratch:
        log = _git(repository, "log", "-p", "-U0", "--remerge-diff", "--no-color", "--no-ext-diff",
                   "--no-textconv", "--format=commit %H", "HEAD",
                   env={"GIT_OBJECT_DIRECTORY": scratch, "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects})
    known: dict[str, bool] = {}
    commits, findings, commit, path, number = 0, [], "", "", 0
    for line in log.splitlines():
        if line.startswith("commit "):
            commits, commit, path = commits + 1, line[7:], ""
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@"):
            added = re.search(r" \+(\d+)", line)
            number = int(added[1]) if added else 0
        elif line.startswith("+") and path:
            for token in _tokens(line[1:]):
                if token not in known:
                    known[token] = hashlib.sha256(token.encode()).hexdigest() in digests
                if known[token] and path not in allowed.get(commit, ()):
                    findings.append(f"{commit} {path}:{number}")
                    break
            number += 1
    return commits, findings


def _tokens(text: str) -> set[str]:
    return {match.group() for pattern in TOKENS for match in pattern.finditer(text)}


def _git(repository: str, *args: str, env: Mapping[str, str] | None = None) -> str:
    result = subprocess.run(["git", "-C", repository, *args], capture_output=True, timeout=600,
                            env={**os.environ, **(env or {})})
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode("utf-8", "replace")


def main(argv: list[str]) -> int:
    repository = argv[1] if len(argv) > 1 else "."
    try:
        commits, findings = history(repository)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"published-passwords: {error}", file=sys.stderr)
        return 2
    if findings:
        print("published-passwords: a password once published in CLAUDE.md (commit path:line):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print(f"published-passwords: none added in the {commits} commits of HEAD but where ALLOWED names them")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0209: Commit messages follow Conventional Commits in English

## Context

UNOS's commit titles had no common form (for example 9ac7b40, "Phase 1 slice 1: broken user flows, and no needless system-disk writes"), so a title did not say what kind of change it is, and nothing tied a commit to the feature it implements (ADR-0203). The book's repository checks Conventional Commits with commitlint, which needs a Node.js package at the repository root.

## Decision

- The commit rule has one home: this ADR and its executable form, `scripts/check-commit-msg.py` (standard library only). `CONTRIBUTING.md` and `docs/TESTING.md` state it in one line and link here.
- A commit message is `type(scope)!: subject`, then optionally a blank line and a body, then trailers:
  - the type is one of feat, fix, docs, test, refactor, perf, build, ci, chore, revert or style; the scope is optional (lowercase letters, digits and `. _ / -`, several separated by commas); `!` marks a breaking change; one space follows the colon;
  - the first line, type, scope, `!` and subject together, has at most 72 characters and does not end with a period;
  - a blank line separates the first line from the body;
  - trailers are allowed, such as `Co-Authored-By:`; a commit that implements an entry of `feature-list.json` names it in a `Feature: <id>` trailer (ADR-0203), and the id must exist there;
  - English only: a message with Cyrillic letters is refused (ADR-0200).
- Lines starting with `#` and everything below git's scissors line are ignored, as git ignores them; merge messages written by git pass.
- The commit-msg hook (`scripts/git-hooks/commit-msg`) runs the checker on every commit. CI's `commits` job runs it with `--range A..B` over the commits of a pull request. The range check skips merge commits and the commits made before the checker existed (those whose tree lacks `scripts/check-commit-msg.py`, such as 9ac7b40), and refuses `fixup!`, `squash!` and `amend!` commits, which the hook lets through: they are squashed before the pull request is merged.
- Tested in `supervisor/tests/test_repo_tooling.py` (good and bad messages, the hook, the range check).

## Consequences

- Titles show the kind of change and, through the trailer, the feature.
- The hook is local: it runs only where `make setup` set `core.hooksPath` (ADR-0217), and it can be skipped. CI's job is the check the owner merges on (ADR-0216), and `--no-verify`, `git commit -n` and another `core.hooksPath` are blocked for agents by the guard (ADR-0211).
- The limit counts the type and the scope, so a long scope leaves less room for the subject.
- Dependabot's pull requests use Conventional Commit prefixes too (ADR-0218).

## Considered options

- commitlint, as the book's repository uses: needs a root `package.json` and a network install; a small checker in the repository does the same job.

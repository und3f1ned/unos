# Contributing to UNOS

UNOS is developed by its owner together with coding agents, on a workstation and one dev device.
This file describes how work flows. [AGENTS.md](AGENTS.md) holds the commands and rules that
agents and developers follow, and [docs/TESTING.md](docs/TESTING.md) the details of the tests and
CI. Run `make setup` once per checkout; it needs the network.

## How work flows

1. **An entry.** Work starts from an entry of [feature-list.json](feature-list.json) or from a
   GitHub issue opened with the bug or enhancement template (ADR-0206). A new issue carries the
   label `needs-triage`; triage gives it `needs-info` (a question is open), `ready-for-agent`,
   `ready-for-human` or `wontfix`. Accepted work becomes a feature-list entry. A refusal is
   written in the issue and, when it is a product decision, in an ADR, so that it is not argued
   twice.
2. **One feature per branch, worktree and pull request** (ADR-0203, ADR-0210). Each feature gets
   its own git worktree, so that the unfinished work of two features never mixes in one checkout:
   `git worktree add --no-track -b <id> ../unos-<id> <base>`, with `<base>` `origin/main`, or the
   batch branch that the owner names. `--no-track` leaves the new branch without an upstream, so
   that a bare `git push` never sends it to the base. A worktree without AGENTS.md or
   `.claude/settings.json` is a stop: ask the owner. A problem found on the way becomes an entry
   of its own.
3. **A plan** of a few lines before the code: the files to change and the ADRs that bind them. A
   choice that no ADR settles is the owner's.
4. **Tests first** (ADR-0186): a test that fails on the code before the change. Backend tests go
   to `supervisor/tests/`, the user flow to a Playwright spec in `webui/tests/`. Weakening such a
   test to get green needs the owner's yes.
5. **`make check` passes** (ADR-0216). The pre-commit hook runs `make check-fast`, unless the
   working tree is the one in which `make check` or `make check-fast` last passed (ADR-0383);
   `make setup` installs the hooks (`git config core.hooksPath scripts/git-hooks`).
6. **A review in a fresh context** (ADR-0017): a reviewer without the session's history gets the
   diff, the ledger entry and its ADRs and tries to refute the change. Its findings are fixed, or
   recorded with the reason, before the feature passes.
7. **The dev device** checks every host-level change, as [GLOSSARY.md](GLOSSARY.md) defines it
   (ADR-0018), at the last of
   [the verification levels](docs/TESTING.md#the-three-verification-levels).
8. **The ledger, the journal and the ADRs** change in the same pull request: the feature's
   `passes` turns true only after its scenario passed (ADR-0203), [PROGRESS.md](PROGRESS.md) gets a
   dated entry (ADR-0204), and every decision gets an ADR.
9. **Conventional Commits** in English, whose first line, type and scope included, has at most 72
   characters: [ADR-0209](docs/adr/0209-conventional-commits-in-english.md) holds the rule, and
   [scripts/check-commit-msg.py](scripts/check-commit-msg.py) checks it in the commit-msg hook
   (and CI's Commit Messages job on GitHub, ADR-0462).
10. **The pull request** follows [the template](.github/pull_request_template.md). The owner opens
    and merges pull requests (ADR-0208).
11. **The end of a batch**: `scripts/prune-merged-branches.py` lists the clean worktrees and the
    local branches of work merged into `origin/main`, and `--apply` removes them. It keeps every
    other worktree, a new one without a commit yet included, names those with changes, and
    changes nothing on GitHub, which deletes the branch of a merged pull request itself
    (ADR-0465).

## Recording decisions

Every decision gets a short ADR, the small ones too (ADR-0001). It is a file
`docs/adr/NNNN-slug.md` with the next free number, made from
[the template](docs/adr/template.md): front matter (status, date, area, decided-by), then Context,
Decision, Consequences and, when the alternatives are worth remembering, Considered options.
[docs/adr/README.md](docs/adr/README.md) lists every ADR by area.

A decision in force that changes gets a new ADR that supersedes the old one. The old file keeps
its text; only its status changes to `superseded`, with `superseded-by` naming the new ADR. Until
its first commit, an ADR written in the same change as its decision, or a proposed ADR accepted
with new evidence, is refined in place instead (ADR-0001). The adr skill walks through these
steps, and `make test-fast` checks the format (`supervisor/tests/test_adrs.py`).
ADRs cite repository files and tests. They carry no secrets, and no exploit details of weaknesses
that are still open (ADR-0213).

## Verification levels

The three verification levels, from the workstation to the dev device, what each one proves and
when it is needed, are in [docs/TESTING.md](docs/TESTING.md#the-three-verification-levels).

## The owner's role

The owner:
- decides what is built, through triage and the feature ledger, and settles open questions.
  Security trade-offs are delegated to the agents, which record each one as an ADR with
  `decided-by: agent` (ADR-0013);
- asks for commits and pushes, and opens and merges the pull requests (ADR-0208);
- runs what needs the network or credentials (`make setup`, `make audit`) and reviews the
  Dependabot pull requests (ADR-0218);
- holds the credentials and the dev device's security settings (ADR-0024), and does the owner
  actions on the device: reboots, image prunes, package installs (ADR-0211);
- starts the maintenance passes of [docs/maintenance.md](docs/maintenance.md).

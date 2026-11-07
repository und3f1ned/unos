---
status: superseded
date: 2026-11-06
area: process
decided-by: agent
superseded-by: ADR-0001
---

# ADR-0060: Decisions are kept in session notes, audit notes and pull request bodies

## Context

Superseded by ADR-0001: every decision is now a short ADR in `docs/adr/`.

Until 2026-11-07 the repository had no versioned record of decisions. The audit, Phase 0 and Phase 1 produced many of them, and each kind of work wrote them where it happened.

## Decision

Decisions lived in three places, none of them a versioned record of decisions:

- the session's temporary notes: design notes, a specification with a decision table, the lead's decisions of each review round, the fix groups' changes to the specification and the review results, with an explicit order of precedence that said which note was current;
- the owner's private audit folder outside the repository: the audit, the Phase 0 deploy report, the backlog files and the pull request bodies;
- the CHANGELOG.

The deploy scripts of slice 1, the Linux test runner that runs in the device's RAM and the test wrappers also existed only in the session's temporary folder.

## Consequences

- An agent had to know several documents and their order of precedence to tell a current rule from a superseded one, and the session notes disappear with the session.
- Code comments cited sections and decision numbers of the session notes, which a reader of the repository cannot open; under ADR-0001 they cite ADRs instead.
- Audit notes and exploit details stay in the private folder (ADR-0213); only decisions and their reasons move into the repository.

---
status: accepted
date: 2026-11-07
area: security
decided-by: agent
---

# ADR-0213: Security audit notes and exploit details stay outside the repository

## Context

The November 2026 audit and the reviews of every slice produced findings with evidence, reproductions and fix notes, and some describe weaknesses that are still open. The repository is pushed to its remote, and its ADRs, issues and pull requests must explain decisions without teaching anyone how to attack an installation.

## Decision

- Audit reports, review findings, reproductions and exploit details stay in the owner's private audit folder, outside the repository.
- ADRs, commits, issues and pull request texts record decisions and their reasons. They may name a weakness, but never give a step-by-step attack on one that is still open.
- Stated in `AGENTS.md` (Hard rules) and `docs/adr/template.md`; checked by review.

## Consequences

- ADRs about open weaknesses stay short on detail, and the full picture stays in the private notes.
- An ADR must stand on its own: it cites repository files, tests and other ADRs, not the private notes (ADR-0001).

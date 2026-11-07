---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0201: Each kind of project knowledge has exactly one home

## Context

Until this conversion, project knowledge was spread over the repository's CLAUDE.md, a second CLAUDE.md in the workspace around the repository, session notes, the private audit folder, pull request bodies and the CHANGELOG (ADR-0060). The same fact in two places drifts, and an agent cannot tell which copy is current.

## Decision

Each kind of knowledge has one home, and other places link to it instead of copying it:

| What | Home |
| --- | --- |
| How to work | `AGENTS.md` and the nested AGENTS.md files (ADR-0202), `CONTRIBUTING.md` |
| Why: every decision | `docs/adr/` (ADR-0001) |
| The project's words | `GLOSSARY.md` |
| Where work stands, the dev device included | `PROGRESS.md` (ADR-0204) |
| What to build next | `feature-list.json` (ADR-0203) |
| A handoff between sessions | `tmp/handoff/`, ignored by git (ADR-0204) |
| Invariants | hooks, tests and CI (ADR-0211, ADR-0216) |
| Procedures | skills in `.agents/skills/` (ADR-0205) |

The root `AGENTS.md` carries this table, with rows for a few more homes, under "Where things live".

## Consequences

- A change that moves a fact moves it out of its old place in the same change, for example the roadmap of `docs/IMPROVEMENTS.md` into `feature-list.json` (ADR-0207).
- A rule that several documents need is written once and linked from the others: what a host-level change is, in `GLOSSARY.md` (ADR-0018); the commit rule, in ADR-0209 and `scripts/check-commit-msg.py`; the table of verification levels, in `docs/TESTING.md`.
- Documents for users and operators (README.md, `docs/INSTALL.md`, `docs/USER_GUIDE.md`, CHANGELOG.md) describe UNOS for its users and do not hold the project's working rules.
- The monthly maintenance passes look for facts that drifted between homes (`docs/maintenance.md`, ADR-0218).

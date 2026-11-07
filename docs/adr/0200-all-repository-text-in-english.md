---
status: accepted
date: 2026-11-07
area: process
decided-by: owner
---

# ADR-0200: All repository text is English, and agents answer the owner in Russian

## Context

The owner works in Russian; the private audit report and the Phase 0 deploy notes outside the repository are in Russian, while the repository, its docs, the CHANGELOG, commit messages and pull request bodies have been in English, and the audit kept its findings in English so that they can be compared with the code. With agents writing most of the text, the rule has to be explicit. On 2026-11-07 the owner said: "Keep all text of the docs and rules in English".

## Decision

- All text written into the repository is English: code, comments, docs, ADRs, the AGENTS.md files, skills, hook and script messages, commit messages and pull request texts.
- Agents answer the owner in Russian, the owner's language.
- An ADR quotes the owner in English translation, in double quotes.
- Stated in `AGENTS.md` (Language) and `docs/adr/template.md`.

## Consequences

- Every agent translates between the conversation and the repository; the owner's original wording stays only in the conversation and the private notes.
- Where the text is structured, a check refuses Cyrillic letters: `supervisor/tests/test_adrs.py` (the ADRs, their index and the template), `supervisor/tests/test_feature_list.py` (`feature-list.json` and `PROGRESS.md`) and `scripts/check-commit-msg.py` (commit messages, ADR-0209). Code, comments and the other docs are checked by review.
- Notes outside the repository, such as the private audit folder (ADR-0213), may stay in Russian.
- The reply language is the owner's preference, not a rule about the repository's text; it stays in `AGENTS.md` because the repository has one owner.

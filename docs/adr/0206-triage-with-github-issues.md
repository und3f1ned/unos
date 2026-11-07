---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0206: Work is triaged in GitHub Issues with five labels and two templates

## Context

Bug reports and ideas arrived in conversations and private backlogs, without a state that says whether they are understood, accepted or refused, so the same question could be argued twice. The book's Issue Triage pattern gives each item one category and one explicit state.

## Decision

- Incoming work is triaged in GitHub Issues of the repository, with bug and enhancement templates (`.github/ISSUE_TEMPLATE/bug.md` and `enhancement.md`).
- Each issue carries one of the labels needs-triage, needs-info (back to needs-triage once the reporter answers), ready-for-agent, ready-for-human (with the reason, such as an owner decision or device access) or wontfix.
- Accepted work becomes a `feature-list.json` entry (ADR-0203).
- A refusal is recorded with its reason in the issue and, when it is a product decision, as an ADR, so that it is not argued twice.

## Consequences

- The labels and templates need upkeep, and triage depends on the owner deciding in time.
- Issues state decisions and reasons, never exploit details (ADR-0213).
- As in the book's pattern, an agent may gather the context and recommend a state, but the owner sets it.

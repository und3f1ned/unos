---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0203: The feature ledger is feature-list.json, and a feature passes only after its scenario

## Context

What to build next lived in prose: the roadmap of `docs/IMPROVEMENTS.md`, the CHANGELOG's Planned list and the private backlogs. Prose statuses get lost, and an agent can call a feature done because its unit tests pass while the user scenario fails (the book's Feature List and One Feature at a Time patterns, and its Premature Success anti-pattern).

## Decision

- `feature-list.json` at the root is the ledger of user-visible features. Each entry has an `id` (`<category>.<slug>`, unique), a `category` (one of the ADR areas), a `description`, `steps` (observable steps of the user scenario), `verified_by` (existing repo files, required when the entry passes), a `passes` flag and optionally `adr` (existing ADR ids) and `notes`. `supervisor/tests/test_feature_list.py` validates it.
- `passes` turns true only after the user scenario passed: its tests, plus the device check for a host-level change (defined in `GLOSSARY.md`, ADR-0018), and after a reviewer without the session's history (a subagent, or a new session with mattpocock-skills:code-review) was given the diff, the entry and its ADRs and tried to refute the change (ADR-0017): each finding is fixed, or recorded with its reason, before the feature passes. The feature-pass skill does this step.
- Entries are not deleted or reworded to make them pass; dropping or changing one is a requirements decision that needs an ADR. The test compares the ledger with the one at the branch's base (`FEATURE_LIST_BASE`, else `git merge-base HEAD origin/main`): an entry is dropped, or its description or steps change, only with an ADR that is new since the base (its number is not in the base's `docs/adr`) and names the entry's id, and a changed entry also lists that ADR in `adr`. An older ADR, or one that does not name the id, unlocks nothing. While the base has no ledger, as long as main has none, the comparison with the base is skipped, locally and in CI; CI's Unit Tests job checks out the whole history, so it runs there too once main has the ledger (the feature-list entry `ci.ledger-changes-checked`). The rule itself runs on small fixture ledgers in every run (`TestTheRequirementChangeRule` in `supervisor/tests/test_feature_list.py`).
- One feature per working session and per pull request; a commit that implements an entry names it in a `Feature: <id>` trailer (ADR-0209). Side findings become entries of their own instead of growing the change.

## Consequences

- The ledger replaces `docs/IMPROVEMENTS.md`, which moves into it (ADR-0207); accepted work from triage (ADR-0206) and the removal of legacy code paths become entries too.
- A feature that is only partly verified stays `passes: false`, however much of it works.
- Verifying a host-level feature on the device costs system-disk writes, so routine regressions run on the stand-in (ADR-0215) and the device check stays a short final step (ADR-0214).

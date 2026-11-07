---
status: accepted
date: 2026-11-06
area: testing
decided-by: agent
---

# ADR-0186: Every fix comes with a test that fails on the code before it

## Context

The November 2026 audit found that the tests caught none of the broken flows: mocks replaced `subprocess` entirely, tests checked only for status 200, and some ended in `pass`. A test that has never failed does not prove that it guards anything. The owner's instructions already asked for backend and E2E tests for every feature, passing before work moves on.

## Decision

- Every behavior fix and every new rule gets a test that is shown to fail on the code before the change: by running it against an unfixed copy, or by a named mutation of the new code that the test catches.
- A finding about a missing test is closed by a test proven to catch the mutation the finding names.
- Every feature has backend tests in `supervisor/tests/` and a user flow in `webui/tests/`, and they pass before the work moves on (`make check`, ADR-0216; `make e2e-local`, ADR-0215).
- The SMB slice applied this to its safety rules: 117 mutations, and every one that survived was closed by a new test.

## Consequences

- A change takes longer: the failing run has to be produced and reported, not only the passing one.
- A test that mocks away the behavior it claims to check cannot fail, so it cannot meet this rule; host behavior is tested against the fakes and runners of ADR-0189.
- The failing run or the mutation is reported with the change, so that independent reviewers can check it (ADR-0017).

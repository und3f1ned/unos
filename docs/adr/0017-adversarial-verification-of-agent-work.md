---
status: accepted
date: 2026-11-05
area: process
decided-by: agent
---

# ADR-0017: Agent work is checked by independent agents that try to refute it

## Context

The 20 pull requests of February 2026, made in about five working days, passed only unit tests with mocks and never ran on the device; the November 2026 audit then found 221 problems in the code, 25 of them critical. Claims and tests written by an agent can be wrong or weak, and an agent whose job is to refute them catches that.

## Decision

- Findings and fixes are checked by separate agents whose job is to refute them, not to confirm them. In the audit, 11 directions with 23 agents examined the code, a last agent looked for what the others had missed, and every critical and high finding (95) was checked again by a separate agent: 76 were confirmed, 19 downgraded and none refuted. Key claims were reproduced by running the code at pinned versions, and the live device was examined read-only.
- Each slice goes through review rounds with independent lenses (security, robustness, concurrency, Samba, the device, the UI, the tests), and a skeptic gives a verdict on every finding before it is fixed. Fixes are made by groups that each own their files.
- The safety rules are tested by mutation: slice 2 ran 117 mutations of them, and every mutation that survived was closed by a test (ADR-0186).
- Claims about third-party behavior are checked in the upstream source of the version in use; for slice 2 that was Samba 4.22.6, as on the device.

## Consequences

- Reviews cost agent time and several rounds per slice; the audit's medium and low findings remain one auditor's view.
- Review findings and verdicts are inputs; the decisions that follow from them are ADRs (ADR-0001).

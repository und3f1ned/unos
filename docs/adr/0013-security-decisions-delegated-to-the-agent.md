---
status: accepted
date: 2026-11-05
area: process
decided-by: owner
---

# ADR-0013: The owner delegates security decisions to the agent, with safety as the criterion

## Context

The November 2026 audit and the hardening after it needed many security trade-offs, from the actions after the Phase 0 deploy to the SMB design of slice 2. Asking the owner about each one would stop the work, and there still has to be one accountable decider. On 2026-11-05 the owner answered: "Decide yourself. It must be safe!"

## Decision

- The lead agent makes each security trade-off itself, with safety as the criterion, and writes down the decision and its reason; since ADR-0001 that record is an ADR with `decided-by: agent`.
- Workflow agents apply the lead's decisions and do not decide them again; a question that no decision settles goes back to the lead or the owner.
- The delegation was first used for the actions after the Phase 0 deploy on 2026-11-05 (ADR-0045, ADR-0046, ADR-0053, ADR-0054, ADR-0055), and then for the whole SMB design of slice 2 (ADR-0059).
- It has limits: credentials and the device's security settings stay with the owner (ADR-0024), and commits, pushes and pull requests happen only when the owner asks (ADR-0208).

## Consequences

- Most security ADRs from 2026-11-05 on say `decided-by: agent`; the owner can overturn any of them with a new ADR that supersedes it.
- A decision without its written reason is not done: the reason is what lets the owner review it later.

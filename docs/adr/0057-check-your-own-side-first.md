---
status: accepted
date: 2026-11-06
area: process
decided-by: owner
---

# ADR-0057: When something breaks, the agent checks its own side first

## Context

A failure that an agent sees (a tool that stops working, a connection that does not answer, a test that breaks) can come from its own environment, tools or changes, from the device, or from something the owner did. The agent cannot see the owner's actions, but it can inspect its own side. On 2026-11-06 the owner told the agent: "I changed nothing! Check yourself".

## Decision

When something breaks, the agent first looks for the cause on its own side: the workstation and its network filter, its tools and their versions, its own commands and its own changes. It suspects the device or the owner only after its own side has been checked.

## Consequences

- Debugging starts with what the agent controls, which is also what it can fix without the owner.
- ADR-0198 is an example of a cause on the agent's side: the device's web ports did not answer the workstation's tools while SSH worked, most likely because of a network filter on the workstation, so device API checks run on the device itself.
- A denial by the workstation's network filter or by the guard of ADR-0211 is a cause on the agent's side too; the agent asks the owner instead of working around it.
- Independent verification (ADR-0017) applies the same idea to the agent's own results.

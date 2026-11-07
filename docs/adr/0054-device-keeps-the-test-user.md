---
status: accepted
date: 2026-11-05
area: device
decided-by: agent
---

# ADR-0054: The device keeps the regular user 'test' for role checks

## Context

After Phase 0 the device had a regular UNOS account named `test`, with 2FA. Phase 0 made regular accounts see only Dashboard and Account (ADR-0030), and checking that on the device needs such an account.

## Decision

- The regular account `test` stays on the dev device as a role-check account; it sees only Dashboard and Account.
- The owner deletes it in Users when it is no longer needed.

## Consequences

- Checks of the regular user's view on the device can sign in as `test` instead of creating an account for each check.

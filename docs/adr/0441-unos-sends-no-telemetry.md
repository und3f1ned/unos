---
status: accepted
date: 2026-11-21
area: system
decided-by: agent
---

# ADR-0441: UNOS sends no telemetry, not even when an administrator turns it on

## Context

The feature-list entry `system.opt-in-telemetry` asked that an administrator can send anonymous
usage statistics, off unless turned on, and noted that it needs a decision on whether UNOS
collects anything at all. A NAS holds its owner's private files and runs in the home network;
UNOS has one dev device and no fleet of installations whose use could be measured (ADR-0207).
The catalog already turns off the telemetry of apps that have it (ADR-0415). Asked to choose,
the owner answered "decide yourself" (2026-11-21).

## Decision

- UNOS collects and sends no usage statistics: no telemetry code, no setting for it, no endpoint
  that receives it.
- What UNOS should do next comes from the owner and from issues people open on its repository.
- The entry `system.opt-in-telemetry` is removed from `feature-list.json`.

## Consequences

- Decisions have no usage data behind them.
- A NAS behind a filter that blocks outbound traffic loses nothing, and there is no anonymity
  promise to keep, document or test.

## Considered options

- Opt-in telemetry with documented anonymous fields: honest, but it needs a server to receive
  it, a privacy statement and tests that it sends only those fields, for a project with no
  installations to measure.

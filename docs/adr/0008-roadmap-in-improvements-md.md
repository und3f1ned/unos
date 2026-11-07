---
status: superseded
date: 2026-02-05
area: process
decided-by: owner
superseded-by: ADR-0014
---

# ADR-0008: Work follows the feature roadmap in docs/IMPROVEMENTS.md

## Context

Superseded by ADR-0014: after the November 2026 audit, work follows a severity-first plan.

A codebase review of 2026-01-24 wrote its improvement proposals into `docs/IMPROVEMENTS.md`. On 2026-02-05, with the release notes of 1.0.0, the file became the "Active development roadmap".

## Decision

- Priorities came from the roadmap's matrix: P0 login rate limiting, RAID monitoring, scheduled backups and snapshot retention; P1 backup encryption, per-user share permissions and UPS integration; P2 SMART prediction and a pool rebalancing UI; P3 network bonding and iSCSI. Its rule: the P0 items (security and data protection) come before new features.
- The file also held architecture ideas (an event bus, a plugin system, high availability) and an implementation status that marked items as completed, among them RAID monitoring, UPS monitoring and audit logging.

## Consequences

- The status was never checked against running code: the November 2026 audit found that the monitors marked completed had never started, because FastAPI ignores router startup hooks when the app has a lifespan (ADR-0043).
- The file's open items move into `feature-list.json` (ADR-0203), and the file is removed (ADR-0207).

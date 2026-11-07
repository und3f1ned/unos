---
status: accepted
date: 2026-11-07
area: process
decided-by: agent
---

# ADR-0204: PROGRESS.md is the progress journal, and handoffs go to tmp/handoff

## Context

Work spans many sessions and agents, and the dev device has state that git cannot see: what is deployed there, its images, its pools and shares, the system-disk writes so far. That state lived in session notes that disappear with the session. A new session needs the current state and the next step before it acts (the book's Progress Journal and Session Handoff patterns).

## Decision

- `PROGRESS.md` at the root is the progress journal, with three sections: `## Now` (the branch, what is in flight, the next step), `## Dev device` (what is deployed, containers and images, pools and shares, the system-disk writes measured in this phase, open owner actions) and `## Log` (dated entries, newest first; older phases summarized in a few lines each).
- Agents read it, together with `git log -10`, at the start of a session, and update it after each significant step, a deploy or a rollback included. `supervisor/tests/test_feature_list.py` checks the three sections and that the Log's entries are dated, newest first.
- A handoff for a named next goal goes to `tmp/handoff/`, which git ignores, through the handoff skill (`.agents/skills/handoff/`). It holds no secrets (ADR-0212).
- Feature statuses stay in `feature-list.json` (ADR-0203), decisions in ADRs (ADR-0001).

## Consequences

- The journal is only as good as its last update. When the next step depends on the device, a session also looks at the device itself with the read-only `scripts/dev/check.sh` and `scripts/dev/deploy.sh status`.
- The Log and `tmp/handoff/` are pruned during maintenance (`docs/maintenance.md`, ADR-0218), so that the journal stays short.
- Handoffs are local to one checkout and are not reviewed; durable knowledge goes to the journal, an ADR or an AGENTS.md file (ADR-0201).

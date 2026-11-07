---
status: superseded
date: 2026-11-05
area: ci
decided-by: agent
superseded-by: ADR-0190
---

# ADR-0056: Changes may ship with the existing svelte-check errors

## Context

Superseded by ADR-0190: CI now runs `svelte-check --threshold error` and fails on any error.

When Phase 0 and the branch that made e-mail notifications verify the mail server's certificate (ADR-0045) were finished, svelte-check reported 7 errors in the web UI. All of them were in files these changes did not touch and were older than the changes. CI ran only the backend tests and an image build at the time.

## Decision

Phase 0 (pull request #21) shipped with the 7 existing svelte-check errors, and the SMTP branch (`smtp-verify-tls`, not merged yet, ADR-0045) was finished with the same 7. The only rule was that a change adds no error of its own: each pull request body reported the same 7 errors, none of them in a file the change touched.

## Consequences

- The old errors stayed in the code, and a new error in one of those files would not have stood out.
- No CI job enforced the rule; each pull request compared the count by hand.
- ADR-0190 replaced it with a gate: the `check` script of `webui/package.json` runs `svelte-check --threshold error`, and CI's frontend job fails on any error.

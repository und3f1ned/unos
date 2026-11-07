---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0136: Import notes can be dismissed for a share that is not served

## Context

A share that is not served must not grant anything, so a PATCH of such a share is refused
while its result would still grant access (ADR-0145). The import notes of an unserved share
that still had access rows could therefore not be dismissed, although dismissing grants
nothing.

## Decision

A PATCH whose only fields are `import_notes` (always `[]`) and `expected_version` skips the
check that a share that is not served must not grant anything (`dismiss_only` in
`service._update_share_locked`). Any other field that is sent, even with its current value,
keeps the check. The Dismiss button of the Shares page sends
`{import_notes: [], expected_version}`.

Implemented in `supervisor/src/shares/service.py` and
`webui/src/routes/shares/+page.svelte`; tested in `supervisor/tests/test_share_api.py`
(TestNotServed: the notes of a share that still grants can be dismissed; no other change
skips the check) and `webui/tests/shares.spec.ts`.

## Consequences

The notes of every share can be dismissed, while every change that could grant something
still needs a folder that may be shared.

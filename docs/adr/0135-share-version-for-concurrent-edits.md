---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0135: Shares carry a version, and PATCH takes expected_version

## Context

Two administrators editing one share's access list at the same time silently lost one edit:
the second save replaced the first.

## Decision

Each share has an integer `version` (`SmbShare.version`, 1 for a new share) that grows by
one with every real change of the share or of its access rows: a PATCH that changes
something, and every deletion of access rows (a user's deletion or promotion, the purge of
stale rows, the cleanup at user creation, a restore that removes the access of a share that
is not served). `SmbShareInfo.version` shows it. A PATCH may send `expected_version`; when
it differs from the share's version, the answer is 409 "The share was changed meanwhile;
reload", and nothing changes.

The share form of the web UI sends the version it loaded. After a 409 the dialog stays open
and shows the error; it loads the users and, when editing, the share again and, when the
share's version changed, refills the form with the current settings and says so, so that the
administrator makes the change again from the current version. A dialog whose request was refused with
409 or 404 closes with `{stale: true}`, and the page then reloads the share list and the
status.

Implemented in `supervisor/src/shares/models.py`, `supervisor/src/shares/service.py` and
`webui/src/lib/components/shares/ShareFormModal.svelte`; tested in
`supervisor/tests/test_share_api.py` (expected_version; the version after an access change
and after dismissed notes) and `webui/tests/shares.spec.ts` (a share changed meanwhile is
refused, shown as it is now, and saved again).

## Consequences

Concurrent edits are detected instead of lost; the administrator whose save was refused sees
the share as it is and saves again. `expected_version` is optional, so a client that leaves
it out still overwrites.

## Considered options

- `updated_at` with an `if_unmodified_since` check: not taken, because an integer version is
  exact where a timestamp is not.

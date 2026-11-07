---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0134: Share edits change only the fields that are sent, and an unknown share is 404

## Context

Before slice 2, `PUT /shares/smb/{name}` replaced a share with a complete body, including
`readonly` and `valid_users`. Slice 2 keeps shares as database rows with per-user access, a
version and import notes (ADR-0074, ADR-0135), and the edit dialog of the Shares page sends
only the fields that changed.

## Decision

`PATCH /shares/smb/{name}` changes only the fields that are sent (`schemas.SmbShareUpdate`;
the name is matched ignoring case): fields that are not sent keep their value, a field sent
as null is refused, extra fields are refused (422), `access`, when sent, is the complete new
list, and `import_notes` accepts only `[]`, which dismisses the notes. A PATCH that changes
nothing writes nothing and leaves no audit entry. An unknown share answers 404 "Share not
found". `PUT /shares/smb/{name}` is gone (405), and `POST /shares/smb` refuses the old
fields such as `readonly` and `valid_users` (422).

Implemented in `supervisor/src/shares/schemas.py`, `supervisor/src/shares/service.py`
(`update_share`) and `supervisor/src/shares/router.py`; tested in
`supervisor/tests/test_share_api.py` (TestPatch, old clients get 422).

## Consequences

Clients send only what changed, together with the share's version (ADR-0135). Clients of the
old PUT stop working.

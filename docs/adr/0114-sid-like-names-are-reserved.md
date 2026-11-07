---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0114: Names that start with 's-' and a digit are reserved

## Context

Samba reads an entry of `valid users` or `write list` that parses as a security identifier
(SID) as that SID, and its parser also accepts lower case, hexadecimal authorities and
trailing text. A UNOS user named like the SID of Everyone, or of unos-smb, the primary group
of every SMB account, would pass that user's share access to every SMB account.

## Decision

`constants.linux_name_problem` refuses every name for which `SID_LIKE_PREFIX`
(`re.compile(r"s-[0-9]")`) matches with `match()`, after the reserved-name check, with
`SID_LIKE_RULE`: "Names that start with 's-' and a digit are reserved: Samba reads them as
security identifiers (SIDs)".

- New UNOS users with such names get 400 (ADR-0115).
- Existing or restored users with such names are "unavailable" for SMB and are never
  rendered into smb.conf (`smbconf._safe_user`).
- `deploy/uninstall.sh` applies the same rule, the glob `s-[0123456789]*`, before it passes
  a name to `pdbedit`, and `supervisor/tests/test_deploy_scripts.py` checks that the script
  refuses exactly the names the supervisor refuses.
- `s1`, `sam` and `s-a` stay valid.

Tested in `supervisor/tests/test_smb_models.py` (names that Samba reads as SIDs, names close
to a SID) and `supervisor/tests/test_usernames.py` (a SID-like name is unavailable and never
rendered).

## Consequences

The rule refuses more names than Samba would read as SIDs, so that it does not depend on the
details of Samba's parser.

## Considered options

- Refusing only names that are complete SIDs: Samba's parser also accepts hexadecimal
  authorities and trailing text, so a narrower rule could miss a form it accepts.

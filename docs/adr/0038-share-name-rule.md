---
status: accepted
date: 2026-11-05
area: shares
decided-by: agent
---

# ADR-0038: Share names have 1 to 32 letters, digits, '_' or '-' and do not start with '-'

## Context

A share name becomes the header of an `smb.conf` section and an argument of `smbcontrol close-share`: a name that Samba reads differently could open another section, and the names of Samba's special sections must not be taken by a share. Until Phase 0 the rule was up to 64 letters, digits and underscores, checked with `re.match` against a `$`-anchored pattern, which also accepts a trailing newline; the old CLAUDE.md still quoted it as "alphanumeric + underscore".

## Decision

- `validate_share_name` (`supervisor/src/security/validators.py`) accepts 1 to 32 ASCII letters, digits, `_` and `-`, not starting with `-`: `SHARE_NAME_PATTERN` is `^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z`, matched with `fullmatch`, so a trailing newline fails.
- Reserved names are refused in any letter case: `global`, `globals` (ADR-0093), `homes`, `printers`, `print$`, `ipc$`, `admin$`, `c$` and `d$` (`RESERVED_SMB_NAMES`).
- The smb.conf renderer checks every section name against the same rule before it writes it, and `_close_share` in `supervisor/src/shares/smb.py` passes only names that match it to `smbcontrol`.
- Tests: `supervisor/tests/test_validators.py` (`TestValidateShareName`), `supervisor/tests/test_share_api.py`.

## Consequences

- Names with spaces, dots or non-ASCII letters cannot be used.
- `docs/USER_GUIDE.md` describes this rule; the older rule of up to 64 letters, digits and underscores no longer applies anywhere.

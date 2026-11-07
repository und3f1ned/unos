---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0113: Only usernames that fit the Linux name rule and clash with nothing can be SMB names

## Context

An SMB account is a host account plus a tdbsam entry, both named after the UNOS username
(ADR-0105). Such a name must not collide with the host's system accounts or groups, and on
the device shadow's `userdel` also removes a group named like the user
(`USERGROUPS_ENAB yes`). tdbsam keys its entries by the lower-cased name, so
`smbpasswd -a alice` would overwrite a hand-made entry `Alice`.

## Decision

The host and Samba name of a user's SMB account is the UNOS username, and only when
`constants.linux_name_problem` accepts it: the name fullmatches `^[a-z][a-z0-9_-]{0,31}$`,
is not in `RESERVED_ACCOUNT_NAMES` (Debian's system users and groups, common service names
such as sshd, docker and sambashare, and unos and unos-smb), does not start with `systemd-`,
and is not SID-like (ADR-0114). `admin` is just a name: administrators cannot use SMB anyway
(ADR-0117). A user whose name breaks the rule is "unavailable" for SMB, with the reason
(`account_state.derive_smb_state`).

At every activation (`accounts._activation_checks`) the answer is 409, before any account,
row or passdb entry is written (only the group `unos-smb` may have been created by then, when
it was missing), when the host has an account of that name that UNOS did not create, a group of that
name, or a passdb entry that equals the name ignoring case but is stored differently; the
texts name the commands that remove a foreign account or entry on the host. Passdb names are
compared through their lower-cased keys everywhere, and the output of `getent` and `pdbedit`
is decoded with `errors="replace"`.

Implemented in `supervisor/src/shares/constants.py` and `supervisor/src/shares/accounts.py`;
tested in `supervisor/tests/test_smb_models.py` (TestConstants),
`supervisor/tests/test_smb_accounts.py` (TestCollisions) and
`supervisor/tests/test_usernames.py` (TestLegacyUsers).

## Consequences

Users with other names keep logging in to UNOS but cannot use SMB; they need a new account,
since users cannot be renamed (ADR-0116). A collision on the host is resolved there by an
administrator.

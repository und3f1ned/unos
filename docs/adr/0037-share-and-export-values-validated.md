---
status: accepted
date: 2026-11-05
area: shares
decided-by: agent
---

# ADR-0037: Share and export values are validated before they reach smb.conf or /etc/exports

## Context

In 1.0.0 share fields went into `smb.conf` unescaped, so a field could add directives such as `root preexec`, and the lines of `/etc/exports` were built the same way; share paths were checked by string prefix and could pass through symbolic links.

## Decision

- Values written into `smb.conf` are validated against directive injection. smb.conf has no quoting, so a line break, a NUL byte or a trailing backslash in any value is refused, and every section name must be a valid share name (ADR-0038); the serializer guards `_safe_value`, `_safe_path` and `_safe_name` of `supervisor/src/shares/smbconf.py` check every value once more before it is written.
- The fields of an NFS export (`supervisor/src/shares/nfs.py`) allow only characters that need no escaping in `/etc/exports`: a path of letters, digits and `._/-`, clients as `*`, an address, a network or a host name, and known options only.
- Share and export paths must lie strictly inside the pools base, without `..` components, and must not pass through symbolic links (`validate_pools_path` and `validate_share_path` in `supervisor/src/security/validators.py`); a share path also refuses `%`, `"`, `\` and leading, trailing or repeated whitespace, which Samba would read differently.
- `smb.conf` is replaced atomically, and only after the host's `testparm` accepts the new file (ADR-0089); when the host has no `testparm`, the file is installed with a warning in the log.
- Tests: `supervisor/tests/test_validators.py`, `supervisor/tests/test_shares.py` (NFS exports, share validation), `supervisor/tests/test_smbconf.py`, `supervisor/tests/test_smbconf_testparm.py`.

## Consequences

- Since slice 2, UNOS renders the whole `smb.conf` from its database (ADR-0074), and these checks guard every value it renders.
- Without `testparm` on the host a broken file is caught only by Samba itself; `deploy/install.sh` installs samba-common-bin, which provides it.

---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0088: Share sections never express a restriction by leaving a line out

## Context

Samba reads a missing line as its default, and for the user lists the default is open: an empty or missing `valid users` admits every user. When Samba reloads smb.conf, a parameter that disappears from a section in use keeps its old value (checked in the Samba 4.22.6 source). A restriction expressed by leaving a line out can therefore fail open, both for new connections and for connections that are open during the reload.

## Decision

Every share section that `render_share` in `supervisor/src/shares/smbconf.py` writes:

- has `read only = yes`; write access comes only from `write list`;
- always has `available = yes|no` and `write list`, with an empty value when nobody writes;
- names only active accounts in `valid users` and `write list` (ADR-0075);
- for a share that is not a guest share, always has `valid users`: its readers and writers, or `valid users = root` with `available = no` when it has no active principal. root is refused by `invalid users = root` in `[global]`, so nobody can connect, and an empty `valid users` line is never written;
- for a guest share, has no `valid users` line: Samba checks it for guests too, and a stale value there can only deny;
- ends with six fixed lines: `veto files = /.snapshots/`, `force group = unos-smb`, `create mask` and `force create mode` 0660, `directory mask` and `force directory mode` 2770 (ADR-0150).

A share that is not served (ADR-0145) is not rendered at all. Tests: `supervisor/tests/test_smbconf.py` (`TestShareSections`), `supervisor/tests/test_smbconf_testparm.py`.

## Consequences

- Removing access always changes or adds a line, never only removes one, so it holds for open connections after the reload, and connections that lost access are closed as well (ADR-0103).
- A share without principals stays closed even under a release that re-renders the file without `available` and `invalid users`: only a Samba account named root could connect, and UNOS never creates one.
- The fixed lines also mark a section as UNOS's own (`is_unos_section`, ADR-0092).

---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0086: The smb.conf import only narrows access and reports every option it drops

## Context

The import (ADR-0084) maps sections of a file that UNOS did not write onto UNOS's simpler model. Samba has many ways to restrict access (host lists, invalid users, required encryption, include files, scripts that refuse connections), and options in `[global]` are the defaults of every share. Dropping an option that restricts access would widen access silently.

## Decision

The import (`parse_smb_conf` and `section_values` in `supervisor/src/shares/smbconf.py`, `_import_section` and `_plan_import` in `supervisor/src/shares/service.py`) only ever narrows access:

- The parser reads the file the way Samba does: synonyms (writable, writeable, write ok, read only, readonly, public, browsable), boolean words in any case, keys compared ignoring case and whitespace, `[globals]` as `[global]`. A value that is not a boolean is read as the safer choice, with a note.
- Each option counts by its effective value: the section's value, else the `[global]` value written before the section, else Samba's default (read only yes, guest ok no, browseable yes, available yes, printable no, empty lists).
- In `[global]`, `hosts allow`, `hosts deny`, any `invalid users` entry and required encryption (`smb encrypt` or `server smb encrypt` set to required, mandatory, force, forced or enforced) remove the access of every imported share and turn guest access off, with a note and a report line. So does a script that refuses connections: `preexec` (or `exec`) with `preexec close` true, or `root preexec` with `root preexec close` true, with a non-empty command; a close value that is not a boolean counts as true.
- In a section, the same host lists, required encryption and scripts remove that share's access, with a note (a script also with a report line); `copy`, and `include` or `config file` anywhere in the file, remove access too. `invalid users` in a section excludes the UNOS users it names, and removes all access when an entry names no UNOS user.
- A group entry in `read list` turns write access through `read only = no` into read access; users matched only ignoring case, and dropped group entries, get a note. Guest access is kept only as read-only, never from a restored backup (ADR-0142).
- Every other option that UNOS does not keep (ownership settings, `admin users`, unknown options) is reported in the share's notes and the import report, never kept.
- In a file that starts with UNOS's header, UNOS's own lines (either variant of its `[global]`, the fixed lines of its sections) are not restrictions and produce no notes.
- Tests: `supervisor/tests/test_share_import.py` (`TestMapping`, `TestEffectiveValues`, `TestRestrictions`, `TestUnosRenderedSource`).

## Consequences

- An imported share never grants more than the old file did; it often grants less, and the notes tell the administrator what to review and grant again on the Shares page.
- Usershares are not imported: the rendered `[global]` turns them off (`usershare max shares = 0`).

## Considered options

- Carrying `[global]` host lists into new columns and listing usershares: not taken; access is removed instead.

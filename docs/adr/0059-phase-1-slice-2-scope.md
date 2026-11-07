---
status: accepted
date: 2026-11-06
area: process
decided-by: agent
---

# ADR-0059: Phase 1 slice 2 gives UNOS users SMB access per share, with guest access off by default

## Context

The November 2026 audit found that SMB worked only in guest mode: UNOS users were rows in SQLite without Samba accounts, the share form had no field for users and an edit dropped restrictions, `[global]` forced `map to guest = Bad Password`, and running `install.sh` again overwrote smb.conf with all its shares. The owner delegated the security decisions ("Decide yourself. It must be safe!", ADR-0013), and writes to the device's system disk must stay minimal (ADR-0214).

## Decision

Slice 2 of Phase 1 (ADR-0014) has one goal: SMB access for UNOS users with read and write permissions per share, guest access off by default, safe handling of existing guest shares, and no needless writes to the system disk.

- The agent makes the slice's security choices under the delegation, with safety as the criterion; each one is an ADR with `decided-by: agent`.
- The design is recorded in the shares ADRs from ADR-0074 on: the database model and smb.conf (ADR-0074 to ADR-0104), the accounts (ADR-0105 to ADR-0133), the share API, the startup reconcile and the restore (ADR-0134 to ADR-0144), the share folders (ADR-0145 to ADR-0156), audit entries (ADR-0157), host facts read again right before destructive commands (ADR-0192), and what waits for later (ADR-0158 to ADR-0164, ADR-0193 to ADR-0195). Rules of the slice in other areas: login and audit bounds (ADR-0065), the audit log (ADR-0066), two-factor codes (ADR-0067, ADR-0068), the Account page (ADR-0174), the connection hints and the accessibility of the web UI (ADR-0196, ADR-0197), uninstall (ADR-0180), the device check (ADR-0185) and the tests (ADR-0189).
- The code is in `supervisor/src/shares/` (service.py, smbconf.py, smb.py, accounts.py, repair.py, models.py, constants.py), the account flows in `supervisor/src/auth/router.py`, and the Shares, Users and Account pages of the web UI.

## Consequences

- The system-disk limit shaped many of the security choices (for example ADR-0082, ADR-0094, ADR-0157 and ADR-0159).
- Groups as share principals (ADR-0076), discovery (ADR-0158), per-share protocol options (ADR-0161) and NFS (ADR-0163) stay out of the slice.
- The slice was designed in session notes that are not kept (ADR-0060); the ADRs above are the record.

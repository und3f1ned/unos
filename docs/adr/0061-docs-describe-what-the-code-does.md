---
status: accepted
date: 2026-11-06
area: process
decided-by: agent
---

# ADR-0061: Docs describe only what the code does, checked against the code

## Context

The audit found documentation that promised what did not exist: RAID 5/6, a default administrator login, signed updates and separate roles. Users and agents act on what the docs say, so a feature described but missing is a defect of its own.

## Decision

- `CHANGELOG.md`, `docs/INSTALL.md`, `docs/USER_GUIDE.md`, `README.md` and `docs/TESTING.md` describe only what the code does, and every statement is checked against the code when it is written or changed. Slice 1 checked them and rewrote or removed the sections about the missing features it found (drag and drop, keyboard shortcuts, SMART screens, a Backup tab).
- `CHANGELOG.md` follows Keep a Changelog, and the project follows Semantic Versioning.
- Slice 2 applied the same rule to its texts. For example, `docs/INSTALL.md` says that `install.sh` writes the smb.conf that UNOS generates and that `smb.conf.unos-import-backup` exists only when the existing file was not written by UNOS (ADR-0085), and `docs/TESTING.md` says that `supervisor/tests/test_smbconf_testparm.py` refuses the two parameters that UNOS tolerates on older Samba releases (ADR-0091). `docs/USER_GUIDE.md` was updated the same way for the SMB pages: what can be shared, guest shares, what disconnects open connections, Repair permissions and SMB passwords.

## Consequences

- A doc change is checked like a code change, and a feature is documented when it works, not when it is planned; plans live in `feature-list.json` (ADR-0203).
- Upgrade notes for older installations are no longer written (ADR-0207).
- A claim that slice 1 did not catch is a defect under this rule: a review on 2026-11-07 found more, among them recovery codes, NFS exports and power control in `README.md`, and in `docs/TESTING.md` mock data that the test stack never loads.
- `LICENSE` lacked the text of the GPL until 2026-11-07 (ADR-0007).

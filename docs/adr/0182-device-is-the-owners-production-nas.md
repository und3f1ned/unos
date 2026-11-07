---
status: superseded
date: 2026-11-06
area: device
decided-by: agent
superseded-by: ADR-0220
---

# ADR-0182: The device is treated as the owner's production NAS

## Context

Superseded by ADR-0220: the device is a development system and keeps no legacy state.

The ArmSoM device held the owner's files from the guest-share era: a `DATA` share on the pool root `/mnt/pools/data` (mode 0777), two files written through it, and transmission's data on the system disk. During the SMB slice it was the only real host to verify on.

## Decision

The SMB slice treated the device as the owner's production NAS: nothing changed the owner's data without consent.

- The pool root's mode 0777 was only reported, with the suggestion `chmod 755 /mnt/pools/data`; UNOS changes no mode of an existing folder (ADR-0154).
- Access on DATA, Repair permissions on the guest-era files, guest shares and lockout tests were kept off the device; its verification used a temporary account and a temporary share (ADR-0185).
- A reboot needed the owner's consent.
- Moving the guest-era files and transmission's data stayed open owner decisions.

## Consequences

- Device checks had to work around the owner's data, and some SMB flows (guest shares, Repair on real files) were verified only in tests.
- The open owner decisions blocked new share folders below the pool root, which answered 409 until the chmod (ADR-0153).

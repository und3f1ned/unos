---
status: accepted
date: 2026-11-11
area: device
decided-by: agent
---

# ADR-0340: The shares scenario check makes a guest share, a user's own SMB activation and a repair

## Context

`shares.read-only-guest-share`, `shares.self-activation`, `shares.repair-permissions` and step 3 of `shares.acl-aware-parent-check` had no device check: ADR-0304 left guest shares out of the scripts, and the user's own activation had run only on the stand-in, which has no Samba. The smb check cannot take them: it ends with its account locked for 5 minutes (ADR-0334), and its null-session and `[global]` steps assume that no guest share exists. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent made them an area of their own.

## Decision

- `scripts/dev/scenario.sh shares` (the `shares` area of `scripts/dev/scenario.py`, after `smbconf` in `all`) has its own baseline and clean-ups, as every area (ADR-0304). Its steps, in order:
  - a sticky folder of root with a sticky folder of `nobody` in it, made over ssh on the pool, where a new share folder is refused with 409 and a share of that folder is accepted with the warning that names its owner and refused by Repair (ADR-0193);
  - Repair permissions of a share whose entries were made with other groups and modes, the private, linked and outside entries left alone, an ACL entry only where the device has `setfacl` (a SKIP otherwise), the last run with its counts, and Repair refused while the share's folder is a symbolic link (ADR-0155);
  - a regular user without two-factor login that signs in with its own token, sees SMB off and then allowed, has an unusable password refused with the reason, turns SMB on with its password, writes in a signed session and turns SMB off again (ADR-0118);
  - guest access on the check's share with the answer's warning and the guest lines of `[global]`, an anonymous session that lists and reads the share while its write is refused, and signing mandatory again after the Delete of that last guest share; with another guest share on the device, that step says so instead (ADR-0339).
- The regular user's token is kept like the administrator's (ADR-0222): by the relay, in a cache file of its own in the device's RAM (`DeviceApi.as_user` of `scripts/dev/api.py`), deleted by the clean-up; its password is made by the check and goes only to standard input.
- What a null session's share list names is reported, not asserted (ADR-0339).
- Tests: the `shares` tests of `supervisor/tests/test_dev_scenarios.py`, against its fake device.

## Consequences

- While the check runs, anyone on the network can read the check's share without a password: the files its earlier steps made, which hold only the check's fixed text, and `guest.txt`; and its `[global]` allows unsigned connections.
- The ACL step stays a SKIP on the dev device until the `acl` package is installed there, an owner action (ADR-0211).
- The Account page, the share dialog and two-factor codes stay with the backend tests and the E2E specs.

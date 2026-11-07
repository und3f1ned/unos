---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0091: The testparm check tolerates only two parameters that older Samba lacks

## Context

The apply refuses a file in which testparm reports an unknown parameter (ADR-0089): Samba would ignore that line, and a line that UNOS writes must never be ignored. Two lines of the rendered `[global]`, `server smb encrypt = default` and `smb3 unix extensions = no`, restate Samba's defaults (ADR-0087), and older Samba releases of supported systems do not know them. Refusing them would block every apply there.

## Decision

- `_testparm` in `supervisor/src/shares/smb.py` tolerates an unknown-parameter message ("Unknown parameter encountered" or "Ignoring unknown parameter") only when its line holds exactly one such message and the line ends with exactly one of the two names in double quotes (`TOLERATED_UNKNOWN_PARAMETERS`). It logs a tolerated line at INFO.
- Every other unknown parameter still refuses the file, also one on the same line before a tolerated name.
- `supervisor/tests/test_smbconf_testparm.py` keeps refusing every unknown parameter, so it fails on an older Samba although UNOS installs the file there; `docs/TESTING.md` says so.
- Tests: `supervisor/tests/test_share_apply.py` (`TestApply`).

## Consequences

- On an older Samba, smbd ignores the two lines; they only restate the defaults of the releases that know them.
- A new line in the rendered `[global]` that some Samba does not know refuses the apply there until it is added here deliberately.

## Considered options

- Refusing every unknown parameter, the earlier rule: it would block every apply on an older Samba.

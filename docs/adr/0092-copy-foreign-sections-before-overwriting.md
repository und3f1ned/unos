---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0362
---

# ADR-0092: Foreign share sections are saved to smb.conf.unos-replaced before an apply

## Context

Superseded by ADR-0362: every copy of foreign share sections is kept, named with its UTC time, the newest 10 of them.

After the one-time import (ADR-0084), every apply replaces smb.conf with UNOS's rendering. A share section that someone added to the file afterwards, by hand or through an older release running after an automatic rollback of `deploy/update.sh`, is neither a database row nor rendered by UNOS, and the next apply would drop it without a trace.

## Decision

- Before an apply writes a new smb.conf, `_prepare_file` in `supervisor/src/shares/service.py` looks for share sections of the live file that are no database row and lack UNOS's six fixed lines (`is_unos_section` in `supervisor/src/shares/smbconf.py`, ADR-0088); `[homes]`, `[printers]` and `[print$]` do not count.
- When there are such sections, the live file is first copied to `<smb.conf>.unos-replaced` (`keep_replaced` in `supervisor/src/shares/smb.py`, a durable write), unless that copy already has exactly these bytes, so a later foreign section refreshes it. A copy that cannot be written refuses the apply.
- After the install, `GET /api/v1/shares/smb-status` lists the sections as a problem ("smb.conf had shares that UNOS did not create (...); they are kept in smb.conf.unos-replaced: create them again on the Shares page") until the supervisor restarts.
- Sections that UNOS rendered itself never trigger the copy, also not the section of a share deleted a moment ago.
- Tests: `supervisor/tests/test_share_apply.py` (`TestReplaced`).

## Consequences

- A share added outside UNOS is never lost silently, but it is not served: an administrator creates it again on the Shares page.
- Only the latest copy is kept; a history of copies is the `feature-list.json` entry `shares.keep-every-replaced-copy` (ADR-0195).
- The older-release case is legacy under ADR-0207: removing the copy, or an ADR that keeps it, is the `feature-list.json` entry `shares.rollback-era-safeguards`; the copy also covers sections added by hand.

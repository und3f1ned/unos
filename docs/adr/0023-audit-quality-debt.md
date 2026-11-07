---
status: proposed
date: 2026-11-05
area: architecture
decided-by: agent
---

# ADR-0023: Pay down the audit's P2 quality and technical debt

## Context

The November 2026 audit ranked quality and technical debt as P2, after the blockers and the broken features (ADR-0014). This record keeps the list so that it is not lost while the P0 and P1 work goes first.

## Decision

Proposed: pay down these items in Phase 2, or earlier when a slice touches the code anyway:

- broad `except Exception` handlers with little logging: the audit counted 160, 28 of which only `pass`; there are about 225 on 2026-11-07;
- about 15 JSON state files under `/data` that are written non-atomically and silently reset to empty on a parse error, which loses the scheduler's and the notifications' settings (ADR-0022);
- the container runs in UTC, while schedules and times in the UI show no time zone;
- very large files (the audit named `supervisor/src/apps/catalog.py`, `supervisor/src/health/router.py` and `webui/src/routes/system/tabs/SecurityTab.svelte`, each over 1000 lines) and bypasses of the UI design system (ADR-0012);
- a desktop-only, English-only and dark-only UI; the accessibility problems of Modal and Select were fixed in slice 2 (ADR-0197);
- a full system disk blocks logins, because every attempt writes to the database and to the audit log;
- disks attached after the supervisor started stay invisible to it, because the container's `/dev` is populated once, when it starts.

## Consequences

- The items are `feature-list.json` entries (ADR-0203): `architecture.structured-logging`, `architecture.atomic-state-files`, `architecture.times-with-time-zones`, `webui.mobile-layout`, `webui.translations`, `webui.light-theme`, `auth.sign-in-with-full-system-disk` and `storage.hot-plugged-disks`, except the large files and the bypasses of the design system, which have none yet.

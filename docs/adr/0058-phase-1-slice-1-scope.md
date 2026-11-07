---
status: accepted
date: 2026-11-06
area: process
decided-by: agent
---

# ADR-0058: Phase 1 slice 1 fixes the broken user flows and the needless system-disk writes

## Context

The November 2026 audit listed user flows that the UI offered but that did not work and that were cheap to fix, and Phase 1 is delivered in reviewable, deployable slices (ADR-0014). Writing as little as possible to the device's system disk is the owner's priority for every slice (ADR-0214).

## Decision

Slice 1 fixes the broken flows of the audit:

- file download (the browser opened the download URL without the token: 403);
- delete, copy and move in the file manager (the request body was not sent: 422);
- restoring a backup from the UI (422);
- scheduled SMART tests (the device path was built as `/dev//dev/sda`);
- `GET /api/v1/containers/images` (shadowed by `GET /api/v1/containers/{container_id}`);
- creating a volume (422);
- an expired session, which looked like empty data instead of leading back to the login.

It also removes the supervisor's needless writes to the system disk (ADR-0171). Reboot and shutdown, which need the host command layer (ADR-0015), and the in-app updater (ADR-0173) stay out of the slice. The slice was committed as 9ac7b40.

## Consequences

- Fixing the flows brought related rules that have their own ADRs: request limits before authentication (ADR-0062), volumes by subvolume id (ADR-0069, ADR-0070), scrubs off the event loop (ADR-0071), SMART on whole disks (ADR-0072), backups (ADR-0165, ADR-0166), the file manager (ADR-0168 to ADR-0170), scheduled tasks (ADR-0172), health checks (ADR-0176), the CI gates (ADR-0190) and docs checked against the code (ADR-0061).
- The cases the slice's reviews left open are ADR-0063, ADR-0073, ADR-0167 and part of ADR-0175. System > Updates still offers an update that starts nothing; `deploy/update.sh` is the way to update (ADR-0173).

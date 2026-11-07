---
status: accepted
date: 2026-11-05
area: process
decided-by: agent
supersedes: [ADR-0008]
---

# ADR-0014: Work follows a severity-first plan: Phase 0, Phase 1 in slices, then Phase 2

## Context

The November 2026 audit rated UNOS 2 of 5 (alpha): it must not hold the only copy of family data yet. Its five main reasons were confirmed in the code, the first three also on the live device: any account was root on the host, the JWT secret was a published default, pools did not survive a reboot, no background automation ever started, and the work of February 2026 had never run on hardware. The roadmap of `docs/IMPROVEMENTS.md` (ADR-0008) ordered the work by features, not by these defects.

## Decision

- Work follows severity: the P0 blockers, without which no real data may be stored, come before P1 (features that are declared but broken), and P1 before P2 (quality and technical debt). Effort is estimated as S (under a day), M (1 to 5 days) or L (over a week).
- Phase 0, "close the holes" (2 to 3 weeks): the JWT secret; `require_admin` with a role matrix and hidden administrator tabs; custom tasks, the archive filter of restores, smb.conf escaping with testparm and safe file paths; the background loops in the lifespan; pools mounted by UUID with `nofail` and no writes into unmounted mountpoints; app data on a chosen pool; the real pool state and mkfs protection; HTTPS, security headers, no tokens in URLs and a local QR code; dependency upgrades; a deploy with a manual smoke test that includes a reboot. It was merged as pull request #21 on 2026-11-05.
- Phase 1, "a NAS that really works" (2 to 3 months), is delivered in slices that can each be reviewed and deployed on their own: the host command layer, SMB users, an event bus with a health banner, the disk lifecycle, off-device backup, Alembic with the JSON state in SQLite, releases with safe updates, CI, and the broken flows. Slice 1 fixed the broken flows and the needless system-disk writes (ADR-0058), slice 2 brought SMB access per user (ADR-0059); the updater (ADR-0173) and off-device backup (ADR-0041) are later slices.
- Phase 2, "product" (about a quarter): SMB extras (previous versions from snapshots, a recycle bin, Time Machine, network discovery), apps as compose stacks, UPS through NUT and disk spin-down, wear and space monitoring of the system disk with Docker log rotation and image cleanup, a runbook and an emergency command line, a responsive UI with Russian and Svelte 5, remote access, and the RK3588's hardware acceleration.
- The audit's quick wins went into Phase 0 and slice 1, except Docker log rotation and the image cleanup on the device.

## Consequences

- Every slice is chosen and judged by this order.
- By the audit's estimate, reaching beta (3 of 5) takes about three months of one developer's work.
- What to build next is tracked in `feature-list.json` (ADR-0203).

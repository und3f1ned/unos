---
status: accepted
date: 2026-11-07
area: process
decided-by: owner
supersedes: [ADR-0019, ADR-0181]
---

# ADR-0207: UNOS keeps no backward compatibility, because it has no older installs to migrate

## Context

Every breaking change of Phases 0 and 1 came with upgrade notes, migration steps and one-time code paths for 1.0.0 installs (ADR-0019), and the SMB slice added downgrade guidance (ADR-0181). But UNOS is developed on one dev device and has no users whose installations must keep working. On 2026-11-07 the owner said: "Do 1-3, no legacy, this system is specifically for development".

## Decision

- No upgrade paths, migration notes or compatibility shims for older UNOS versions, and no downgrade guidance. The dev device's state may be reset (ADR-0220).
- Removed now: the chapter "Updating from 1.0.0 or Earlier" of `docs/INSTALL.md` and the upgrade notes of `CHANGELOG.md`. `docs/IMPROVEMENTS.md` moves into `feature-list.json` (ADR-0203).
- Legacy code paths still in the code, such as the one-time migrations for 1.0.0 installs, become `feature-list.json` entries for their removal; each removal is a change of its own.
- A breaking change updates every caller in the same change (the web UI, the tests, the scripts) instead of keeping the old form.
- Features for new installations stay, for example the import of a host's existing smb.conf (ADR-0084).

## Consequences

- A change carries no migration for the data or settings of an older UNOS version.
- Fewer docs and less code to keep correct; the decisions that served only old installs are superseded.
- A later need to migrate users is a new decision: a new ADR supersedes this one (ADR-0001).

## Considered options

- Keep upgrade notes and migrations for 1.0.0 installs (ADR-0019): effort in every change for installations that do not exist.

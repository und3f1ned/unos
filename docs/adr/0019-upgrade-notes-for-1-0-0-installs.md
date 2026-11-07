---
status: superseded
date: 2026-11-05
area: process
decided-by: agent
superseded-by: ADR-0207
---

# ADR-0019: Breaking changes get upgrade notes and migration paths for 1.0.0 installs

## Context

Superseded by ADR-0207: UNOS keeps no backward compatibility, because it has no older installs to migrate.

1.0.0 was declared on 2026-02-05 without a tag or a release, and the repository is private, so the dev device was the only known older install. Phase 0 and Phase 1 changed much of how an install works: HTTPS only, a generated JWT secret, administrator-only APIs, pool mounts, app data and SMB.

## Decision

- Each breaking change got an upgrade note in CHANGELOG.md (`[Unreleased]`, "Upgrade notes (breaking changes)") and a step in the chapter "Updating from 1.0.0 or Earlier" of `docs/INSTALL.md`, so that existing installs would not break silently.
- The code carried one-time paths for installs of 1.0.0 or earlier: existing pools had to be mounted once from the Storage page to get their managed fstab entry, apps with data in `/mnt/pools/default` on the system disk were flagged and no longer started, the compose network had to be recreated once for its fixed subnet, the systemd unit was updated, and existing task ids and password hashes kept working.
- Slice 2 added notes such as restarting Samba once after the update, turning on SMB only after UNOS has installed its own smb.conf, folders that other accounts can change, and going back to the release before SMB accounts (ADR-0181), and it imported an existing smb.conf once.

## Consequences

- The notes and the chapter grew with every slice, and each one-time path was code to keep and test for an install that existed nowhere but on the dev device.
- Under ADR-0207 the chapter and the notes are removed, and the remaining one-time paths become `feature-list.json` entries for their removal; the import of a host's existing smb.conf stays, as a feature for new installations.

---
status: accepted
date: 2026-11-05
area: security
decided-by: agent
---

# ADR-0024: Credentials and the device's security settings are changed only by the owner

## Context

The delegation of ADR-0013 lets the agent decide security trade-offs, but a mistake with credentials or with the host's security settings can lock the owner out of the device or expose the NAS. Several such actions came up after the Phase 0 deploy on 2026-11-05.

## Decision

Agents do not change credentials or the device's system security settings; these are owner actions:

- changing the UNOS administrator's password and the device's root password, which CLAUDE.md once published, and switching SSH to key-only logins;
- removing the passwords from the docs once they are changed; the conversion of 2026-11-07 did this earlier, before the change (see Consequences);
- checking the router's inbound IPv6 filter for the device (ADR-0095);
- revoking the classic GitHub token and adding the device's key as a read-only deploy key (ADR-0053);
- keeping the repository private until the published passwords are changed and the git history is cleaned.

An action that the agent's permission system refuses, for example stopping a container, also goes to the owner; the agent does not work around a denial.

## Consequences

- The open owner actions are listed under Dev device in PROGRESS.md until the owner has done them.
- The repository's files must hold no secrets: since the conversion of 2026-11-07 neither the device's root password nor the UNOS administrator password that CLAUDE.md had published appears in them, and the local E2E tests, which had kept using the administrator password, use one that was never a device password (ADR-0212). Both passwords are still in git history and must be rotated on the device; that and key-only SSH are owner actions (`device.key-only-ssh` in `feature-list.json`).
- An app login that an agent sets up with the owner's permission, such as the transmission login of ADR-0219, is kept in `dev.env` (ADR-0212); the owner's own accounts and the device's security settings stay with the owner.

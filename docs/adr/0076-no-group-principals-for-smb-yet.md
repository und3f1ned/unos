---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0076: Share access names individual users, not groups, for now

## Context

The audit proposed share permissions for users and groups. In the UNOS model Samba enforces each user's level per share, and on disk every share folder belongs to one shared group, unos-smb (ADR-0150), whoever the share's principals are. Slice 2 had to stay reviewable and deployable (ADR-0014).

## Decision

- The principals of share access are individual UNOS users (ADR-0075) plus guests (ADR-0096). No row of `smb_share_access` and no entry of the rendered `valid users` and `write list` is a group.
- "Everyone: Read only" and "Everyone: Read & write" in the access picker of the Shares page (`webui/src/lib/components/shares/ShareAccessPicker.svelte`) only set the level of every listed user; they store nothing about a group.
- UNOS groups as share principals wait for the `feature-list.json` entry `shares.group-access`.

## Consequences

- A new user gets no access through a group: an administrator grants it share by share.
- Group entries met by the import (for example `@staff` in `valid users`) are not kept; the share gets a note instead (ADR-0086).

---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0377
---

# ADR-0161: Per-share protocol options wait for a later slice

## Context

Superseded by ADR-0377: a share can require encryption, serve Time Machine and list snapshots as Previous Versions.

Slice 2 (ADR-0059) gives users per-share access with a hardened, fixed `[global]` (ADR-0087)
and the same fixed lines in every share section (ADR-0088). Several per-share Samba options
fall outside that goal.

## Decision

Not part of slice 2: per-share required encryption, SMB3 as the minimum protocol
(`server min protocol = SMB3_00`), the macOS modules (`vfs objects = fruit streams_xattr`),
Time Machine, and Previous Versions over `.snapshots` (`shadow_copy2`). The minimum protocol
stays SMB2_02, and encryption stays negotiated (`server smb encrypt = default`).

## Consequences

macOS clients get no Time Machine target, Windows clients see no previous versions of files,
and no share requires encryption.

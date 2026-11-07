---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0164: Six SMB and audit follow-ups wait for later slices

## Context

The review of slice 2 (ADR-0059) before its last fixes raised more than those fixes covered,
and six items were left for later slices.

## Decision

Left for later:

- an append-only audit log: audit_log.json is still read and rewritten as a whole for every
  entry (ADR-0066);
- the commit of an API token's `last_used_at`, which still happens at every request made
  with the token (ADR-0191);
- Samba password changes that a user makes over SAMR, outside UNOS (ADR-0195);
- logging failed SMB sign-ins through Samba's `auth_audit` class, to identify a client that
  keeps failing;
- a history of `smb.conf.unos-replaced` copies: only the latest is kept (ADR-0092,
  ADR-0195);
- keeping several import backups: `smb.conf.unos-import-backup` is written once and never
  replaced (ADR-0085).

## Consequences

- Each item keeps its current behavior until its slice.
- The proposed ADR-0066, ADR-0191 and ADR-0195 describe four of them: the audit log, the
  token's last use, the SAMR password change and the history of `smb.conf.unos-replaced`.
  The `auth_audit` logging has no ADR of its own, and ADR-0085 describes the import backup
  as it is today.
- Every item is an entry of `feature-list.json`: `security.append-only-audit-log`,
  `auth.api-token-last-used-throttle`, `shares.password-changes-only-through-unos`,
  `shares.smb-auth-audit-logging`, and `shares.keep-every-replaced-copy` for both copies.

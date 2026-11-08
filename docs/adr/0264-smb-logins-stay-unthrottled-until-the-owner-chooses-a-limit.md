---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0102]
---

# ADR-0264: SMB logins have no rate limit and no lockout, as an accepted risk until the owner chooses how to limit them

## Context

ADR-0102 accepted two gaps of SMB sign-ins as a risk: no rate limit or lockout, and no check of SMB-bound passwords against a list of common passwords. ADR-0263 closes the second gap. Closing the first needs a choice with a security cost either way: Samba's lockout per account lets any client on the LAN lock an account out by failing on purpose, and a rate limit per client is a change of the host's firewall, which UNOS does not manage (ADR-0194, `shares.smb-login-protection`).

## Decision

- SMB logins still have no rate limit and no lockout. The risk stays accepted, with the mitigations of ADR-0102: SMB answers LAN addresses only (ADR-0095), SMB-bound passwords have at least 8 characters and are not common passwords (ADR-0263), administrators never use SMB (ADR-0117), and an administrator allows every SMB account (ADR-0118).
- How to limit them is the owner's choice, among:
  - Samba's lockout policy, set by the reconcile like the password history (ADR-0262): `pdbedit -P "bad lockout attempt"`, `"lockout duration"` and `"reset count minutes"`. A locked account writes nothing more to passdb until its lock ends, and with the password history a client that retries an old password does not count; but any client on the LAN can keep an account locked.
  - A limit per client address for TCP port 445 in the host's firewall, which UNOS would have to manage; it counts connections, and a client can try several sign-ins on one connection.
  - Blocking for a while a client that keeps failing, found in the journal's auth_audit lines (ADR-0261), as fail2ban does.
- Failed sign-ins are logged with the client's address and the account (ADR-0261), so that a client that keeps failing can be found in System > Logs.

## Consequences

- A client on the LAN can still try SMB passwords without being slowed down: the first step of `shares.smb-login-protection` stays open until the owner chooses.
- Each failed sign-in of an account with a passdb entry still writes to the system disk, unless it is a retry with one of the account's two previous passwords (ADR-0159, ADR-0262).

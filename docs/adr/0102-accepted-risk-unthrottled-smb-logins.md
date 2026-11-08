---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0280
---

# ADR-0102: SMB logins have no rate limit and no common-password check, as an accepted risk

## Context

Superseded by ADR-0280: SMB-bound passwords are now checked against a list of common passwords (ADR-0279), and only the missing rate limit stays an accepted risk.

The web login is limited to 5 attempts per minute per client (ADR-0010) and can require a second factor. SMB has no second factor, and UNOS sets neither a login rate limit nor a lockout for it. SMB passwords are the users' UNOS passwords (ADR-0119), and UNOS does not check them against a list of common passwords.

## Decision

For slice 2 both gaps are accepted as a risk, under the owner's delegation of security decisions (ADR-0013):

- SMB logins have no rate limit and no lockout;
- SMB-bound passwords are not checked against a list of common passwords.

The risk is accepted because of these mitigations:

- SMB answers LAN addresses only (ADR-0095);
- SMB-bound passwords have at least 8 characters (ADR-0122);
- administrators can never use SMB (ADR-0117);
- every SMB account is allowed by an administrator (ADR-0118).

Protecting SMB logins, with a rate limit or a lockout and a check of SMB-bound passwords against a list of common passwords, is the `feature-list.json` entry `shares.smb-login-protection`.

## Consequences

- A client on the LAN can try SMB passwords without being slowed down.
- Each failed sign-in of an account with a passdb entry writes to the system disk (ADR-0082); ADR-0194 lists ways to reduce these writes, among them a rate limit on the host for SMB's port.

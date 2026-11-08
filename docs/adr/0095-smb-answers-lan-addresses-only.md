---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0095: SMB answers only LAN addresses, through hosts allow in [global]

## Context

smbd listens on every address of the host. A NAS host can have global IPv6 addresses and no inbound firewall, so SMB, with its password-only logins (ADR-0102), can be reachable from the internet unless it refuses such clients itself. `hosts allow` in `[global]` applies to every connection, whatever a share sets.

## Decision

- The rendered `[global]` (ADR-0087) always has `hosts allow = 127.0.0.0/8 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 fc00::/7 fe80::/10` (`HOSTS_ALLOW` in `supervisor/src/shares/smbconf.py`): loopback, the RFC 1918 ranges, CGNAT and VPN addresses, IPv4 link-local, IPv6 ULA and link-local.
- Public IPv4 and global IPv6 clients are refused by design. The Shares page says so (`shares.smb.networkNote` in `webui/src/lib/i18n/catalog/shares.ts`), and `docs/USER_GUIDE.md` tells clients to use a LAN address (IPv4, ULA or link-local).
- Tests: `supervisor/tests/test_smbconf.py` (`TestGlobal`), `supervisor/tests/test_smbconf_testparm.py`.

## Consequences

- SMB no longer depends on the router to keep it off the internet.
- A client that reaches the NAS only through a global IPv6 address cannot use SMB.
- Docker's bridge networks lie inside 172.16.0.0/12, so containers on the host can reach SMB.
- Binding SMB to the LAN interfaces as well (`interfaces`, `bind interfaces only`) is the `feature-list.json` entry `shares.smb-bind-interfaces`; checking that the router blocks inbound IPv6 for the other services is an owner action (ADR-0024).

## Considered options

- Binding to LAN interfaces: planned as an addition (`shares.smb-bind-interfaces`).
- Relying on the router: no longer needed for SMB.

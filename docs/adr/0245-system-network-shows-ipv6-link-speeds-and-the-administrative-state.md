---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0245: System > Network shows IPv6, link speeds from sysfs and each interface's administrative state

## Context

`GET /api/v1/network/interfaces` listed only IPv4 addresses and no speed, and the routes only the IPv4 table (feature-list entry `system.network`). The only speed reader, the unused `GET /api/v1/system/network`, needs ethtool, which minimal Debian hosts lack. The page's Enable or Disable followed the operational state, so an enabled interface without a link offered Enable. The DNS servers that `resolvectl` lists on further lines were lost, its fallback servers were shown as configured ones, failed requests reached only the browser's console, and a VLAN such as `eth0.100` was listed but every action on it was refused, because the name check took no dot.

## Decision

- Addresses: every `inet` and `inet6` address of `ip -j addr show`, link-local ones included, as address/prefix length.
- Speed: `/sys/class/net/<if>/speed` on the host, read for interfaces whose operational state is UP; a value that is not positive, or a read that fails (no link, Wi-Fi), is no speed.
- `enabled` is the administrative state (the `UP` flag) and drives Disable and Enable; `state` stays the operational state, and the page shows "Disabled" for an interface that is not enabled.
- Routes: `ip -j route show` and `ip -6 -j route show`, the main tables; a host without IPv6 gets the IPv4 table. Docker's interfaces stay out, as before.
- DNS: `resolvectl status`'s "DNS Servers" with their continuation lines and its "DNS Domain" values, without fallback servers, IPv6 link-local servers and routing-only domains (`~.`); else the `nameserver` and `search` lines of `/etc/resolv.conf`.
- Every command of `supervisor/src/network/` runs on the host through `src/host.py` (ADR-0020); `ip`, `ss`, `systemd-run` and `hostnamectl` joined `HOST_TOOLS`. `resolvectl` did not, because a host without systemd-resolved is normal.
- Interface names: the API takes the names the kernel gives VLANs (`eth0.100`). `INTERFACE_NAME_PATTERN` of `supervisor/src/security/validators.py` allows `.` after the leading letter, which still keeps out `.`, `..` and names that look like options; the power API's Wake-on-LAN and the system API use the same check.
- A request that fails answers 500 with the host's message, and the page shows it: what could not be loaded in one alert, a refused Disable or Enable in another. A host command that does not answer in time fails with "the host did not answer within N seconds", not with its command line, which holds nsenter and whole host scripts.
- The tab is built from the UI components and the design tokens (ADR-0012), as its changed markup must be.
- Tests: `supervisor/tests/test_network.py`, `supervisor/tests/test_validators.py`, `webui/tests/network.spec.ts`.

## Consequences

- An interface with several IPv6 addresses shows all of them; the list's header shows the first address and how many more there are.
- The live traffic rates of the page still come from the WebSocket's statistics of the supervisor's container, matched by name (feature-list entry `system.dashboard`).

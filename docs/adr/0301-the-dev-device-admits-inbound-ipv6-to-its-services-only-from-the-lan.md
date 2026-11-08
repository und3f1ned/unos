---
status: accepted
date: 2026-11-08
area: device
decided-by: owner
supersedes: [ADR-0227]
---

# ADR-0301: The dev device admits inbound IPv6 to its services only from the LAN

## Context

The dev device has global IPv6 addresses from a Hurricane Electric tunnel prefix, and its services
listen on every IPv6 address: SSH, the web UI and transmission (through docker-proxy), Samba,
LLMNR, and NFS when it runs. ADR-0227 left inbound IPv6 unfiltered and let each service protect
itself. A probe from a network outside the LAN reached every service port. Asked whether to turn
on the filter of `device.ipv6-inbound-filter`, the owner said "Yes, turn it on".

## Decision

- The nftables table `ip6 unos_ipv6_inbound` (`scripts/dev/device/ipv6-inbound.nft`) filters the
  input and forward hooks with policy drop and accepts:
  - loopback, and established and related connections, among them the ICMPv6 errors about
    them such as packet too big for path MTU discovery; invalid packets are dropped, among them
    ICMPv6 errors about no known connection, redirects and the answers to a multicast ping;
  - the rest of ICMPv6: neighbor and router solicitations and advertisements and multicast
    listener messages (which conntrack leaves untracked) and ping;
  - every packet from the set `lan`: link-local `fe80::/10`, unique-local `fc00::/7` and the
    LAN's global prefixes;
  - TCP and UDP to the BitTorrent port 51413 from anywhere.
- IPv4 is not touched: the router's NAT keeps outside connections out, and the workstation
  reaches the device over IPv4.
- The file replaces only its own table, in one transaction (`table`, `delete table`, then the
  table). It never flushes the ruleset, which also holds Docker's iptables-nft rules.
- Docker publishes its ports on IPv6 through docker-proxy, a local socket that the input chain
  covers; IPv6 forwarding is off. The forward chain applies the same rules to the ports of a
  Docker network with IPv6, should one come (ADR-0046); such a network with global addresses
  outside the set would need its prefix in it.
- The LAN's global prefixes stay out of the repository: `scripts/dev/ipv6-filter.sh install`
  reads the device's on-link global routes (`ip -6 -o route show`, routes without a gateway,
  multipath ones excluded) and adds them to the set at the end of
  `/etc/unos-dev/ipv6-inbound.nft`. It refuses when the device has global addresses but no such
  route (a static or DHCPv6 address, or a prefix announced off-link); a device without a global
  address, as before the first router advertisement, gets no global prefix, so install runs
  once the device has its global addresses. It builds the files in RAM, checks the ruleset with
  `nft -c`, writes only a file that differs from the installed one (ADR-0214), and stops before
  the reload when a write fails.
- The file is in `/etc/unos-dev`, not `/etc/unos`: `deploy/uninstall.sh` deletes `/etc/unos` but
  does not know this unit, which would then fail open at the next boot.
- `unos-ipv6-inbound.service` (`scripts/dev/device/`) loads the file at boot before
  `network-pre.target`, as Debian's `nftables.service` does, but its stop deletes only the table
  (a table already gone does not fail it). `nftables.service` stays disabled: its stop flushes
  the whole ruleset.
- `ipv6-filter.sh install` and `remove` change a security setting of the device: they run on the
  owner's request (ADR-0024), take exactly one word, hold the device lock and report their SD
  writes; `status` only reads. `remove` fails unless the unit, both files and the table are gone.
  Like `nftables.service`, the unit fails open: `scripts/dev/check.sh` reports a problem when
  the unit is not active or the table is not loaded.
- `supervisor/tests/test_dev_scripts.py` pins the ruleset and the unit, runs the installer's and
  remove's device side with stand-ins for `ip`, `nft` and `systemctl`, and runs `check.sh` with
  the filter loaded and missing.

## Consequences

- From outside the LAN only ping and the BitTorrent port answer over IPv6: there is no remote
  IPv6 access to SSH, the web UI, Samba or transmission's web page. Remote access would need a
  VPN, or a new decision.
- The filter decides by source address. Routers never forward link-local sources, but a
  datagram from outside that forges a unique-local or LAN source is stopped only by the
  router's ingress filtering; a forged source cannot complete a TCP handshake, so this concerns
  the UDP services (LLMNR, mDNS, WS-Discovery, and rpcbind and NFS when they run).
- After the LAN's global prefix changes, `scripts/dev/ipv6-filter.sh install` runs again; until
  then LAN hosts reach the services over IPv4, unique-local and link-local addresses.
- The services' own protection stays (Samba's `hosts allow` of ADR-0095, SSH by key of
  ADR-0226, the passwords): the filter is a second layer.
- The filter belongs to the dev device: `deploy/install.sh` installs no firewall on a user's
  host.

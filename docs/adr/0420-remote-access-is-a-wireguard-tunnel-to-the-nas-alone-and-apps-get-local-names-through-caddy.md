---
status: superseded
superseded-by: ADR-0430
date: 2026-11-19
area: system
decided-by: agent
---

# ADR-0420: Remote access is a WireGuard tunnel on the host that reaches the NAS alone, and apps get single-label local names through Caddy

## Context

Superseded by ADR-0430: only in part, the tab of the local names for apps: they reach the LAN only and are in System > Network; every other rule here stays in force.

`system.remote-access` asks for remote access set up in the web UI, Tailscale or WireGuard, and for
local names of apps behind the reverse proxy. Tailscale needs an account of the owner's, WireGuard
only the kernel module and wireguard-tools, which the dev device has. The owner delegated the
decisions, "safe above all" (ADR-0013): the server's private key never leaves the host and is never
shown or logged; a peer's configuration is shown once; peers can be revoked; turning the tunnel on
or off never cuts the LAN's access to the web UI, SSH or SMB; with ufw on (ADR-0416) the tunnel's
port is let in; the tunnel comes back after a reboot.

## Decision

**WireGuard on the host** (`supervisor/src/network/wireguard.py`, System > Remote Access,
`webui/src/lib/components/remote/RemoteAccessCard.svelte`, the API under
`/api/v1/network/remote-access`, administrators only, the `network` area of API tokens, ADR-0392):

- The interface `unos-wg` is wireguard-tools' `wg-quick@unos-wg` unit of the host's systemd, enabled
  while remote access is on, so that the host brings it up at boot without the supervisor. Its
  configuration is `/etc/wireguard/unos-wg.conf` (0600), which UNOS alone writes. The kernel's own
  interfaces (`ip link`) cannot set WireGuard's keys and peers (that takes WireGuard's generic
  netlink family), so `wg` and `wg-quick` join `OPTIONAL_HOST_TOOLS` (package wireguard-tools,
  which `deploy/install.sh` does not install): without them `GET` answers `installed: false`, the
  card names the package and every change answers 409. With `UNOS_HOST_EXEC=local` every route
  answers 503, as the firewall's do (ADR-0416).
- **The server's key.** A host script makes it with `wg genkey` under `umask 077` straight into
  `/etc/wireguard/unos-wg.key` (0600), once; the configuration loads it with
  `PostUp = wg set %i private-key /etc/wireguard/unos-wg.key` and never holds it. The supervisor
  reads only `wg pubkey` of it. It never runs a command that prints a private key (`wg showconf`,
  `wg show ... dump` or `private-key`, `wg-quick strip`, a read of the key file), which
  `supervisor/tests/test_remote_access.py` checks for every command it sends.
- **Peers.** `POST .../peers` makes a peer's key pair in the supervisor's memory (X25519 of
  `cryptography`), or takes the public key of a device that made its own, and answers its
  configuration once (201): its private key and `/32` address, the server's public key, the
  endpoint and port, and `AllowedIPs` of the server's tunnel address alone. Nothing keeps that
  answer: not the database, not a log, not the audit entry. The web UI shows it once, with a QR code
  that the browser draws with the `qrcode` package it has (ADR-0027). The configuration file keeps
  each peer as a `[Peer]` with its public key, its `/32` and a comment line with its name (one to
  32 characters, none a control character or a line break); no preshared keys, so that the file the
  supervisor reads holds no secret. A revoke removes the peer from the file and, while the tunnel is
  up, from the interface (`wg set unos-wg peer <key> remove`): its tunnel ends at once.
- **What a peer reaches: the NAS alone.** The server takes from each peer only its own `/32`
  (WireGuard's cryptokey routing). The configuration's first `PostUp`, before the key, inserts
  `iptables -I FORWARD -i unos-wg -m conntrack ! --ctstate DNAT -j DROP`, and `PostDown` deletes it
  (`|| true`): the host forwards (Docker turns forwarding on) nothing from the tunnel but what
  Docker's DNAT of a published port made, so a peer reaches the NAS's tunnel address, with the web
  UI, SSH, SMB (unless SMB is bound to chosen interfaces, ADR-0391, which leave the tunnel out) and
  the apps' published ports, and not the LAN, also where the FORWARD policy is ACCEPT; a container
  only at its published ports (Docker's own FORWARD rules, which may come before UNOS's, accept no
  more). The tunnel carries IPv4 only. Reaching the whole LAN (forwarding and masquerading the
  tunnel network) is not built.
- **Values, checked before they reach a command (ADR-0293).** The listen port is UDP 1024 to 65535,
  51820 by default, and refused (409) while another program listens on it (`ss -Hulnp`). The tunnel
  network is IPv4 `/16` to `/28`, `10.99.0.0/24` by default, inside `10.0.0.0/8`, `172.16.0.0/12`
  or `192.168.0.0/16`, so that SMB's `hosts allow` (ADR-0095) and UNOS's firewall rules
  (ADR-0416) let peers in; it overlaps no address or route of the host (`ip -j -4 addr`,
  `ip -j -4 route show table all`), so the LAN, Docker's networks and other tunnels keep theirs,
  and holds neither the asking computer's address nor the peer of an established TCP connection of
  the host (`ss -Htn state established`), whose replies its route would take: a network behind the
  router, such as a guest network, that the host's tables do not show. The server takes its first
  address, each new peer the lowest free one. The endpoint, the router's public address or a
  dynamic DNS name that the administrator gives, is a DNS name or an IP address; UNOS asks no
  outside service for it. The port and the network change only while remote access is off and
  without peers (409), whose configurations name them; the endpoint changes at any time, also by an
  Enable while on, which then writes only the file, and only new peers' configurations use it.
- **On and off never cut the LAN.** Turning on writes the key and the configuration, adds the
  firewall rule below, then runs `systemctl enable --now wg-quick@unos-wg`; it compares the host's
  IPv4 routes and addresses outside `unos-wg` before and after, and turns the tunnel off again with
  a 500 when anything else changed; a failed rule or a unit that does not start turns it off and
  takes the rule away again. Turning off runs `systemctl disable --now wg-quick@unos-wg`, then
  removes the firewall rule (a rule that stays is answered with a 500 after the audit entry); the
  key, the configuration and the peers stay. Neither touches the LAN's interfaces, the default
  route, DNS, SSH, Samba or Docker.
- **The firewall.** While remote access is on and the host has ufw (on or off: its rules persist),
  UNOS's rules hold `ufw allow in proto udp from any to any port <port>` with the comment
  `UNOS: remote access` (WireGuard answers no packet that a peer's key did not sign). It is one of
  UNOS's rules of ADR-0416 (its comment starts with `UNOS:`): System > Security does not delete it,
  and the firewall's Enable and Reset add it when it is missing (`_missing_unos_rules` of
  `supervisor/src/security/firewall.py` asks `supervisor/src/network/remote_rules.py`). A change of
  it holds the firewall's lock and is refused (409) while a firewall change waits to be kept, so
  that the firewall's revert never takes it back. The changes of remote access and of the app names
  hold one lock (`wireguard.lock`), and each reads the other's rules under the firewall's lock, so
  that neither takes back the other's.
- The audit log gets `remote_access_enabled`, `remote_access_disabled`, `remote_access_changed`
  (the endpoint), `remote_access_peer_added` (name, address and public key) and
  `remote_access_peer_revoked`.

**From outside the LAN** (docs/USER_GUIDE.md): the owner forwards UDP `<port>` on the router to
the NAS's LAN IPv4 address, gives the router's public address or a dynamic DNS name as the
endpoint, and imports a peer's configuration on a device outside the LAN. Behind a carrier-grade
NAT (no public IPv4 address) no port forward reaches the NAS: that is Tailscale's case. **Tailscale
is not built** (it needs the owner's account); its seam: a second kind of remote access on the same
tab that runs the host's tailscaled (package tailscale) with an auth key the administrator gives;
its addresses, `100.64.0.0/10`, are in the local networks of ADR-0095 already, and the WireGuard
tunnel network may not use them, so both can run side by side.

**Local names for apps** (`supervisor/src/network/app_names.py`, the same tab,
`/api/v1/network/app-names`):

- **The name** is one label, `<label>-<host name>.local` (`jellyfin-unos.local`): mDNS clients
  resolve multi-label `.local` names unreliably (nss-mdns, by default, at most two labels), and a
  name that is not below the web UI's own name is another site than the web UI, so an app's pages
  never send the web UI's cookies (SameSite). An administrator gives a published TCP port of an
  installed app's main container a label of 1 to 32 characters `a-z`, `0-9` and `-`; the app's web
  port, the host port of the first TCP port of its catalog template, is offered first (Docker lists
  a container's ports sorted as text, so its first is not the web port). The list
  (`/app-names/names.json`: label, app, port) is written only on a change.
- **On the LAN** the host's unit `unos-app-names.service` (`/etc/systemd/system`, enabled, part of
  avahi-daemon's) runs `avahi-publish -a -R <name> <address>` for each name, with the host's IPv4
  address of its default route, which it reads when it starts: at boot, at a change and at
  Announce again (`POST .../apply`); without one it waits, without a log line. `-R` publishes no
  reverse record: avahi refuses a second reverse record of the host's own address, which is why
  `/etc/avahi/hosts` cannot hold these aliases. `avahi-publish` joins `OPTIONAL_HOST_TOOLS`
  (package avahi-utils, which `deploy/install.sh` does not install; avahi-daemon's own D-Bus
  interface would need a client that stays connected, which the host's tools do not offer): without
  it the card names the package and a new name answers 409. The status says when the host's address
  differs from the announced one. mDNS does not cross the tunnel: peers use the NAS's tunnel address
  and the app's port.
- **Caddy** routes by name: the supervisor writes one site per name into `/app-names/on/sites.caddy`
  of the volume `app-names` (read-write in the supervisor, read-only in the web UI,
  `deploy/docker-compose.yml`), which `webui/Caddyfile` imports, with
  `reverse_proxy 172.31.254.1:<port>` (the host on `unos-net`, plain HTTP: an app port that speaks
  only HTTPS is not served by a name), and has Caddy load it with ADR-0404's `caddy reload`, under
  the certificates' lock and with the TLS that Caddy serves; Caddy's refusal puts the file back as
  it was, and the unit with it. The web UI's command starts Caddy with the app names only when
  `caddy validate` passes with them, else without them and with one line that says so: a file that
  does not load never takes the web UI down. The apps' own ports stay as they are. A change lets
  the firewall reach the ports of the old list and the new first, then changes the unit and Caddy,
  saves the list once both took it, and removes the rules of ports no longer named last (one that
  stays is logged).
- **TLS**: HTTPS from Caddy's local CA, a certificate per name (`tls internal`); HTTP redirects to
  it, as for the web UI (ADR-0025). A browser that trusts the local CA's root for the web UI trusts
  the names too. Caddy renews each name's certificate about every 8 hours (a few KB on the system
  disk, ADR-0214): there are no names until an administrator adds one.
- **The firewall**: while names exist and the host has ufw, UNOS's rules hold
  `ufw allow in proto tcp from 172.31.254.2 to any port <port>` for each named port, with the
  comment `UNOS: app names`: Caddy's connections to the apps' ports reach the host through ufw's
  INPUT chain (ADR-0416).
- The audit log gets `app_names_changed` for a name added or removed and for Announce again.

## Consequences

- Remote access needs wireguard-tools on the host and, from outside, a port forward on the router
  and a public IPv4 address; the owner checks that path, and the device check proves the tunnel
  with a peer in a network namespace on the device itself, over a veth pair. App names need
  avahi-utils.
- A restore of UNOS's backup brings back neither the peers nor the server's key: they are the
  host's, as the firewall's rules are (ADR-0416).
- A peer is as trusted as a LAN client: SMB, SSH and the web UI answer it as one. An administrator
  revokes a lost device's peer.
- Every app name costs a certificate of Caddy's local CA and its renewals; a browser that does not
  trust the local CA warns once per name. A name announced with an address that the host has lost
  stays wrong until the unit starts again (a reboot, a change or Announce again).

## Considered options

- Tailscale: no port forward and works behind carrier-grade NAT, but needs the owner's account and
  a host package UNOS does not install; left as the seam above.
- The supervisor making the server's key: the key would pass through the container and could reach
  a log or a backup; the host's `wg genkey` keeps it on the host.
- `<app>.<host>.local` names under one wildcard certificate: one certificate, but many clients do not
  resolve them, and an app would be the same site as the web UI.
- Aliases in `/etc/avahi/hosts`: avahi refuses each, as its reverse record collides with the host's.
- Peers reaching the whole LAN: needs forwarding and masquerading of the tunnel network, and turns a
  lost device into a door to every host of the LAN.

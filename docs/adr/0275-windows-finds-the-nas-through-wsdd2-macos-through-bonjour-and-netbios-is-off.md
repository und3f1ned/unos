---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0158]
---

# ADR-0275: Windows finds the NAS through wsdd2, macOS through Samba's Bonjour announcement, and NetBIOS is off

## Context

ADR-0158 kept NetBIOS (nmbd) until UNOS added WS-Discovery for Windows and Bonjour for macOS
(`shares.smb-discovery`). Debian 13, the dev device's OS, packages wsdd2 but not wsdd; Debian 12
and Ubuntu 22.04 and 24.04 package both (Ubuntu in universe), Debian 11 neither. Debian's Samba
links the Avahi client library (samba-libs), so smbd registers its `_smb._tcp` service with
avahi-daemon itself (`multicast dns register`, on by default), and registers it again when
avahi-daemon restarts. wsdd2 binds the wildcard address and opens its TCP ports (WS-Discovery 3702,
LLMNR 5355) on every interface that is not virtual, multicast-capable or not, so also on IPv6
tunnels, whose global addresses nothing filters (ADR-0227); it answers only senders on a connected
subnet, but reads whatever reaches it. Debian's avahi-daemon settings (`use-ipv6=yes`, and
`publish-aaaa-on-ipv4` on by default) announce the host's global IPv6 addresses, and then no
link-local one, also over IPv4; macOS prefers a global IPv6 address to IPv4 (RFC 6724), and
`hosts allow` refuses clients that connect from a global address (ADR-0095). avahi-daemon
recommends libnss-mdns, which puts `mdns4_minimal [NOTFOUND=return]` before `dns` in the hosts line
of `/etc/nsswitch.conf`. Debian's `wsdd2.service` runs with `DynamicUser=true`, as the user named
after the unit, and systemd uses a static account of that name instead, with its primary group.

## Decision

- The rendered `[global]` ends with `disable netbios = yes`, `smb ports = 445` and
  `multicast dns register = yes`, extending ADR-0087 (`global_settings` in
  `supervisor/src/shares/smbconf.py`, and its copy in `deploy/install.sh`); `dns proxy = no` stays.
- `deploy/install.sh`'s `start_samba` enables and restarts smbd and turns nmbd off
  (`systemctl disable --now nmbd`), on a first installation and on a rerun.
- Both discovery services answer over IPv4 only. `configure_discovery` writes
  `/etc/avahi/avahi-daemon.unos.conf` (Debian's avahi-daemon settings with `use-ipv6=no` and
  `publish-aaaa-on-ipv4=no`), the drop-in `/etc/systemd/system/avahi-daemon.service.d/unos.conf`
  (`ExecStart=/usr/sbin/avahi-daemon -s -f /etc/avahi/avahi-daemon.unos.conf`) and the drop-in
  `/etc/systemd/system/wsdd2.service.d/unos.conf` (`ExecStart=/usr/sbin/wsdd2 -4`: WS-Discovery
  and LLMNR over IPv4 only), and reloads systemd, before it installs the packages, whose
  installation starts the services. Every run of `install.sh` writes the three files again.
- It installs `avahi-daemon` on its own line with `--no-install-recommends`, so without
  libnss-mdns, then enables and restarts it. When `apt-cache show wsdd2` finds no package (Debian
  11; Ubuntu without universe), it warns that Windows does not list the server, and the
  installation goes on; otherwise it installs `wsdd2`, enables and restarts it. A package that
  exists but does not install stops the installation with apt-get's error shown and the advice to
  run `install.sh` again, as the other packages do.
- wsdd2 keeps LLMNR: with NetBIOS off, Windows resolves the server's name through it.
- `wsdd2` is a reserved account name (`RESERVED_ACCOUNT_NAMES` in
  `supervisor/src/shares/constants.py`, and its copy in `deploy/uninstall.sh`), so no SMB host
  account takes the place of wsdd2's dynamic user.
- `deploy/uninstall.sh` enables nmbd again when it restores `smb.conf.original`, the file from
  before UNOS, and restarts smbd and nmbd. avahi-daemon, wsdd2, the drop-ins and
  `/etc/avahi/avahi-daemon.unos.conf` stay, as the samba package does; none of them is in
  `/etc/unos`, which it removes. The completion text says so, how to stop wsdd2, and how to give
  avahi-daemon its own settings back.
- The manual installation of `docs/INSTALL.md` runs the same commands and writes the same files.
- Tests: `supervisor/tests/test_smbconf.py` (TestGlobal), `supervisor/tests/test_deploy_scripts.py`
  (TestInstallScript, TestUninstallScript, TestInstallGuide), `supervisor/tests/test_smb_models.py`
  (`test_reserved_names`), and `supervisor/tests/test_smbconf_testparm.py` with the device's
  testparm.

## Consequences

- Windows lists the NAS in File Explorer's Network view and macOS in the Finder sidebar, and both
  connect to its IPv4 address, which `hosts allow` admits. Only a Windows and a macOS computer on
  the LAN can show it, in the device check.
- UDP 137 and 138 and TCP 139 close. New listeners: wsdd2 on UDP and TCP 3702 and 5355 and
  avahi-daemon on UDP 5353, all over IPv4. smbd stops listening on 139 when it restarts, not on a
  reload.
- On a LAN with IPv6 only, neither service announces the NAS.
- avahi-daemon no longer reads `/etc/avahi/avahi-daemon.conf`: settings made there (on a Raspberry
  Pi OS host, for example) no longer apply, and `install.sh` overwrites UNOS's file. The host's
  name resolution is unchanged: no libnss-mdns.
- Clients that find servers only through NetBIOS (some TVs and media players) no longer list the
  NAS, and NetBIOS name lookups of it fail: they connect by address. Clients that speak only SMB1,
  such as Windows XP, could not connect before either (`server min protocol = SMB2_02`, ADR-0087).
- On Debian 11, Windows does not list the NAS; macOS does.
- nmbd no longer runs: ADR-0094's restart after `install.sh` writes smb.conf concerns smbd alone,
  and `browse.dat` and `log.nmbd` are no longer written. `smbcontrol all reload-config` (ADR-0090)
  still reaches every smbd process. The Samba source of System > Logs still reads nmbd.service
  (ADR-0257), which now holds only what nmbd logged before.
- `scripts/dev/check.sh` still expects nmbd to be active, so it reports a problem once nmbd is off,
  until `device.check-network-discovery` changes it. On the dev device the packages and the
  services change only through the owner (ADR-0211).

## Considered options

- wsdd (Python): Debian 13 does not package it.
- wsdd2 over both IP versions: its TCP ports would listen on the global IPv6 addresses of tunnels.
- `wsdd2 -i <interface>`: `install.sh` would have to guess the LAN interface, and a renamed or
  added interface would lose discovery without a sign.
- systemd's `IPAddressAllow=` for wsdd2: on a kernel without cgroup BPF, systemd runs the service
  unfiltered.
- avahi-daemon over both IP versions: Finder would list the NAS and fail to connect from a Mac
  with a global IPv6 address.
- `use-ipv6=no` alone: avahi-daemon would still announce the IPv6 addresses over IPv4.
- Editing `/etc/avahi/avahi-daemon.conf` in place: it is a conffile, so dpkg would ask at upgrades,
  and it exists only once the package is installed, whose installation starts avahi-daemon with
  IPv6.
- UNOS's avahi-daemon settings in `/etc/unos`: `uninstall.sh` removes that folder and keeps the
  drop-in, and avahi-daemon would not start without its file.
- A static avahi service file for `_smb._tcp`: smbd registers the service itself, and Finder would
  list the NAS twice.

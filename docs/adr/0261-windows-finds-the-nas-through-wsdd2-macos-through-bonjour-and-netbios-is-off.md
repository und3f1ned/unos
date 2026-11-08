---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0158]
---

# ADR-0261: Windows finds the NAS through wsdd2, macOS through Samba's Bonjour announcement, and NetBIOS is off

## Context

ADR-0158 kept NetBIOS (nmbd) until UNOS added WS-Discovery for Windows and Bonjour for macOS
(`shares.smb-discovery`). Debian 13, the dev device's OS, packages wsdd2 but not wsdd; Debian 12
and Ubuntu 22.04 and 24.04 package both, Debian 11 neither. Debian's Samba links the Avahi client
library (samba-libs), so smbd registers its `_smb._tcp` service with avahi-daemon itself
(`multicast dns register`, on by default). wsdd2 binds the wildcard address and opens its TCP ports
(WS-Discovery 3702, LLMNR 5355) on every interface that is not virtual, multicast-capable or not,
so also on IPv6 tunnels, whose global addresses nothing filters (ADR-0227); it answers only senders
on a connected subnet, but reads whatever reaches it.

## Decision

- The rendered `[global]` ends with `disable netbios = yes`, `smb ports = 445` and
  `multicast dns register = yes`, extending ADR-0087 (`global_settings` in
  `supervisor/src/shares/smbconf.py`, and its copy in `deploy/install.sh`); `dns proxy = no` stays.
- `deploy/install.sh` installs `avahi-daemon` with its other packages. Its `start_samba` enables
  and restarts smbd and turns nmbd off (`systemctl disable --now nmbd`), on a first installation
  and on a rerun.
- Its `configure_discovery` enables and starts avahi-daemon, then writes the drop-in
  `/etc/systemd/system/wsdd2.service.d/unos.conf` (`ExecStart=/usr/sbin/wsdd2 -4`: WS-Discovery
  and LLMNR over IPv4 only) and reloads systemd before it installs `wsdd2`, whose installation
  starts it; then it enables and restarts wsdd2. Without a wsdd2 package (Debian 11) it warns that
  Windows does not list the server, and the installation goes on.
- wsdd2 keeps LLMNR: with NetBIOS off, Windows resolves the server's name through it.
  avahi-daemon keeps its defaults: it uses the multicast interfaces that are not point-to-point,
  and drops unicast queries from addresses that are not on the link.
- `deploy/uninstall.sh` enables nmbd again when it restores `smb.conf.original`, the file from
  before UNOS, and restarts smbd and nmbd. avahi-daemon, wsdd2 and the drop-in stay, as the samba
  package does; the completion text says so and how to stop wsdd2.
- The manual installation of `docs/INSTALL.md` runs the same commands.
- Tests: `supervisor/tests/test_smbconf.py` (TestGlobal), `supervisor/tests/test_deploy_scripts.py`
  (TestInstallScript, TestUninstallScript, TestInstallGuide), and
  `supervisor/tests/test_smbconf_testparm.py` with the device's testparm.

## Consequences

- Windows lists the NAS in File Explorer's Network view and macOS in the Finder sidebar. Only a
  Windows and a macOS computer on the LAN can show it, in the device check.
- UDP 137 and 138 and TCP 139 close. New listeners: wsdd2 on UDP and TCP 3702 and 5355 over IPv4,
  avahi-daemon on UDP 5353. smbd stops listening on 139 when it restarts, not on a reload.
- Clients that find servers only through NetBIOS (Windows XP, some TVs and media players) no longer
  list the NAS, and NetBIOS name lookups of it fail: they connect by address.
- On Debian 11, Windows does not list the NAS; macOS does.
- nmbd no longer runs: ADR-0094's restart after `install.sh` writes smb.conf concerns smbd alone,
  and `browse.dat` and `log.nmbd` are no longer written. `smbcontrol all reload-config` (ADR-0090)
  still reaches every smbd process.
- `scripts/dev/check.sh` still expects nmbd to be active. On the dev device the packages and the
  services change only through the owner (ADR-0211).

## Considered options

- wsdd (Python): Debian 13 does not package it.
- wsdd2 over both IP versions: its TCP ports would listen on the global IPv6 addresses of tunnels.
- `wsdd2 -i <interface>`: `install.sh` would have to guess the LAN interface, and a renamed or
  added interface would lose discovery without a sign.
- systemd's `IPAddressAllow=` for wsdd2: on a kernel without cgroup BPF, systemd runs the service
  unfiltered.
- A static avahi service file for `_smb._tcp`: smbd registers the service itself, and Finder would
  list the NAS twice.

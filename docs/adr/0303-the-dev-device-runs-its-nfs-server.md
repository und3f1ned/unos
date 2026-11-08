---
status: accepted
date: 2026-11-08
area: device
decided-by: owner
---

# ADR-0303: The dev device runs its NFS server

## Context

`nfs-kernel-server` was installed on the dev device for the device check of
`shares.nfs-exports`; `deploy/install.sh` does not install it (ADR-0292). Its rpcbind and nfsd
listen on every address of the host, and while inbound IPv6 was unfiltered (ADR-0227) they were
stopped and disabled after the check. Since ADR-0301 the device admits inbound IPv6 to its
services only from the LAN. Asked whether to turn NFS on again, the owner chose "Enable NFS".

## Decision

- `rpcbind.socket`, `rpcbind.service` and `nfs-server.service` are enabled and run on the dev
  device, so that its NFS exports are served, also after a reboot.
- They are reached over IPv6 only from LAN sources (ADR-0301), and over IPv4 only from the LAN
  behind the router's NAT. UNOS exports only to addresses and networks of the local network
  (ADR-0293).
- Nothing changes for other hosts: `deploy/install.sh` still leaves the NFS server to the owner
  of the host (ADR-0292).

## Consequences

- The NFS tab works on the device; the device check of `shares.nfs-exports` ran against it.
- rpcbind and NFS are among the UDP services that a datagram forging a LAN source reaches,
  unless the router's ingress filtering stops it (ADR-0301).
- The NFS server keeps its state in `/var/lib/nfs` on the SD card: an export added or deleted
  writes about 0.3 MiB, starting the server once 0.35 MiB (ADR-0214).
- `systemctl disable --now nfs-server rpcbind.socket rpcbind` turns it off again.

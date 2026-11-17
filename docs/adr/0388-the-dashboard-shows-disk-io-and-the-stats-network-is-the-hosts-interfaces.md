---
status: accepted
date: 2026-11-17
area: system
decided-by: agent
---

# ADR-0388: The dashboard shows the disks' I/O, and the network of the stats message is the traffic of the host's interfaces

## Context

The entry `system.dashboard` says that the dashboard shows live CPU, memory, network and disk I/O,
but its stats cards had no disk I/O, which the stats message of `/ws/v1/events` already carried.
Its network was not the host's: psutil reads `/proc/net/dev`, which lists the interfaces of the
network namespace of the process that reads it, and the supervisor's container has its own
(`unos-net` of `deploy/docker-compose.yml`), so the dashboard and the Resource Monitor showed the
traffic of the container's `eth0`, mostly this socket's own. System > Network, which matches the
live rates with the host's interfaces by name, showed that traffic on a host port named `eth0`
and no live rates at all on a host whose port has another name. The disk rates came from psutil's
totals over every device of `/sys/block`: zram (where Armbian keeps its swap and `/var/log`), loop,
device mapper and md devices counted as disk I/O, a write through LUKS or LVM twice, a disk
removed made the rates negative and one attached added all its counters at once. The rates were
taken over the wall clock, which a board without a battery-backed clock sets at boot, and over
whatever time had passed since the last sample.

## Decision

- The dashboard's stats widget (`webui/src/lib/components/widgets/SystemStatsWidget.svelte`) has
  five cards: CPU, Memory, Network, Disk I/O and Temperature, in two columns on a phone, three from
  `md` and five from `xl`. The Disk I/O card shows `disk_io.read_rate` (Read) and
  `disk_io.write_rate` (Write) in bytes per second, written as the Network card writes its rates:
  powers of 1024 with one decimal and the units of `dashboard.unit.*` and `dashboard.rate`
  (ADR-0297).
- The network of the stats message (`get_system_stats` of `supervisor/src/websocket/events.py`) is
  the host's: PID 1's `/proc/1/net/dev` (`HOST_NET_DEV`; with `pid: host` PID 1 is the host's
  init), read as a file in the sampler's worker thread and parsed by `links.parse_net_dev` of
  `supervisor/src/network/links.py`, which `GET /api/v1/network/stats` uses too; with
  `UNOS_HOST_EXEC=local` (the stand-in, the compose test stack) psutil's counters of the machine
  the supervisor runs on. Only the interfaces that System > Network lists count: not `lo`,
  `dummy0`, `docker0`, `veth*` or `br-*` (`links.is_hidden`).
- The disk rates are of the host's physical disks: the devices of `/proc/diskstats` (not
  namespaced) with a `device` in their folder of `/sys/block` (`SYS_BLOCK`), which leaves out
  partitions and zram, loop, device mapper and md devices; where there is no `/sys/block` (the
  stand-in on macOS), psutil lists whole disks only, and each counts. One read of
  `/proc/diskstats` per sample gives these rates and `disk_io.disks`, every block device but loop
  and RAM devices, by name, for the Storage page (ADR-0360).
- Each interface's and each disk's rate is the growth of its counters since the last sample; the
  network's and the disks' rates are the sums. An interface or a disk that the last sample did not
  have counts no rate yet, one that is gone counts nothing, and one whose counter went back (a
  driver loaded again) counts nothing for that sample: no rate is ever negative or adds all of a
  device's counters at once.
- The message carries what the pages read: `network` has `bytes_sent_rate`, `bytes_recv_rate` and
  `interfaces`, each with `name`, `bytes_sent`, `bytes_recv`, `bytes_sent_rate` and
  `bytes_recv_rate`; `disk_io` has `read_rate`, `write_rate` and `disks`, each with `name`. Gone
  (ADR-0207): an interface's `ipv4`, `speed` and `is_up`, which were the container's (System >
  Network takes them from the host); the network's totals of bytes and packets, the disks' totals
  of bytes and operations and each block device's counters, which no page read
  (system.stats-without-unread-parts).
- The host's interfaces with their traffic are System > Network's, an administrator's page
  (ADR-0030): a regular user's socket gets the network's rates with `interfaces` empty
  (`_for_role`, checked with the role of the socket's last credential check), and the sample that
  every socket shares stays as it is.
- When `/proc/1/net/dev` cannot be read, the message has no interfaces and no traffic, not the
  container's, and the log says so once, not every two seconds (ADR-0214).
- Every rate is taken over the sampler's monotonic clock (`_clock`), not the wall clock.
- Samples are taken only while a socket asks for them (ADR-0374), so the first after a gap would
  give the average since the last one as the current rate: a dashboard opened in the morning showed
  the night's backup for two seconds. A sample more than `RATE_MAX_GAP_SECONDS` (10 s, five
  intervals) after the last has rates of 0, like the first sample after a start, and is the base
  of the next.
- Cost on a slow board (ADR-0374): one read of a small procfs file and a stat of each block
  device's `device` per sample, which every socket shares, and no process; psutil's per-sample
  `net_if_stats` and `net_if_addrs` and its second read of `/proc/diskstats` are gone.
- Tests: `supervisor/tests/test_ws_stats.py` (the host's interfaces from the file, the hidden ones
  left out, the stand-in's psutil, interfaces and disks that come, go or start again, the physical
  disks only, the monotonic clock, a sample after a gap, an unreadable file, the message's shape);
  `supervisor/tests/test_ws_auth.py` (`TestTheInterfacesOnTheEventsSocket`: the interfaces by
  role); `webui/tests/dashboard.spec.ts` (the cards follow each message of a fake socket while a
  minute passes without a request to the API, the cards show each message of the stand-in's own
  socket, the cards in Russian, a regular user's socket without interfaces).

## Consequences

- The dashboard, the Resource Monitor and System > Network show the host's traffic. A bridge, bond
  or VLAN that the host's own configuration puts over a port (a `br0` over `eth0`, not Docker's
  `br-*`) counts the same traffic on both, and a tunnel (`wg0`, a 6in4 tunnel) counts again what
  its port carries: the total is then higher than the traffic on the wire.
- I/O to a software RAID or an encrypted or LVM volume counts once, on its disks; I/O of zram, a
  RAM disk, does not count.
- On the stand-in, the cards show the workstation's interfaces as psutil names them, its
  loopback included where it is not called `lo`.
- Three endpoints that no page calls still report the container's network: `GET
  /api/v1/system/stats`, and `GET /api/v1/monitor/network`, with `ipv4`, `speed` and `is_up` and
  rates over the wall clock that can be negative, beside `GET /api/v1/monitor/disk-io`, whose
  rates of psutil's totals are over the wall clock too. The ledger entry
  `system.no-container-stats-endpoints` removes them (ADR-0207).
- The first message of a socket that opens after a gap has no rates; the next, 2 seconds later,
  has them.
- The device check of the host's traffic and disks on the dashboard is by hand: the stand-in has
  no other network namespace and no `/sys/block` of a NAS.

## Considered options

- `nsenter` with `cat /proc/net/dev` per sample, as `GET /api/v1/network/stats` does: a process
  every two seconds while a dashboard is open, on boards where one sample is already a share of a
  core (ADR-0374).
- Totals clamped at 0 instead of rates per interface and per disk: a sample in which a device went
  away would hide the real traffic, and one that came would still add all its counters at once.
- Null rates for the first sample after a gap, shown as a dash: every reader of the rates (the
  dashboard, the Resource Monitor, System > Network) would have to tell a missing rate from a
  number, for two seconds after a page opens.

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
(`unos-net` of `deploy/docker-compose.yml`), so the dashboard, the Resource Monitor and the live
rates of System > Network showed the container's traffic, mostly this socket's own. The total of
every interface also fell when one went away (a USB adapter, a container's veth), which made a rate
negative for a sample, and the rates were taken over the wall clock, which a board without a
battery-backed clock sets at boot, and over whatever time had passed since the last sample.

## Decision

- The dashboard's stats widget (`webui/src/lib/components/widgets/SystemStatsWidget.svelte`) has
  five cards: CPU, Memory, Network, Disk I/O and Temperature, in two columns on a phone, three from
  `md` and five from `xl`. The Disk I/O card shows `disk_io.read_rate` (Read) and
  `disk_io.write_rate` (Write) in bytes per second, written as the Network card writes its rates:
  powers of 1024 with one decimal and the units of `dashboard.unit.*` and `dashboard.rate`
  (ADR-0297).
- The network of the stats message (`get_system_stats` of `supervisor/src/websocket/events.py`) is
  the host's: PID 1's `/proc/1/net/dev` (`HOST_NET_DEV`; with `pid: host` PID 1 is the host's
  init), read as a file in the sampler's worker thread; with `UNOS_HOST_EXEC=local` (the stand-in,
  the compose test stack) psutil's counters of the machine the supervisor runs on. Only the
  interfaces that System > Network lists count: not the loopback or Docker's bridges and veths
  (`links.is_hidden` of `supervisor/src/network/links.py`).
- Each interface's rate is the growth of its counters since the last sample; the totals and their
  rates are the sums over the interfaces. An interface that the last sample did not have counts no
  rate yet, one that is gone counts nothing, and one whose counter went back (its driver loaded
  again) counts nothing for that sample: no rate is ever negative or adds all of an interface's
  counters at once.
- An entry of `network.interfaces` has `name`, `bytes_sent`, `bytes_recv`, `bytes_sent_rate` and
  `bytes_recv_rate`. Its `ipv4`, `speed` and `is_up` were the container's, and no page read them:
  they are gone (ADR-0207). System > Network takes an interface's addresses, speed and state from
  the host and its live rates from this list, by name.
- When `/proc/1/net/dev` cannot be read, the message has no interfaces and no traffic, not the
  container's, and the log says so once, not every two seconds (ADR-0214).
- The disk I/O rates are of psutil's totals of the host's disks (`/proc/diskstats` is not
  namespaced), whole disks without their partitions; a total that went back (a disk removed)
  gives 0 for that sample.
- Every rate is taken over the sampler's monotonic clock (`_clock`), not the wall clock.
- Samples are taken only while a socket asks for them (ADR-0374), so the first after a gap would
  give the average since the last one as the current rate: a dashboard opened in the morning showed
  the night's backup for two seconds. A sample more than `RATE_MAX_GAP_SECONDS` (10 s, five
  intervals) after the last has rates of 0, like the first sample after a start, and is the base
  of the next.
- Cost on a slow board (ADR-0374): one read of a small procfs file per sample, which every socket
  shares, and no process; psutil's per-sample `net_if_stats` and `net_if_addrs` are gone.
- Tests: `supervisor/tests/test_ws_stats.py` (the host's interfaces from the file, the hidden ones
  left out, interfaces that come, go or start again, the monotonic clock, a sample after a gap,
  an unreadable file, the disk rates, the message's shape); `webui/tests/dashboard.spec.ts` (the
  cards follow each message of a fake socket while a minute passes without a request to the API,
  the cards show each message of the stand-in's own socket, the cards in Russian).

## Consequences

- The dashboard, the Resource Monitor and System > Network show the host's traffic. A bridge, bond
  or VLAN that the host's own configuration puts over a port (a `br0` over `eth0`, not Docker's
  `br-*`) counts the same traffic on both, and a tunnel (`wg0`) counts again what its port
  carries: the total is then higher than the traffic on the wire.
- psutil's disk totals count every device of `/sys/block` without its partitions, also md, device
  mapper (LUKS, LVM), loop and zram devices: I/O through a device mapper or md counts on both
  devices.
- On the stand-in, the cards show the workstation's interfaces as psutil names them, its
  loopback included where it is not called `lo`.
- `GET /api/v1/system/stats`, which no page calls, still reports the container's network counters.
- The first message of a socket that opens after a gap has no rates; the next, 2 seconds later,
  has them.
- The device check of the host's traffic on the dashboard is by hand: the stand-in has no other
  network namespace.

## Considered options

- `nsenter` with `cat /proc/net/dev` per sample, as `GET /api/v1/network/stats` does: a process
  every two seconds while a dashboard is open, on boards where one sample is already a share of a
  core (ADR-0374).
- Totals clamped at 0 instead of rates per interface: a sample in which an interface went away
  would hide the real traffic, and one that came would still add all its counters at once.

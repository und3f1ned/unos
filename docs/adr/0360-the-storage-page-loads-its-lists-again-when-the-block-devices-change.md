---
status: accepted
date: 2026-11-12
area: storage
decided-by: agent
---

# ADR-0360: The Storage page loads its lists again when the block devices of the stats stream change

## Context

ADR-0023 recorded that disks attached after the supervisor started stay invisible to it, because the container's `/dev` is populated once, when it starts (`storage.hot-plugged-disks`). For the disk list that no longer holds: `GET /api/v1/storage/disks` runs lsblk at every request in the host's namespaces (`supervisor/src/storage/disks.py`, ADR-0020), which sees the host's `/dev`, `/sys` and udev database, and nothing caches its answer. The Storage page asked for its lists only when it opened and when the Create Pool dialog opened, so a disk attached while the page was open was never shown until the user opened the page again. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent chose how the page learns of a new disk.

## Decision

- `webui/src/routes/storage/+page.svelte` follows the names of `disk_io.disks` in the stats of `/ws/v1/events`, which the server sends every 2 seconds with every device of `/proc/diskstats` (disks and partitions, without loop and RAM devices; `get_system_stats` of `supervisor/src/websocket/events.py`). The kernel lists a device there as soon as it has one, whatever the container's `/dev` holds.
- When those names differ from the ones of the last load and stayed the same for one message, the page loads the disks, the pools, the filesystems found on the disks and the scrub statuses again (`loadData(false, false)`), without its spinner: a disk shows within about 4 seconds of the kernel naming it, and a removed one goes the same way. The wait for the second message gives udev the time to read a new device, so that lsblk reports its model, serial number and filesystems; the names of the stream's first message are those of the lists the page loads when it opens.
- Not while a dialog of the page is open (Create Pool, Mount or Unmount, Import, Remove Pool, Volumes): its lists stay as the user saw them, and they are loaded once it closes. Not while the lists load either: once they are loaded.
- The page's loads are ordered: each takes the next number of a sequence, and its answers (the disks, the pools, the filesystems found on the disks, the scrub statuses) are shown only while it is the latest load of their list; the disk list that the Create Pool dialog loads when it opens is a load of the disk list. So a reload whose answers come late, lsblk or btrfs being slow, does not overwrite what a newer load shows, such as the one after an unmount. A dialog that opens while a reload for the devices is in flight drops that reload's answers, and the reload runs again once the dialog closes, unless a newer load of the lists started meanwhile.
- A reload for the devices keeps the error at the top of the page (a scrub that did not start or stop): the user did not start it. A load that the user starts, when the page opens or after a change made in a dialog, clears it, as before.
- No HTTP polling (ADR-0006): the page makes requests only when the devices changed. The server is unchanged.
- Tests: `webui/tests/storage.spec.ts` (Physical disks: a disk attached while the page is open is listed without a reload and a removed one goes; while a dialog is open the lists stay, and are loaded once it closes; a reload for the devices that answers after a dialog opened changes nothing in it and runs again once it closes; one that answers after the user unmounted a pool does not show it mounted again; a reload for the devices keeps the error of the page, and a load the user starts clears it), with `fakeBlockDevices` of `webui/tests/storage-fakes.ts` in place of the stream and its `holdReads` option for a slow host; `supervisor/tests/test_disk_safety.py` (`TestDisksEndpoint`: a disk attached or removed after a request is in the next answer).

## Consequences

- A change of the host's block devices that is not a disk of the Storage page, such as a device-mapper or md device that comes or goes, loads the lists once too: a few host commands, which only read.
- With the stats stream down, the page shows the lists of its last load until the stream is back, as before this change.
- The cause that ADR-0023 named still holds for smartctl, which runs in the container (ADR-0290): the node of a disk attached after the supervisor started is missing from the container's `/dev`, so its SMART data, its self-tests and its snapshots of the SMART monitor fail until the supervisor restarts. Fixing that takes the host's `/dev` in the container (a bind mount `/dev:/dev` in `deploy/docker-compose.yml`, a host-level change for the dev device's check) or smartctl on the host, a decision of its own (`storage.hot-plugged-disks-smart`).

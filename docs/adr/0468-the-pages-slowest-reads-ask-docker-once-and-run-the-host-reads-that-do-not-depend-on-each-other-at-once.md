---
status: superseded
date: 2026-11-23
area: architecture
decided-by: agent
superseded-by: ADR-0476
---

# ADR-0468: The pages' slowest reads ask Docker for its lists and run the host reads that do not depend on each other at once

## Context

Superseded by ADR-0476: the container list asks Docker only for the images it shows.

On the dev device (BPI-W3, RK3588) on 2026-11-23 the reads that the web UI's pages wait for took,
p50: `GET /api/v1/containers?all=true` 417 ms (the Dashboard), `/shares/paths` 181,
`/shares/smb-interfaces` 130, `/shares/smb-status` 103, `/storage/pools` 102, `/storage/disks` 84.
The causes, counted with the fakes of the tests and timed on the device with `scripts/dev/api.py
GET` only:

- The container list asked Docker 2N+2 requests: docker-py's `containers.list` inspects every
  container (system ones too), the old `_image_label` inspected each container's image, and
  `get_client` asks the version. With Docker's containerd image store an image's inspect computes
  the image's size: about 40 ms each on the device (`GET /containers/images`, 109 inspects, took
  4.5 s), so six containers cost about 400 ms.
- `GET /shares/paths` ran 10 host commands one after another: `list_pools` (a `btrfs filesystem
  show --raw` that probes every block device and can wake a sleeping disk, ADR-0378; findmnt; the
  fstab; each mounted pool's `btrfs filesystem usage` and `device stats`, which the answer does not
  use), `managed_pool_names` (the fstab again) and each pool's `list_volumes` (findmnt and the scan
  again, `rootid`, the subvolume list).
- The other four ran 3 to 9 host commands one after another, each a process started through
  nsenter (ADR-0020), about 10 to 15 ms each on the device (`/storage/disks`: 7 commands, 84 ms).

## Decision

- **Two Docker lists for the container list.** `containers/docker.py` builds every
  `ContainerInfo` from the container's summary in Docker's list of containers,
  `client.api.containers(all=...)` (`_info`), and the images' tags from Docker's list of images,
  `client.api.images()` (`GET /images/json`, `_image_tags`): no inspect of a container or of an
  image, only the version query that `get_client` makes for every client, as before. The answer
  keeps its fields and their meaning, except where the lists hold less:
  - `image` is the container's image's first tag that is no digest form (`name@sha256:...`,
    which the containerd image store lists among the tags of an image pulled by its digest);
    without one, the reference the container was created from while it still names the
    container's image, as `docker ps` shows it (the digest that the app catalog created it from);
    else (Docker then gives the image's ID, or the container was created by an ID) the image's
    short ID, `sha256:` and 12 hex digits. The first version of this decision showed the
    reference alone, without the image list: after its deploy on 2026-11-23 Immich, which the
    catalog creates from a digest, showed `ghcr.io/immich-app/immich-server@sha256:...` instead of
    its image's tag `ghcr.io/immich-app/immich-server:v3.4.1`. Transmission's image has only its
    digest form, which it shows, as before.
  - `created` is RFC 3339 in UTC to the second (`2026-11-18T09:14:03Z`), without Docker's
    nanoseconds.
  - `ports` are sorted by the text `port/protocol`, as Docker sorts its inspect's port map (the
    list gives them in no order), one for each address of the host that publishes the port, and
    one without a host port for a port that no host port publishes.
  - `name` is the container's own name: of the list's `Names`, the one that is no legacy link's
    (`/<other>/<alias>`).
- `GET /containers/{id}`, and the answers of start, stop and restart, find the container with
  Docker's inspect (its ID, a prefix of it, or its name; 404 otherwise) and answer its summary of
  the list filtered by its full ID (Docker looks the filter `id` up in its index of IDs, as an ID
  or a prefix of one, not as a regular expression) with the images' tags: the same answer as the
  list's, also for the ports that no host port publishes, which the single answer left out
  before.
- **Host reads at once.** `host.at_once(*reads)` (`supervisor/src/host.py`) runs blocking reads
  that do not depend on each other, each in a thread of its own and in a copy of the caller's
  context, at most `AT_ONCE_THREADS` (8) of one call at a time, and returns their results in
  order once all have ended; the first read in order that raised raises then. A read that calls
  `at_once` again has its own bound (one shared bound could deadlock), and a read for which no
  thread can be started runs in the caller's thread. It is only for reads that change nothing. A
  request then waits for the slowest read of each round instead of their sum:
  - `GET /shares/smb-interfaces`: `ip -j addr show`, `ss` and `smbstatus --json` at once (one
    round instead of three, `listen._read_now`).
  - `GET /shares/smb-status`: the Samba tools' check, the gid's group (`gid_problem`) and the
    fstab's pool names at once, then the group `unos-smb`, the host's accounts and `pdbedit -L` at
    once (two rounds instead of six; `service._host_checks`, `service.service_status`,
    `accounts.host_status`). `pdbedit -L` now runs also when `getent passwd` fails; its answer is
    then not used, as before.
  - `GET /storage/pools` (and the RAID monitor's `pools_and_managed_names(scan=False)`): btrfs's
    list, findmnt and the fstab at once, then every pool at once, each with its `usage` and `device
    stats` at once (two rounds instead of three and two per mounted pool). The scan of the disks
    stays: the Storage page lists the pools found on them (ADR-0378's consequences).
  - `GET /storage/disks`: lsblk, findmnt, swapon, `/proc/cmdline`, the idle timeouts' rule and
    hdparm's check at once (`disks._collect_host_state` serves the other readers of the disks
    too), then every disk at once, each with its power read before its wipefs, which still
    probes only an awake disk (two rounds, three with a disk that lsblk shows nothing on, instead
    of six commands plus one or two per disk). A disk whose power read hangs holds the page for its
    own limit, no longer added to the other disks' reads.
- **The share paths read only what they list.** `pools.mounted_pools()` reads the mounted
  filesystems as the kernel knows them (`mounted_btrfs_filesystems`: `btrfs filesystem show
  --mounted`, no disk read), findmnt and the fstab once, at once: the pools that `list_pools`
  gives a mountpoint, and the names that UNOS manages. The router reads each pool's volumes
  (`pools.mounted_pool_volumes`: `rootid`, the subvolume list and the paths' check) for all pools
  at once, and no pool's use or error counters. A sleeping disk stays asleep. The answer is the
  same, also when the fstab, findmnt, `btrfs filesystem show --mounted` or a pool's subvolumes
  cannot be read; only a failure of the scan of the disks or of a pool's use, which listed nothing
  before, no longer hides the mounted pools.
- **No cache.** Every answer is read when it is asked: nothing can be stale, and nothing needs to
  be invalidated after a change that UNOS makes.
- Tests: `supervisor/tests/test_fast_page_reads.py` counts the Docker requests against a real
  docker-py client with a fake HTTP API (two lists for the container list; Immich's and
  Transmission's images as on the device), the share paths' commands and woken disks, and how many
  host commands each other read waits for one after another (each fake command takes 0.5 s);
  `test_containers.py`, `test_volumes.py`, `test_share_api.py`, `test_smb_interfaces.py` and the
  storage tests keep the answers; `test_disk_spindown.py` no longer expects the disks' reads in
  lsblk's order.

## Consequences

- A page's request starts several processes on the host at the same moment: 6 for the disk
  list's first round, 3 for most others, and for the pool list two per mounted pool, at most 8
  pools at a time. The same CPU as before, spent in a shorter burst: the work is the same for the
  four reads that only run at once; the container list and the share paths do less. A thread is
  made for each read, also for one that waits for the bound.
- When a read of a round fails, the reads after it in that round have run too; their results
  are dropped as before.
- A container's `image` passes over a digest form among its image's tags, which the first tag
  could be before; `created` loses its fraction of a second.
- The list of images is of every image, not only the containers': with the containerd image
  store Docker computes each listed image's size for it (109 images on the dev device), which a
  measurement after the deploy shows; still one request instead of one inspect per image.
- The tests of the reads that run at once take one to two seconds each; a test thread that starts
  more than 0.5 s late would make one fail.
- Left as they are: `GET /containers/images` still inspects every image (4.5 s on the device),
  and the App Store's container reads use docker-py's `containers.list`; neither is one of these
  six reads.

## Considered options

- A short cache of each answer (a few seconds): a disk pulled or a pool's new errors would show
  late, and every change of UNOS would have to drop it; the fixes leave little to save.
- The reference that the container was created from alone, without the list of images (the first
  version): a container that the app catalog created from a digest showed the digest instead of
  its image's tag.
- An inspect of each container's image for its tags: about 40 ms each on the device, the cost
  that this decision removed.
- Reading the host's files through `/proc/1/root` instead of `cat` through nsenter: one process
  less per file, but a change of the host boundary (ADR-0020) and of every fake that answers
  `cat`, beyond these reads.
- Running the reads as coroutines with `asyncio.gather`: the readers are blocking functions
  called from worker threads and from the loops; threads keep them as they are.

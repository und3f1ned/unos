---
status: accepted
date: 2026-11-24
area: architecture
decided-by: agent
supersedes: [ADR-0478]
---

# ADR-0495: The pages' slowest reads start no Samba tool while passdb is unchanged, ask Docker only for the images they show and run the host reads that do not depend on each other at once

## Context

ADR-0478's device check on the BPI-W3 on 2026-11-23 (25 reads each through Caddy, p50/p95 ms)
failed for `GET /shares/smb-status`, 126/202 against its p50 before, 103, and for the p95 of
`/storage/disks`, 89 against 84; measured again on 2026-11-24 with the SD standby's card mounted
(ADR-0488) and Docker's data on the pool (ADR-0486): smb-status 121/185 and 129/198,
`/shares/smb-interfaces` 128/176 and 124/142 (130 before), `/storage/pools` 93/109 and 87/107
(102 before), the disks 62/79 and 59/76. The host's processes that each request started, watched
on the device in `/proc` while it ran, and the commands timed there read-only, named the causes:

- Samba's tools start slowly: `pdbedit -V`, which only prints its version, takes 49 to 89 ms on
  the W3's Cortex-A76 cores and about 165 ms on its Cortex-A55 cores (`taskset`), loading about 100
  shared libraries; `smbstatus` loads about 140 and takes 74 to 115 ms. The smb-status read waited
  for `pdbedit -L` (51 to 94 ms through nsenter, with an empty passdb) after the first round of
  its reads, and the smb-interfaces read for `smbstatus --json` (103 to 115 ms), only to count
  the open sessions. Every other command of these reads takes 3 to 10 ms, `ss -p` about 43.
- The SD standby's card holds a btrfs filesystem labelled `UNOS-STANDBY`, a valid pool name, not
  mounted at a pool's mountpoint and not managed: the pool list took it for a pool found on the
  disks and checked its devices with lsblk, findmnt, swapon and the kernel's command line, a third
  round of host commands (about 25 ms) before it left it out.

ADR-0478's own context follows, restated. ADR-0468 had the container list ask Docker for its list
of every image, for the images' tags. On the BPI-W3 (Docker 29.9.3, containerd image store, 117
images, 104 of them untagged leftovers of builds) that list took 700 to 750 ms, and
`GET /api/v1/containers?all=true` went from 77 ms to 836 ms p50 on 2026-11-23; measured there,
the images filtered by a reference took 101 ms, with `dangling=false` 151 ms, an image's inspect
68 ms, the list of containers 53 ms. Before ADR-0468, on 2026-11-23, the reads that the web UI's
pages wait for took, p50: `GET /api/v1/containers?all=true` 417 ms (the Dashboard),
`/shares/paths` 181, `/shares/smb-interfaces` 130, `/shares/smb-status` 103, `/storage/pools`
102, `/storage/disks` 84. The causes, counted with the fakes of the tests and timed on the device
with `scripts/dev/api.py GET` only:

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

- **Two Docker lists for the container list, the second only of the images it shows.**
  `containers/docker.py` builds every `ContainerInfo` from the container's summary in Docker's
  list of containers, `client.api.containers(all=...)` (`_info`), and the images' tags from one
  list of Docker's images, `client.api.images(filters=...)` (`GET /images/json`, `_image_tags`),
  for the containers that the answer shows (UNOS's own left out): none without such a container;
  while each of them names its image by a reference (a tag, or a digest, that still names it),
  the images of their repositories, the filter `reference` with each one's familiar name
  (`_repository`: without its tag, digest, `docker.io/` and `library/`), which Docker matches
  against each name of an image in both image stores and then lists only the names that match;
  once one of them names its image by its ID (`_by_image_id`: its reference names another image
  now, or it was created by an ID or a prefix of one), which no reference filter finds, the
  images that have a name, `dangling=false`, without the untagged leftovers. No inspect of a
  container or of an image, only the version query that `get_client` makes for every client, as
  before. The answer keeps its fields and their meaning, except where the lists hold less:
  - `image` is, for a container whose reference still names its image, its image's first tag
    that is no digest form (`name@sha256:...`, which the containerd image store lists among the
    tags of an image pulled by its digest) in the repository of that reference, else the
    reference, as `docker ps` shows it (the digest that the app catalog created it from); for one
    named by its image's ID, the image's first such tag, else its short ID, `sha256:` and 12 hex
    digits. Immich, which the catalog creates from a digest, shows its image's tag
    `ghcr.io/immich-app/immich-server:v3.4.1`; Transmission's image has only its digest form,
    which it shows.
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
  or a prefix of one, not as a regular expression) with its image's tags, asked for that one
  container as the list asks: the same answer as the list's, also for the ports that no host
  port publishes.
- **Host reads at once.** `host.at_once(*reads)` (`supervisor/src/host.py`) runs blocking reads
  that do not depend on each other, each in a thread of its own and in a copy of the caller's
  context, at most `AT_ONCE_THREADS` (8) of one call at a time, and returns their results in
  order once all have ended; the first read in order that raised raises then. A read that calls
  `at_once` again has its own bound (one shared bound could deadlock), and a read for which no
  thread can be started runs in the caller's thread. It is only for reads that change nothing. A
  request then waits for the slowest read of each round instead of their sum:
  - `GET /shares/smb-interfaces`: `ip -j addr show` and `ss -H -n -t -p state listening state
    established '( sport = :445 )'` at once (one round instead of three, `listen._read_now`).
    New here: the open connections, the answer's `sessions`, are the established TCP connections
    to the host's port 445 in that `ss` (`listen.read_smbd_sockets`), not the sessions of
    `smbstatus --json`: what a
    restart of smbd closes, as the Shares page already says ("open SMB connections"). Restart
    SMB's audit entry counts them the same way. `smbstatus` stays for the flows that end a user's
    sessions (ADR-0104), which need their user names.
  - `GET /shares/smb-status`: the Samba tools' check, passdb.tdb's stat, the gid's group
    (`gid_problem`) and the fstab's pool names at once, then the group `unos-smb`, the host's
    accounts and Samba's passdb names at once (two rounds instead of six; `service._host_checks`,
    `service.service_status`, `accounts.host_status`).
  - `GET /storage/pools` (and the RAID monitor's `pools_and_managed_names(scan=False)`): btrfs's
    list, findmnt and the fstab at once, then every pool at once, each with its `usage` and `device
    stats` at once (two rounds instead of three and two per mounted pool). The scan of the disks
    stays: the Storage page lists the pools found on them (ADR-0378's consequences). New here: a
    btrfs filesystem labelled `UNOS-BOOT` or `UNOS-STANDBY` (`disks.STANDBY_LABELS`) is no pool
    (`pools._pool_choices`), as the start's guard of pools that are not mounted already holds
    (ADR-0488): the SD standby's card, never a data disk, whose devices the pool list no longer
    asks lsblk about. No pool action reads, mounts or unmounts a filesystem by either name
    (`pools._pool_filesystems`, 404), and no pool is created or imported under either name
    (`pools._new_pool_name`, 400): disks.py takes a device with either label for the standby's
    card.
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
- **No cache, but Samba's passdb names for the status, kept for passdb.tdb's stat.** Every answer
  is read when it is asked, but for one list, new here: the passdb names of
  `GET /shares/smb-status` (`unmanaged_samba_users`). `accounts.passdb_stamp()` reads
  `stat --format='%d %i %s %.9Y %.9Z' -- /var/lib/samba/private/passdb.tdb` (`PASSDB_STAMP`:
  tdbsam, as `smbconf.py` renders `passdb backend`, in Debian's and Ubuntu's private directory)
  in the first round, before pdbedit can run; `accounts.status_passdb_names(stamp)` answers the
  names that `pdbedit -L` listed after the same stamp, and runs `pdbedit -L` otherwise, keeping
  its list with that stamp. tdbsam changes passdb only in a transaction, which writes the file
  and syncs it: each change sets the file's modification and change times, also through its
  memory map (the write fault after the sync), and a replaced file has another inode, so the same
  stat is the same passdb; UNOS's own changes (smbpasswd, pdbedit) and smbd's (a bad password
  count) need no invalidation. A file changed fewer than `PASSDB_SETTLE` (2) seconds before its
  stat keeps no list, since a second change within the kernel's timestamp granularity could
  leave the times as they were; nor does a stat that fails or prints something else: pdbedit
  then runs at every read, as before. Only the status uses it: every flow that changes accounts
  lists passdb itself right before a destructive command (ADR-0192). The list lives in the
  supervisor's memory and is listed again after each start.
- Tests: `supervisor/tests/test_fast_page_reads.py` counts the Docker requests against a real
  docker-py client with a fake HTTP API that answers and filters as each image store does
  (`containerd`, `classic`): the image list's filters, none without a container, the answers
  (Immich's and Transmission's images as on the device, an image tagged in two repositories), the
  share paths' commands and woken disks, how many host commands each other read waits for one
  after another (each fake command takes 0.5 s), the SMB reads without smbstatus and, while
  passdb.tdb's stat is the same, without pdbedit, and the pool list with the SD standby's card,
  which is no pool by its name either;
  `test_containers.py`, `test_volumes.py`, `test_share_api.py`, `test_smb_interfaces.py` and the
  storage tests keep the answers; `test_disk_spindown.py` no longer expects the disks' reads in
  lsblk's order. The FakeSambaHost of `tests/samba_fake.py` answers passdb.tdb's stat, whose
  size and times change with passdb's entries.

## Consequences

- A page's request starts several processes on the host at the same moment: 6 for the disk
  list's first round, 4 for the status's, 2 or 3 for most others, and for the pool list two per
  mounted pool, at most 8 pools at a time. The same CPU as before, spent in a shorter burst; the
  container list, the share paths, the pool list with the SD standby's card and the two SMB reads
  do less. A thread is made for each read, also for one that waits for the bound.
- When a read of a round fails, the reads after it in that round have run too; their results
  are dropped as before.
- The first status read after the supervisor's start, and the first after each change of
  passdb, still waits for pdbedit, 50 to 165 ms on the W3; an administrator's unmanaged Samba
  account shows at the next read after its change, as before. A clock set forward right after a
  change could keep a list for a stat that a second change in the same timestamp did not move.
- `sessions` of `GET /shares/smb-interfaces` counts TCP connections, not authenticated
  sessions: a client still signing in counts, and a multichannel client counts each of its
  connections. A host whose smbd listens on another port than 445 counts none (UNOS renders
  `smb ports = 445`, ADR-0275).
- A btrfs filesystem labelled `UNOS-STANDBY` or `UNOS-BOOT` is never a pool: it is not listed,
  read, mounted or unmounted by that name, and the checks of its devices keep it out of the
  importable filesystems and refuse its import (disks.py takes its device for the standby's card).
- A container's `image` passes over a digest form among its image's tags; for an image tagged in
  several repositories it is the first tag of the container's own repository, not the first of
  all, whichever other containers the list holds; `created` loses its fraction of a second.
- With the containerd image store Docker computes each listed image's size: the list of images
  costs about 100 ms on the BPI-W3 for the containers' repositories, about 150 ms once a
  container is named by its image's ID, and grows with the images listed.
- The tests of the reads that run at once take one to two seconds each; a test thread that starts
  more than 0.5 s late would make one fail.
- Left as they are: `GET /containers/images` still inspects every image (4.5 s on the device),
  and the App Store's container reads use docker-py's `containers.list`; neither is one of these
  six reads. The CPU frequency governor (ondemand, the A76 cores idling at 408 MHz) and the
  scheduler's choice of an A55 core are the host's settings, not UNOS's.

## Considered options

- Reading the names from passdb.tdb's records in Python, without pdbedit: no tool, but a reader
  of tdb's file format without its locks, which could see a transaction half written, and the
  stored names inside Samba's packed account records.
- Leaving the open connections out of the page's read and counting them when a restart dialog
  opens: an API and a web UI change, when the `ss` that the read runs already lists them.
- Starting pdbedit at once with the other reads and the database's: still 50 to 165 ms.
- Pinning Samba's tools to the big cores (`taskset`): specific to the board, and its start still
  costs 41 to 60 ms.
- A short cache of each answer (a few seconds): a disk pulled or a pool's new errors would show
  late, and every change of UNOS would have to drop it.
- The reference that the container was created from alone, without the list of images (the first
  version of ADR-0468): a container that the app catalog created from a digest showed the digest
  instead of its image's tag.
- The list of every image (ADR-0468): 700 to 750 ms on the BPI-W3, most of it for the untagged
  leftovers of builds.
- `dangling=false` always: exact for every image, but its cost grows with all the tagged images
  of the host, not with the containers'.
- An inspect of each container's image for its tags: 40 to 68 ms each on the device, one after
  another.
- Reading the host's files through `/proc/1/root` instead of `cat` through nsenter: one process
  less per file, but a change of the host boundary (ADR-0020) and of every fake that answers
  `cat`, beyond these reads.
- Running the reads as coroutines with `asyncio.gather`: the readers are blocking functions
  called from worker threads and from the loops; threads keep them as they are.

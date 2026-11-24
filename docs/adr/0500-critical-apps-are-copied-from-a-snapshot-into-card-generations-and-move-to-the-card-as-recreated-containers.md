---
status: accepted
date: 2026-11-24
area: system
decided-by: agent
---

# ADR-0500: Critical apps are copied from a pool snapshot into generations on the card and move to the card as containers created again from their definitions

## Context

ADR-0471 (phase 3) and ADR-0488 design the critical apps of the SD standby: the mark, the copy of an app's
folder with its images and Docker definitions, the move of a failed pool's apps to the card, the run on the
standby and the discarding return; the entry `system.sd-standby-critical-apps` lists its steps. They leave
open where the copy and its state live, how a sync is scheduled and compared, how the containers are made
again, what the triggers count, and what a board allows. The owner delegated these choices ("go on without
me", 2026-11-24), above all safe: no write to a pool that btrfs forced read-only, no deletion of a pool's
data, no woken disk (ADR-0378), no container with a bind to a missing pool path, no write to the eMMC beyond a
real change (ADR-0214).

## Decision

**The mark** (`supervisor/src/standby/models.py`, `src/apps/critical.py`). The table `standby_apps` (app,
interval in hours of 1, 2, 4, 6, 12 or 24, marked_at; Alembic revision 0002, created IF NOT EXISTS as the
baseline is, ADR-0444). `GET /api/v1/apps/standby` says whether the mark is offered (the standby set up, its
card present, UNOS on the eMMC); `GET` and `PUT /api/v1/apps/installed/{id}/critical` answer the dialog and
mark, change or unmark, for administrators only, audited as `app_critical_changed`. The dialog lists the
entries of `{pool}/apps/<app>/` with `du -sb` (caches and copies left out of each), the caches, the copies of
ADR-0408 (`<folder>.previous` of a `copy_before_update` bind), and the app's other pool folders without a
size: a media folder can hold millions of files and is never copied. A new mark is refused (409) when the
card's free space less the app's copy and twice its images' size (the archives, and their loaded copy on a
run of the standby) is below the reserve, 10% of the card and at least 2 GiB. Also refused: no standby, UNOS
on the card, an app outside the catalog or without data on a pool, and an app that runs from the card. An
unmark deletes the app's copies on the card. A catalog `VolumeMapping` gets `cache` (a writable folder under
`{pool}/apps/<app>/` only): Jellyfin's cache, Immich's model-cache, Netdata's cache, Linkwarden's next-cache,
Plex's transcode and Paperless-ngx's scratch.

**The copy** (`supervisor/src/standby/apps.py`). On the card's btrfs top level, `@app-<app>-<n>` holds `data/`
(the app's folder without its caches, `*.previous`, `*.copying` and `*.rolled-back`), `images/<id>.tar.gz`
(`docker save` by image id, kept from the previous generation while the id stays) and `definitions.json`
(each current container's Config, HostConfig, network aliases, image id and whether it ran, companions first,
and the app's network). `state.json` keeps `apps.<app>`: the generation, its time, the pool, the MiB of the
last 8 syncs that wrote, the bytes of data and images, an error, and a move. A sync first compares with reads
only (rsync's dry run from the live folder, the image ids, the definitions but for whether a container runs)
and writes nothing, the pool included, when nothing changed (rsync's status 24, files that vanished while it
read the running app's folder, is no failure there); otherwise, while the card keeps its reserve free, it
takes a read-only snapshot
`.unos-standby-snapshot` of the subvolume that holds the folder (the nearest parent whose inode is 256), copies
from it into a snapshot of the last generation in the sync's unit (ADR-0481, ADR-0488), names the new
generation in `state.json`, then deletes the old one and the snapshot; its flushes are `sync -f` of the card
alone, so that a hung pool holds no app's sync. Every write of `state.json`, and the supervisor's read of it,
hold one lock (`service._stored_lock`): a move's record and a sync's result never replace each other. App
syncs run from the RAID monitor's
pass (every 300 s), one at a time with the system's syncs under the standby's lock: an interval of 24 hours is
due once the day's sync time passed, a shorter one when it passed since the last complete sync less 3
minutes; a pool that is not mounted, forced read-only or whose disks sleep (`spindown.pool_sleeps`) is passed
over, and the first pass that finds it awake syncs. The Standby tab's space by part comes from `df` of the
card and from `du` of each generation, run once when a sync wrote it. A check that found nothing changed is
kept in RAM: `standby_copy_old` waits for the first RAID pass after a start.

**The move** (`move_app`). It applies to a pool that holds a critical app's data, at the RAID monitor's pass:
at once when btrfs forced it read-only, after one more copy from the live folder (no snapshot: nothing is
written to that pool) within 5 minutes while `ls` of it answers and no other sync holds the card; or at the
second pass in a row that finds it
managed (its fstab entry) and not mounted or not listed, or missing more devices than its data profile spares
(single, raid0, dup and an unknown profile 0; raid1, raid10, raid5 1; raid1c3, raid6 2; raid1c4 3). A pool
that UNOS unmounted or removed (not managed) and one read while UNOS changes it count nothing; the counts are
in RAM. The move takes the app's lock, writes a writable snapshot `@app-<app>-run` of the generation (which
stays as synced) with the definitions read anew, removes the current containers (not ADR-0408's earlier ones)
and creates them again through Docker's create of the inspected Config and HostConfig: binds under
`{pool}/apps/<app>/` on the run copy's `data/`, a cache on a tmpfs `size=256m`, every other bind below the
pools' folder on the card's `empty` folder or `empty-file`, read-only; the label `unos.app.pool` gives way to
`unos.standby.card=<pool>`, so that an unmount's check of the pool's users and a mount's start of its apps
pass them over. "Ran" includes a container that Docker failed to start at its own start (its pool missing)
and whose policy restarts it. When the images that run now are not those of the copy (an update after the
last sync), the copy's own definitions are used, their images loaded from its archives, since a newer
version may not read the older data. Companions start first, those the main container depends on waited for
(180 s). An app without a copy is stopped and stays as it is. A move whose creates fail is recorded all the
same (a repeated failure of the automatic move is audited once), and the next start of the supervisor
creates the app's containers on the card from the run copy's definitions; containers already on the card
are only recorded again. A drill or a planned move syncs each app once more first. `state.json` records the move, which `apps_on_standby` (error,
its key naming the apps, so that a new set alerts again) and `standby_apps_moved` (by the system, or the
administrator's drill and planned move) report. While moved, an app gets no sync, and its update, rollback,
uninstall, reinstall and unmark, and Remove the standby, answer 409.

**The way back and the starts.** The discarding return (`POST /api/v1/system/standby/apps/return`, the card's
model typed) needs the pool mounted, writable and not degraded and every pool folder that the containers bind
there, and changes nothing otherwise; it creates the pool-bound containers from the definitions saved at the
move, starts those that ran, clears the record and deletes the run copy last. An uninstall unmarks the app
and deletes its copies. Before it serves, the
supervisor of either system moves again a pool-bound container of an app that `state.json` records as moved
(bounded to 120 s). On the standby's root it loads each critical app's archives that its Docker lacks
(`docker load`), creates its network and containers by image id, on the run copy when moved, on the pool when
it is mounted and healthy, and moves it otherwise. The Storage page's unmount and removal, refused with 409,
read the standby's apps of the pool and offer the planned move, then try again.

**The boards.** On the BPI-W3 and the BPI-F3, Docker's data is on the data pool (ADR-0486): a failure of that
pool stops Docker, the move cannot run there, and `unos-standby-watch.service` reboots to the card (ADR-0488),
whose start runs the apps from their copies. The move in place covers the other pools, such as the drills'
test pool on a USB stick. Nothing here depends on the card's device name (`mmcblk1` on the W3, `mmcblk0` on
the F3): it comes from the card's mounts.

## Consequences

- A sync that found a change writes a btrfs snapshot's metadata to the app's pool and the changed blocks to
  the card; the mark is a row of the database, whose volume is on the pool (ADR-0486), and nothing of it is
  written to the eMMC. An idle app costs a dry run of its folder per interval.
- Changes made between the last sync and a failure stay on the failed pool; a return discards the card's,
  until `system.sd-standby-failback` copies them back.
- The supervisor's start after a reboot forgets the counted passes: a pool that stays failed moves its apps two
  passes after the start.
- Immich's machine learning keeps its models in its cache: on the card's tmpfs of 256 MiB it downloads them
  again and may not fit, so face and search indexing wait for the return.
- Tests: `supervisor/tests/test_standby_apps.py`, with `tests/standby_fake.py` and `tests/docker_fake.py`;
  `webui/tests/standby.spec.ts`, `apps-critical.spec.ts` and `storage.spec.ts`.

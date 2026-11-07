---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
supersedes: [ADR-0232]
---

# ADR-0234: The Snapshots page offers mounted pools and their volumes, also a pool whose volumes cannot be read, and the Volumes dialog takes a quota in GiB

## Context

ADR-0232 made the Snapshots page offer the volumes of mounted pools and gave the Volumes dialog its quota field. Its review found that one pool whose volumes could not be read (a disk that dropped off, a hung filesystem) made `GET /api/v1/snapshots/subvolumes` answer 500, so that the page offered no pool at all; that the names UNOS gives snapshots and restored copies came from the container's clock, while the list shows the browser's time without saying so; and that `GET /api/v1/snapshots/` without a pool or volume listed only the folders right in the pools base, without the volumes' snapshots. This restates ADR-0232, the web UI of storage.snapshots and storage.volumes, with those changes.

## Decision

- `GET /api/v1/snapshots/subvolumes` lists every pool mounted at its mountpoint, each followed by its volumes, as `{path, pool, volume, volumes_error}` (`volume` is null for the pool itself): `storage.pools.list_pools` and `list_volumes`, which read the host through `supervisor/src/host.py`, as the Storage page does. A pool mounted at a subvolume other than its top level is offered without volumes (ADR-0069). A pool whose volumes cannot be read (`list_volumes` raises RuntimeError, or btrfs does not answer within `VOLUME_TIMEOUT`) is offered alone with the reason in `volumes_error`, beside the other pools. A mount table or pool list that cannot be read answers 500 with the reason instead of an empty list. The findmnt call in the container and the field `device` are gone (ADR-0207).
- The Snapshots page (`webui/src/routes/snapshots/+page.svelte`) picks one of them in "Pool or volume" and lists its snapshots; a pool with `volumes_error` gets the warning "The volumes of <pool> could not be read" with the reason. Create, restore and delete use the dialogs of the shared components instead of the browser's `confirm()` and `alert()` (ADR-0012, ADR-0197); their commands act on the folders that were opened (ADR-0233).
- `GET /api/v1/snapshots/` lists the snapshots of the pool or volume in `subvolume`, which is required: 422 without it (ADR-0207).
- A snapshot's `created` is btrfs's creation time in UTC with its offset (`2025-01-24T12:49:31+00:00`): the browser shows it in its own time zone, and the list sorts by it, newest first. A time btrfs prints in another form is passed on as printed. Create answers its time in the same form.
- The names UNOS gives, `snap_<date>_<time>` (a snapshot made without a name), `restore_<date>_<time>` (a restored copy) and the scheduler's `auto_<date>_<time>`, take the date and time in UTC (`utc_stamp` in `supervisor/src/snapshots/router.py`), whatever the container's time zone; the page and `docs/USER_GUIDE.md` say so.
- The list measures nothing: no `du`, and `SnapshotInfo` has no `size`.
- When `.snapshots` cannot be created (a pool that is read-only), create answers 500 with the reason, such as "Read-only file system".
- Scheduled snapshot tasks still take pools only (`target_pool`); volumes are snapshotted on the Snapshots page. The page, the Volumes dialog and the guide say to take snapshots of each volume as well.
- The Volumes dialog (`webui/src/routes/storage/+page.svelte`) has "Quota in GiB (optional)": empty for no limit, otherwise digits with an optional decimal point, more than 0 and at most 8388607 GiB, the most whose bytes stay exact in JSON. It is sent as `quota` in bytes (`quotaBytes` in `webui/src/lib/api/storage.ts`); Create stays disabled for any other text. The 409 when quotas are not enabled on the pool is shown as the supervisor words it, with the `btrfs quota enable` command; UNOS does not enable quotas itself.
- Tests: `supervisor/tests/test_snapshots.py`, `supervisor/tests/test_scheduler_validation.py`, `webui/tests/snapshots.spec.ts`, `webui/tests/storage.spec.ts`, `webui/tests/scheduler.spec.ts` and `webui/tests/api.spec.ts`.

## Consequences

- A volume keeps its snapshots in its own `.snapshots` folder. The volume cannot be deleted while it holds them (ADR-0069), and the restored copy of a volume's snapshot is a subvolume inside the volume that no page deletes yet (storage.restore-copies-deletable).
- The page shows no snapshot sizes. What a snapshot holds alone would come from quota groups, which only pools with quotas enabled have.
- The list costs one `btrfs subvolume show` per snapshot, and the picker reads the pools and volumes on the host like the Storage page. A pool that hangs still holds the picker back until btrfs times out on it.
- A name and the Created column of the same snapshot differ by the browser's offset from UTC; the texts say which is which.

## Considered options

- Sizes from the "Usage exclusive" line of `btrfs subvolume show` where quotas are enabled: more parsing for a number that most pools do not have.
- A unit menu (MiB, GiB, TiB) for the quota: GiB with decimals covers the same sizes in one field.
- Names with a `Z` after the time: a new name format for what the page and the guide already say.

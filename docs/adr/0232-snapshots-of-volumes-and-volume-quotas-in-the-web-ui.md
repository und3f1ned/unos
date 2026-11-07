---
status: accepted
date: 2026-11-07
area: storage
decided-by: agent
---

# ADR-0232: The Snapshots page offers the volumes of mounted pools, and the Volumes dialog takes a quota in GiB

## Context

The snapshot API took any folder in a pool (`_validate_subvolume` in `supervisor/src/snapshots/router.py`), but `GET /snapshots/subvolumes` listed only the btrfs mountpoints that findmnt showed in the container, so the Snapshots page could not snapshot a volume (ADR-0069). Its list ran `du -sh` on every snapshot at every listing, a walk of all its files with a timeout of 600 s each that wakes the disks, and dropped the offset of btrfs's creation time, so a browser outside UTC showed the time hours off. ADR-0069 has the Volumes dialog send an optional quota, but it sent only the name. This settles the web UI of storage.snapshots and storage.volumes.

## Decision

- `GET /api/v1/snapshots/subvolumes` lists every pool mounted at its mountpoint, each followed by its volumes, as `{path, pool, volume}` (`volume` is null for the pool itself): `storage.pools.list_pools` and `list_volumes`, which read the host through `supervisor/src/host.py`, as the Storage page does. A pool mounted at a subvolume other than its top level is offered without volumes (ADR-0069). Any other read error, an unreadable mount table of the host included, answers 500 with the reason instead of an empty list. The findmnt call in the container and the field `device` are gone (ADR-0207).
- The Snapshots page (`webui/src/routes/snapshots/+page.svelte`) picks one of them in "Pool or volume" and lists its snapshots. Create, restore and delete use the dialogs of the shared components instead of the browser's `confirm()` and `alert()` (ADR-0012, ADR-0197).
- A snapshot's `created` is btrfs's creation time in UTC with its offset (`2025-01-24T12:49:31+00:00`): the browser shows it in its own time zone, and the list sorts by it, newest first. A time btrfs prints in another form is passed on as printed. Create answers its time in the same form.
- The list measures nothing: no `du`, and `SnapshotInfo` has no `size`.
- When `.snapshots` cannot be created (a pool that is read-only), create answers 500 with the reason, such as "Read-only file system".
- Scheduled snapshot tasks still take pools only (`target_pool`); volumes are snapshotted on the Snapshots page.
- The Volumes dialog (`webui/src/routes/storage/+page.svelte`) has "Quota in GiB (optional)": empty for no limit, otherwise digits with an optional decimal point, more than 0 and at most 8388607 GiB, the most whose bytes stay exact in JSON. It is sent as `quota` in bytes (`quotaBytes` in `webui/src/lib/api/storage.ts`); Create stays disabled for any other text. The 409 when quotas are not enabled on the pool is shown as the supervisor words it, with the `btrfs quota enable` command; UNOS does not enable quotas itself.
- Tests: `supervisor/tests/test_snapshots.py`, `supervisor/tests/test_scheduler_validation.py`, `webui/tests/snapshots.spec.ts`, `webui/tests/storage.spec.ts` and `webui/tests/scheduler.spec.ts`.

## Consequences

- A volume keeps its snapshots in its own `.snapshots` folder. The volume cannot be deleted while it holds them (ADR-0069), and the restored copy of a volume's snapshot is a subvolume inside the volume that no page deletes yet (storage.restore-copies-deletable).
- The page shows no snapshot sizes. What a snapshot holds alone would come from quota groups, which only pools with quotas enabled have.
- The list costs one `btrfs subvolume show` per snapshot, and the picker reads the pools and volumes on the host like the Storage page.
- Snapshot deletes still go by path after the router's checks (ADR-0042), unlike volume deletes by subvolume id (ADR-0069).

## Considered options

- Sizes from the "Usage exclusive" line of `btrfs subvolume show` where quotas are enabled: more parsing for a number that most pools do not have.
- A unit menu (MiB, GiB, TiB) for the quota: GiB with decimals covers the same sizes in one field.

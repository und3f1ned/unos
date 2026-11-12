---
status: accepted
date: 2026-11-12
area: security
decided-by: agent
---

# ADR-0350: Pool, volume, snapshot, container and app changes are audited, and answer when their entry cannot be written

## Context

`AuditAction` has actions for pools, volumes, snapshots, containers and apps, but nothing wrote
them, so the audit log did not show who created or removed a pool, deleted a volume or a
snapshot, or removed an app (steps 1 and 2 of `security.audit-admin-actions`). `log_action`
passes on the error of an entry it cannot write (ADR-0251), and only a sign-in and a user's own
account changes answer all the same (ADR-0343). The agent decided the details under the owner's
delegation ("work on through the features, ask no questions; I allow everything", in
translation), as for ADR-0340.

## Decision

- After the change was made, each of these writes its action with the user as `username`, the
  client address and browser of the request and the resource (`audit_change` of
  `supervisor/src/audit/changes.py`, which the storage, snapshots, containers and apps routers
  use, as do those of ADR-0351):

  | Request | Action | Resource | Details | Severity |
  | --- | --- | --- | --- | --- |
  | `POST /storage/pools` | `pool_created` | `pool` name | "Created the pool tank (raid1) on /dev/sdb, /dev/sdc" | info |
  | `DELETE /storage/pools/{name}` | `pool_deleted` | `pool` name | "Removed the pool tank; its disks and their data were kept" | warning |
  | `POST /storage/pools/{pool}/volumes` | `volume_created` | `volume` `<pool>/<name>` | "Created the volume documents in the pool data" (", with a quota of N bytes") | info |
  | `DELETE /storage/pools/{pool}/volumes/{name}` | `volume_deleted` | `volume` `<pool>/<name>` | "Deleted the volume documents of the pool data with everything in it" | warning |
  | `POST /snapshots/` | `snapshot_created` | `snapshot` path | "Created the read-only snapshot <name> of <subvolume>" | info |
  | `DELETE /snapshots/{name}` | `snapshot_deleted` | `snapshot` path | "Deleted the snapshot <name> of <subvolume>" | warning |
  | `POST /snapshots/restore` | `snapshot_restored` | `snapshot` path | "Restored the snapshot as the writable copy <path>" | info |
  | `POST /containers/{id}/start`, `.../restart` | `container_started` | `container` name | "Started" or "Restarted the container web" | info |
  | `POST /containers/{id}/stop` | `container_stopped` | `container` name | "Stopped the container web" | info |
  | `DELETE /containers/{id}` | `container_removed` | `container` name | "Removed the container web" (" (forced)") | warning |
  | `POST /apps/install` | `app_installed` | `app` name | "Installed Jellyfin from the catalog as jellyfin" (", with its data on the pool alpha") | info |
  | `DELETE /apps/installed/{id}` | `app_uninstalled` | `app` name | "Uninstalled the app jellyfin; its data folders were kept" | warning |
  | `POST /apps/installed/{id}/start`, `.../restart`, `.../stop` | `container_started`, `container_stopped` | `container` name | "Started", "Restarted" or "Stopped the app jellyfin" | info |

  A snapshot's path is `<subvolume>/.snapshots/<name>`. A restart starts the container again
  and writes `container_started`; no action of its own is added. UNOS's own containers
  (`is_system_container` of `supervisor/src/containers/docker.py`) are refused for a restart as
  for a stop and a removal, with 404, and write nothing. The Audit Log page names the
  new resource types `pool`, `volume`, `snapshot`, `container` and `app` (`RESOURCE_TYPES` of
  `webui/src/routes/audit/+page.svelte`, ADR-0298).
- Real changes only (ADR-0320): a refused request, and a failed one that changed nothing,
  write nothing. A removal of a pool writes its entry only when it unmounted the pool, dropped
  its fstab entry or removed its mountpoint (`delete_pool` of `supervisor/src/storage/pools.py`
  says whether); a name that is no pool answers 200 as before and writes nothing. Docker
  starts a running container and stops one that does not run without an error and without a
  change, and docker-py does not pass on its 304: a start writes its entry only when the
  container was not running before it (`RUNNING_STATES` of
  `supervisor/src/containers/docker.py`: running, restarting, paused), a stop only when it was.
- A request that made part of its change and then failed writes its action with success false
  (**Failed** on the Audit Log page), the action's severity, and details that say what was done
  and then the error that the request answers:

  | Request | Action | Details |
  | --- | --- | --- |
  | Create Pool, when a step after `mkfs.btrfs` fails (`PoolFormattedError`) | `pool_created` | "Formatted /dev/sdb for the pool tank (single); then: Mounting /mnt/pools/tank failed: ..." |
  | Remove Pool, when a step fails after the pool was unmounted or its fstab entry dropped (`PoolPartlyRemovedError`) | `pool_deleted` | "Unmounted the pool tank; then: ...", "Unmounted the pool tank and dropped its fstab entry; then: ...", "Dropped the fstab entry of the pool tank; then: ..." |
  | Uninstall, when it stopped the running app and could not remove it | `container_stopped` | "Stopped the app jellyfin; then: Failed to remove: ..." |

  A Create Pool that fails after `mkfs.btrfs` answers 500 with the error of the step that
  failed, also where that step alone would answer 409: the disks are formatted. A Remove Pool
  answers as its failed step does: 409 when the pool, unmounted, is still in use elsewhere and
  mounting it again failed, 500 otherwise.
- An entry that cannot be written, such as on a full `/data`, is in the supervisor's log
  (`log_action`, ADR-0251) and the request answers what it did: the change is made, and a 500
  would tell the administrator it was not. This extends ADR-0343's rule to these requests;
  ADR-0251's consequence that other audited actions answer 500 when their entry cannot be
  written holds for the rest.
- Tests: `supervisor/tests/test_audit_storage_and_apps.py`; what `delete_pool` answers in
  `test_pool_persistence.py`.

## Consequences

- The audit log shows who changed the storage, the snapshots, the containers and the apps,
  from where, and the **Action** filter of the Audit Log page finds these entries.
- Each such change appends a line of a few hundred bytes to the audit log on the system disk
  (ADR-0214, ADR-0224); a start or stop that changed nothing appends none. A change that failed
  part way is in the log as failed, so that formatted disks or an unmounted pool have an entry.
- Two starts (or stops) of one container at the same moment can both find it stopped (or
  running) and both write an entry, though Docker made only one change. Mounting, unmounting
  and importing a pool, creating a container on the Containers page and pulling or removing an
  image have no action and are not audited; the snapshots that scheduled tasks take and clean
  up, the apps started when a pool is mounted and other changes the supervisor makes on its own
  are not audited either. An install whose container does not start writes no entry: the
  container is removed again, but the image it pulled and the folders it made on the pool stay.

## Considered options

- Answering 500 when the entry cannot be written, as `log_action`'s other callers do: the pool,
  the snapshot or the removal is already made, and the error would invite a retry of a change
  that took place.
- Telling a real start or stop by Docker's 304: docker-py's `Container.start` and `stop` drop
  it, and asking the API by hand would bypass docker-py for two calls.

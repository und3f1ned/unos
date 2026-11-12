import { api } from "./client";

export interface SnapshotInfo {
  id: string;
  name: string;
  /** The folder the snapshot was made of: a pool's mountpoint or a volume. */
  subvolume: string;
  /** `<subvolume>/.snapshots/<name>` */
  path: string;
  /** When btrfs made it, in UTC with its offset; as btrfs printed it when that could not be read. */
  created: string;
}

/** A mounted pool, or one of its volumes, that snapshots can be made of (ADR-0243). */
export interface Subvolume {
  /** The pool's mountpoint, or the volume's folder in it. */
  path: string;
  pool: string;
  /** The volume's name; null for the pool itself. */
  volume: string | null;
  /** For a pool: why its volumes are missing, when they could not be read; null otherwise. */
  volumes_error: string | null;
}

/**
 * Each pool mounted at its mountpoint, followed by its volumes. HTTP 500 with
 * the reason when the host's mounts or pools cannot be read; a pool whose
 * volumes cannot be read comes alone, with the reason in volumes_error.
 */
export async function getSubvolumes(): Promise<Subvolume[]> {
  return api.get<Subvolume[]>("/snapshots/subvolumes");
}

/** The snapshots of a pool or volume, newest first. */
export async function getSnapshots(subvolume: string): Promise<SnapshotInfo[]> {
  return api.get<SnapshotInfo[]>(`/snapshots/?subvolume=${encodeURIComponent(subvolume)}`);
}

/** A read-only snapshot of a pool or volume; without a name the server makes one (snap_<date>_<time>, in UTC). */
export async function createSnapshot(subvolume: string, name?: string): Promise<SnapshotInfo> {
  return api.post<SnapshotInfo>("/snapshots/", { subvolume, name });
}

export async function deleteSnapshot(snapshotId: string, subvolume: string): Promise<void> {
  return api.delete(`/snapshots/${encodeURIComponent(snapshotId)}?subvolume=${encodeURIComponent(subvolume)}`);
}

/** A writable copy that a restore made right in a pool or a volume (ADR-0356). */
export interface RestoreCopy {
  /** `restore_<date>_<time>`, in UTC. */
  name: string;
  pool: string;
  /** The volume it is in; null for one in the pool's top folder. */
  volume: string | null;
  /** The folder it is in: the pool's mountpoint or the volume's folder. */
  subvolume: string;
  path: string;
  /** When the restore made it, from its name: UTC, with its offset. */
  created: string;
}

/** The copies that restores made right in a pool or volume, newest first. */
export async function getRestoreCopies(subvolume: string): Promise<RestoreCopy[]> {
  return api.get<RestoreCopy[]>(`/snapshots/copies?subvolume=${encodeURIComponent(subvolume)}`);
}

/**
 * Delete a restored copy by its subvolume id. HTTP 409 while a running container, an SMB share,
 * an NFS export or the off-device copy uses it; 400 for what no restore made.
 */
export async function deleteRestoreCopy(name: string, subvolume: string): Promise<void> {
  return api.delete(`/snapshots/copies/${encodeURIComponent(name)}?subvolume=${encodeURIComponent(subvolume)}`);
}

/**
 * A writable copy of the snapshot, `restore_<date>_<time>` (in UTC), in the
 * folder it was made of (or in targetPath); nothing is overwritten.
 */
export async function restoreSnapshot(
  snapshotPath: string,
  targetPath?: string
): Promise<{ status: string; source: string; target: string }> {
  return api.post("/snapshots/restore", { snapshot_path: snapshotPath, target_path: targetPath });
}

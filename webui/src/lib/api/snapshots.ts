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

/** A mounted pool, or one of its volumes, that snapshots can be made of (ADR-0232). */
export interface Subvolume {
  /** The pool's mountpoint, or the volume's folder in it. */
  path: string;
  pool: string;
  /** The volume's name; null for the pool itself. */
  volume: string | null;
}

/**
 * Each pool mounted at its mountpoint, followed by its volumes. HTTP 500 with
 * the reason when the host's pools or volumes cannot be read.
 */
export async function getSubvolumes(): Promise<Subvolume[]> {
  return api.get<Subvolume[]>("/snapshots/subvolumes");
}

/** The snapshots of a pool or volume, newest first. */
export async function getSnapshots(subvolume: string): Promise<SnapshotInfo[]> {
  return api.get<SnapshotInfo[]>(`/snapshots/?subvolume=${encodeURIComponent(subvolume)}`);
}

/** A read-only snapshot of a pool or volume; without a name the server makes one (snap_<date>_<time>). */
export async function createSnapshot(subvolume: string, name?: string): Promise<SnapshotInfo> {
  return api.post<SnapshotInfo>("/snapshots/", { subvolume, name });
}

export async function deleteSnapshot(snapshotId: string, subvolume: string): Promise<void> {
  return api.delete(`/snapshots/${encodeURIComponent(snapshotId)}?subvolume=${encodeURIComponent(subvolume)}`);
}

/**
 * A writable copy of the snapshot, `restore_<date>_<time>`, in the folder it
 * was made of (or in targetPath); nothing is overwritten.
 */
export async function restoreSnapshot(
  snapshotPath: string,
  targetPath?: string
): Promise<{ status: string; source: string; target: string }> {
  return api.post("/snapshots/restore", { snapshot_path: snapshotPath, target_path: targetPath });
}

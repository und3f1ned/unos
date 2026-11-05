import { api } from "./client";

export interface SnapshotInfo {
  id: string;
  name: string;
  subvolume: string;
  path: string;
  created: string;
  size: string | null;
}

export interface Subvolume {
  path: string;
  device: string;
}

export async function getSubvolumes(): Promise<Subvolume[]> {
  return api.get<Subvolume[]>("/snapshots/subvolumes");
}

export async function getSnapshots(subvolume?: string): Promise<SnapshotInfo[]> {
  const params = subvolume ? `?subvolume=${encodeURIComponent(subvolume)}` : "";
  return api.get<SnapshotInfo[]>(`/snapshots${params}`);
}

export async function createSnapshot(subvolume: string, name?: string): Promise<SnapshotInfo> {
  return api.post<SnapshotInfo>("/snapshots", { subvolume, name });
}

export async function deleteSnapshot(snapshotId: string, subvolume: string): Promise<void> {
  return api.delete(`/snapshots/${encodeURIComponent(snapshotId)}?subvolume=${encodeURIComponent(subvolume)}`);
}

export async function restoreSnapshot(snapshotPath: string, targetPath?: string): Promise<{ status: string; source: string; target: string }> {
  return api.post("/snapshots/restore", { snapshot_path: snapshotPath, target_path: targetPath });
}

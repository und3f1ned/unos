import { api } from './client';

export interface PartitionInfo {
  name: string;
  path: string;
  size: number;
  fstype: string | null;
  label: string | null;
  mountpoints: string[];
}

export interface Disk {
  name: string;
  path: string;
  model: string | null;
  serial: string | null;
  /** World Wide Name, if the disk reports one. */
  wwn: string | null;
  size: number;
  rotational: boolean;
  mountpoint: string | null;
  type: string;
  /** Filesystem signature on the whole disk, e.g. "btrfs". */
  fstype: string | null;
  label: string | null;
  /** Partition table type ("gpt" / "dos"), if any. */
  pttype: string | null;
  partitions: PartitionInfo[];
  /** Host mountpoints of the disk and all its partitions. */
  mountpoints: string[];
  /** Any filesystem, partition table, partition or other signature was found. */
  has_data: boolean;
  /** Backs the host's /, /boot, /boot/efi or active swap, or is the boot disk. */
  is_system: boolean;
  /** is_system, mounted on the host, or a member of a mounted pool. */
  in_use: boolean;
  in_use_reason: string | null;
  /** Label of the btrfs filesystem the disk (or a partition) belongs to. */
  pool: string | null;
}

export interface SmartData {
  device: string;
  healthy: boolean;
  temperature: number | null;
  power_on_hours: number | null;
  reallocated_sectors: number | null;
  raw_data: Record<string, unknown> | null;
}

export type PoolHealth = 'healthy' | 'degraded' | 'unmounted' | 'error' | 'unknown';

export interface Pool {
  name: string;
  uuid: string | null;
  devices: string[];
  total_size: number;
  used_size: number;
  mountpoint: string | null;
  raid_level: string | null;
  mounted: boolean;
  /** A UNOS-managed /etc/fstab entry exists on the host. */
  persistent: boolean;
  health: PoolHealth;
  missing_devices: number;
  /** Sum of the btrfs device error counters (0 when unmounted or unknown). */
  device_errors: number;
  /** Mounted, but writes fail: mounted read-only on purpose, or forced_read_only. */
  read_only: boolean;
  /**
   * Mounted read-write, but btrfs made the filesystem read-only after a
   * serious error such as an aborted transaction (health "error").
   */
  forced_read_only: boolean;
}

export type RaidLevel = 'single' | 'raid0' | 'raid1' | 'raid1c3' | 'raid1c4' | 'raid10';

/** A selected disk as GET /storage/disks listed it. */
export interface DeviceIdentity {
  path: string;
  serial: string | null;
  wwn: string | null;
  size: number;
}

export interface PoolCreate {
  name: string;
  devices: string[];
  raid_level: RaidLevel;
  /** Must equal name (HTTP 400 otherwise). */
  confirm_name: string;
  /** Required when any selected disk has_data (HTTP 409 otherwise). */
  force: boolean;
  /**
   * What the user saw for each selected disk. Disk names change when a disk
   * is reconnected, so the pool is only created if every path still holds
   * the disk with that serial, WWN and size (HTTP 409 otherwise). With force,
   * every selected disk needs one (HTTP 400 otherwise).
   */
  device_identities?: DeviceIdentity[];
}

export function deviceIdentity(disk: Disk): DeviceIdentity {
  return { path: disk.path, serial: disk.serial, wwn: disk.wwn, size: disk.size };
}

/** The 409 detail of POST /storage/pools when a path now holds another disk. */
export function isChangedDiskError(message: string): boolean {
  return message.includes('is no longer the disk that was selected');
}

/** A btrfs subvolume in the top directory of a mounted pool. */
export interface Volume {
  name: string;
  pool: string;
  /** Host path: the pool's mountpoint and the volume name. */
  path: string;
  used: number | null;
  quota: number | null;
}

/** Body of POST /storage/pools/{pool}/volumes; the pool is the one in the URL. */
export interface VolumeCreate {
  /** Must match VOLUME_NAME_PATTERN (HTTP 400 otherwise). */
  name: string;
  /**
   * Bytes, at most 2^64 - 2 (HTTP 422 otherwise; btrfs reads 2^64 - 1 as no
   * limit); omit or null for no limit. Needs quotas enabled on the pool
   * (HTTP 409 otherwise, and nothing is created).
   */
  quota?: number | null;
}

/** Volume names the API accepts (VOLUME_NAME_PATTERN in supervisor/src/security/validators.py). */
export const VOLUME_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/;

const GIB = 1024 ** 3;

/** The largest quota the Volumes dialog takes, in whole GiB: its bytes must be exact in JSON. */
export const MAX_QUOTA_GIB = Math.floor(Number.MAX_SAFE_INTEGER / GIB);

/**
 * VolumeCreate.quota in bytes for a quota typed in GiB ("10", "0.5"), or null
 * when the text is not one: digits with an optional decimal point, more than 0
 * and at most MAX_QUOTA_GIB (ADR-0243). Read from the text alone, so that "1e3",
 * "1,5" or "10 GB" are refused instead of being taken for another number.
 */
export function quotaBytes(gib: string): number | null {
  if (!/^\d+(\.\d+)?$/.test(gib)) return null;
  const bytes = Math.round(Number(gib) * GIB);
  return bytes >= 1 && bytes <= MAX_QUOTA_GIB * GIB ? bytes : null;
}

export interface ScrubStatus {
  pool: string;
  status: 'running' | 'finished' | 'aborted' | 'idle';
  duration: number | null;
  bytes_scrubbed: number | null;
  total_bytes: number | null;
  rate: number | null;
  errors: number;
  last_run: string | null;
}

export async function getDisks(): Promise<Disk[]> {
  return api.get<Disk[]>('/storage/disks');
}

/** SMART data of a disk as getDisks() lists it, by name (HTTP 400 for any other device). */
export async function getDiskSmart(name: string): Promise<SmartData> {
  return api.get<SmartData>(`/storage/disks/${name}/smart`);
}

export async function getPools(): Promise<Pool[]> {
  return api.get<Pool[]>('/storage/pools');
}

export async function getPool(name: string): Promise<Pool> {
  return api.get<Pool>(`/storage/pools/${name}`);
}

export async function createPool(pool: PoolCreate): Promise<Pool> {
  return api.post<Pool>('/storage/pools', pool);
}

export async function deletePool(name: string): Promise<void> {
  return api.delete(`/storage/pools/${name}`);
}

/** Mount on the host at /mnt/pools/{name} and keep the managed fstab entry. */
export async function mountPool(name: string): Promise<Pool> {
  return api.post<Pool>(`/storage/pools/${encodeURIComponent(name)}/mount`, {});
}

/** Unmount on the host and remove the managed fstab entry; 409 while busy. */
export async function unmountPool(name: string): Promise<Pool> {
  return api.post<Pool>(`/storage/pools/${encodeURIComponent(name)}/unmount`, {});
}

/**
 * Volumes of a mounted pool, also a read-only one (HTTP 409 when it is not
 * mounted, or is mounted at a subvolume other than the top level).
 */
export async function getVolumes(poolName: string): Promise<Volume[]> {
  return api.get<Volume[]>(`/storage/pools/${encodeURIComponent(poolName)}/volumes`);
}

/**
 * Create a volume in a mounted pool (HTTP 409 when it is not mounted, read-only
 * or not at its top level, or the name is taken).
 */
export async function createVolume(poolName: string, volume: VolumeCreate): Promise<Volume> {
  return api.post<Volume>(`/storage/pools/${encodeURIComponent(poolName)}/volumes`, volume);
}

/**
 * Delete a volume with everything in it. HTTP 409 when the pool is read-only or
 * the volume is used by a running container, an SMB share or an NFS export;
 * 503 when Docker cannot be asked about the containers. Nothing is deleted then.
 */
export async function deleteVolume(poolName: string, volumeName: string): Promise<void> {
  return api.delete(
    `/storage/pools/${encodeURIComponent(poolName)}/volumes/${encodeURIComponent(volumeName)}`
  );
}

export async function startScrub(poolName: string): Promise<ScrubStatus> {
  return api.post<ScrubStatus>(`/storage/pools/${poolName}/scrub`, {});
}

export async function getScrubStatus(poolName: string): Promise<ScrubStatus> {
  return api.get<ScrubStatus>(`/storage/pools/${poolName}/scrub`);
}

export async function cancelScrub(poolName: string): Promise<ScrubStatus> {
  return api.delete<ScrubStatus>(`/storage/pools/${poolName}/scrub`);
}

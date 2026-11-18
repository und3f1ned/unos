import { api } from './client';

export interface PartitionInfo {
  name: string;
  path: string;
  size: number;
  fstype: string | null;
  label: string | null;
  mountpoints: string[];
}

/**
 * A whole disk's power, read without waking it (hdparm -C on the host, smartctl -n idle for an EPC
 * disk's power condition, or smartctl -n standby, ADR-0378): active (spinning, or idle), standby
 * (spun down), or unknown (the disk could not be asked or did not answer).
 */
export type PowerState = 'active' | 'standby' | 'unknown';

/**
 * The idle timeouts the Storage page offers, in minutes, 0 for off (SPINDOWN_MINUTES of
 * supervisor/src/storage/schemas.py): values that hdparm -S expresses (ADR-0378).
 */
export const SPINDOWN_MINUTES = [0, 5, 10, 20, 30, 60, 120, 180, 240, 300] as const;

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
  /** Read first; a disk in standby is not probed for signatures, so has_data is true for it. */
  power_state: PowerState;
  /** The idle timeout in minutes that UNOS's udev rule sets for the disk, 0 for none. */
  spindown: number;
  /** Why the disk gets no idle timeout (the server's message), or null when it can have one. */
  spindown_problem: string | null;
}

export interface SmartData {
  device: string;
  /** null when the disk sleeps: smartctl left it alone (power_state standby). */
  healthy: boolean | null;
  power_state: PowerState;
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

/**
 * Why a btrfs filesystem is not the pool its label names: it has none, it is not a valid pool
 * name, or another btrfs filesystem of the host has it too, and the pool of that name is the
 * other one (mounted or managed under it) or neither (ADR-0283).
 */
export type LabelProblem = 'missing' | 'invalid' | 'duplicate';

/** A btrfs filesystem on the host's disks that can be imported as a pool (ADR-0283). */
export interface ImportableFilesystem {
  uuid: string;
  /** As btrfs prints it; null without a label. */
  label: string | null;
  label_problem: LabelProblem;
  /** The members that are present. */
  devices: string[];
  total_devices: number;
  missing_devices: number;
  /** Bytes, of the members that are present. */
  size: number;
  used: number;
}

/** Pool names the API accepts (POOL_NAME_PATTERN in supervisor/src/security/validators.py). */
export const POOL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/;

/**
 * A pool name made from a filesystem's label, for the import dialog to start from: characters a
 * pool name cannot have become "_", and what does not start with a letter or digit is dropped.
 * "" without a label.
 */
export function suggestedPoolName(label: string | null): string {
  return (label ?? '')
    .replace(/[^A-Za-z0-9_-]/g, '_')
    .replace(/^[^A-Za-z0-9]+/, '')
    .slice(0, 63);
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

/**
 * How the kernel counts a volume's use (supervisor/src/storage/qgroups.py): counted;
 * inconsistent (counted, but btrfs says a rescan is needed); quotas_off (quotas are off on the
 * pool, and nothing is counted); unknown (its quota group cannot be read).
 */
export type VolumeUsageState = 'counted' | 'inconsistent' | 'quotas_off' | 'unknown';

/** A btrfs subvolume in the top directory of a mounted pool. */
export interface Volume {
  name: string;
  pool: string;
  /** Host path: the pool's mountpoint and the volume name. */
  path: string;
  /** The bytes it references, which its quota limits; null unless counted. */
  used: number | null;
  /** The limit on them in bytes; null without a quota. */
  quota: number | null;
  /** How the list read them; null in the answer to a create. */
  usage: VolumeUsageState | null;
}

/** A volume's use at one time. */
export interface VolumeUsagePoint {
  /** ISO 8601 with the UTC offset. */
  time: string;
  used: number;
  quota: number | null;
}

/**
 * GET /storage/pools/{pool}/volumes/{name}/usage: one point per hour of the last 30 days that the
 * supervisor kept, oldest first, and the current reading last.
 */
export interface VolumeUsageHistory {
  name: string;
  pool: string;
  used: number | null;
  quota: number | null;
  usage: VolumeUsageState;
  points: VolumeUsagePoint[];
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

/** A pool's scrub as `btrfs scrub status` reports it (ADR-0313). */
export interface ScrubStatus {
  pool: string;
  /**
   * running also for a scrub that btrfs has not recorded yet (no start time or duration then);
   * idle: no scrub recorded (a restart of the supervisor forgets the last one, ADR-0171);
   * aborted: cancelled; interrupted: ended neither by finishing nor by a cancel, for example
   * because the btrfs process that ran it was killed.
   */
  status: 'running' | 'finished' | 'aborted' | 'interrupted' | 'idle';
  duration: number | null;
  bytes_scrubbed: number | null;
  total_bytes: number | null;
  rate: number | null;
  /** Every error of btrfs's error summary (read, super, verify and csum). */
  errors: number;
  /** When the scrub started, in UTC with its offset (ADR-0401); null when btrfs printed none. */
  last_run: string | null;
}

/** The usage limit of a balance that the dialog offers first (BALANCE_DEFAULT_USAGE, ADR-0379). */
export const BALANCE_DEFAULT_USAGE = 20;

/**
 * What a balance rewrites (ADR-0379): the data and metadata chunks less than `usage` percent full
 * (1 to 99), or every chunk with `full`, which takes no usage.
 */
export type BalanceStart = { usage: number } | { full: true };

/** A pool's balance as `btrfs balance status` reports it, and how the last one ended (ADR-0379). */
export interface BalanceStatus {
  pool: string;
  /**
   * pausing and cancelling: a pause or a cancel waits for the chunk being moved; idle: none runs
   * and none is paused.
   */
  status: 'running' | 'pausing' | 'cancelling' | 'paused' | 'idle';
  /** Chunks balanced since the start or resume, of about `expected`; of a finished one, relocated. */
  completed: number | null;
  expected: number | null;
  /** Of a finished balance: the chunks it considered. */
  considered: number | null;
  /** The estimate of a balance that runs; null until it has balanced a chunk. */
  seconds_left: number | null;
  /** Of an idle pool: how its last balance ended, when known. */
  result: 'finished' | 'cancelled' | 'failed' | null;
  /** btrfs's error of a failed one, in English. */
  error: string | null;
}

export async function getDisks(): Promise<Disk[]> {
  return api.get<Disk[]>('/storage/disks');
}

/** SMART data of a disk as getDisks() lists it, by name (HTTP 400 for any other device). */
export async function getDiskSmart(name: string): Promise<SmartData> {
  return api.get<SmartData>(`/storage/disks/${name}/smart`);
}

/**
 * What PUT /storage/disks/{name}/spindown answers: the timeout set, and `warning` (the server's
 * text) when the disk's own automatic offline scans could not be turned off for it, or on again
 * once it was removed; the timeout is set all the same (ADR-0378).
 */
export interface DiskSpindown {
  name: string;
  minutes: number;
  warning: string | null;
}

/**
 * Give a whole data disk the idle timeout `minutes` of SPINDOWN_MINUTES, 0 for off: hdparm -S on
 * the host now, which spins a sleeping disk up, the disk's own automatic offline scans turned off
 * for a timeout (on again when it is removed), and UNOS's udev rule, which sets it again whenever
 * the disk appears. HTTP 400 for a boot or virtual device or a partition, 409 for the system disk
 * and the other disks that spindown_problem names, 500 when hdparm fails.
 */
export async function setDiskSpindown(name: string, minutes: number): Promise<DiskSpindown> {
  return api.put<DiskSpindown>(`/storage/disks/${encodeURIComponent(name)}/spindown`, {
    minutes
  });
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

/**
 * Unmount on the host and remove the managed fstab entry; 409 while busy, 503 when Docker
 * cannot be asked which containers use the pool. Nothing is unmounted then.
 */
export async function unmountPool(name: string): Promise<Pool> {
  return api.post<Pool>(`/storage/pools/${encodeURIComponent(name)}/unmount`, {});
}

/** The btrfs filesystems on the host's disks that can be imported as pools (ADR-0283). */
export async function getImportableFilesystems(): Promise<ImportableFilesystem[]> {
  return api.get<ImportableFilesystem[]>('/storage/filesystems/importable');
}

/**
 * Import a filesystem of getImportableFilesystems() as the pool `name`, without formatting it: it
 * is mounted by UUID at /mnt/pools/{name}, gets the name as its label and an fstab entry. HTTP 409,
 * and nothing is changed, when it is in use, misses devices or is on a boot, virtual or system
 * device, when the name is taken or SMB shares, NFS exports or apps of an earlier pool still use
 * it, or when it mounts another subvolume than its top level by default.
 */
export async function importFilesystem(uuid: string, name: string): Promise<Pool> {
  return api.post<Pool>(`/storage/filesystems/${encodeURIComponent(uuid)}/import`, { name });
}

/**
 * Volumes of a mounted pool, also a read-only one (HTTP 409 when it is not
 * mounted, or is mounted at a subvolume other than the top level).
 */
export async function getVolumes(poolName: string): Promise<Volume[]> {
  return api.get<Volume[]>(`/storage/pools/${encodeURIComponent(poolName)}/volumes`);
}

/**
 * A volume's use over time (HTTP 404 for an unknown volume, 409 when the pool is not mounted or is
 * mounted at a subvolume other than the top level).
 */
export async function getVolumeUsage(poolName: string, volumeName: string): Promise<VolumeUsageHistory> {
  return api.get<VolumeUsageHistory>(
    `/storage/pools/${encodeURIComponent(poolName)}/volumes/${encodeURIComponent(volumeName)}/usage`
  );
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

function balancePath(poolName: string): string {
  return `/storage/pools/${encodeURIComponent(poolName)}/balance`;
}

/**
 * Start a balance; HTTP 409 when the pool is not mounted or read-only, while a scrub of it runs or
 * a balance of it runs or is paused.
 */
export async function startBalance(poolName: string, what: BalanceStart): Promise<BalanceStatus> {
  return api.post<BalanceStatus>(balancePath(poolName), what);
}

export async function getBalanceStatus(poolName: string): Promise<BalanceStatus> {
  return api.get<BalanceStatus>(balancePath(poolName));
}

export async function pauseBalance(poolName: string): Promise<BalanceStatus> {
  return api.post<BalanceStatus>(`${balancePath(poolName)}/pause`, {});
}

export async function resumeBalance(poolName: string): Promise<BalanceStatus> {
  return api.post<BalanceStatus>(`${balancePath(poolName)}/resume`, {});
}

export async function cancelBalance(poolName: string): Promise<BalanceStatus> {
  return api.delete<BalanceStatus>(balancePath(poolName));
}

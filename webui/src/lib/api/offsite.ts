import { api } from "./client";
import type { BackupInfo } from "./backup";

// The off-device copy (ADR-0337): one copy repository, on a disk mounted below /mnt or in an
// S3-compatible store, that restic fills with the configuration and the pools' folders.

export type OffsiteKind = "disk" | "s3";

/** The copy target and what is copied, as GET /offsite/settings answers it: no secret. */
export interface OffsiteSettings {
  kind: OffsiteKind;
  /** disk: the folder of the repository, below /mnt. */
  path?: string | null;
  /** s3: https://host[:port] or http://host[:port]. */
  endpoint?: string | null;
  bucket?: string | null;
  prefix?: string | null;
  region?: string | null;
  access_key_id?: string | null;
  /** Folders of the pools. */
  sources: string[];
  /** KiB/s, 0: no limit; s3 only. */
  limit_upload_kib: number;
  limit_download_kib: number;
  keep_daily: number;
  keep_weekly: number;
  keep_monthly: number;
  /** The share of the data that each check reads, 0 to 100. */
  check_percent: number;
}

/**
 * PUT /offsite/settings. The secrets are write-only: sent to set them, never answered; an edit of
 * the same target without them keeps the stored ones.
 */
export interface OffsiteSettingsRequest extends OffsiteSettings {
  password?: string;
  s3_secret_access_key?: string;
}

/** A warning about the settings: same_disk, a disk target on the filesystem of a folder it copies. */
export interface OffsiteWarning {
  kind: string;
  message: string;
}

export interface OffsiteSettingsAnswer {
  configured: boolean;
  settings: OffsiteSettings | null;
  has_password: boolean;
  has_s3_secret_access_key: boolean;
  /** The managed pools: their mount points are the folders a copy takes by default. */
  pools: { name: string; path: string }[];
  /** The folder of the pools' mount points (/mnt/pools): a source below it names its pool. */
  pools_base: string;
  warnings: OffsiteWarning[];
}

export interface CopySummary {
  files_new: number;
  files_changed: number;
  files_unmodified: number;
  bytes_added: number;
  configuration_snapshot: string | null;
  data_snapshot: string | null;
}

/** The last copy. Times are ISO 8601 in UTC. */
export interface CopyResult {
  started: string;
  ended: string | null;
  /** task: a task of the Scheduler; manual: Run now. */
  trigger: string;
  success: boolean;
  /** The step that failed: settings, folders, unlock, configuration, data, forget, check, restore_test. */
  step: string | null;
  /** Why it failed, in the server's words. */
  error: string | null;
  summary: CopySummary | null;
  /** passed or failed; null when the copy did not get there. */
  check: string | null;
  restore_test: string | null;
}

export interface RestoreResult {
  snapshot_id: string;
  path: string | null;
  folder: string;
  started: string;
  ended: string | null;
  success: boolean;
  error: string | null;
}

/** What runs now: a copy, a restore, or a test or removal of the settings. */
export interface OffsiteActivity {
  kind: string;
  started: string;
  step: string | null;
  snapshot_id: string | null;
  folder: string | null;
}

export interface OffsiteStatus {
  running: OffsiteActivity | null;
  last_copy: CopyResult | null;
  last_restore: RestoreResult | null;
}

export interface OffsiteSnapshot {
  id: string;
  short_id: string;
  time: string;
  /** configuration, data or other, by its tags. */
  kind: string;
  tags: string[];
  paths: string[];
  hostname: string | null;
  /** Bytes and files that it holds, when restic says. */
  size: number | null;
  files: number | null;
}

export async function getOffsiteSettings(): Promise<OffsiteSettingsAnswer> {
  return api.get<OffsiteSettingsAnswer>("/offsite/settings");
}

/** Save the settings; the server tests the target first, and answers 400 with restic's message. */
export async function saveOffsiteSettings(settings: OffsiteSettingsRequest): Promise<OffsiteSettingsAnswer> {
  return api.put<OffsiteSettingsAnswer>("/offsite/settings", settings);
}

/** Forget the target: its settings, secrets and state. The copies stay where they are. */
export async function removeOffsiteSettings(): Promise<{ status: string }> {
  return api.delete<{ status: string }>("/offsite/settings");
}

export async function getOffsiteStatus(): Promise<OffsiteStatus> {
  return api.get<OffsiteStatus>("/offsite/status");
}

/** Run now: the copy runs in the background. */
export async function runOffsiteCopy(): Promise<{ status: string }> {
  return api.post<{ status: string }>("/offsite/run");
}

export async function getOffsiteSnapshots(): Promise<{ snapshots: OffsiteSnapshot[] }> {
  return api.get<{ snapshots: OffsiteSnapshot[] }>("/offsite/snapshots");
}

/** Put a snapshot's configuration into the backup list, where it is restored. */
export async function restoreOffsiteConfiguration(snapshotId: string): Promise<{ status: string; backup: BackupInfo }> {
  return api.post<{ status: string; backup: BackupInfo }>("/offsite/restore-configuration", {
    snapshot_id: snapshotId,
  });
}

/** Restore a snapshot, or one of its folders, into a new folder at the top of a pool, in the background. */
export async function restoreOffsiteFiles(
  snapshotId: string,
  pool: string,
  path?: string
): Promise<{ status: string; folder: string }> {
  return api.post<{ status: string; folder: string }>("/offsite/restore-files", {
    snapshot_id: snapshotId,
    pool,
    path: path || null,
  });
}

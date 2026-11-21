import { api } from './client';

export interface ReleaseAsset {
  name: string;
  download_url: string;
  size: number;
  content_type: string;
}

export interface ReleaseInfo {
  tag_name: string;
  name: string;
  body: string;
  published_at: string;
  html_url: string;
  prerelease: boolean;
  assets: ReleaseAsset[];
}

export interface CurrentVersion {
  version: string;
  commit_hash: string;
  install_date: string | null;
  is_development: boolean;
}

export interface UpdateCheck {
  current_version: string;
  /** null when no release is published. */
  latest_version: string | null;
  update_available: boolean;
  release_info: ReleaseInfo | null;
  checked_at: string;
}

/** What GET /updates/progress answers and the events socket sends (ADR-0443). */
export type UpdateStatus =
  | 'idle'
  | 'checking'
  | 'downloading'
  | 'backing_up'
  | 'installing'
  | 'verifying'
  | 'rollback'
  | 'completed'
  | 'failed';

export interface UpdateProgress {
  status: UpdateStatus;
  progress_percent: number;
  /** The server's English sentence; the tab shows its own text for the status. */
  message: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  can_rollback: boolean;
  action: 'update' | 'rollback' | null;
  /** The release the update installs, or the version the rollback returns to. */
  version: string | null;
  rolled_back: boolean;
  /** Why this NAS cannot install releases (the release installer is not decided); null when it can. */
  install_unavailable: string | null;
  next_automatic_check: string | null;
}

export interface UpdateHistoryEntry {
  id: number;
  from_version: string;
  to_version: string;
  status: 'success' | 'failed';
  started_at: string;
  completed_at: string | null;
  error: string | null;
  changelog: string | null;
}

export interface UpdateConfig {
  auto_install: boolean;
  check_interval_hours: number;
}

/** Whether a job of the updater runs. */
export function updateRuns(progress: UpdateProgress | null): boolean {
  return progress !== null && !['idle', 'completed', 'failed'].includes(progress.status);
}

export async function getUpdateProgress(): Promise<UpdateProgress> {
  return api.get<UpdateProgress>('/updates/progress');
}

export async function installUpdate(version: string): Promise<void> {
  await api.post(`/updates/install?version=${encodeURIComponent(version)}`);
}

export async function rollbackUpdate(): Promise<void> {
  await api.post('/updates/rollback');
}

export async function getUpdateHistory(): Promise<UpdateHistoryEntry[]> {
  return api.get<UpdateHistoryEntry[]>('/updates/history');
}

export async function getUpdateConfig(): Promise<UpdateConfig> {
  return api.get<UpdateConfig>('/updates/config');
}

export async function saveUpdateConfig(config: UpdateConfig): Promise<UpdateConfig> {
  return api.put<UpdateConfig>('/updates/config', config);
}

export async function getCurrentVersion(): Promise<CurrentVersion> {
  return api.get<CurrentVersion>('/updates/current');
}

export async function checkForUpdates(includePrereleases = false): Promise<UpdateCheck> {
  const params = includePrereleases ? '?include_prereleases=true' : '';
  return api.get<UpdateCheck>(`/updates/check${params}`);
}

export async function getLatestRelease(includePrereleases = false): Promise<ReleaseInfo> {
  const params = includePrereleases ? '?include_prereleases=true' : '';
  return api.get<ReleaseInfo>(`/updates/latest${params}`);
}

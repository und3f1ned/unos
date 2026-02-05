import { api } from './client';

export type UpdateStatus =
  | 'idle'
  | 'checking'
  | 'downloading'
  | 'verifying'
  | 'backing_up'
  | 'installing'
  | 'restarting'
  | 'completed'
  | 'failed'
  | 'rollback';

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
  checksum_sha256: string | null;
}

export interface CurrentVersion {
  version: string;
  commit_hash: string;
  install_date: string | null;
  is_development: boolean;
}

export interface UpdateCheck {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  release_info: ReleaseInfo | null;
  checked_at: string;
}

export interface UpdateProgress {
  status: UpdateStatus;
  progress_percent: number;
  message: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  can_rollback: boolean;
}

export interface UpdateHistoryEntry {
  id: number;
  from_version: string;
  to_version: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  changelog: string | null;
}

export interface UpdateConfig {
  auto_check: boolean;
  check_interval_hours: number;
  auto_install: boolean;
  include_prereleases: boolean;
  github_repo: string;
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

export async function installUpdate(version?: string): Promise<{ status: string; message: string; version: string }> {
  const params = version ? `?version=${encodeURIComponent(version)}` : '';
  return api.post<{ status: string; message: string; version: string }>(`/updates/install${params}`);
}

export async function getUpdateProgress(): Promise<UpdateProgress> {
  return api.get<UpdateProgress>('/updates/progress');
}

export async function rollbackUpdate(): Promise<{ status: string; message: string }> {
  return api.post<{ status: string; message: string }>('/updates/rollback');
}

export async function resetUpdateStatus(): Promise<{ status: string; message: string }> {
  return api.post<{ status: string; message: string }>('/updates/reset');
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

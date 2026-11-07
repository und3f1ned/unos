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
  /** null when no release is published. */
  latest_version: string | null;
  update_available: boolean;
  release_info: ReleaseInfo | null;
  checked_at: string;
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

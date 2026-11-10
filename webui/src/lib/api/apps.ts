import { api } from "./client";

/**
 * Catalog host paths that start with this placeholder live on the storage pool
 * chosen at install time; the server replaces it with the pool's mount point.
 */
export const POOL_PLACEHOLDER = "{pool}";

/**
 * PUID and PGID of an app installed to run in the group unos-smb (ADR-0274): the user of
 * such apps, which no other app has (SMB_APP_UID of supervisor/src/shares/constants.py),
 * and the group id of unos-smb (SMB_GROUP_GID). The server sets both; UMASK becomes 007.
 */
export const UNOS_SMB_IDS: Readonly<Record<"PUID" | "PGID", number>> = { PUID: 80000, PGID: 70000 };

/** The variables of the user and group of an app's processes, which only the server sets. */
export function isSetByUnos(name: string): name is "PUID" | "PGID" {
  return name === "PUID" || name === "PGID";
}

export interface PortMapping {
  host: number;
  container: number;
  protocol: string;
}

export interface VolumeMapping {
  host: string;
  container: string;
  readonly: boolean;
  /** The source is a single file, not a directory. */
  is_file: boolean;
  /** Owner the server gives a folder it creates (images that run as a non-root user). */
  uid?: number | null;
  gid?: number | null;
  /** The source lives on the selected pool; otherwise it is a fixed host path. */
  on_pool: boolean;
}

export interface EnvVar {
  name: string;
  value: string;
  description: string;
  required: boolean;
  /** Password, token or key: mask it; placeholders like "changeme" are rejected. */
  secret: boolean;
}

export interface AppTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  image: string;
  version: string;
  website: string;
  source?: string;
  github?: string;
  documentation?: string;
  min_memory?: string;
  recommended_memory?: string;
  ports: PortMapping[];
  volumes: VolumeMapping[];
  environment: EnvVar[];
  /**
   * The app can be installed to run in the group unos-smb: its image takes PUID, PGID and
   * UMASK (linuxserver.io), and it has a folder besides its own data. Only such an app is
   * given a share's folder.
   */
  can_run_in_unos_smb: boolean;
}

export interface InstalledApp {
  id: string;
  template_id: string;
  name: string;
  container_id: string;
  status: string;
  web_port: number | null;
  created_at: string;
  /** Pool chosen at install time; null for an app without data on a pool. */
  pool: string | null;
  /** The container's bind mounts with the host paths it really uses. */
  volumes: AppVolume[];
  /** Data folders that are not on a mounted pool, as their pool is not mounted. The server refuses to start such an app. */
  data_outside_pools: string[];
  /** The app runs in the group unos-smb (PUID 80000, PGID 70000): its container says so. */
  runs_in_unos_smb: boolean;
}

/** A mounted storage pool that can hold app data. */
export interface AppPool {
  name: string;
  path: string;
  free_bytes: number;
  total_bytes: number;
}

/** A bind mount with the host path the server actually used. */
export interface AppVolume {
  host: string;
  container: string;
  readonly: boolean;
}

export interface AppInstallRequest {
  template_id: string;
  name?: string;
  /** Pool for the app's data; omitted = first mounted pool by name. */
  pool?: string;
  port_overrides?: Record<number, number>;
  /** Container path -> host path inside a mounted pool. */
  volume_overrides?: Record<string, string>;
  /** Values of the template's variables; never PUID or PGID, which the server sets. */
  env_overrides?: Record<string, string>;
  /**
   * Run the app in the group unos-smb (PUID 80000, PGID 70000, UMASK 007); only with
   * can_run_in_unos_smb.
   */
  run_in_unos_smb?: boolean;
}

export interface AppInstallResponse {
  id: string;
  name: string;
  container_id: string;
  status: string;
  web_url: string | null;
  /** Pool holding the app's data, or null if it stores nothing on a pool. */
  pool: string | null;
  volumes: AppVolume[];
}

export async function getAppCatalog(category?: string, query?: string): Promise<AppTemplate[]> {
  const params = new URLSearchParams();
  if (category) params.append("category", category);
  if (query) params.append("query", query);
  const qs = params.toString();
  return api.get<AppTemplate[]>(`/apps/catalog${qs ? "?" + qs : ""}`);
}

export async function getAppTemplate(appId: string): Promise<AppTemplate> {
  return api.get<AppTemplate>(`/apps/catalog/${appId}`);
}

/** Mounted storage pools sorted by name; the first one is the server's default. */
export async function getAppPools(): Promise<AppPool[]> {
  return api.get<AppPool[]>("/apps/pools");
}

export async function getInstalledApps(): Promise<InstalledApp[]> {
  return api.get<InstalledApp[]>("/apps/installed");
}

export async function installApp(req: AppInstallRequest): Promise<AppInstallResponse> {
  return api.post<AppInstallResponse>("/apps/install", req);
}

export async function uninstallApp(containerId: string): Promise<void> {
  return api.delete(`/apps/installed/${containerId}`);
}

export async function startApp(containerId: string): Promise<void> {
  return api.post(`/apps/installed/${containerId}/start`);
}

export async function stopApp(containerId: string): Promise<void> {
  return api.post(`/apps/installed/${containerId}/stop`);
}

export async function restartApp(containerId: string): Promise<void> {
  return api.post(`/apps/installed/${containerId}/restart`);
}

/** Host path a catalog volume gets on a pool; fixed host paths are returned unchanged. */
export function poolHostPath(volume: VolumeMapping, poolPath: string): string {
  if (!volume.on_pool) return volume.host;
  return poolPath + volume.host.slice(POOL_PLACEHOLDER.length);
}

/** Collapse "." and ".." segments of an absolute path; null if it is not absolute. */
function normalizePath(path: string): string | null {
  if (!path.startsWith("/")) return null;
  const parts: string[] = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return "/" + parts.join("/");
}

/**
 * Whether `path` lies inside one of the pools. This is only a lexical pre-check
 * for the form; the server resolves symlinks and has the final say.
 */
export function isInsidePools(path: string, pools: AppPool[]): boolean {
  const normalized = normalizePath(path.trim());
  if (normalized === null) return false;
  return pools.some((pool) => normalized === pool.path || normalized.startsWith(pool.path + "/"));
}

/** Readable text for a failed request, with a fallback for empty or opaque messages. */
export function describeApiError(e: unknown, fallback: string): string {
  const message = e instanceof Error ? e.message.trim() : "";
  // An error object stringified into the message carries no information.
  if (!message || message.includes("[object Object]")) return fallback;
  return message;
}

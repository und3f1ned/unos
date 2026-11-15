import { api } from "./client";

// SMB shares and per-user access are stored in the UNOS database, and
// smb.conf is rendered from it (supervisor/src/shares). Share names are
// case-insensitive on the server. NFS exports are the lines of the host's
// export files; UNOS writes only its own file (supervisor/src/shares/nfs.py).

export type ShareAccessLevel = "read" | "write";

/** SMB state of a UNOS user (supervisor/src/shares/account_state.py). */
export type SmbAccountState = "active" | "password_needed" | "off" | "unavailable";

/** One user's access to a share, as the server reports it. */
export interface ShareAccess {
  user_id: number;
  username: string;
  level: ShareAccessLevel;
  smb_state: SmbAccountState;
  /** In smb.conf now: the share is served and the user's SMB account is active. */
  effective: boolean;
}

/**
 * One entry of an access list sent to the server. `username` is the user's
 * current name: the server answers 409 when the id now belongs to someone else.
 */
export interface ShareAccessEntry {
  user_id: number;
  username: string;
  level: ShareAccessLevel;
}

export interface SmbShare {
  name: string;
  path: string;
  comment: string;
  browseable: boolean;
  /** Guests may read the share without a password; they can never write. */
  guest_ok: boolean;
  /** Sorted by username. */
  access: ShareAccess[];
  /** Send it back as `expected_version` when changing the share. */
  version: number;
  /** The path passes the server's path rules: the share is in smb.conf. */
  served: boolean;
  /** Why the share is not served; null when served. */
  path_problem: string | null;
  /** Served, and guests or at least one user can connect. */
  available: boolean;
  pool: string | null;
  /** null: the host's mount table could not be read. */
  pool_mounted: boolean | null;
  /** Whether the folder is set up for SMB users; null: not mounted, missing or not served. */
  permissions_ok: boolean | null;
  /** Messages of the one-time import of the old smb.conf, until dismissed. */
  import_notes: string[];
  /**
   * The protocol options as stored (ADR-0377): clients that cannot encrypt are refused; Macs can
   * back up with Time Machine; Windows lists the snapshots of the share's pool or volume. The SMB
   * status names one that is on and not in smb.conf.
   */
  encryption_required: boolean;
  time_machine: boolean;
  previous_versions: boolean;
  /** Only in answers to changes. */
  warnings?: string[];
}

/** The protocol options of a share, each off by default (ADR-0377). */
export type ShareOption = 'encryption_required' | 'time_machine' | 'previous_versions';
export const SHARE_OPTIONS: ShareOption[] = ['encryption_required', 'time_machine', 'previous_versions'];

export interface SmbShareCreate {
  name: string;
  path: string;
  comment?: string;
  browseable?: boolean;
  guest_ok?: boolean;
  access?: ShareAccessEntry[];
  encryption_required?: boolean;
  time_machine?: boolean;
  previous_versions?: boolean;
}

/** PATCH body: only the fields that are sent change. */
export interface SmbShareUpdate {
  /** The server answers 409 when the share's version differs. */
  expected_version?: number;
  name?: string;
  path?: string;
  comment?: string;
  browseable?: boolean;
  guest_ok?: boolean;
  /** The complete list: it replaces the share's access list. */
  access?: ShareAccessEntry[];
  /** Only [] is accepted: it dismisses the import notes. */
  import_notes?: [];
  encryption_required?: boolean;
  time_machine?: boolean;
  previous_versions?: boolean;
}

export interface SmbShareDeleted {
  status: "deleted";
  warnings: string[];
}

export type RepairState = "running" | "done" | "incomplete" | "failed";

/** A repair of a share's permissions: while it runs (the counts so far), then its result. */
export interface RepairResult {
  share: string;
  state: RepairState;
  started_at: string;
  /** null while it runs. */
  finished_at: string | null;
  visited: number;
  changed: number;
  /** Keys: other_filesystem, snapshots, symlink, special, hardlink, private, too_deep, raced. */
  skipped: Record<string, number>;
  /** At most 20 relative paths of skipped volumes and mount points. */
  skipped_paths: string[];
  errors: number;
  /** At most 20, "relative/path: error". */
  error_samples: string[];
  message: string;
}

export interface SmbServiceStatus {
  /** null: the check failed. */
  samba_tools_missing: string[] | null;
  group_ok: boolean | null;
  problems: string[];
  /** Non-empty: guests can read these shares, and signing is not required. */
  guest_shares: string[];
  import_report: string[];
  /** Samba accounts that UNOS did not create; they cannot use UNOS shares. */
  unmanaged_samba_users: string[];
  active_smb_users: number;
}

/** One client of an export, as exportfs reads it. */
export interface NfsExportClient {
  /** "*" for everyone, also for a line without clients. */
  client: string;
  /** The line's default options, then the client's own. */
  options: string[];
  /** The last of ro and rw; null when sec= sets them per security flavor. */
  access: ShareAccessLevel | null;
}

/** One entry of the host's export files, as exportfs reads it. */
export interface NfsExport {
  path: string;
  clients: NfsExportClient[];
  /**
   * A line of the export file of UNOS, exactly as UNOS writes it: it can be deleted here. Others,
   * also lines written by hand into that file, are changed on the NAS.
   */
  managed: boolean;
  /** The export file, and the line where the entry starts. */
  file: string;
  line: number;
  /** Only in answers to changes: what exportfs reported. */
  warnings?: string[];
}

/** An export that UNOS writes: an existing folder, for one address or network of the local network. */
export interface NfsExportCreate {
  path: string;
  client: string;
  access: ShareAccessLevel;
}

export interface NfsExportDeleted {
  status: "deleted";
  /** What exportfs reported. */
  warnings: string[];
}

export interface NfsServiceStatus {
  /** The host has no exportfs (the package nfs-kernel-server); null: the check failed. */
  server_missing: boolean | null;
  /**
   * Lines that exportfs cannot read (it reads no further in their files, and UNOS changes no
   * export until they are corrected), and exports of UNOS below folders that other accounts can change.
   */
  problems: string[];
}

export interface SharePath {
  path: string;
  /** The pool's name, or pool/volume; the page writes the kind in its language (ADR-0299). */
  name: string;
  type: "pool" | "volume";
  /** Its pool is one that UNOS manages (fstab marker): only such locations can hold SMB shares. */
  managed: boolean;
}

export async function getSharePaths(): Promise<SharePath[]> {
  return api.get<SharePath[]>("/shares/paths");
}

function smbSharePath(name: string): string {
  return `/shares/smb/${encodeURIComponent(name)}`;
}

export async function getSmbShares(): Promise<SmbShare[]> {
  return api.get<SmbShare[]>("/shares/smb");
}

export async function getSmbShare(name: string): Promise<SmbShare> {
  return api.get<SmbShare>(smbSharePath(name));
}

export async function createSmbShare(share: SmbShareCreate): Promise<SmbShare> {
  return api.post<SmbShare>("/shares/smb", share);
}

export async function updateSmbShare(name: string, patch: SmbShareUpdate): Promise<SmbShare> {
  return api.patch<SmbShare>(smbSharePath(name), patch);
}

export async function deleteSmbShare(name: string): Promise<SmbShareDeleted> {
  return api.delete<SmbShareDeleted>(smbSharePath(name));
}

/**
 * Start a repair in the background; answers it running (ADR-0363). Its progress and its end come
 * over /ws/v1/events (the store repairJob of $lib/stores/repair).
 */
export async function repairSharePermissions(name: string): Promise<RepairResult> {
  return api.post<RepairResult>(`${smbSharePath(name)}/repair-permissions`);
}

/** Ask the running repair of the share to stop; its end comes over /ws/v1/events. */
export async function stopRepair(name: string): Promise<RepairResult> {
  return api.delete<RepairResult>(`${smbSharePath(name)}/repair-permissions`);
}

/** The last finished repair of the share since UNOS started; 404 when none has run. */
export async function getRepairResult(name: string): Promise<RepairResult> {
  return api.get<RepairResult>(`${smbSharePath(name)}/repair-permissions`);
}

export async function getSmbStatus(): Promise<SmbServiceStatus> {
  return api.get<SmbServiceStatus>("/shares/smb-status");
}

export async function clearImportReport(): Promise<{ status: string }> {
  return api.delete<{ status: string }>("/shares/smb-status/import-report");
}

export async function getNfsExports(): Promise<NfsExport[]> {
  return api.get<NfsExport[]>("/shares/nfs");
}

export async function getNfsStatus(): Promise<NfsServiceStatus> {
  return api.get<NfsServiceStatus>("/shares/nfs-status");
}

export async function createNfsExport(exp: NfsExportCreate): Promise<NfsExport> {
  return api.post<NfsExport>("/shares/nfs", exp);
}

export async function deleteNfsExport(path: string): Promise<NfsExportDeleted> {
  return api.delete<NfsExportDeleted>(`/shares/nfs?path=${encodeURIComponent(path)}`);
}

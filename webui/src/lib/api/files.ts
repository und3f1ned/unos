import { api, apiFetch, downloadAs } from "./client";

export interface FileInfo {
  name: string;
  path: string;
  type: "file" | "directory" | "symlink";
  size: number;
  modified: string;
  permissions: string;
  owner: string;
  group: string;
}

export interface DirectoryListing {
  path: string;
  /** null for the pools directory, the top of the file manager. */
  parent: string | null;
  items: FileInfo[];
  total_size: number;
  item_count: number;
}

/**
 * List a directory. Without a path the server lists the pools directory
 * (UNOS_POOLS_BASE_PATH, /mnt/pools on a device): the file manager cannot go
 * above it, so the UI starts there instead of assuming its location.
 */
export async function listDirectory(path?: string): Promise<DirectoryListing> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return api.get<DirectoryListing>(`/files/list${query}`);
}

export async function getFileInfo(path: string): Promise<FileInfo> {
  return api.get<FileInfo>(`/files/info?path=${encodeURIComponent(path)}`);
}

/** Download a file through the API with the session token. */
export async function downloadFile(path: string): Promise<void> {
  const name = path.split("/").pop() || "download";
  await downloadAs(`/files/download?path=${encodeURIComponent(path)}`, name);
}

/** Upload a file into the directory `path`; the server keeps only the file's base name. */
export async function uploadFile(path: string, file: File): Promise<FileInfo> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await apiFetch(`/files/upload?path=${encodeURIComponent(path)}`, {
    method: "POST",
    body: formData,
  });
  return response.json();
}

export async function createDirectory(path: string, name: string): Promise<FileInfo> {
  return api.post<FileInfo>("/files/mkdir", { path, name });
}

export async function renameItem(path: string, newName: string): Promise<FileInfo> {
  return api.post<FileInfo>("/files/rename", { path, new_name: newName });
}

// Copy, move and delete take the paths as a bare JSON array in the body;
// copy and move take the destination folder as a query parameter.

export async function copyItems(sources: string[], destination: string): Promise<{ copied: string[] }> {
  return api.post(`/files/copy?destination=${encodeURIComponent(destination)}`, sources);
}

export async function moveItems(sources: string[], destination: string): Promise<{ moved: string[] }> {
  return api.post(`/files/move?destination=${encodeURIComponent(destination)}`, sources);
}

/**
 * Move items into the trash of their pool or volume (ADR-0259), or with
 * `permanent` delete them for good at once.
 */
export async function deleteItems(paths: string[], permanent = false): Promise<{ deleted: string[] }> {
  return api.delete(`/files/delete${permanent ? "?permanent=true" : ""}`, paths);
}

/** An item in the trash of a pool or a volume. */
export interface TrashItem {
  /** Its place in the trash: what restoreFromTrash and deleteFromTrash take. */
  path: string;
  /** The name it had (the entry's own name when its original path is not known). */
  name: string;
  type: "file" | "directory" | "symlink";
  size: number;
  /** Where it was deleted from; null when that is not known, and it cannot be restored. */
  original_path: string | null;
  /** When it was deleted, in UTC with its offset. */
  deleted: string;
  /** From then on it is deleted for good. */
  expires: string;
}

export interface TrashListing {
  /** Newest first. */
  items: TrashItem[];
  retention_days: number;
  /** The trashes that could not be read, each with the reason. */
  problems: string[];
}

/** The items in the trash of every pool and volume. */
export async function listTrash(): Promise<TrashListing> {
  return api.get<TrashListing>("/files/trash");
}

/** Put items of the trash back where they were; nothing is replaced. */
export async function restoreFromTrash(paths: string[]): Promise<{ restored: string[] }> {
  return api.post("/files/trash/restore", paths);
}

/** Delete items of the trash for good. */
export async function deleteFromTrash(paths: string[]): Promise<{ deleted: string[] }> {
  return api.delete("/files/trash", paths);
}

/** Delete everything in the trash of every pool and volume for good. */
export async function emptyTrash(): Promise<{ deleted: number }> {
  return api.post("/files/trash/empty");
}

/** A hidden file that an upload left when the supervisor stopped in the middle of it (ADR-0350). */
export interface UploadLeftover {
  path: string;
  /** What had arrived, in bytes. */
  size: number;
  /** When it was last written, in UTC with its offset. */
  modified: string;
}

export interface UploadLeftovers {
  /** By path. */
  files: UploadLeftover[];
  /** When the last check ended; null before the first, which runs 10 minutes after the supervisor's start. */
  checked: string | null;
  checking: boolean;
  /** The folders the check could not read, each with the reason. */
  problems: string[];
}

/** What the last check found below the pools directory, each file looked at again. */
export async function listUploadLeftovers(): Promise<UploadLeftovers> {
  return api.get<UploadLeftovers>("/files/upload-leftovers");
}

/** Remove such files for good; answers the ones that were there. */
export async function removeUploadLeftovers(paths: string[]): Promise<{ removed: string[] }> {
  return api.delete("/files/upload-leftovers", paths);
}

/**
 * `bytes` in the largest unit that keeps it at 1 or more, with one decimal at most: "1.5 MB".
 * `units` names bytes, KB, MB, GB and TB in the language of the page: files.unit.* of the catalogs
 * (ADR-0297).
 */
export function formatSize(bytes: number, units: readonly string[]): string {
  if (bytes === 0) return `0 ${units[0]}`;
  const k = 1024;
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + units[i];
}

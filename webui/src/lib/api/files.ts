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

export async function deleteItems(paths: string[]): Promise<{ deleted: string[] }> {
  return api.delete("/files/delete", paths);
}

export function formatSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

import { api } from "./client";

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
  parent: string | null;
  items: FileInfo[];
  total_size: number;
  item_count: number;
}

export async function listDirectory(path: string): Promise<DirectoryListing> {
  return api.get<DirectoryListing>(`/files/list?path=${encodeURIComponent(path)}`);
}

export async function getFileInfo(path: string): Promise<FileInfo> {
  return api.get<FileInfo>(`/files/info?path=${encodeURIComponent(path)}`);
}

export async function downloadFile(path: string): Promise<void> {
  const token = localStorage.getItem("token");
  const url = `/api/v1/files/download?path=${encodeURIComponent(path)}`;
  
  const a = document.createElement("a");
  a.href = url;
  a.download = path.split("/").pop() || "download";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

export async function uploadFile(path: string, file: File): Promise<FileInfo> {
  const formData = new FormData();
  formData.append("file", file);
  
  const token = localStorage.getItem("token");
  const res = await fetch(`/api/v1/files/upload?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Upload failed");
  }
  
  return res.json();
}

export async function createDirectory(path: string, name: string): Promise<FileInfo> {
  return api.post<FileInfo>("/files/mkdir", { path, name });
}

export async function renameItem(path: string, newName: string): Promise<FileInfo> {
  return api.post<FileInfo>("/files/rename", { path, new_name: newName });
}

export async function copyItems(sources: string[], destination: string): Promise<{ copied: string[] }> {
  return api.post("/files/copy", null, { sources, destination });
}

export async function moveItems(sources: string[], destination: string): Promise<{ moved: string[] }> {
  return api.post("/files/move", null, { sources, destination });
}

export async function deleteItems(paths: string[]): Promise<{ deleted: string[] }> {
  return api.delete("/files/delete", { paths });
}

export function formatSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

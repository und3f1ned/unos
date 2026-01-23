import { api } from "./client";

export interface BackupInfo {
  filename: string;
  size: number;
  created: string;
  version: string;
}

export interface BackupConfig {
  include_users: boolean;
  include_shares: boolean;
  include_pools: boolean;
  include_apps: boolean;
  include_scheduler: boolean;
  include_notifications: boolean;
  include_network: boolean;
}

export interface RestoreResult {
  status: string;
  restored: string[];
  manifest: {
    version: string;
    created: string;
    hostname: string;
  };
  message: string;
}

export async function listBackups(): Promise<{ backups: BackupInfo[] }> {
  return api.get<{ backups: BackupInfo[] }>("/backup/list");
}

export async function createBackup(config?: Partial<BackupConfig>): Promise<{ status: string; backup: BackupInfo }> {
  return api.post<{ status: string; backup: BackupInfo }>("/backup/create", config || {});
}

export async function downloadBackup(filename: string): Promise<void> {
  const token = localStorage.getItem("token");
  const response = await fetch("/api/v1/backup/download/" + filename, {
    headers: { Authorization: "Bearer " + token }
  });

  if (!response.ok) {
    throw new Error("Failed to download backup");
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function uploadBackup(file: File): Promise<{ status: string; backup: BackupInfo }> {
  const token = localStorage.getItem("token");
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/v1/backup/upload", {
    method: "POST",
    headers: { Authorization: "Bearer " + token },
    body: formData
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to upload backup");
  }

  return response.json();
}

export async function restoreBackup(filename: string): Promise<RestoreResult> {
  return api.post<RestoreResult>("/backup/restore/" + filename, {});
}

export async function deleteBackup(filename: string): Promise<{ status: string }> {
  return api.delete<{ status: string }>("/backup/" + filename);
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

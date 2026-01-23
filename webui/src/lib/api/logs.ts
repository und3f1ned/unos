import { api } from "./client";

export interface LogEntry {
  timestamp: string;
  facility: string;
  priority: string;
  message: string;
  source: string;
}

export interface LogSource {
  name: string;
  path: string | null;
  available: boolean;
}

export interface LogFile {
  name: string;
  path: string;
  size: number;
  modified: string;
}

export interface LogsResponse {
  entries: LogEntry[];
  total: number;
}

export interface LogFileContent {
  filename: string;
  content: string;
  lines: number;
}

export async function getLogSources(): Promise<{ sources: LogSource[] }> {
  return api.get<{ sources: LogSource[] }>("/logs/sources");
}

export async function getLogFiles(): Promise<{ files: LogFile[] }> {
  return api.get<{ files: LogFile[] }>("/logs/files");
}

export async function getLogEntries(
  source: string = "journal",
  limit: number = 100,
  priority?: string,
  search?: string,
  since?: string
): Promise<LogsResponse> {
  const params = new URLSearchParams();
  params.append("source", source);
  params.append("limit", limit.toString());
  if (priority) params.append("priority", priority);
  if (search) params.append("search", search);
  if (since) params.append("since", since);

  return api.get<LogsResponse>(`/logs/entries?${params.toString()}`);
}

export async function getLogFileContent(
  filename: string,
  lines: number = 100
): Promise<LogFileContent> {
  return api.get<LogFileContent>(`/logs/file/${filename}?lines=${lines}`);
}

export async function clearLogs(source: string): Promise<{ status: string }> {
  return api.delete<{ status: string }>(`/logs/clear/${source}`);
}

export function getPriorityColor(priority: string): string {
  switch (priority) {
    case "emerg":
    case "alert":
    case "crit":
      return "text-red-500";
    case "err":
      return "text-red-400";
    case "warning":
      return "text-yellow-400";
    case "notice":
      return "text-blue-400";
    case "info":
      return "text-slate-300";
    case "debug":
      return "text-slate-500";
    default:
      return "text-slate-400";
  }
}

export function getPriorityBadgeColor(priority: string): string {
  switch (priority) {
    case "emerg":
    case "alert":
    case "crit":
      return "bg-red-600";
    case "err":
      return "bg-red-500";
    case "warning":
      return "bg-yellow-500";
    case "notice":
      return "bg-blue-500";
    case "info":
      return "bg-slate-600";
    case "debug":
      return "bg-slate-700";
    default:
      return "bg-slate-600";
  }
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

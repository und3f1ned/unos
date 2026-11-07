import { api } from "./client";

export interface LogEntry {
  /** UTC with its offset, such as 2026-11-07T09:59:30+00:00; "" when the journal gives none. */
  timestamp: string;
  facility: string;
  priority: string;
  message: string;
  source: string;
  /** The program that logged it. */
  unit?: string | null;
}

/** A part of the host's journal that GET /logs/entries reads (ADR-0247). */
export interface LogSource {
  name: string;
  label: string;
  description: string;
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

export type LogPriority = "emerg" | "alert" | "crit" | "err" | "warning" | "notice" | "info" | "debug";

export const PRIORITY_LEVELS: { value: LogPriority; label: string }[] = [
  { value: "emerg", label: "Emergency" },
  { value: "alert", label: "Alert" },
  { value: "crit", label: "Critical" },
  { value: "err", label: "Error" },
  { value: "warning", label: "Warning" },
  { value: "notice", label: "Notice" },
  { value: "info", label: "Info" },
  { value: "debug", label: "Debug" }
];

export const TIME_RANGES = [
  { value: "", label: "All time" },
  { value: "5 minutes ago", label: "Last 5 minutes" },
  { value: "15 minutes ago", label: "Last 15 minutes" },
  { value: "1 hour ago", label: "Last hour" },
  { value: "6 hours ago", label: "Last 6 hours" },
  { value: "24 hours ago", label: "Last 24 hours" },
  { value: "7 days ago", label: "Last 7 days" }
];

export async function getLogSources(): Promise<{ sources: LogSource[] }> {
  return api.get<{ sources: LogSource[] }>("/logs/sources");
}

export async function getLogFiles(): Promise<{ files: LogFile[] }> {
  return api.get<{ files: LogFile[] }>("/logs/files");
}

export interface LogQueryParams {
  source?: string;
  limit?: number;
  /** This level and the more severe ones. */
  priority?: string;
  /** Text of the message, in any case: the server finds the newest matches. */
  search?: string;
  /** One of TIME_RANGES. */
  since?: string;
}

/** The newest entries of a source, newest first. */
export async function getLogEntries(params: LogQueryParams = {}): Promise<LogsResponse> {
  const urlParams = new URLSearchParams();
  urlParams.append("source", params.source || "journal");
  urlParams.append("limit", (params.limit || 200).toString());
  if (params.priority) urlParams.append("priority", params.priority);
  if (params.search) urlParams.append("search", params.search);
  if (params.since) urlParams.append("since", params.since);

  return api.get<LogsResponse>(`/logs/entries?${urlParams.toString()}`);
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

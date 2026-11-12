import { api, getToken, handleUnauthorized } from "./client";
import { WS_CLOSE_FORBIDDEN, WS_CLOSE_UNAUTHORIZED } from "$lib/stores/stats";

export interface LogEntry {
  /** UTC with its offset, such as 2026-11-07T09:59:30+00:00; "" when the journal gives none. */
  timestamp: string;
  facility: string;
  priority: string;
  message: string;
  source: string;
  /** The program that logged it. */
  unit?: string | null;
  /** The journal's cursor of the entry. */
  cursor?: string;
}

/** A part of the host's journal that GET /logs/entries reads (ADR-0257). */
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

/** The journal's levels; System > Logs names them through its catalog (ADR-0287). */
export type LogPriority = "emerg" | "alert" | "crit" | "err" | "warning" | "notice" | "info" | "debug";

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
  /** journalctl's --since as the server takes it: <N> minutes ago, <N> hours ago or <N> days ago. */
  since?: string;
}

/** The query of GET /logs/entries and of the journal socket. */
function entriesQuery(params: LogQueryParams): string {
  const urlParams = new URLSearchParams();
  urlParams.append("source", params.source || "journal");
  urlParams.append("limit", (params.limit || 200).toString());
  if (params.priority) urlParams.append("priority", params.priority);
  if (params.search) urlParams.append("search", params.search);
  if (params.since) urlParams.append("since", params.since);
  return urlParams.toString();
}

/** The newest entries of a source, newest first. */
export async function getLogEntries(params: LogQueryParams = {}): Promise<LogsResponse> {
  return api.get<LogsResponse>(`/logs/entries?${entriesQuery(params)}`);
}

/** How the journal socket ended: the server refused it (close code 4403), or the connection
 * ended, after an error that onError gave or because it was lost. */
export type JournalStreamEnd = "forbidden" | "ended";

export interface JournalStreamHandlers {
  /** The newest entries of the query, newest first, as GET /logs/entries lists them. */
  onEntries(entries: LogEntry[]): void;
  /** A new entry of the query, as the journal got it. */
  onEntry(entry: LogEntry): void;
  /** Why the server ended the stream: the journal could not be read, or a value was refused. */
  onError(message: string): void;
  /** The stream is over. Not called after close(). */
  onEnd(reason: JournalStreamEnd): void;
}

export interface JournalStream {
  /** Close the stream; no handler is called after this. */
  close(): void;
}

/**
 * Follow the host's journal over /ws/v1/journal (supervisor/src/websocket/events.py, ADR-0364):
 * the newest entries of the query, then each new one. The query goes in the URL as that of
 * GET /logs/entries; the token goes in the first message, never in the URL (ADR-0031), and a close
 * with 4401 ends the session like an HTTP 401 does.
 */
export function streamLogEntries(params: LogQueryParams, handlers: JournalStreamHandlers): JournalStream {
  const token = getToken();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/v1/journal?${entriesQuery(params)}`);
  let done = false;

  socket.onopen = () => {
    socket.send(JSON.stringify({ type: "auth", token }));
  };

  socket.onmessage = (event) => {
    let message: { type?: unknown; data?: unknown; message?: unknown };
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === "entries" && Array.isArray(message.data)) handlers.onEntries(message.data as LogEntry[]);
    else if (message.type === "entry" && message.data) handlers.onEntry(message.data as LogEntry);
    else if (message.type === "error" && typeof message.message === "string") handlers.onError(message.message);
  };

  // No onerror handler: an error event is always followed by this close event.
  socket.onclose = (event) => {
    if (done) return;
    done = true;
    if (event.code === WS_CLOSE_UNAUTHORIZED) {
      // Token expired or the account is gone: same as an HTTP 401.
      handleUnauthorized(token);
    }
    handlers.onEnd(event.code === WS_CLOSE_FORBIDDEN ? "forbidden" : "ended");
  };

  return {
    close() {
      if (done) return;
      done = true;
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close(1000);
      }
    }
  };
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

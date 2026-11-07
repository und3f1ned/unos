import { api, downloadAs } from './client';

// The audit log (administrators only): supervisor/src/audit/router.py, ADR-0237.

export type AuditSeverity = 'info' | 'warning' | 'critical';

export interface AuditEntry {
  id: string;
  /** The server's local time without a zone, e.g. 2026-11-07T14:30:12.123456. */
  timestamp: string;
  action: string;
  severity: AuditSeverity;
  username: string | null;
  ip_address: string | null;
  user_agent: string | null;
  resource_type: string | null;
  resource_id: string | null;
  details: string | null;
  success: boolean;
}

export interface AuditAction {
  value: string;
  name: string;
}

/**
 * The filters that GET /audit/logs, /logs/count and /logs/export share; an empty value
 * filters nothing. username and action match exactly, search any part of an entry in any
 * case, and the dates (YYYY-MM-DD, the server's days) include their whole day.
 */
export interface AuditFilters {
  search: string;
  username: string;
  action: string;
  start_date: string;
  end_date: string;
}

export type AuditExportFormat = 'csv' | 'json';

function query(filters: AuditFilters, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extra })) {
    if (value) params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

/** The newest `limit` entries that match, newest first. */
export function getAuditLogs(filters: AuditFilters, limit: number): Promise<AuditEntry[]> {
  return api.get<AuditEntry[]>(`/audit/logs${query(filters, { limit: String(limit) })}`);
}

export async function getAuditCount(filters: AuditFilters): Promise<number> {
  return (await api.get<{ count: number }>(`/audit/logs/count${query(filters)}`)).count;
}

export async function getAuditActions(): Promise<AuditAction[]> {
  return (await api.get<{ actions: AuditAction[] }>('/audit/actions')).actions;
}

/** Download every entry that matches, newest first, as `filename`. */
export function exportAuditLog(
  filters: AuditFilters,
  format: AuditExportFormat,
  filename: string
): Promise<void> {
  return downloadAs(`/audit/logs/export${query(filters, { format })}`, filename);
}

/**
 * login_failed -> "Login failed": the action with spaces for its underscores, which the
 * search of the API finds too.
 */
export function actionLabel(action: string): string {
  const label = action.replace(/_/g, ' ');
  return label.charAt(0).toUpperCase() + label.slice(1);
}

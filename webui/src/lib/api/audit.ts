import { api, downloadAs } from './client';

// The audit log (administrators only): supervisor/src/audit/router.py, ADR-0401.

export type AuditSeverity = 'info' | 'warning' | 'critical';

export interface AuditEntry {
	id: string;
	/** In UTC with its offset, e.g. 2026-11-07T14:30:12.123456Z (ADR-0401). */
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
	/** The hash chain (ADR-0328): the hash of the entry before it, and its own. */
	prev_hash: string | null;
	hash: string | null;
	/** Where the entry stands in the chain: broken where it was edited, or after a removed entry. */
	chain: 'ok' | 'broken' | null;
}

export interface AuditAction {
	value: string;
	name: string;
}

/**
 * The filters that GET /audit/logs, /logs/count and /logs/export share; an empty value
 * filters nothing. username and action match exactly, search any part of an entry in any
 * case, and the bounds (a date, or a date and time, in UTC or with an offset) include the whole
 * unit they name: the Audit Log page sends the first and the last instant of the browser's days.
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
export function exportAuditLog(filters: AuditFilters, format: AuditExportFormat, filename: string): Promise<void> {
	return downloadAs(`/audit/logs/export${query(filters, { format })}`, filename);
}

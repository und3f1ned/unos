// The audit log as a timeline (ADR-0401): the entries by the browser's days, their texts and
// marks, and the days of the filters. The server answers each time in UTC with its offset.
import type { AuditEntry } from '$lib/api/audit';
import type { Translate } from '$lib/i18n';
import { localDay } from '$lib/time';

/** The page's $t: a key's text in the language of the page. */

export interface Day {
	day: string;
	entries: AuditEntry[];
}

/** The entries by the browser's day of their time, in their order (newest first). */
export function byDay(list: AuditEntry[]): Day[] {
	const groups: Day[] = [];
	for (const entry of list) {
		const day = localDay(entry.timestamp) ?? entry.timestamp;
		const last = groups[groups.length - 1];
		if (last && last.day === day) last.entries.push(entry);
		else groups.push({ day, entries: [entry] });
	}
	return groups;
}

/**
 * The bound of the browser's day `day` (YYYY-MM-DD, as a date field holds it) for the server:
 * its first instant, or (`last`) its last millisecond, which the server takes as the whole
 * millisecond. In UTC with its offset; '' for no day.
 */
export function dayBound(day: string, last: boolean): string {
	const [year, month, date] = day.split('-').map(Number);
	if (!year || !month || !date) return '';
	const moment = last ? new Date(year, month - 1, date + 1).getTime() - 1 : new Date(year, month - 1, date).getTime();
	return Number.isNaN(moment) ? '' : new Date(moment).toISOString();
}

/** The day `day` (YYYY-MM-DD) as the language of `locale` writes it in full; else `day`. */
export function dayLabel(day: string, locale: string): string {
	const [year, month, date] = day.split('-').map(Number);
	const moment = new Date(year, month - 1, date);
	if (Number.isNaN(moment.getTime())) return day;
	return moment.toLocaleDateString(locale, {
		weekday: 'long',
		year: 'numeric',
		month: 'long',
		day: 'numeric'
	});
}

/** The browser's date today as YYYYMMDD, for the file name of an export. */
export function today(): string {
	const now = new Date();
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
}

/** The colour of an entry's mark on the timeline: failures and critical entries are errors. */
export function markerClass(entry: AuditEntry): string {
	if (!entry.success || entry.severity === 'critical') return 'bg-status-error';
	if (entry.severity === 'warning') return 'bg-status-warning';
	return 'bg-status-info';
}

/** The text of `value` by its key in `keys`, in the language of `t`; else `value`. */
export function textOf(keys: Record<string, string>, value: string, t: Translate): string {
	return Object.prototype.hasOwnProperty.call(keys, value) ? t(keys[value]) : value;
}

/** An entry's resource: its type by `types` and its id, such as "Share media". */
export function resourceOf(entry: AuditEntry, types: Record<string, string>, t: Translate): string {
	return [entry.resource_type && textOf(types, entry.resource_type, t), entry.resource_id].filter(Boolean).join(' ');
}

// The Audit Log page's reads (routes/audit/+page.ts): the newest entries without a filter, a page of
// them and how many there are, as the page's first load read them (ADR-0401); the actions of the
// Action filter; and the NAS's usernames, which the User field suggests. The filters and Show more
// are the page's state, not its URL: the load reads the unfiltered first page alone, and a filter
// or Show more is a read of the page's own (AuditLog), never kept in the cache.
import {
	getAuditActions,
	getAuditCount,
	getAuditLogs,
	type AuditAction,
	type AuditEntry,
	type AuditFilters
} from '$lib/api/audit';
import { getUsers } from '$lib/api/auth';
import { EVERY_AREA } from './cache';
import type { PageReader } from './load';
import { settle, type Settled } from './read';

/** Entries per page: the first load's, and each Show more's (AuditLog). */
export const AUDIT_PAGE_SIZE = 100;

/** The filters of the page with every field empty, in the page's order of their keys. */
export const NO_FILTERS: AuditFilters = { search: '', username: '', action: '', start_date: '', end_date: '' };

export type AuditSnapshot = {
	/** The filters the entries were read with: NO_FILTERS. */
	filters: AuditFilters;
	entries: Settled<{ entries: AuditEntry[]; total: number }>;
	actions: Settled<AuditAction[]>;
	usernames: Settled<string[]>;
};

async function readAudit(): Promise<AuditSnapshot> {
	const filters = NO_FILTERS;
	const [entries, actions, usernames] = await Promise.all([
		settle(
			Promise.all([getAuditLogs(filters, AUDIT_PAGE_SIZE), getAuditCount(filters)]).then(([entries, total]) => ({
				entries,
				total
			}))
		),
		settle(getAuditActions()),
		settle(getUsers().then((users) => users.map((u) => u.username).sort((a, b) => a.localeCompare(b))))
	]);
	return { filters, entries, actions, usernames };
}

export const auditReader: PageReader<AuditSnapshot> = {
	key: 'audit',
	// Every change is written to the audit log: a change of any area makes the entries old.
	areas: [EVERY_AREA],
	adminOnly: true,
	read: readAudit
};

// System > Logs' reads (routes/system/logs/+page.ts, ADR-0257, ADR-0366): the sources, then the
// newest entries of the default filters, the journal's 200. journalctl can take seconds on a large
// journal, so the tab shows with its sources and filters without waiting for the entries, whose
// place waits; a source or a filter picked meanwhile is the page's own read, and its answer the one
// shown. Auto's journal socket is the page's too.
import { getLogEntries, getLogSources, type LogEntry, type LogSource } from '$lib/api/logs';
import type { PageReader } from '$lib/pages/load';
import { later, settle, type Later, type Settled } from '$lib/pages/read';

/** The filters the tab opens with: the source and the number of entries. */
export const DEFAULT_LOG_SOURCE = 'journal';
export const DEFAULT_LOG_LIMIT = 200;

export type LogsSnapshot = {
	sources: Settled<LogSource[]>;
	entries: Later<Settled<LogEntry[]>>;
};

async function readLogs(): Promise<LogsSnapshot> {
	const sources = await settle(getLogSources().then((answer) => answer.sources));
	const entries = later(
		settle(getLogEntries({ source: DEFAULT_LOG_SOURCE, limit: DEFAULT_LOG_LIMIT }).then((answer) => answer.entries)),
		{ ok: false, error: '' } as Settled<LogEntry[]>
	);
	return { sources, entries };
}

export const logsReader: PageReader<LogsSnapshot> = {
	key: 'system:logs',
	areas: ['logs'],
	adminOnly: true,
	read: readLogs
};

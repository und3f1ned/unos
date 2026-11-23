// The pages' cache: what each page's load last read in this tab, so that a page visited before
// appears at once with it while a fresh read replaces it (stale-while-revalidate; webui/AGENTS.md,
// "Page loads"). In the tab's memory only, never in a storage; an entry serves the session it was
// read in, and the cache is emptied at a sign-out and whenever the account changes. It holds what
// the pages show and no secret: a page whose data hold one (a new token, a TOTP secret, recovery
// codes) reads without it.
//
// Every request that may change something (any method but GET and HEAD, $lib/api/client) changes
// its area, the first part of its path (`storage` of /storage/pools/data/scrub): the entries that
// read the area are forgotten at once, and a read of the area that started before the change is
// old (upToDate), so that a page never shows data older than its own last change.
import { get } from 'svelte/store';
import type { User } from '$lib/api/auth';
import { currentSession, onApiChange } from '$lib/api/client';
import { user } from '$lib/stores/auth';

type Entry = { session: number; areas: readonly string[]; mark: number; value: unknown };

/** The area of a reader whose snapshot every change changes: the Audit Log, which records them all. */
export const EVERY_AREA = '*';

const entries = new Map<string, Entry>();
// The changes of this tab: their count, and the count at the last change of each area and of
// everything (a configuration restored).
let changes = 0;
const changedAt = new Map<string, number>();
let everythingChangedAt = 0;

/** The area of an API path: its first part, without the query. */
export function areaOf(path: string): string {
	return path.split(/[?#]/)[0].split('/').filter(Boolean)[0] ?? '';
}

/** The count of changes so far: a read notes it when it starts. */
export function changeMark(): number {
	return changes;
}

/** Whether no change of `areas` came after `mark` (a read's changeMark when it started). */
export function upToDate(areas: readonly string[], mark: number): boolean {
	return (
		everythingChangedAt <= mark &&
		areas.every((area) => (area === EVERY_AREA ? changes : (changedAt.get(area) ?? 0)) <= mark)
	);
}

/** The value kept under `key` for this session, with the mark of its read; undefined when there is none. */
export function recall<T>(key: string): { value: T; mark: number } | undefined {
	const entry = entries.get(key);
	if (!entry || entry.session !== currentSession()) return undefined;
	return { value: entry.value as T, mark: entry.mark };
}

/**
 * Keep what a read of `areas` in `session` that started at `mark` answered, unless a change made it
 * old meanwhile or the session is over.
 */
export function remember(key: string, areas: readonly string[], mark: number, value: unknown, session: number): void {
	if (session === currentSession() && upToDate(areas, mark)) entries.set(key, { session, areas, mark, value });
}

/** Forget the entry of `key` (a page's Refresh reads afresh). */
export function forget(key: string): void {
	entries.delete(key);
}

/** Everything may have changed (a backup of the configuration restored): every entry and read is old. */
export function changedEverything(): void {
	changes += 1;
	everythingChangedAt = changes;
	entries.clear();
}

/** Something of `area` changed: the entries that read it are old. */
export function changed(area: string): void {
	changes += 1;
	changedAt.set(area, changes);
	for (const [key, entry] of entries) {
		if (entry.areas.includes(area) || entry.areas.includes(EVERY_AREA)) entries.delete(key);
	}
}

if (typeof window !== 'undefined') {
	onApiChange((path) => changed(areaOf(path)));
	// A sign-out, a session that ended, another account or another role: nothing read for the last
	// one stays.
	const whose = (me: User | null) => (me ? `${me.id}:${me.is_admin === true}` : null);
	let account = whose(get(user));
	user.subscribe((me) => {
		if (whose(me) === account) return;
		account = whose(me);
		entries.clear();
	});
}

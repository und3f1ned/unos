// A page's load (its +page.ts; webui/AGENTS.md, "Page loads"): SvelteKit runs it before it shows
// the page, so that a navigation keeps the page that is open until the next one has its data, and
// no skeleton comes between them. The load reads through a page reader: the page's API reads, for
// the signed-in account, as one snapshot that holds each read's value or its failure.
//
// - A page visited before in this session appears at once with its cached snapshot, and a fresh
//   read replaces it when it answers (`fresh`).
// - Otherwise the navigation waits for the fresh read.
// - Only the tab's first page, under the boot screen, shows its skeleton, and only when its read
//   takes longer than FIRST_LOAD_SKELETON_DELAY_MS.
// - A read that a change of its areas made old is not kept (cache.ts); a navigation's reads again.
// - A read that answers after the session or the account changed is nobody's: neither kept nor shown.
// - For no account, or another account than the reader's (administrators only), it reads nothing:
//   the root layout leads to the sign-in page or away (ADR-0030).
import { get } from 'svelte/store';
import { invalidate } from '$app/navigation';
import type { User } from '$lib/api/auth';
import { currentSession, knownSignedOut, obtainSessionKey, sessionStopped } from '$lib/api/client';
import { isSetupOnly, loadCurrentUser, user } from '$lib/stores/auth';
import { connectWebSocket } from '$lib/stores/stats';
import { changeMark, forget, recall, remember, upToDate } from './cache';

/** How long the tab's first page waits for its data before it shows its skeleton. */
export const FIRST_LOAD_SKELETON_DELAY_MS = 300;

/** The dependency of every page load on the account it read for (the root layout invalidates it). */
export const ACCOUNT_DEPENDENCY = 'unos:account';

/** The dependency of a page's load, which refreshPage invalidates. */
export function pageDependency(key: string): `unos:${string}` {
	return `unos:page:${key}`;
}

/** A page's reads. */
export type PageReader<T> = {
	/** The page and what it reads: `storage`, or `files:<folder>` for a page that reads by its URL. */
	key: string;
	/**
	 * The areas whose changes change what it reads: the first part of each API path it reads
	 * (`storage` of /storage/pools), and of what those answers show (Shares lists the pools).
	 */
	areas: readonly string[];
	/** Only for administrators: for another account it reads nothing. */
	adminOnly: boolean;
	/** false when the snapshot holds a secret: it is never cached. */
	cache?: boolean;
	/** The reads, for the account. It does not throw: each read's failure is part of the snapshot. */
	read: (account: User) => Promise<T>;
};

/** A snapshot as read: the mark of its read (changeMark when it started), and when it ended. */
export type Snapshot<T> = { value: T; mark: number; readAt: number };

/** What a page's load gives the page (its `data`). */
export type PageRead<T> = {
	/** The session and the account it was read for: the root layout shows the page only for them. */
	readFor: string;
	key: string;
	areas: readonly string[];
	/** changeMark when the snapshot shown was read: a change of its areas since makes it old. */
	mark: number;
	/** When the snapshot shown was read: a preload's that waited longer than PRELOAD_MAX_AGE_MS is read again. */
	readAt: number;
	/**
	 * What the page shows at once: the fresh snapshot, or the cached one of the last visit; null
	 * while the tab's first load waits longer than a moment (the page shows its skeleton), and
	 * when nothing was read (no account, or not this reader's).
	 */
	shown: T | null;
	/**
	 * A fresh snapshot on its way when `shown` is cached or null: null when it failed or was read for
	 * a session or an account that is no longer this tab's.
	 */
	fresh: Promise<Snapshot<T> | null> | null;
};

/** How long a snapshot read without a fresh one (a preload that was not followed at once) is shown alone. */
export const PRELOAD_MAX_AGE_MS = 5000;

/** Who a load read for, as the root layout compares it: the session, the account, and its role. */
export function readForOf(session: number, account: User | null): string {
	return `${session}:${account?.id ?? ''}:${account?.is_admin === true}`;
}

// The tab's first page has been shown: from then on a load is a navigation's.
let started = false;

/** The root layout's afterNavigate: the first navigation, the tab's load, has ended. */
export function markStarted(): void {
	started = true;
}

/**
 * The signed-in account, as the root layout's ensureUser reads it (one request for every caller),
 * with the live connection, which the layout would start: on the tab's first page the load runs
 * before the layout is drawn. null without a session or when it cannot be read; the layout then
 * says why, or leads to the sign-in page (a tab that found no session key knows it has none).
 */
async function account(): Promise<User | null> {
	const known = get(user);
	if (known) return known;
	if (knownSignedOut()) return null;
	if (!(await obtainSessionKey())) {
		sessionStopped();
		return null;
	}
	try {
		const me = await loadCurrentUser();
		if (!isSetupOnly(me)) connectWebSocket();
		return me;
	} catch {
		return null;
	}
}

/**
 * One read for `me` in `session`, kept in the cache unless a change of its areas came during it.
 * null when the session or the account changed meanwhile: what it read is nobody's now.
 */
async function readOnce<T>(reader: PageReader<T>, me: User, session: number): Promise<Snapshot<T> | null> {
	const mark = changeMark();
	const value = await reader.read(me);
	const now = get(user);
	if (currentSession() !== session || now?.id !== me.id || now?.is_admin !== me.is_admin) return null;
	if (reader.cache !== false) remember(reader.key, reader.areas, mark, value, session);
	return { value, mark, readAt: Date.now() };
}

// A navigation's read that a change of its areas made old reads again, at most this many times in all.
const READS = 3;

/** A navigation's read: again while a change of its areas came during it, READS times at most. */
async function readCurrent<T>(reader: PageReader<T>, me: User, session: number): Promise<Snapshot<T> | null> {
	for (let attempt = 1; ; attempt++) {
		const read = await readOnce(reader, me, session);
		if (!read || upToDate(reader.areas, read.mark) || attempt >= READS) return read;
	}
}

function delay(ms: number): Promise<null> {
	return new Promise((resolve) => setTimeout(() => resolve(null), ms));
}

/**
 * The load of a page: `export const load: PageLoad = (event) => readPage(event, reader)`. A reader
 * that throws fails a navigation (SvelteKit's error page) rather than leaving a skeleton.
 */
export async function readPage<T>(
	event: { depends: (...deps: `${string}:${string}`[]) => void },
	reader: PageReader<T>
): Promise<PageRead<T>> {
	event.depends(ACCOUNT_DEPENDENCY, pageDependency(reader.key));
	const session = currentSession();
	const me = await account();
	const none: PageRead<T> = {
		readFor: readForOf(session, me),
		key: reader.key,
		areas: reader.areas,
		mark: changeMark(),
		readAt: Date.now(),
		shown: null,
		fresh: null
	};
	if (!me || isSetupOnly(me) || (reader.adminOnly && !me.is_admin)) return none;
	const kept = reader.cache === false ? undefined : recall<T>(reader.key);
	if (kept)
		return { ...none, mark: kept.mark, shown: kept.value, fresh: readOnce(reader, me, session).catch(() => null) };
	const reading = readCurrent(reader, me, session);
	if (started) {
		const read = await reading;
		return read ? { ...none, mark: read.mark, readAt: read.readAt, shown: read.value } : none;
	}
	const fresh = reading.catch(() => null);
	const first = await Promise.race([fresh, delay(FIRST_LOAD_SKELETON_DELAY_MS)]);
	if (first) return { ...none, mark: first.mark, readAt: first.readAt, shown: first.value };
	return { ...none, fresh };
}

/** Read the page `key` afresh (its Refresh): the cached snapshot goes, and its load runs again. */
export async function refreshPage(key: string): Promise<void> {
	forget(key);
	await invalidate(pageDependency(key));
}

/** Whether `read`'s snapshot is old: a change of its areas came after its read. */
export function isOld(read: PageRead<unknown>): boolean {
	return !upToDate(read.areas, read.mark);
}

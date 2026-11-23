const API_BASE = '/api/v1';

class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
		this.name = 'ApiError';
	}
}

// The session is in the HttpOnly session cookie (ADR-0397): no script of the page reads it, and the
// browser sends it with every request and WebSocket of this origin. The page holds the session key,
// which the sign-in answers and which goes with the cookie in every request: the key alone is no
// session, and the cookie, which the browser also sends to other ports of the NAS, is none without
// it. The key stays in this tab's sessionStorage, so that a reload keeps it; another tab asks for
// it over the channel below.
export const SESSION_KEY_HEADER = 'X-UNOS-Session-Key';
const SESSION_KEY_STORE = 'unos-session-key';

function storedKey(): string | null {
	try {
		return typeof window !== 'undefined' ? window.sessionStorage.getItem(SESSION_KEY_STORE) : null;
	} catch {
		return null;
	}
}

let sessionKey: string | null = storedKey();

function keepSessionKey(key: string | null): void {
	sessionKey = key;
	try {
		if (key) window.sessionStorage.setItem(SESSION_KEY_STORE, key);
		else window.sessionStorage.removeItem(SESSION_KEY_STORE);
	} catch {
		// No storage: the key lasts as long as the page.
	}
}

/** The session key of this tab, which a WebSocket's first message carries; null without a session. */
export function currentSessionKey(): string | null {
	return sessionKey;
}

// Earlier versions kept the session token itself in localStorage (ADR-0009): a browser that used
// them forgets it at its first visit.
if (typeof window !== 'undefined') {
	try {
		window.localStorage.removeItem('token');
	} catch {
		// No storage, nothing kept in it.
	}
}

// The page keeps a count of the sign-ins and sign-outs it knows of, here and in the other tabs,
// which share the cookie: an answer to a request sent before the last of them speaks of another
// session.
let sessionGeneration = 0;
// The session ended in this tab, or another tab signed out: until a sign-in, a page that needs a
// session goes to the sign-in page without asking the server.
let ended = false;

/** The count of sign-ins and sign-outs that this tab knows of; a request remembers it when sent. */
export function currentSession(): number {
	return sessionGeneration;
}

/** Whether this tab knows that it has no session: after a 401, a sign-out, here or in another tab. */
export function knownSignedOut(): boolean {
	return ended;
}

// The tabs of this origin tell each other of a sign-in, with its session key, and of a sign-out, and
// a new tab asks the others for the key: a cookie changes without an event. Same origin only.
const SESSION_CHANNEL = 'unos-session';
export type SessionChange = 'signed-in' | 'signed-out';
type SessionMessage =
	{ type: 'signed-in'; key: string } | { type: 'signed-out' } | { type: 'key?' } | { type: 'key'; key: string };
let channel: BroadcastChannel | null = null;
const sessionListeners = new Set<(change: SessionChange) => void>();
let keyAnswered: ((key: string) => void) | null = null;

function sessionChannel(): BroadcastChannel | null {
	if (channel === null && typeof BroadcastChannel !== 'undefined') {
		channel = new BroadcastChannel(SESSION_CHANNEL);
		channel.onmessage = (event: MessageEvent) => {
			const message = event.data as SessionMessage | null;
			if (!message || typeof message !== 'object') return;
			if (message.type === 'key?') {
				if (sessionKey && !ended) channel?.postMessage({ type: 'key', key: sessionKey });
			} else if (message.type === 'key' && typeof message.key === 'string') {
				keyAnswered?.(message.key);
			} else if (message.type === 'signed-in' && typeof message.key === 'string') {
				sessionGeneration += 1;
				ended = false;
				keepSessionKey(message.key);
				for (const listener of sessionListeners) listener('signed-in');
			} else if (message.type === 'signed-out') {
				sessionGeneration += 1;
				ended = true;
				keepSessionKey(null);
				for (const listener of sessionListeners) listener('signed-out');
			}
		};
	}
	return channel;
}

/** This tab signed in with the session whose key is `key`: the other tabs go on in it too. */
export function sessionStarted(key: string): void {
	sessionGeneration += 1;
	ended = false;
	keepSessionKey(key);
	sessionChannel()?.postMessage({ type: 'signed-in', key });
}

/**
 * This tab has no session from now on: requests sent before speak of another one. The other tabs
 * learn of it with announceSignedOut, once the server has ended the session.
 */
export function sessionStopped(): void {
	sessionGeneration += 1;
	ended = true;
	keepSessionKey(null);
}

/** Tell the other tabs that the session has ended. A channel does not deliver to its own sender. */
export function announceSignedOut(): void {
	sessionChannel()?.postMessage({ type: 'signed-out' });
}

let keyRequest: Promise<boolean> | null = null;

/**
 * Whether this tab has a session key, after asking the other tabs of the web UI for theirs when it
 * has none, as a new tab does. Without an answer within a moment there is none to go on with.
 */
export function obtainSessionKey(): Promise<boolean> {
	if (sessionKey) return Promise.resolve(true);
	const asking = sessionChannel();
	if (ended || asking === null) return Promise.resolve(false);
	keyRequest ??= new Promise<boolean>((resolve) => {
		const done = (key: string | null) => {
			clearTimeout(timer);
			keyAnswered = null;
			keyRequest = null;
			if (key && !sessionKey && !ended) keepSessionKey(key);
			resolve(sessionKey !== null);
		};
		const timer = setTimeout(() => done(null), 500);
		keyAnswered = (key) => done(key);
		asking.postMessage({ type: 'key?' });
	});
	return keyRequest;
}

/**
 * Call `listener` whenever another tab signs in (maybe as another account) or out. Returns a
 * function that stops listening.
 */
export function onSessionChangedElsewhere(listener: (change: SessionChange) => void): () => void {
	if (typeof window === 'undefined') return () => {};
	sessionChannel();
	sessionListeners.add(listener);
	return () => sessionListeners.delete(listener);
}

/** Pages that work without a session: sign-in and first-run setup. */
export function isPublicRoute(pathname: string): boolean {
	return pathname.startsWith('/login') || pathname.startsWith('/setup');
}

let unauthorizedHandler: (() => void) | null = null;

/**
 * Register what happens when the server rejects the session (HTTP 401, or WebSocket close code
 * 4401). The root layout uses it to reset the session state and navigate to /login.
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
	unauthorizedHandler = handler;
}

function onPublicRoute(): boolean {
	return typeof window !== 'undefined' && isPublicRoute(window.location.pathname);
}

/**
 * End the session because the server rejected a request sent during session `sentIn`
 * (currentSession() when it was sent).
 *
 * Responses arrive late: several parallel requests fail with the same ended session, and a request
 * sent before a sign-in, here or in another tab, can fail after it. Acting only while `sentIn` is
 * still the current session ends an ended session exactly once and never takes a newer session
 * down with it. The sign-in and first-run pages need no session: nothing happens there.
 */
export function handleUnauthorized(sentIn: number): void {
	if (sentIn !== sessionGeneration || typeof window === 'undefined' || onPublicRoute()) return;
	sessionStopped();
	if (unauthorizedHandler) {
		unauthorizedHandler();
	} else {
		window.location.assign('/login');
	}
}

// The server's answer (403) to every request of an account that can only set up two-factor
// authentication, once its grace period is over (ADR-0386; TWO_FACTOR_SETUP_REQUIRED of
// supervisor/src/auth/requirement.py, which supervisor/tests/test_two_factor_requirement.py
// compares with this text). Server messages stay English (ADR-0287).
export const TWO_FACTOR_SETUP_REQUIRED = 'Two-factor authentication is required: set it up to continue';

let setupRequiredHandler: (() => void) | null = null;

/**
 * Register what happens when the server answers that the account can only set up two-factor
 * authentication: the root layout reloads the account and leads to the setup page.
 */
export function setSetupRequiredHandler(handler: (() => void) | null): void {
	setupRequiredHandler = handler;
}

/**
 * The server refused the account because it can only set up two-factor authentication: a 403 with
 * TWO_FACTOR_SETUP_REQUIRED, or the events socket closed with 4403, which it sends for nothing else.
 */
export function handleSetupRequired(): void {
	setupRequiredHandler?.();
}

// The methods that change nothing on the server.
const READ_METHODS = ['GET', 'HEAD'];
const changeListeners = new Set<(path: string) => void>();

/**
 * Call `listener` with the path of every request of this tab that may have changed something on the
 * server (any method but GET and HEAD), once it has been answered or has failed: the pages' cache
 * forgets what the change made old ($lib/pages/cache). Returns a function that stops listening.
 */
export function onApiChange(listener: (path: string) => void): () => void {
	changeListeners.add(listener);
	return () => changeListeners.delete(listener);
}

// These endpoints answer 401 for wrong credentials, not for an expired session.
const CREDENTIAL_ENDPOINTS = ['/auth/login'];

// Shown for HTTP 429; every rate limit of the API is per minute.
const RATE_LIMITED_MESSAGE = 'Too many attempts. Wait a minute and try again.';

function errorDetail(status: number, payload: unknown): string {
	// slowapi answers 429 with {"error": "Rate limit exceeded: 5 per 1 minute"}.
	if (status === 429) return RATE_LIMITED_MESSAGE;
	const body = payload as { detail?: unknown; error?: unknown } | null;
	const detail = body?.detail;
	if (typeof detail === 'string' && detail) return detail;
	// FastAPI validation errors (422): [{"loc": [...], "msg": "...", ...}, ...]
	if (Array.isArray(detail)) {
		const messages = detail.map((item) => (item && typeof item.msg === 'string' ? item.msg : '')).filter(Boolean);
		if (messages.length > 0) return messages.join('; ');
	}
	if (typeof body?.error === 'string' && body.error) return body.error;
	return 'Request failed';
}

/**
 * Send a request to `${API_BASE}${path}` with the session cookie and return the
 * response. Use it directly only for what the JSON helpers in `api` cannot
 * send or read, such as multipart uploads and file downloads: it handles
 * errors the same way, so an expired session ends the same way everywhere.
 *
 * A 401 ends the session (see handleUnauthorized), except from endpoints
 * that check credentials. Every error status is thrown as an ApiError with
 * the server's message.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
	const sentIn = sessionGeneration;
	// Same origin: the browser sends the session cookie, and with every method but GET and HEAD the
	// Origin, which the server requires with the cookie; the page adds the session key (ADR-0397).
	const headers = new Headers(init.headers);
	if (sessionKey) headers.set(SESSION_KEY_HEADER, sessionKey);
	const reads = READ_METHODS.includes((init.method ?? 'GET').toUpperCase());
	let response: Response;
	try {
		response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: 'same-origin' });
	} finally {
		// Whatever the answer, even none: the server may have made the change.
		if (!reads) for (const listener of changeListeners) listener(path);
	}

	if (!response.ok) {
		const payload = await response.json().catch(() => null);
		if (response.status === 401 && !CREDENTIAL_ENDPOINTS.includes(path.split('?')[0])) {
			handleUnauthorized(sentIn);
		}
		const detail = errorDetail(response.status, payload);
		if (response.status === 403 && detail === TWO_FACTOR_SETUP_REQUIRED && sentIn === sessionGeneration) {
			handleSetupRequired();
		}
		throw new ApiError(response.status, detail);
	}

	return response;
}

// How long a downloaded file stays reachable through its blob: URL. The
// browser reads the URL when the download starts, after click() has returned.
const DOWNLOAD_URL_LIFETIME_MS = 60_000;

/**
 * Download `path` from the API with the session and let the browser save it
 * as `filename`. Through apiFetch, an error is an ApiError on the page and an
 * ended session ends as it does everywhere; a plain link or window.open()
 * would show the server's answer in place of the page.
 *
 * The response is read into a Blob first (browsers keep large blobs on disk),
 * so the browser shows the download only once the whole file has arrived.
 */
export async function downloadAs(path: string, filename: string): Promise<void> {
	const response = await apiFetch(path);
	const url = URL.createObjectURL(await response.blob());
	const link = document.createElement('a');
	link.href = url;
	link.download = filename;
	link.hidden = true;
	document.body.appendChild(link);
	try {
		link.click();
	} finally {
		link.remove();
		setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_LIFETIME_MS);
	}
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
	const response = await apiFetch(path, {
		method,
		headers: { 'Content-Type': 'application/json' },
		// undefined and null send no body; anything else is sent as JSON, also
		// arrays (the file manager endpoints take a JSON list of paths).
		body: body === undefined || body === null ? undefined : JSON.stringify(body)
	});

	if (response.status === 204) {
		return {} as T;
	}

	return response.json();
}

export const api = {
	get: <T>(path: string) => request<T>('GET', path),
	post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
	put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
	patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
	delete: <T>(path: string, body?: unknown) => request<T>('DELETE', path, body)
};

export { ApiError };

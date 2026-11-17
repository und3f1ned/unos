const API_BASE = '/api/v1';
const TOKEN_KEY = 'token';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export function getToken(): string | null {
  if (typeof localStorage !== 'undefined') {
    return localStorage.getItem(TOKEN_KEY);
  }
  return null;
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem(TOKEN_KEY);
  }
}

/**
 * Call `listener` with the stored token whenever another tab changes it
 * (login, logout, another account). The browser sends no such event to the
 * tab that made the change. Returns a function that stops listening.
 */
export function onTokenChangedElsewhere(listener: (token: string | null) => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.storageArea !== localStorage) return;
    // key is null when the other tab cleared the whole storage.
    if (event.key !== null && event.key !== TOKEN_KEY) return;
    listener(getToken());
  };
  window.addEventListener('storage', onStorage);
  return () => window.removeEventListener('storage', onStorage);
}

/** Pages that work without a session: sign-in and first-run setup. */
export function isPublicRoute(pathname: string): boolean {
  return pathname.startsWith('/login') || pathname.startsWith('/setup');
}

let unauthorizedHandler: (() => void) | null = null;

/**
 * Register what happens when the server rejects the stored credentials
 * (HTTP 401, or WebSocket close code 4401). The root layout uses it to reset
 * the session state and navigate to /login.
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function onPublicRoute(): boolean {
  return typeof window !== 'undefined' && isPublicRoute(window.location.pathname);
}

/**
 * End the session because the server rejected `rejectedToken`.
 *
 * Responses arrive late: several parallel requests fail with the same expired
 * token, and a request sent before a fresh login can fail after it. Acting only
 * while `rejectedToken` is still the stored token logs an expired session out
 * exactly once and never takes a newer session down with it.
 *
 * `rejectedToken` is null for a request sent without a token, e.g. after a
 * logout in another tab. That ends the session on pages that need one, and
 * does nothing on /login and /setup.
 */
export function handleUnauthorized(rejectedToken: string | null): void {
  if (getToken() !== rejectedToken) return;
  if (rejectedToken !== null) {
    clearToken();
  } else if (typeof window === 'undefined' || onPublicRoute()) {
    return;
  }
  if (unauthorizedHandler) {
    unauthorizedHandler();
  } else if (typeof window !== 'undefined' && !onPublicRoute()) {
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
    const messages = detail
      .map((item) => (item && typeof item.msg === 'string' ? item.msg : ''))
      .filter(Boolean);
    if (messages.length > 0) return messages.join('; ');
  }
  if (typeof body?.error === 'string' && body.error) return body.error;
  return 'Request failed';
}

/**
 * Send a request to `${API_BASE}${path}` with the stored token and return the
 * response. Use it directly only for what the JSON helpers in `api` cannot
 * send or read, such as multipart uploads and file downloads: it handles
 * errors the same way, so an expired session ends the same way everywhere.
 *
 * A 401 ends the session (see handleUnauthorized), except from endpoints
 * that check credentials. Every error status is thrown as an ApiError with
 * the server's message.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (response.status === 401 && !CREDENTIAL_ENDPOINTS.includes(path.split('?')[0])) {
      handleUnauthorized(token);
    }
    const detail = errorDetail(response.status, payload);
    if (response.status === 403 && detail === TWO_FACTOR_SETUP_REQUIRED && getToken() === token) {
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
 * Download `path` from the API with the stored token and let the browser
 * save it as `filename`. A plain link or window.open() cannot be used for
 * API files: they send no Authorization header, so the server rejects them.
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

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const response = await apiFetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    // undefined and null send no body; anything else is sent as JSON, also
    // arrays (the file manager endpoints take a JSON list of paths).
    body: body === undefined || body === null ? undefined : JSON.stringify(body),
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
  delete: <T>(path: string, body?: unknown) => request<T>('DELETE', path, body),
};

export { ApiError };

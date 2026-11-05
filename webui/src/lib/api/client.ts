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

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (response.status === 401 && !CREDENTIAL_ENDPOINTS.includes(path.split('?')[0])) {
      handleUnauthorized(token);
    }
    throw new ApiError(response.status, errorDetail(response.status, payload));
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
};

export { ApiError };

import { derived, writable } from 'svelte/store';
import { getMe, type User } from '$lib/api/auth';
import { getToken } from '$lib/api/client';

export const user = writable<User | null>(null);
export const isLoggedIn = writable(false);

/** True only once the signed-in user is known to be an administrator. */
export const isAdmin = derived(user, ($user) => $user?.is_admin === true);

/**
 * Pages whose API routers are administrator-only (they answer 403 to regular
 * accounts); see the admin-only routers in supervisor/src/main.py, and the
 * audit router, administrator-only as a whole in supervisor/src/audit/router.py.
 * Regular users get neither the navigation entry nor the page.
 */
const ADMIN_ONLY_ROUTES = [
  '/storage',
  '/snapshots',
  '/shares',
  '/files',
  '/containers',
  '/apps',
  '/users',
  '/audit'
];

export function isAdminOnlyRoute(pathname: string): boolean {
  return ADMIN_ONLY_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(route + '/')
  );
}

/**
 * The page of an account that every account's requirement of two-factor authentication applies to:
 * it asks during the grace period, and after it is the one page of the account.
 */
export const TWO_FACTOR_SETUP_PATH = '/two-factor-setup';

/** The account can only set up two-factor authentication: the server refuses it everything else. */
export function isSetupOnly(currentUser: User | null): boolean {
  return currentUser?.two_factor_required?.setup_only === true;
}

let pending: { token: string | null; request: Promise<User> } | null = null;

/**
 * Load the signed-in user from /auth/me and publish it in the stores.
 * Concurrent callers share one request. Rejects with the ApiError on failure.
 */
export function loadCurrentUser(): Promise<User> {
  const token = getToken();
  if (pending && pending.token === token) return pending.request;

  const request = getMe()
    .then((me) => {
      // A logout or another login may have happened while this was in flight.
      if (getToken() === token) {
        user.set(me);
        isLoggedIn.set(true);
      }
      return me;
    })
    .finally(() => {
      if (pending?.request === request) pending = null;
    });
  pending = { token, request };
  return request;
}

export function clearCurrentUser(): void {
  pending = null;
  user.set(null);
  isLoggedIn.set(false);
}

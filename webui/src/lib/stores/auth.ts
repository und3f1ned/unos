import { derived, writable } from 'svelte/store';
import { getMe, logout, type User } from '$lib/api/auth';
import {
  announceSignedOut,
  currentSession,
  currentSessionKey,
  onSessionChangedElsewhere,
  sessionStopped
} from '$lib/api/client';

export const user = writable<User | null>(null);
export const isLoggedIn = writable(false);

/** True only once the signed-in user is known to be an administrator. */
export const isAdmin = derived(user, ($user) => $user?.is_admin === true);

/**
 * Pages whose API routers are administrator-only (they answer 403 to regular
 * accounts); see the admin-only routers in supervisor/src/main.py, and the
 * audit router, administrator-only as a whole in supervisor/src/audit/router.py.
 * Regular users get neither the navigation entry nor the page. System is the NAS's, its
 * tabs' APIs administrator-only: a regular account's own settings are on /account (ADR-0430).
 */
const ADMIN_ONLY_ROUTES = [
  '/storage',
  '/snapshots',
  '/shares',
  '/files',
  '/containers',
  '/apps',
  '/users',
  '/audit',
  '/system'
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

let pending: { session: number; request: Promise<User> } | null = null;

/**
 * Load the signed-in user from /auth/me and publish it in the stores.
 * Concurrent callers share one request. Rejects with the ApiError on failure.
 */
export function loadCurrentUser(): Promise<User> {
  const session = currentSession();
  if (pending && pending.session === session) return pending.request;

  const request = getMe()
    .then((me) => {
      // A sign-out or another sign-in, here or in another tab, may have happened while this was in
      // flight.
      if (currentSession() === session) {
        user.set(me);
        isLoggedIn.set(true);
      }
      return me;
    })
    .finally(() => {
      if (pending?.request === request) pending = null;
    });
  pending = { session, request };
  return request;
}

export function clearCurrentUser(): void {
  pending = null;
  user.set(null);
  isLoggedIn.set(false);
}

/**
 * The last sign-out of this tab did not reach the server, or the server failed it: the session and
 * its cookie may still be a session of this browser. The sign-in page says so and offers to try
 * again, until a sign-in here or in another tab.
 */
export const signOutFailed = writable(false);

// The session key of a sign-out that the server did not confirm, for Sign Out Again (in this page
// only: the tab no longer counts it as its own).
let unconfirmedKey: string | null = null;

/** A sign-in, here or in another tab: an earlier sign-out that failed is no longer this tab's to retry. */
export function forgetFailedSignOut(): void {
  unconfirmedKey = null;
  signOutFailed.set(false);
}

if (typeof window !== 'undefined') onSessionChangedElsewhere(forgetFailedSignOut);

async function endOnServer(key: string | null): Promise<void> {
  const session = currentSession();
  const ended = await logout(key);
  // A sign-in here or in another tab since then is a session of its own.
  if (currentSession() !== session) return;
  unconfirmedKey = ended ? null : key;
  if (ended) announceSignedOut();
  signOutFailed.set(!ended);
}

/**
 * Sign out: the page forgets the account and the session key at once; the session ends on the
 * server, which deletes the session cookie, and then the other tabs sign out too (ADR-0397). The
 * cookie cannot be deleted by the page, so a sign-out that fails leaves the session as it was:
 * signOutFailed.
 */
export async function signOut(): Promise<void> {
  const key = currentSessionKey();
  // This tab has no session from now on; the other tabs learn of it once the server ended it.
  sessionStopped();
  clearCurrentUser();
  await endOnServer(key);
}

/** Sign Out Again: end the session of the sign-out that the server did not confirm. */
export async function signOutAgain(): Promise<void> {
  if (unconfirmedKey === null) {
    signOutFailed.set(false);
    return;
  }
  await endOnServer(unconfirmedKey);
}

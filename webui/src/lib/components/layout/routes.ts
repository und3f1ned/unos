// Which pages the signed-in account gets, for the layout of every page (+layout.svelte).
import type { User } from '$lib/api/auth';
import { isPublicRoute } from '$lib/api/client';
import { isAdminOnlyRoute, isSetupOnly, TWO_FACTOR_SETUP_PATH } from '$lib/stores/auth';

/** A page's gate: public (sign-in, first run), allowed to the account, or not (yet). */
export type Gate = 'public' | 'allowed' | 'blocked';

export function gateFor(path: string, currentUser: User | null): Gate {
	if (isPublicRoute(path)) return 'public';
	if (isSetupOnly(currentUser)) return path === TWO_FACTOR_SETUP_PATH ? 'allowed' : 'blocked';
	if (currentUser && (currentUser.is_admin || !isAdminOnlyRoute(path))) return 'allowed';
	return 'blocked';
}

/** Whether the link `href` of the navigation is the page at `currentPath`, or one under it. */
export function isActive(href: string, currentPath: string): boolean {
	const target = href.split('#')[0];
	if (target === '/') return currentPath === '/';
	return currentPath === target || currentPath.startsWith(target + '/');
}

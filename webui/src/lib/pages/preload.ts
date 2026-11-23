// When a link starts its page's load before the click (SvelteKit's data-sveltekit-preload-data):
// on hover only for a page whose load is cheap and reads no data disk, so that a pointer passing
// over a link never wakes a sleeping disk (ADR-0378); every other page on tap (the press of the
// pointer or the touch), which app.html's body sets for every link. A page joins HOVER_PAGES only
// with supervisor/tests/test_webui_page_loads.py's check of what its reader asks.

/**
 * The pages whose loads may run when the pointer rests on a link to them: those that read only
 * UNOS's own records on the system disk (the accounts, the groups, the audit log, and System >
 * Notifications, which reads the database alone: ADR-0474), never a data disk.
 */
export const HOVER_PAGES: readonly string[] = ['/users', '/audit', '/account', '/system/notifications'];

/** The preload of a link to `href`: hover for the pages of HOVER_PAGES, tap for the others. */
export function preloadOf(href: string): 'hover' | 'tap' {
	return HOVER_PAGES.includes(href.split('#')[0]) ? 'hover' : 'tap';
}

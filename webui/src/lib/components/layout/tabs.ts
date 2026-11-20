// The tab of a page with tabs (System, Account) that the URL's hash names (ADR-0430).

/**
 * The tab to show for the URL's hash `rawHash` (with its `#`): the one it names among `available`;
 * without a hash the first, the page's own (the command palette's System and Account open it);
 * for a hash of no available tab `current` while it is available, else the first. A tab the account
 * may not open is never shown (`/account#sharing` opened by an administrator).
 */
export function tabFromHash(rawHash: string, available: readonly { id: string }[], current: string): string {
	const hash = rawHash.slice(1);
	if (hash && available.some((tab) => tab.id === hash)) return hash;
	if (hash && available.some((tab) => tab.id === current)) return current;
	return available[0]?.id ?? current;
}

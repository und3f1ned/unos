// The badges of an app of the App Store, on its card and in its details.

/**
 * The badge of the source of an app's image (AppSource), with the key of its label: linuxserver.io
 * is LinuxServer on a card and LinuxServer.io in the details (`details`).
 */
export function sourceBadge(
	source: string | undefined,
	details: boolean
): { variant: 'success' | 'info' | 'purple' | 'warning'; key: string } {
	switch (source?.toUpperCase()) {
		case 'OFFICIAL':
			return { variant: 'success', key: 'apps.source.official' };
		case 'LINUXSERVER':
		case 'LINUXSERVER.IO':
			return { variant: 'purple', key: details ? 'apps.source.linuxserverIo' : 'apps.source.linuxserver' };
		case 'VERIFIED':
			return { variant: 'info', key: 'apps.source.verified' };
		default:
			return { variant: 'warning', key: 'apps.source.community' };
	}
}

/** The emoji before an app's category in its details. */
export function categoryIcon(category: string): string {
	const icons: Record<string, string> = {
		media: '🎬',
		productivity: '📝',
		download: '📥',
		backup: '💾',
		monitoring: '📊',
		network: '🌐',
		development: '💻',
		security: '🔒'
	};
	return icons[category] || '📦';
}

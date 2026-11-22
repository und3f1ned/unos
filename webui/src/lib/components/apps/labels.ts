// The badges of an app of the App Store, on its card and in its details, and the names of the
// categories.
import type { Translate } from '$lib/i18n';

// One label per category (AppCategory of supervisor/src/apps/schemas.py), the category list's,
// also on the cards and in the details (ADR-0291); '' is every app.
const CATEGORY_LABELS: Record<string, string> = {
	'': 'apps.category.all',
	home_automation: 'apps.category.homeAutomation',
	media: 'apps.category.media',
	ai: 'apps.category.ai',
	productivity: 'apps.category.productivity',
	download: 'apps.category.download',
	backup: 'apps.category.backup',
	monitoring: 'apps.category.monitoring',
	network: 'apps.category.network',
	documents: 'apps.category.documents',
	development: 'apps.category.development',
	security: 'apps.category.security',
	communication: 'apps.category.communication',
	privacy: 'apps.category.privacy',
	gaming: 'apps.category.gaming',
	other: 'apps.category.other'
};

/** The name of a category in the language of the page; one without a label as the server answers it. */
export function categoryLabel(category: string, t: Translate): string {
	return Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, category) ? t(CATEGORY_LABELS[category]) : category;
}

/**
 * The badge of the source of an app's image (AppSource), with the key of its label: linuxserver.io
 * is LinuxServer on a card and LinuxServer.io in the details (`details`). A source is a category,
 * not a state: every badge is neutral, and an official or verified image carries a check
 * (`verified`) instead of a colour (webui.ux-apps).
 */
export function sourceBadge(source: string | undefined, details: boolean): { verified: boolean; key: string } {
	switch (source?.toUpperCase()) {
		case 'OFFICIAL':
			return { verified: true, key: 'apps.source.official' };
		case 'LINUXSERVER':
		case 'LINUXSERVER.IO':
			return { verified: false, key: details ? 'apps.source.linuxserverIo' : 'apps.source.linuxserver' };
		case 'VERIFIED':
			return { verified: true, key: 'apps.source.verified' };
		default:
			return { verified: false, key: 'apps.source.community' };
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

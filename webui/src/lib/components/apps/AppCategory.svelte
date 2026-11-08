<script lang="ts">
	import { t } from '$lib/i18n';

	/** A category as the server answers it (AppCategory of supervisor/src/apps/schemas.py); '' is every app. */
	export let category: string;

	// One label per category, the category list's, also on the cards and in the details (ADR-0291).
	const LABELS: Record<string, string> = {
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
</script>

<!-- A category without a label is shown as the server answers it. -->
{Object.prototype.hasOwnProperty.call(LABELS, category) ? $t(LABELS[category]) : category}

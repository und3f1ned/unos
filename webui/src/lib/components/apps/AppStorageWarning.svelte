<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { InstalledApp } from '$lib/api/apps';

	type Props = {
		/** Installed app whose data folders are checked; nothing is shown when all are on mounted pools. */
		app: InstalledApp;
		class?: string;
	};

	let { app, class: className = '' }: Props = $props();
</script>

{#if app.data_outside_pools.length > 0}
	<Alert variant="warning" class={className || ''} title={$t('apps.storage.unavailableTitle')}>
		<p>{$t('apps.storage.unavailable', { name: app.name })}</p>
		<ul class="mt-2 space-y-1">
			{#each app.data_outside_pools as path}
				<li class="mono text-xs break-all">{path}</li>
			{/each}
		</ul>
	</Alert>
{/if}

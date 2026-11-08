<script lang="ts">
	import { FolderOpen, Share2, Download, Settings } from 'lucide-svelte';
	import { goto } from '$app/navigation';
	import { Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	export let onAction: ((action: string) => void) | undefined = undefined;

	// Each action's label is a key of the catalog.
	const actions = [
		{
			id: 'files',
			icon: FolderOpen,
			key: 'dashboard.actions.files',
			href: '/files',
			color: 'icon-storage'
		},
		{
			id: 'shares',
			icon: Share2,
			key: 'dashboard.actions.shares',
			href: '/shares',
			color: 'icon-network'
		},
		{
			id: 'apps',
			icon: Download,
			key: 'dashboard.actions.apps',
			href: '/apps',
			color: 'icon-memory'
		},
		{
			id: 'settings',
			icon: Settings,
			key: 'dashboard.actions.settings',
			href: '/settings',
			color: 'text-secondary'
		}
	];

	function handleClick(action: (typeof actions)[0]) {
		if (onAction) {
			onAction(action.id);
		} else if (action.href) {
			goto(action.href);
		}
	}
</script>

<Card>
	<h3 class="card-title mb-4">{$t('dashboard.actions.title')}</h3>
	<div class="grid grid-cols-2 md:grid-cols-4 gap-2">
		{#each actions as action}
			<button
				on:click={() => handleClick(action)}
				class="flex flex-col items-center gap-2 p-3 bg-surface-overlay hover:bg-slate-600 rounded-lg transition-colors"
			>
				<svelte:component this={action.icon} class="w-6 h-6 {action.color}" />
				<span class="text-xs text-center">{$t(action.key)}</span>
			</button>
		{/each}
	</div>
</Card>

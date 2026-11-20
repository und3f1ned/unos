<script lang="ts">
	import { FolderOpen, Share2, Download, Settings } from 'lucide-svelte';
	import { goto } from '$app/navigation';
	import { Card, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		// An action was chosen, by its id; without it, the action's page opens.
		onAction?: (action: string) => void;
	};

	let { onAction }: Props = $props();

	// Each action's label is a key of the catalog.
	const actions = [
		{
			id: 'files',
			icon: FolderOpen,
			key: 'dashboard.actions.files',
			href: '/files'
		},
		{
			id: 'shares',
			icon: Share2,
			key: 'dashboard.actions.shares',
			href: '/shares'
		},
		{
			id: 'apps',
			icon: Download,
			key: 'dashboard.actions.apps',
			href: '/apps'
		},
		{
			id: 'settings',
			icon: Settings,
			key: 'dashboard.actions.settings',
			href: '/settings'
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

<!-- Four tiles in a 2 x 2 grid, so that the card is about as tall as its neighbour (ADR-0428). -->
<Card class="h-full flex flex-col">
	<h3 class="card-title mb-4">{$t('dashboard.actions.title')}</h3>
	<div class="grid grid-cols-2 gap-2 flex-1">
		{#each actions as action}
			<Button variant="plain"
				onclick={() => handleClick(action)}
				class="min-h-24 flex flex-col items-start justify-between gap-3 p-3.5 rounded-lg border border-border bg-surface-muted hover:bg-surface-overlay hover:border-border-strong transition-colors text-left"
			>
				<action.icon class="w-5 h-5 text-content-secondary" aria-hidden="true" />
				<span class="text-sm font-medium">{$t(action.key)}</span>
			</Button>
		{/each}
	</div>
</Card>

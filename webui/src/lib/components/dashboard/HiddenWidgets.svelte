<script lang="ts">
	import { Eye, RotateCcw } from 'lucide-svelte';
	import { Button, Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// While the widgets are arranged (ADR-0395): the hint, the hidden widgets with a button to show
	// each again, and Reset.

	type Props = {
		/** The hidden widgets, in the layout's order, each by its id and its name. */
		widgets: { id: string; name: string }[];
		saving: boolean;
		/** The layout is the default one already. */
		resetDisabled: boolean;
		onshow: (id: string) => void;
		onreset: () => void;
	};

	let { widgets, saving, resetDisabled, onshow, onreset }: Props = $props();
</script>

<Card>
	<p class="text-sm text-secondary mb-3">{$t('dashboard.layout.hint')}</p>
	<section aria-label={$t('dashboard.layout.hiddenTitle')} class="mb-3" data-hidden-list>
		<h2 class="section-title mb-2">{$t('dashboard.layout.hiddenTitle')}</h2>
		{#if widgets.length === 0}
			<p class="text-sm text-muted">{$t('dashboard.layout.noneHidden')}</p>
		{:else}
			<div class="flex flex-wrap gap-2">
				{#each widgets as widget (widget.id)}
					<Button variant="secondary" size="sm" icon={Eye} disabled={saving} onclick={() => onshow(widget.id)}>
						{$t('dashboard.layout.show', { name: widget.name })}
					</Button>
				{/each}
			</div>
		{/if}
	</section>
	<Button variant="ghost" size="sm" icon={RotateCcw} disabled={saving || resetDisabled} onclick={onreset}>
		{$t('dashboard.layout.reset')}
	</Button>
</Card>

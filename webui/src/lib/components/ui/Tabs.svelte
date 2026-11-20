<script lang="ts" generics="T extends string">
	import type { IconComponent } from './types';
	import Button from './Button.svelte';

	// Page tabs (ADR-0432, WAI-ARIA's tabs): a line under the row, the chosen tab's bar in
	// brand-ring (ADR-0428). The arrow keys move to the previous or next tab (wrapping around), Home
	// and End to the first or last, and choose it; only the chosen tab is in the Tab order. The page
	// draws the panel: an element with the id `panelId(id)`, role tabpanel, labelled by the tab
	// (`tabId(id)`). Filters and views are a Segmented.
	type Tab = { id: T; label: string; icon?: IconComponent | null };
	type Props = {
		tabs: readonly Tab[];
		active: T;
		/** The tab list's name for screen readers. */
		label: string;
		tabId: (id: T) => string;
		panelId: (id: T) => string;
		onselect: (id: T) => void;
		class?: string;
	};

	let { tabs, active, label, tabId, panelId, onselect, class: className = '' }: Props = $props();

	function keydown(event: KeyboardEvent, from: T) {
		const index = tabs.findIndex((tab) => tab.id === from);
		let next: number;
		if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
		else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = tabs.length - 1;
		else return;
		event.preventDefault();
		const id = tabs[next].id;
		onselect(id);
		document.getElementById(tabId(id))?.focus();
	}
</script>

<div class="tab-bar {className}" role="tablist" aria-label={label}>
	{#each tabs as tab (tab.id)}
		<Button
			variant="plain"
			role="tab"
			id={tabId(tab.id)}
			aria-selected={active === tab.id}
			aria-controls={panelId(tab.id)}
			tabindex={active === tab.id ? 0 : -1}
			class="tab-underline {active === tab.id ? 'tab-underline-active' : ''}"
			onclick={() => onselect(tab.id)}
			onkeydown={(event) => keydown(event, tab.id)}
		>
			{#if tab.icon}
				{@const Icon = tab.icon}
				<Icon class="w-4 h-4" aria-hidden="true" />
			{/if}
			{tab.label}
		</Button>
	{/each}
</div>

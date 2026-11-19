<script lang="ts" generics="T extends string">
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The tabs of the shares page. They control the panel `shares-panel`, which the page labels
	// with the selected tab (`shares-tab-<id>`).
	type Props = {
		tabs: readonly { id: T; label: string }[];
		active: T;
		/** A tab was chosen, with a click or the keyboard. */
		onselect: (id: T) => void;
	};

	let { tabs, active, onselect }: Props = $props();

	/**
	 * Keyboard of the SMB / NFS tabs: the arrow keys move to the previous or
	 * next tab (wrapping around), Home and End to the first or last; the tab
	 * reached is selected and focused. Only the selected tab is in the Tab order.
	 */
	function tabKeydown(event: KeyboardEvent, from: T) {
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
		document.getElementById(`shares-tab-${id}`)?.focus();
	}
</script>

<div class="flex gap-1 p-1 bg-surface-elevated rounded-lg w-fit" role="tablist" aria-label={$t('shares.tabs.label')}>
	{#each tabs as tab (tab.id)}
		<Button
			variant="plain"
			type="button"
			role="tab"
			id="shares-tab-{tab.id}"
			aria-selected={active === tab.id}
			aria-controls="shares-panel"
			tabindex={active === tab.id ? 0 : -1}
			class="tab-pill {active === tab.id ? 'tab-pill-active' : ''}"
			onclick={() => onselect(tab.id)}
			onkeydown={(event) => tabKeydown(event, tab.id)}
		>
			{tab.label}
		</Button>
	{/each}
</div>

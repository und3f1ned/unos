<script module lang="ts">
	export type TabId = 'containers' | 'images';
</script>

<script lang="ts">
	// The Containers / Images tabs of the Containers page. The keyboard: the arrow keys move to the
	// previous or next tab (wrapping around), Home and End to the first or last; the tab reached is
	// selected and focused.
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		active: TabId;
		onselect: (tab: TabId) => void;
	};

	let { active, onselect }: Props = $props();

	const tabs: { id: TabId; key: string }[] = [
		{ id: 'containers', key: 'containers.tabs.containers' },
		{ id: 'images', key: 'containers.tabs.images' }
	];

	function tabKeydown(event: KeyboardEvent, from: TabId) {
		const index = tabs.findIndex((tab) => tab.id === from);
		let next: number;
		if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
		else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = tabs.length - 1;
		else return;
		event.preventDefault();
		onselect(tabs[next].id);
		document.getElementById(`docker-tab-${tabs[next].id}`)?.focus();
	}
</script>

<!-- Page tabs: a line under the row (ADR-0428). -->
<div
	class="tab-bar"
	role="tablist"
	aria-label={$t('containers.tabs.label')}
>
	{#each tabs as tab (tab.id)}
		<Button variant="plain"
			type="button"
			role="tab"
			id="docker-tab-{tab.id}"
			aria-selected={active === tab.id}
			aria-controls="docker-panel-{tab.id}"
			tabindex={active === tab.id ? 0 : -1}
		 class="tab-underline {active === tab.id ? 'tab-underline-active' : ''}"
			onclick={() => onselect(tab.id)}
			onkeydown={(event) => tabKeydown(event, tab.id)}
		>
			{$t(tab.key)}
		</Button>
	{/each}
</div>

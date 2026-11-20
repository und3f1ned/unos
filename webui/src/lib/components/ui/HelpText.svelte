<script lang="ts">
	import type { Snippet } from 'svelte';
	import { t } from '$lib/i18n';

	// Help in one line (ADR-0432): the sentence a user needs, and what it leaves out behind
	// "Details", which opens below it. The details are no second copy of a rule that a field states.
	type Props = {
		/** The one line. */
		text: string;
		/** The rest, shown on demand. */
		more?: Snippet;
		class?: string;
	};

	let { text, more, class: className = '' }: Props = $props();
	let open = $state(false);
	const moreId = `help-${Math.random().toString(36).slice(2, 9)}`;
</script>

<div class="help-text {className}">
	<p>
		{text}
		{#if more}
			<button
				type="button"
				class="help-text-toggle"
				aria-expanded={open}
				aria-controls={moreId}
				onclick={() => (open = !open)}>{open ? $t('ui.help.hide') : $t('ui.help.details')}</button
			>
		{/if}
	</p>
	{#if more}
		<div id={moreId} class="help-text-more" hidden={!open}>{@render more()}</div>
	{/if}
</div>

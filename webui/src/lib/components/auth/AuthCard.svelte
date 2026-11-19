<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Server } from 'lucide-svelte';
	import { t } from '$lib/i18n';

	// The frame of the pages before a session, sign-in and first-run setup: a card in the middle
	// of the screen, the product's name at its top, and an introduction under it.

	type Props = {
		/** The text under the product's name; none on the sign-in page. */
		intro?: string;
		children: Snippet;
	};

	let { intro = '', children }: Props = $props();
</script>

<div class="min-h-screen flex items-center justify-center bg-surface">
	<div class="bg-surface-elevated p-8 rounded-lg shadow-xl w-full max-w-md">
		<div class="flex items-center justify-center gap-2 {intro ? 'mb-4' : 'mb-8'}">
			<Server class="w-10 h-10 text-brand-ring" />
			<span class="text-2xl font-bold">{$t('common.product')}</span>
		</div>

		<!-- The intro and the page's own content in one branch: between them, and after the product's
		     name, the same single spaces as the pages had. -->
		{#if intro}
			<p class="text-center text-content-secondary mb-8">
				{intro}
			</p>
			{@render children()}
		{:else}
			{@render children()}
		{/if}
	</div>
</div>

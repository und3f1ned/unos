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

<!-- A 400 px card in the middle of the page, the mark and the product's name above it (ADR-0428). -->
<div class="min-h-screen flex flex-col items-center justify-center gap-6 bg-surface px-4 py-8">
	<div class="flex items-center justify-center gap-2.5">
		<span
			class="grid place-items-center w-9 h-9 rounded-[9px] bg-brand text-content-on-brand"
			aria-hidden="true"
		>
			<Server class="w-5 h-5" />
		</span>
		<span class="text-2xl font-bold tracking-[0.04em]">{$t('common.product')}</span>
	</div>

	<div class="bg-surface-elevated border border-border shadow-raised p-6 sm:p-8 rounded-card w-full max-w-[400px]">
		<!-- The intro and the page's own content in one branch: between them, and after the product's
		     name, the same single spaces as the pages had. -->
		{#if intro}
			<p class="text-center text-content-secondary mb-6">
				{intro}
			</p>
			{@render children()}
		{:else}
			{@render children()}
		{/if}
	</div>
</div>

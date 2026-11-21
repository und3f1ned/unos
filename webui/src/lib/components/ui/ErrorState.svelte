<script lang="ts">
	import { AlertCircle, RefreshCw } from 'lucide-svelte';
	import { isTechnical } from '$lib/errors';
	import { t } from '$lib/i18n';
	import Button from './Button.svelte';
	import TechDetails from './TechDetails.svelte';

	// A load that failed (ADR-0432), in place of what it would have shown, never an empty list: a
	// title of the catalog ("Could not load the tasks"), the server's message (a technical one folded
	// under its details, behind a plain sentence) and Retry.
	type Props = {
		title?: string;
		/** What the failure means for the user, in a plain sentence before the server's message. */
		description?: string;
		/** The server's message (messageOf). */
		message?: string;
		onretry?: (() => void) | null;
		retrying?: boolean;
		/** Inside a card: less room, a title that is no heading. */
		compact?: boolean;
		class?: string;
	};

	let {
		title = '',
		description = '',
		message = '',
		onretry = null,
		retrying = false,
		compact = true,
		class: className = ''
	}: Props = $props();

	const technical = $derived(message !== '' && isTechnical(message));
</script>

<div class="empty-state {compact ? 'empty-state-compact' : ''} {className}" data-error-state>
	<span class="empty-state-icon-wrap error-state-icon-wrap" aria-hidden="true">
		<AlertCircle class="empty-state-icon" />
	</span>
	{#if compact}
		<p class="empty-state-title">{title || $t('ui.error.title')}</p>
	{:else}
		<h3 class="empty-state-title">{title || $t('ui.error.title')}</h3>
	{/if}
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if message}
		{#if technical}
			{#if !description}<p class="empty-state-description">{$t('ui.error.technical')}</p>{/if}
			<TechDetails text={message} class="w-full max-w-[60ch]" />
		{:else}
			<p class="empty-state-description break-words">{message}</p>
		{/if}
	{/if}
	{#if onretry}
		<div class="mt-4">
			<Button variant="secondary" size="sm" icon={RefreshCw} loading={retrying} onclick={onretry}>
				{$t('common.retry')}
			</Button>
		</div>
	{/if}
</div>

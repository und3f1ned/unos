<script lang="ts">
	import type { Snippet } from 'svelte';
	import Alert from './Alert.svelte';
	import Button from './Button.svelte';
	import ErrorState from './ErrorState.svelte';
	import Skeleton from './Skeleton.svelte';
	import { t } from '$lib/i18n';

	// What a region that loads shows (ADR-0432, on the skeletons of ADR-0430): its skeleton until the
	// first answer, under aria-busy; the ErrorState when the load failed, never an empty state then;
	// its empty state when the answer has nothing; else its content. A load that failed after an
	// earlier one succeeded keeps the content (`keepOnError`) with the error above it, as a refresh
	// keeps what is shown (ADR-0430).
	type Props = {
		/** No answer yet: the first load runs. A refresh keeps it false. */
		loading: boolean;
		/** Why the last load failed (messageOf), or empty. */
		error?: string | null;
		errorTitle?: string;
		/** The answer has nothing to show. */
		empty?: boolean;
		/** The content of an earlier load stays under the error. */
		keepOnError?: boolean;
		onretry?: (() => void) | null;
		retrying?: boolean;
		/** The skeleton of the final layout; one card of three lines by default. */
		skeleton?: Snippet;
		/** The empty state (an EmptyState). */
		emptyState?: Snippet;
		children: Snippet;
		class?: string;
	};

	let {
		loading,
		error = '',
		errorTitle = '',
		empty = false,
		keepOnError = false,
		onretry = null,
		retrying = false,
		skeleton,
		emptyState,
		children,
		class: className = ''
	}: Props = $props();

	const phase = $derived(loading ? 'loading' : error && !keepOnError ? 'error' : empty ? 'empty' : 'ready');
</script>

<div class={className} aria-busy={phase === 'loading' ? 'true' : undefined} data-load-state={phase}>
	{#if phase === 'loading'}
		{#if skeleton}{@render skeleton()}{:else}<Skeleton count={3} />{/if}
	{:else if phase === 'error'}
		<ErrorState title={errorTitle} message={error ?? ''} {onretry} {retrying} />
	{:else}
		{#if error}
			<Alert variant="error" size="sm" title={errorTitle || $t('ui.error.title')} class="mb-3">
				<p class="break-words">{error}</p>
				{#if onretry}
					<Button variant="secondary" size="sm" class="mt-2" loading={retrying} onclick={onretry}>{$t('common.retry')}</Button>
				{/if}
			</Alert>
		{/if}
		{#if phase === 'empty'}
			{@render emptyState?.()}
		{:else}
			{@render children()}
		{/if}
	{/if}
</div>

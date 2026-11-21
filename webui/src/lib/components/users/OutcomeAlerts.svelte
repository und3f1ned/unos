<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import type { Outcome } from './outcome.svelte';

	type Props = {
		outcome: Outcome;
		// The title of a refusal that names none of its own.
		failureTitle?: string;
	};

	let { outcome, failureTitle = '' }: Props = $props();
</script>

{#if outcome.warnings.length > 0}
	<Alert variant="warning" dismissible ondismiss={() => (outcome.warnings = [])}>
		{#each outcome.warnings as warning}
			<p>{warning}</p>
		{/each}
	</Alert>
{/if}
{#if outcome.failure}
	<Alert
		variant="error"
		title={outcome.failure.title ?? failureTitle}
		dismissible
		ondismiss={() => (outcome.failure = null)}>{outcome.failure.message}</Alert
	>
{/if}

<script lang="ts">
	// The disables that come back on by themselves unless they are kept (ADR-0244, ADR-0246): the
	// newest first, each with its countdown and Keep. After a Disable the page moves the focus here,
	// one Tab away from the Keep of the interface just disabled.
	import { Alert, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		// Interface -> when it comes back on by itself (Date.now()), the newest first.
		countdowns: [string, number][];
		// The page's clock, which a countdown counts against.
		now: number;
		// The interface being changed.
		busy: string | null;
		onkeep: (name: string) => void;
	};

	let { countdowns, now, busy, onkeep }: Props = $props();

	let region = $state<HTMLElement | null>(null);

	function secondsLeft(deadline: number, at: number): number {
		return Math.max(0, Math.ceil((deadline - at) / 1000));
	}

	/** The focus to the list, one Tab away from its first Keep. */
	export function focus() {
		region?.focus();
	}
</script>

<section class="space-y-3" aria-label={$t('network.pending.list')} tabindex="-1" bind:this={region}>
	{#each countdowns as [name, deadline] (name)}
		{@const left = secondsLeft(deadline, now)}
		<Alert variant="warning" title={$t('network.pending.title', { name })}>
			{#if left > 0}
				<p>{$t('network.pending.countdown', { name, seconds: left })}</p>
				<Button
					variant="secondary"
					size="sm"
					class="mt-3"
					loading={busy === name}
					disabled={busy !== null && busy !== name}
					onclick={() => onkeep(name)}
				>
					{$t('network.pending.keep', { name })}
				</Button>
			{:else}
				<p>{$t('network.pending.expired', { name })}</p>
			{/if}
		</Alert>
	{/each}
</section>

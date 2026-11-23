<script lang="ts">
	import type { DrillKind, StandbyStatus } from '$lib/api/standby';
	import { Button, Card, HelpText } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The drills of the SD card standby and the ways back (ADR-0471): on the eMMC's system the three
	// drills, or after the standby ran Return through the standby and Discard its changes; on the
	// card Return to the eMMC. Each asks first (the page's confirm) and then reboots the NAS.
	type Props = {
		status: StandbyStatus;
		busy: boolean;
		ondrill: (kind: DrillKind) => void;
		onreturn: () => void;
		ondiscard: () => void;
	};

	let { status, busy, ondrill, onreturn, ondiscard }: Props = $props();

	const KINDS: DrillKind[] = ['mark', 'emmc_unreadable', 'emmc_root_missing'];
	const LABELS: Record<DrillKind, string> = {
		mark: 'standby.drill.mark',
		emmc_unreadable: 'standby.drill.emmc_unreadable',
		emmc_root_missing: 'standby.drill.emmc_root_missing'
	};
	const ready = $derived(status.switch_ready && !status.syncing);
</script>

<Card title={$t('standby.drills.title')}>
	<div class="flex flex-wrap gap-2" data-testid="standby-drills">
		{#if status.root === 'card'}
			<Button variant="warning" disabled={busy} onclick={onreturn}>{$t('standby.return.button')}</Button>
		{:else if status.standby_ran}
			<Button variant="warning" disabled={busy || !status.in_boot_path} onclick={() => ondrill('mark')}>
				{$t('standby.returnThrough.button')}
			</Button>
			<Button variant="secondary" disabled={busy || status.syncing} onclick={ondiscard}>
				{$t('standby.discard.button')}
			</Button>
		{:else}
			{#each KINDS as kind (kind)}
				<Button
					variant="secondary"
					disabled={busy || !ready}
					disabledReason={ready ? undefined : $t('standby.drills.unavailable')}
					onclick={() => ondrill(kind)}
				>
					{$t(LABELS[kind])}
				</Button>
			{/each}
		{/if}
	</div>
	<HelpText
		class="mt-3"
		text={$t(
			status.root !== 'card' && !ready && !status.standby_ran ? 'standby.drills.unavailable' : 'standby.drills.help'
		)}
	/>
</Card>

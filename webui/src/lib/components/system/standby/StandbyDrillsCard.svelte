<script lang="ts">
	import type { DrillKind, StandbyStatus } from '$lib/api/standby';
	import { Alert, Button, Card, HelpText } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	// The drills of the SD card standby and the ways back (ADR-0471): on the eMMC's system the three
	// drills, or after the standby ran Return through the standby and Discard its changes; on the
	// card Return to the eMMC with the card's changes, or without them, offered only while the eMMC
	// reads and its root passes its check, which runs again on request (system.sd-standby-failback);
	// otherwise the card stays the system disk. Each asks first (the page's confirm) and then reboots.
	type Props = {
		status: StandbyStatus;
		zone: string | undefined;
		busy: boolean;
		ondrill: (kind: DrillKind) => void;
		onreturn: () => void;
		onreturndiscard: () => void;
		oncheck: () => void;
		ondiscard: () => void;
	};

	let { status, zone, busy, ondrill, onreturn, onreturndiscard, oncheck, ondiscard }: Props = $props();
	const emmc = $derived(status.emmc_return);
	const STEPS: Record<string, string> = {
		check: 'standby.emmc.step.check',
		stop: 'standby.emmc.step.stop',
		mount: 'standby.emmc.step.mount',
		copy: 'standby.emmc.step.copy',
		fstab: 'standby.emmc.step.fstab',
		verify: 'standby.emmc.step.verify',
		finish: 'standby.emmc.step.finish'
	};

	const KINDS: DrillKind[] = ['mark', 'emmc_unreadable', 'emmc_root_missing'];
	const LABELS: Record<DrillKind, string> = {
		mark: 'standby.drill.mark',
		emmc_unreadable: 'standby.drill.emmc_unreadable',
		emmc_root_missing: 'standby.drill.emmc_root_missing'
	};
	const ready = $derived(status.switch_ready && !status.syncing);
</script>

<Card title={$t('standby.drills.title')}>
	{#if status.root === 'card' && emmc?.failed}
		<Alert variant="error" title={$t('standby.emmc.failedTitle')} class="mb-3">
			<span data-testid="standby-emmc-failed">
				{$t('standby.emmc.failedText', {
					step: $t(STEPS[emmc.failed.step ?? ''] ?? 'standby.emmc.step.other'),
					message: emmc.failed.message
				})}
			</span>
		</Alert>
	{/if}
	{#if status.root === 'card' && emmc?.running}
		<Alert variant="info" title={$t('standby.emmc.running')} class="mb-3">{$t('standby.emmc.runningText')}</Alert>
	{:else if status.root === 'card' && !emmc?.offered}
		<Alert variant="warning" title={$t('standby.emmc.notOffered')} class="mb-3">
			<span data-testid="standby-emmc-check">
				{emmc?.detail ? $t('standby.emmc.notOfferedText', { detail: emmc.detail }) : $t('standby.emmc.notChecked')}
			</span>
		</Alert>
	{/if}
	<div class="flex flex-wrap gap-2" data-testid="standby-drills">
		{#if status.root === 'card'}
			{#if emmc?.offered}
				<Button variant="warning" disabled={busy} onclick={onreturn}>{$t('standby.return.button')}</Button>
				<Button variant="secondary" disabled={busy} onclick={onreturndiscard}>
					{$t('standby.returnDiscard.button')}
				</Button>
			{/if}
			{#if !emmc?.running}
				<Button variant="ghost" disabled={busy} onclick={oncheck}>{$t('standby.emmc.check')}</Button>
			{/if}
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
	{#if status.root === 'card' && emmc?.offered && emmc.checked_at}
		<p class="mt-3 text-sm text-secondary" data-testid="standby-emmc-check">
			{$t('standby.emmc.passed', {
				detail: emmc.detail ?? '',
				time: formatDateTime(emmc.checked_at, $intlLocale, { zone })
			})}
		</p>
	{/if}
	<HelpText
		class="mt-3"
		text={$t(
			status.root !== 'card' && !ready && !status.standby_ran ? 'standby.drills.unavailable' : 'standby.drills.help'
		)}
	/>
</Card>

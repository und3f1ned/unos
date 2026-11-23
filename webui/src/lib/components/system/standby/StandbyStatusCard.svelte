<script lang="ts">
	import type { StandbyStatus } from '$lib/api/standby';
	import { Alert, Badge, Button, Card, CardAction, HelpText, StatusDot, TechDetails } from '$lib/components/ui';
	import { formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { RefreshCw } from 'lucide-svelte';
	import { reasonText, standbyState, syncText } from './texts';

	// The SD card standby once set up (ADR-0471): its state, its card, its copy, its syncs and their
	// daily time on the NAS's clock. Sync now and the daily time are its actions here.
	type Props = {
		status: StandbyStatus;
		/** The NAS's time zone, for the times of the syncs; undefined while it is not known. */
		zone: string | undefined;
		/** Sync now runs. */
		syncing: boolean;
		savingTime: boolean;
		onsync: () => void;
		onsavetime: (time: string) => void;
	};

	let { status, zone, syncing, savingTime, onsync, onsavetime }: Props = $props();

	const mark = $derived(standbyState(status));
	// The time typed, until it is saved; the server's otherwise.
	let typed = $state<string | null>(null);
	const time = $derived(typed ?? status.sync_time);
	const canSync = $derived(status.root !== 'card' && !status.standby_ran && status.card_present === true);

	function when(value: string | null): string {
		return value ? formatDateTime(value, $intlLocale, { zone }) : $t('standby.sync.never');
	}

	function save(event: SubmitEvent) {
		event.preventDefault();
		onsavetime(time);
		typed = null;
	}
</script>

{#snippet badge()}
	<Badge variant={mark.variant} dot>{$t(mark.key)}</Badge>
{/snippet}

{#snippet actions()}
	{#if canSync}
		<CardAction icon={RefreshCw} loading={syncing || status.syncing} onclick={onsync}>
			{$t('standby.sync.button')}
		</CardAction>
	{/if}
{/snippet}

<Card title={$t('standby.status.title')} status={badge} {actions}>
	<div class="kv" data-testid="standby-status">
		<div>
			<span class="kv-label">{$t('standby.card')}</span>
			<span class="kv-value break-words">
				{#if status.card_present === false}
					{$t('standby.cardMissing')}
				{:else if status.card}
					<span class="mono">{status.card.device}</span> · {status.card.model ?? ''}{status.card.size
						? ` · ${formatSize(status.card.size, $intlLocale, $t)}`
						: ''}
				{/if}
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('standby.thisBoot')}</span>
			<span class="kv-value">
				{status.root === 'card'
					? $t('standby.boot.card', { reason: reasonText($t, status.boot_reason) })
					: status.in_boot_path
						? $t('standby.boot.emmc')
						: $t('standby.boot.none')}
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('standby.copy')}</span>
			<span class="kv-value">
				{status.generation === null ? $t('standby.noCopy') : $t('standby.copyNumber', { n: status.generation })}
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('standby.lastSync')}</span>
			<span class="kv-value" data-testid="standby-last-sync">
				{#if status.last_sync}
					{when(status.last_sync.at)} · {syncText($t, $intlLocale, status.last_sync)}
				{:else}
					{$t('standby.sync.never')}
				{/if}
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('standby.lastComplete')}</span>
			<span class="kv-value">{when(status.last_complete_at)}</span>
		</div>
		{#if status.next_sync_at}
			<div>
				<span class="kv-label">{$t('standby.nextSync')}</span>
				<span class="kv-value">{when(status.next_sync_at)}</span>
			</div>
		{/if}
	</div>

	{#if status.syncing}
		<p class="mt-3 flex items-center gap-2 text-sm text-content-secondary">
			<StatusDot status="info" pulse />{$t('standby.syncing')}
		</p>
	{/if}
	{#if status.last_sync?.result === 'wrote' && status.last_sync.changes?.length}
		<TechDetails class="mt-3" summary={$t('standby.sync.changes')} text={status.last_sync.changes.join('\n')} />
	{/if}
	{#if status.last_sync?.error}
		<Alert variant="warning" size="sm" class="mt-4" title={$t('standby.sync.error')}>
			<TechDetails text={status.last_sync.error} />
		</Alert>
	{/if}
	{#if status.marked_failed && status.root !== 'card'}
		<Alert variant="warning" size="sm" class="mt-4">{$t('standby.marked')}</Alert>
	{/if}
	{#if status.drill_pending}
		<Alert variant="info" size="sm" class="mt-4">{$t('standby.drillPending')}</Alert>
	{/if}

	{#if status.root !== 'card'}
		<form class="mt-5 flex flex-wrap items-end gap-3" onsubmit={save}>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-content-secondary">{$t('standby.syncTime.label')}</span>
				<input
					type="time"
					class="form-input w-36"
					value={time}
					oninput={(event) => (typed = event.currentTarget.value)}
					data-testid="standby-sync-time"
				/>
			</label>
			<Button type="submit" variant="secondary" size="sm" loading={savingTime} disabled={time === status.sync_time}>
				{$t('standby.syncTime.save')}
			</Button>
		</form>
		<HelpText class="mt-3" text={$t('standby.sync.help')} />
	{/if}
</Card>

<script lang="ts">
	import type { StandbyStatus } from '$lib/api/standby';
	import { Badge, Button, Card, HelpText } from '$lib/components/ui';
	import { formatNumber, formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	// The card's space by part and the critical apps of the SD standby (ADR-0500): each app's interval,
	// its copy and whether it runs from the card; Move the critical apps to the card (a drill) and
	// Return to the pool, discarding the card's changes, which the page asks first. An app moved for a
	// repair of its pool is offered back once the pool is mounted again (its return_blocked is null).
	type Props = {
		status: StandbyStatus;
		zone: string | undefined;
		busy: boolean;
		onmove: () => void;
		onreturn: () => void;
	};

	let { status, zone, busy, onmove, onreturn }: Props = $props();

	const size = (bytes: number | null | undefined) => (bytes == null ? '—' : formatSize(bytes, $intlLocale, $t));
	const space = $derived(status.space);
	const moved = $derived(status.apps.filter((app) => app.moved));
	const returnable = $derived(moved.filter((app) => app.return_blocked === null));
	const movable = $derived(status.apps.filter((app) => !app.moved));
	const REASONS: Record<string, string> = {
		pool_failed: 'standby.apps.reason.pool_failed',
		pool_read_only: 'standby.apps.reason.pool_read_only',
		drill: 'standby.apps.reason.drill',
		planned: 'standby.apps.reason.planned'
	};
	// The parts of the card, each with its bytes, as the Standby tab lists them.
	const parts = $derived(
		space
			? [
					{ label: $t('standby.space.system'), bytes: space.system },
					{ label: $t('standby.space.unosImages'), bytes: space.unos_images },
					{ label: $t('standby.space.unosVolumes'), bytes: space.unos_volumes },
					...status.apps.map((app) => ({
						label: $t('standby.space.app', { name: app.app }),
						bytes: (app.data_bytes ?? 0) + (app.images_bytes ?? 0)
					})),
					{ label: $t('standby.space.loaded'), bytes: space.loaded_images },
					{ label: $t('standby.space.free'), bytes: space.free },
					{ label: $t('standby.space.reserve'), bytes: space.reserve }
				]
			: []
	);
</script>

<Card title={$t('standby.apps.title')}>
	{#if space}
		<h4 class="text-sm font-semibold">{$t('standby.space.title', { size: size(space.size) })}</h4>
		<ul class="mt-1 mb-4 space-y-1 text-sm" data-testid="standby-space">
			{#each parts as part, index (index)}
				<li class="flex justify-between gap-3">
					<span>{part.label}</span><span>{size(part.bytes)}</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if status.apps.length === 0}
		<HelpText text={$t('standby.apps.none')} />
	{:else}
		<ul class="divide-y divide-border" data-testid="standby-apps">
			{#each status.apps as app (app.app)}
				<li class="py-2 flex flex-wrap items-start justify-between gap-2" data-testid="standby-app-{app.app}">
					<div class="min-w-0">
						<p class="font-medium break-all">{app.app}</p>
						<p class="text-sm text-secondary">
							{app.interval_hours === 24
								? $t('standby.apps.daily')
								: $t('standby.apps.every', { n: app.interval_hours, hours: app.interval_hours })}
							·
							{app.synced_at
								? $t('standby.apps.synced', { time: formatDateTime(app.synced_at, $intlLocale, { zone }) })
								: $t('standby.apps.notSynced')}
							{#if app.mib_per_sync != null}
								· {$t('standby.apps.mib', { mib: formatNumber(app.mib_per_sync, $intlLocale, { digits: 1 }) })}
							{/if}
						</p>
						{#if app.moved}
							<p class="text-sm">
								{$t(app.moved.has_copy ? 'standby.apps.movedText' : 'standby.apps.stoppedText', {
									time: formatDateTime(app.moved.at, $intlLocale, { zone }),
									reason: $t(REASONS[app.moved.reason] ?? 'standby.apps.reason.other'),
									pool: app.moved.pool
								})}
							</p>
							{#if app.return_blocked}
								<p class="text-sm text-secondary">{$t('standby.apps.blocked', { why: app.return_blocked })}</p>
							{/if}
						{/if}
						{#if app.error}
							<p class="text-sm text-status-error break-words">{$t('standby.apps.error', { error: app.error })}</p>
						{/if}
					</div>
					{#if app.moved}
						<Badge variant="warning">{$t('standby.apps.onCard')}</Badge>
					{:else}
						<Badge variant="outline">{$t('standby.apps.onPool', { pool: app.pool ?? '—' })}</Badge>
					{/if}
				</li>
			{/each}
		</ul>
		<div class="mt-3 flex flex-wrap gap-2">
			{#if movable.length > 0 && status.card_present}
				<Button variant="secondary" disabled={busy} onclick={onmove}>{$t('standby.apps.move')}</Button>
			{/if}
			{#if returnable.length > 0}
				<Button variant="warning" disabled={busy} onclick={onreturn}>
					{$t('standby.apps.return', { n: returnable.length })}
				</Button>
			{/if}
		</div>
		<HelpText class="mt-3" text={$t('standby.apps.help')} />
	{/if}
</Card>

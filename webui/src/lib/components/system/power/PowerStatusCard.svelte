<script lang="ts">
	import type { PendingAction, PowerFinding, PowerSchedule } from '$lib/api/power';
	import { Alert, Badge, Button, Card, Skeleton } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime, formatTimeOfDay } from '$lib/time';
	import { COUNTED, FOUND, FOUND_WITHOUT_SUBJECT, STATES, grouped } from './texts';

	// What the NAS's power does next (ADR-0407): the reboot or shutdown that waits for its minute,
	// and the plan the server made of the schedule. Status only: the settings are the Schedule card's.
	// A shutdown's reasons to wait show only while that shutdown is on, each reason once
	// with its count.
	type Props = {
		pending: PendingAction | null;
		// The pending action has been read (or its read failed).
		pendingKnown: boolean;
		// Why the pending action could not be read; "" when the page says it once for every card.
		pendingError: string;
		schedule: PowerSchedule | null;
		// Why the plan could not be read; "" as above.
		scheduleError: string;
		// The NAS's time zone for the times shown, or the browser's (undefined).
		zone: string | undefined;
		// The plan cannot be shown, for the reason the page shows.
		unavailable: boolean;
		cancelling: boolean;
		oncancel: () => void;
		// Read the pending action again, after its read alone failed.
		onretry: () => void;
	};

	let {
		pending,
		pendingKnown,
		pendingError,
		schedule,
		scheduleError,
		zone,
		unavailable,
		cancelling,
		oncancel,
		onretry
	}: Props = $props();

	let describe = $derived((finding: PowerFinding, count: number): string => {
		const key =
			(finding.subject === null && FOUND_WITHOUT_SUBJECT[finding.kind]) || FOUND[finding.kind] || FOUND.unreadable;
		const mib = ((finding.amount ?? 0) / 1048576).toLocaleString($intlLocale, { maximumFractionDigits: 1 });
		const counted = COUNTED.has(finding.kind);
		const text = $t(key, {
			subject: finding.subject ?? '',
			detail: finding.detail ?? '',
			n: counted ? count : (finding.amount ?? 0),
			mib
		});
		return count > 1 && !counted ? $t('power.found.repeated', { text, n: count }) : text;
	});
	let when = $derived((at: string | null): string =>
		formatDateTime(at, $intlLocale, {
			weekday: 'short',
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit',
			timeZone: zone
		})
	);
	let timeOf = $derived((at: string): string =>
		formatTimeOfDay(at, $intlLocale, { hour: '2-digit', minute: '2-digit', timeZone: zone })
	);
</script>

{#snippet findingList(findings: PowerFinding[])}
	<ul class="mt-1 list-disc pl-5 text-content-secondary">
		{#each grouped(findings) as { finding, count }}
			<li class="break-words">{describe(finding, count)}</li>
		{/each}
	</ul>
{/snippet}

{#snippet off()}
	<span class="text-content-muted">{$t('power.status.off')}</span>
{/snippet}

<Card title={$t('power.status.title')}>
	<div class="space-y-4">
		{#if pendingError}
			<Alert variant="error" size="sm" title={$t('power.pending.readError')}>
				<p class="break-words">{pendingError}</p>
				<Button variant="secondary" size="sm" class="mt-2" onclick={onretry}>{$t('common.retry')}</Button>
			</Alert>
		{:else if pending?.action}
			{@const reboot = pending.action === 'reboot'}
			<Alert variant="warning" size="sm" title={reboot ? $t('power.pending.reboot') : $t('power.pending.shutdown')}>
				<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
					<p>
						{#if pending.at}
							{$t(reboot ? 'power.pending.rebootAt' : 'power.pending.shutdownAt', { time: timeOf(pending.at) })}
						{:else}
							{reboot ? $t('power.pending.rebootSoon') : $t('power.pending.shutdownSoon')}
						{/if}
					</p>
					<Button variant="secondary" size="sm" loading={cancelling} onclick={oncancel}>
						{reboot ? $t('power.pending.cancelReboot') : $t('power.pending.cancelShutdown')}
					</Button>
				</div>
			</Alert>
		{/if}

		{#if unavailable}
			<p class="text-sm text-content-muted">{$t('power.unavailable')}</p>
		{:else if scheduleError}
			<Alert variant="error" size="sm" title={$t('power.schedule.readError')}>{scheduleError}</Alert>
		{:else if !schedule || !pendingKnown}
			<div class="grid gap-3 sm:grid-cols-[11rem_minmax(0,1fr)]" aria-busy="true">
				<Skeleton shape="line" count={3} />
				<Skeleton shape="line" count={3} />
			</div>
		{:else}
			<dl
				class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[11rem_minmax(0,1fr)]"
				aria-label={$t('power.schedule.planLabel')}
			>
				<dt class="text-content-secondary">{$t('power.schedule.nextShutdown')}</dt>
				<dd data-testid="power-next-shutdown">
					{#if !schedule.shutdown.enabled}
						{@render off()}
					{:else if schedule.shutdown.next}
						<span class="tabular">{when(schedule.shutdown.next)}</span>
						<Badge variant={schedule.shutdown.state === 'postponed' ? 'warning' : 'neutral'}>
							{$t(STATES[schedule.shutdown.state] ?? STATES.planned)}
						</Badge>
						{#if schedule.shutdown.held_by.length}
							<p class="mt-1 text-content-secondary">{$t('power.schedule.heldBy')}</p>
							{@render findingList(schedule.shutdown.held_by)}
						{/if}
					{:else}
						<span class="text-content-muted">{$t('power.status.notPlanned')}</span>
					{/if}
				</dd>

				<dt class="text-content-secondary">{$t('power.schedule.nextWake')}</dt>
				<dd data-testid="power-next-wake">
					{#if !schedule.wake.enabled}
						{@render off()}
					{:else if !schedule.wake.rtc.available}
						<!-- Why is the Schedule card's, under the wake it concerns: said once. -->
						<Badge variant="warning">{$t('power.status.noWake')}</Badge>
					{:else}
						<p class="tabular">{schedule.wake.next ? when(schedule.wake.next) : $t('power.status.notPlanned')}</p>
						<p class="text-content-muted">
							{schedule.wake.rtc.alarm
								? $t('power.schedule.alarmAt', { time: when(schedule.wake.rtc.alarm) })
								: $t('power.schedule.noAlarm')}
						</p>
					{/if}
				</dd>

				<dt class="text-content-secondary">{$t('power.schedule.idleTitle')}</dt>
				<dd data-testid="power-idle">
					{#if !schedule.idle.enabled}
						{@render off()}
					{:else if schedule.idle.idle}
						{#if schedule.idle.since && schedule.idle.shutdown_at}
							{$t('power.schedule.idleSince', {
								since: when(schedule.idle.since),
								at: when(schedule.idle.shutdown_at)
							})}
						{:else}
							{$t('power.schedule.idleNow')}
						{/if}
					{:else}
						<p>{$t('power.schedule.notIdle')}</p>
						{@render findingList(schedule.idle.found)}
					{/if}
				</dd>
			</dl>
		{/if}
	</div>
</Card>

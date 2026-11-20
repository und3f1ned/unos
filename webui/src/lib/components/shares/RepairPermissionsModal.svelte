<script lang="ts">
	import { onMount } from 'svelte';
	import { StopCircle, Wrench } from 'lucide-svelte';
	import { ApiError } from '$lib/api/client';
	import { getRepairResult, repairSharePermissions, stopRepair, type RepairResult } from '$lib/api/shares';
	import { Alert, Badge, Button, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { isRepairOf, repairJob } from '$lib/stores/repair';
	import { messageOf } from './errors';

	type Props = {
		/**
		 * The share whose folder is repaired. The dialog is open while mounted. A repair runs in the
		 * background (ADR-0363): the dialog follows it through the store repairJob, which the start's
		 * answer and then the events socket fill, and can be closed while it runs.
		 */
		name: string;
		// The user closed the dialog (not while a repair is starting): the page unmounts it.
		onclose?: () => void;
	};

	let { name, onclose }: Props = $props();

	// The share's last finished run as the dialog read it when it opened.
	let fetched: RepairResult | null = $state(null);
	let starting = $state(false);
	let stopping = $state(false);
	let error = $state('');
	let stopError = $state('');
	let lastError = $state('');

	// This share's repair as the store has it: the running one, or the last one that ended since
	// the page loaded, which is newer than the one read when the dialog opened.
	const job = $derived(isRepairOf($repairJob, name) ? $repairJob : null);
	const running = $derived(job?.state === 'running');
	const result = $derived(running ? null : (job ?? fetched));

	type BadgeVariant = 'success' | 'warning' | 'error';

	// The states of a run that ended, each with the key of its label; a state without one is shown
	// as it comes.
	const stateBadges: Partial<Record<RepairResult['state'], { variant: BadgeVariant; key: string }>> = {
		done: { variant: 'success', key: 'shares.repair.state.done' },
		incomplete: { variant: 'warning', key: 'shares.repair.state.incomplete' },
		failed: { variant: 'error', key: 'shares.repair.state.failed' }
	};

	// What a run skipped, by the keys of RepairResult.skipped; a key without one is shown as it comes.
	const skippedKeys: Record<string, string> = {
		other_filesystem: 'shares.repair.skip.otherFilesystem',
		snapshots: 'shares.repair.skip.snapshots',
		symlink: 'shares.repair.skip.symlink',
		special: 'shares.repair.skip.special',
		hardlink: 'shares.repair.skip.hardlink',
		private: 'shares.repair.skip.private',
		too_deep: 'shares.repair.skip.tooDeep',
		raced: 'shares.repair.skip.raced'
	};

	onMount(async () => {
		// The last finished run since UNOS started, if any (404: none).
		try {
			fetched = await getRepairResult(name);
		} catch (e) {
			if (!(e instanceof ApiError && e.status === 404)) lastError = messageOf(e);
		}
	});

	async function repair() {
		if (starting || running) return;
		starting = true;
		error = '';
		stopError = '';
		try {
			// Answered at once with the repair running; the events socket sends what follows, and
			// what it has sent of this run already (a short one may have ended) stays.
			const started = await repairSharePermissions(name);
			repairJob.update((job) =>
				isRepairOf(job, started.share) && job.started_at === started.started_at ? job : started
			);
		} catch (e) {
			// 409: not served, pool not mounted or read-only, group problem, already running.
			error = messageOf(e);
		} finally {
			starting = false;
		}
	}

	async function stop() {
		if (stopping) return;
		stopping = true;
		stopError = '';
		try {
			// The walk stops before its next entry; its end comes over the events socket.
			await stopRepair(name);
		} catch (e) {
			// 409: it ended meanwhile.
			stopError = messageOf(e);
		} finally {
			stopping = false;
		}
	}

	function close() {
		if (!starting) onclose?.();
	}

	function formatTime(value: string, locale: string): string {
		return formatDateTime(value, locale);
	}

	const skippedCounts = $derived(
		result
			? Object.entries(result.skipped)
					.filter(([, count]) => count > 0)
					.map(([key, count]) => `${skippedKeys[key] ? $t(skippedKeys[key]) : key}: ${count}`)
			: []
	);
</script>

<Modal open title={$t('shares.repair.title', { name })} size="lg" closable={!starting} onclose={close}>
	<div class="space-y-3 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1">
		<!-- The outcome first: after a run it is what the user is waiting for. -->
		{#if lastError}
			<Alert variant="error" title={$t('shares.repair.lastError')}>{lastError}</Alert>
		{/if}

		{#if error}
			<Alert variant="error" title={$t('shares.repair.failed')}>{error}</Alert>
		{/if}

		{#if stopError}
			<Alert variant="error" title={$t('shares.repair.notStopped')}>{stopError}</Alert>
		{/if}

		{#if job && running}
			<section class="card-nested space-y-2 text-sm" aria-label={$t('shares.repair.progress')}>
				<div class="flex flex-wrap items-center gap-2">
					<span class="font-medium">{$t('shares.repair.progress')}</span>
					<span class="text-xs text-muted"
						>{$t('shares.repair.since', { time: formatTime(job.started_at, $intlLocale) })}</span
					>
				</div>
				<p>{$t('shares.repair.counts', { visited: job.visited, changed: job.changed, n: job.errors })}</p>
				<p class="text-secondary">{$t('shares.repair.running')}</p>
			</section>
		{/if}

		{#if result}
			{@const badge = stateBadges[result.state]}
			<section class="card-nested space-y-2 text-sm" aria-label={$t('shares.repair.result')}>
				<div class="flex flex-wrap items-center gap-2">
					<span class="font-medium">{$t('shares.repair.lastRun')}</span>
					<Badge variant={badge?.variant ?? 'warning'}>{badge ? $t(badge.key) : result.state}</Badge>
					<span class="text-xs text-muted"
						>{formatTime(result.finished_at ?? result.started_at, $intlLocale)}</span
					>
				</div>
				<p>
					{$t('shares.repair.counts', {
						visited: result.visited,
						changed: result.changed,
						n: result.errors
					})}
				</p>
				{#if skippedCounts.length > 0}
					<p>{$t('shares.repair.skipped', { list: skippedCounts.join(', ') })}</p>
				{/if}
				{#if result.skipped_paths.length > 0}
					<div>
						<p>{$t('shares.repair.skippedPaths')}</p>
						<ul class="ml-5 list-disc">
							{#each result.skipped_paths as path}
								<li class="mono-xs break-all">{path}</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if result.error_samples.length > 0}
					<div>
						<p>{$t('shares.repair.errors')}</p>
						<ul class="ml-5 list-disc">
							{#each result.error_samples as sample}
								<li class="mono-xs break-all">{sample}</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if result.message}
					{#if result.state === 'done'}
						<p>{result.message}</p>
					{:else}
						<Alert variant={result.state === 'failed' ? 'error' : 'warning'}>{result.message}</Alert>
					{/if}
				{/if}
			</section>
		{/if}

		<p class="text-sm">{$t('shares.repair.intro')}</p>
		<ul class="list-disc ml-5 text-sm space-y-1">
			<li>{$t('shares.repair.group')}</li>
			<li>{$t('shares.repair.others')}</li>
			<li>{$t('shares.repair.owners')}</li>
			<li>{$t('shares.repair.private')}</li>
			<li>{$t('shares.repair.skips')}</li>
			<li>{$t('shares.repair.duration')}</li>
		</ul>
		<Alert variant="warning">{$t('shares.repair.warning')}</Alert>
	</div>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={starting} onclick={close}>
			{$t('shares.repair.close')}
		</Button>
		{#if running}
			<Button variant="danger" class="flex-1" icon={StopCircle} loading={stopping} onclick={stop}>
				{$t('shares.repair.stop')}
			</Button>
		{:else}
			<Button variant="warning" class="flex-1" icon={Wrench} loading={starting} onclick={repair}>
				{$t('shares.repair.run')}
			</Button>
		{/if}
	{/snippet}
</Modal>

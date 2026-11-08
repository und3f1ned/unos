<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { Wrench } from 'lucide-svelte';
	import { ApiError } from '$lib/api/client';
	import { getRepairResult, repairSharePermissions, type RepairResult } from '$lib/api/shares';
	import { Alert, Badge, Button, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';

	/** The share whose folder is repaired. The dialog is open while mounted. */
	export let name: string;

	const dispatch = createEventDispatcher<{ close: void; repaired: RepairResult }>();

	let result: RepairResult | null = null;
	let running = false;
	let error = '';
	let lastError = '';

	type BadgeVariant = 'success' | 'warning' | 'error';

	// The states of a run, each with the key of its label; a state without one is shown as it comes.
	const stateBadges: Record<RepairResult['state'], { variant: BadgeVariant; key: string }> = {
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
		// The last run since UNOS started, if any (404: none).
		try {
			result = await getRepairResult(name);
		} catch (e) {
			if (!(e instanceof ApiError && e.status === 404)) lastError = messageOf(e);
		}
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function repair() {
		if (running) return;
		running = true;
		error = '';
		try {
			result = await repairSharePermissions(name);
			dispatch('repaired', result);
		} catch (e) {
			// 409: not served, pool not mounted or read-only, group problem, already running.
			error = messageOf(e);
		} finally {
			running = false;
		}
	}

	function close() {
		if (!running) dispatch('close');
	}

	function formatTime(value: string, locale: string): string {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale);
	}

	$: skippedCounts = result
		? Object.entries(result.skipped)
				.filter(([, count]) => count > 0)
				.map(([key, count]) => `${skippedKeys[key] ? $t(skippedKeys[key]) : key}: ${count}`)
		: [];
</script>

<Modal
	open
	title={$t('shares.repair.title', { name })}
	size="lg"
	closable={!running}
	on:close={close}
>
	<div class="space-y-3 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1">
		<!-- The outcome first: after a run it is what the user is waiting for. -->
		{#if lastError}
			<Alert variant="error" title={$t('shares.repair.lastError')}>{lastError}</Alert>
		{/if}

		{#if error}
			<Alert variant="error" title={$t('shares.repair.failed')}>{error}</Alert>
		{/if}

		{#if running}
			<p class="text-sm text-secondary">{$t('shares.repair.running')}</p>
		{/if}

		{#if result}
			{@const badge = stateBadges[result.state]}
			<section class="card-nested space-y-2 text-sm" aria-label={$t('shares.repair.result')}>
				<div class="flex flex-wrap items-center gap-2">
					<span class="font-medium">{$t('shares.repair.lastRun')}</span>
					<Badge variant={badge?.variant ?? 'warning'}>{badge ? $t(badge.key) : result.state}</Badge>
					<span class="text-xs text-muted">{formatTime(result.finished_at, $intlLocale)}</span>
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

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={running} on:click={close}>
			{$t('shares.repair.close')}
		</Button>
		<Button variant="warning" class="flex-1" icon={Wrench} loading={running} on:click={repair}>
			{$t('shares.repair.run')}
		</Button>
	</svelte:fragment>
</Modal>

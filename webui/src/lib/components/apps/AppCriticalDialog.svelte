<script lang="ts">
	// The critical mark of an installed app (ADR-0500): its folders under {pool}/apps/<app>/ that the SD
	// standby copies and those it does not (its other pool folders, its caches, the copies an update
	// keeps), their sizes, its images' size and the card's space; the mark and the sync interval, with
	// the writes to the card a week that the interval costs (ADR-0214). The server refuses a mark that
	// would leave the card less than its reserve; its message shows here as it comes.
	import { untrack } from 'svelte';
	import { Alert, Button, Checkbox, Modal, Select, Skeleton } from '$lib/components/ui';
	import { getCriticalPlan, setCritical, type CriticalFolder, type CriticalPlan } from '$lib/api/apps';
	import { messageOf } from '$lib/errors';
	import { formatNumber, formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';

	type Props = {
		/** The app's name: the dialog is open while it is set. */
		app: string | null;
		onclose: () => void;
		/** The server took the mark: what it answers now. */
		onsaved: (plan: CriticalPlan) => void;
	};

	let { app, onclose, onsaved }: Props = $props();

	let plan = $state<CriticalPlan | null>(null);
	let loadError = $state('');
	let error = $state('');
	let saving = $state(false);
	let critical = $state(false);
	let interval = $state('24');
	let readFor = '';

	$effect.pre(() => {
		const name = app;
		untrack(() => {
			if (!name) readFor = '';
			else if (name !== readFor) void read(name);
		});
	});

	async function read(name: string) {
		readFor = name;
		plan = null;
		loadError = '';
		error = '';
		try {
			plan = await getCriticalPlan(name);
			// The dialog is the mark's: an app not marked yet opens with it set.
			critical = true;
			interval = String(plan.interval_hours);
		} catch (e) {
			loadError = messageOf(e);
		}
	}

	const KINDS: Record<CriticalFolder['kind'], string> = {
		data: 'apps.critical.kind.data',
		cache: 'apps.critical.kind.cache',
		previous: 'apps.critical.kind.previous',
		other_pool: 'apps.critical.kind.otherPool'
	};

	const size = (bytes: number | null) => (bytes == null ? '—' : formatSize(bytes, $intlLocale, $t));
	const copied = $derived(plan?.folders.filter((folder) => folder.copied) ?? []);
	const notCopied = $derived(plan?.folders.filter((folder) => !folder.copied) ?? []);
	// The writes a week of the chosen interval: the average of the recent syncs times the syncs a week.
	const weekly = $derived(
		plan?.mib_per_sync != null
			? $t('apps.critical.weekly', {
					mib: formatNumber((plan.mib_per_sync * 168) / Number(interval), $intlLocale, { digits: 0, grouping: true })
				})
			: $t('apps.critical.weeklyUnknown')
	);

	async function save() {
		if (!plan || saving) return;
		saving = true;
		error = '';
		try {
			const answer = await setCritical(plan.app, critical, Number(interval));
			onsaved(answer);
		} catch (e) {
			error = messageOf(e);
		} finally {
			saving = false;
		}
	}
</script>

<Modal open={app !== null} title={$t('apps.critical.title', { name: app ?? '' })} closable={!saving} {onclose}>
	<div class="space-y-4" data-testid="app-critical-dialog">
		<p class="text-sm text-secondary">{$t('apps.critical.intro')}</p>
		{#if loadError}
			<div role="alert"><Alert variant="error">{loadError}</Alert></div>
		{:else if !plan}
			<div aria-busy="true" class="space-y-2">
				<Skeleton class="w-64 max-w-full" />
				<Skeleton class="w-48 max-w-full" />
			</div>
		{:else}
			<div>
				<h4 class="text-sm font-semibold">{$t('apps.critical.copied')}</h4>
				<ul class="mt-1 space-y-1 text-sm" data-testid="app-critical-copied">
					{#each copied as folder (folder.path)}
						<li class="flex justify-between gap-3">
							<span class="mono-xs break-all">{folder.path}</span><span>{size(folder.bytes)}</span>
						</li>
					{:else}
						<li class="text-secondary">{$t('apps.critical.nothing')}</li>
					{/each}
					<li class="flex justify-between gap-3">
						<span>{$t('apps.critical.images')}</span><span>{size(plan.images_bytes)}</span>
					</li>
				</ul>
			</div>
			{#if notCopied.length}
				<div>
					<h4 class="text-sm font-semibold">{$t('apps.critical.notCopied')}</h4>
					<ul class="mt-1 space-y-1 text-sm" data-testid="app-critical-not-copied">
						{#each notCopied as folder (folder.path)}
							<li class="flex justify-between gap-3">
								<span class="min-w-0">
									<span class="mono-xs break-all">{folder.path}</span>
									<span class="text-secondary">· {$t(KINDS[folder.kind])}</span>
								</span>
								<span>{size(folder.bytes)}</span>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
			<p class="text-sm" data-testid="app-critical-card">
				{$t('apps.critical.card', {
					free: size(plan.card_free),
					size: size(plan.card_size),
					reserve: size(plan.reserve),
					needed: size(plan.needed)
				})}
			</p>
			{#if !plan.fits && !plan.critical}
				<Alert variant="warning">{$t('apps.critical.noRoom')}</Alert>
			{/if}
			<Checkbox
				bind:checked={critical}
				label={$t('apps.critical.mark')}
				description={$t('apps.critical.markHint')}
				disabled={plan.failed_over}
			/>
			{#if critical}
				<Select
					label={$t('apps.critical.interval')}
					bind:value={interval}
					options={plan.intervals.map((hours) => ({
						value: String(hours),
						label: hours === 24 ? $t('apps.critical.daily') : $t('apps.critical.hours', { n: hours, hours })
					}))}
					hint={weekly}
				/>
			{/if}
			{#if error}
				<div role="alert"><Alert variant="error">{error}</Alert></div>
			{/if}
		{/if}
	</div>
	{#snippet footer()}
		<Button variant="secondary" disabled={saving} onclick={onclose}>{$t('common.cancel')}</Button>
		<Button variant="primary" loading={saving} disabled={!plan || plan.failed_over} onclick={save}>
			{$t('apps.critical.save')}
		</Button>
	{/snippet}
</Modal>

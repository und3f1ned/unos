<script lang="ts">
	import {
		armStandby,
		discardCriticalApps,
		discardStandby,
		moveCriticalApps,
		removeStandby,
		returnCriticalApps,
		returnToEmmc,
		setStandbySyncTime,
		startDrill,
		syncStandby,
		type DrillKind,
		type StandbyStatus
	} from '$lib/api/standby';
	import { Alert, Button, Card, ErrorState, HelpText, Skeleton, confirm, toast } from '$lib/components/ui';
	import {
		StandbyAppsCard,
		StandbyConditions,
		StandbyDrillsCard,
		StandbyStatusCard
	} from '$lib/components/system/standby';
	import { reasonText, syncText } from '$lib/components/system/standby/texts';
	import { messageOf } from '$lib/errors';
	import { formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { followPage } from '$lib/pages/follow.svelte';
	import { refreshPage } from '$lib/pages/load';
	import { standbyReader, type StandbySnapshot } from '$lib/pages/system/standby';
	import { nasTimeZone } from '$lib/stores/nasTime';
	import { formatDateTime, knownZone } from '$lib/time';
	import { RefreshCw } from 'lucide-svelte';
	import type { PageProps } from './$types';

	// System > Standby (ADR-0471): the SD card standby of a system on an eMMC. Its conditions, and
	// where all are met Set up the standby; once set up its state, its syncs, the drills, the ways
	// back and Remove the standby. Each action asks first, and what it did is a toast.
	let { data }: PageProps = $props();

	let status = $state<StandbyStatus | null>(null);
	let error = $state('');
	// The tab's data have come (its load, ADR-0469): until then, on the tab's first page, its skeleton.
	let loaded = $state(false);
	let loading = $state(false);
	let busy = $state(false);
	let syncing = $state(false);
	let savingTime = $state(false);

	followPage(
		() => data,
		(snapshot) => apply(snapshot)
	);

	function apply(snapshot: StandbySnapshot) {
		if (snapshot.standby.ok) {
			status = snapshot.standby.value;
			error = '';
		} else {
			status = null;
			error = snapshot.standby.error || $t('standby.loadError');
		}
		loaded = true;
	}

	async function reload() {
		loading = true;
		await refreshPage(standbyReader.key);
		loading = false;
	}

	const zone = $derived(knownZone($nasTimeZone) ? $nasTimeZone : undefined);
	const card = $derived(status?.card ?? null);
	const freeCard = $derived(status && !status.armed && status.available && !status.unavailable_reason ? card : null);

	/** Run an action: its toast, then the tab read again. */
	async function act(run: () => Promise<string>, failedKey: string) {
		busy = true;
		try {
			toast.success(await run());
		} catch (e) {
			toast.error(messageOf(e), { title: $t(failedKey) });
		} finally {
			busy = false;
			await reload();
		}
	}

	async function setUp() {
		if (!freeCard?.device || !freeCard.model) return;
		const asked = await confirm({
			title: $t('standby.setup.title', { device: freeCard.device }),
			message: $t('standby.setup.message', { model: freeCard.model }),
			consequences: [$t('standby.setup.loader'), $t('standby.setup.hidden'), $t('standby.setup.copy')],
			confirmLabel: $t('standby.setup.button'),
			typeToConfirm: freeCard.model,
			focusCancel: true
		});
		if (!asked) return;
		const model = freeCard.model;
		await act(async () => {
			await armStandby(model);
			return $t('standby.setup.done');
		}, 'standby.setup.failed');
	}

	async function syncNow() {
		syncing = true;
		try {
			const answer = await syncStandby();
			toast.success(
				$t('standby.sync.done', { result: answer.sync ? syncText($t, $intlLocale, answer.sync) : answer.message })
			);
		} catch (e) {
			toast.error(messageOf(e), { title: $t('standby.sync.error') });
		} finally {
			syncing = false;
			await reload();
		}
	}

	async function saveTime(time: string) {
		savingTime = true;
		try {
			await setStandbySyncTime(time);
			toast.success($t('standby.syncTime.saved'));
		} catch (e) {
			toast.error(messageOf(e), { title: $t('standby.syncTime.failed') });
		} finally {
			savingTime = false;
			await reload();
		}
	}

	const DRILL_NAMES: Record<DrillKind, string> = {
		mark: 'standby.drill.mark',
		emmc_unreadable: 'standby.drill.emmc_unreadable',
		emmc_root_missing: 'standby.drill.emmc_root_missing'
	};

	async function drill(kind: DrillKind) {
		const through = Boolean(status?.standby_ran);
		const asked = await confirm(
			through
				? {
						title: $t('standby.returnThrough.title'),
						message: $t('standby.returnThrough.message'),
						consequences: [$t('standby.drill.consequence')],
						confirmLabel: $t('standby.drill.confirm'),
						tone: 'warning'
					}
				: {
						title: $t('standby.drill.title', { name: $t(DRILL_NAMES[kind]) }),
						message: $t('standby.drill.message'),
						consequences: [
							$t('standby.drill.consequence'),
							...(kind === 'emmc_root_missing' ? [$t('standby.drill.rootMissing')] : [])
						],
						confirmLabel: $t('standby.drill.confirm'),
						tone: 'warning'
					}
		);
		if (!asked) return;
		await act(async () => {
			await startDrill(kind);
			return $t('standby.drill.started');
		}, 'standby.drill.failed');
	}

	// On the card: the return to the eMMC with the card's changes, or without them; the eMMC's check again
	// (a dry run of the return) (system.sd-standby-failback).
	async function returnBack() {
		const asked = await confirm({
			title: $t('standby.return.title'),
			message: $t('standby.return.message'),
			consequences: [$t('standby.return.consequence'), $t('standby.return.apps')],
			confirmLabel: $t('standby.return.confirm'),
			tone: 'warning'
		});
		if (!asked) return;
		await act(async () => {
			await returnToEmmc();
			return $t('standby.return.started');
		}, 'standby.return.failed');
	}

	async function returnDiscarding() {
		const asked = await confirm({
			title: $t('standby.returnDiscard.title'),
			message: $t('standby.returnDiscard.message'),
			confirmLabel: $t('standby.returnDiscard.confirm'),
			tone: 'warning'
		});
		if (!asked) return;
		await act(async () => {
			await returnToEmmc({ discard: true });
			return $t('standby.returnDiscard.started');
		}, 'standby.return.failed');
	}

	async function checkEmmc() {
		await act(async () => {
			await returnToEmmc({ dryRun: true });
			return $t('standby.emmc.checkPassed');
		}, 'standby.emmc.checkFailed');
	}

	async function discard() {
		const model = card?.model ?? '';
		const asked = await confirm({
			title: $t('standby.discard.title'),
			message: $t('standby.discard.message'),
			confirmLabel: $t('standby.discard.confirm'),
			typeToConfirm: model,
			focusCancel: true
		});
		if (!asked) return;
		await act(async () => {
			await discardStandby(model);
			return $t('standby.discard.done');
		}, 'standby.discard.failed');
	}

	// The critical apps (ADR-0500): all of them to the card as a drill, and back to their pool.
	async function moveApps() {
		const asked = await confirm({
			title: $t('standby.apps.moveTitle'),
			message: $t('standby.apps.moveMessage'),
			consequences: [$t('standby.apps.moveCache'), $t('standby.apps.moveOthers')],
			confirmLabel: $t('standby.apps.move'),
			tone: 'warning'
		});
		if (!asked) return;
		await act(async () => {
			const answer = await moveCriticalApps('drill');
			return $t('standby.apps.moved', { n: answer.apps.filter((app) => !app.error).length });
		}, 'standby.apps.moveFailed');
	}

	// The pool return (system.sd-standby-failback): its dry run first, for the MiB the copy writes.
	async function returnApps() {
		busy = true;
		let mib: number;
		try {
			const planned = await returnCriticalApps(true);
			mib = planned.apps.reduce((sum, app) => sum + (app.mib ?? 0), 0);
		} catch (e) {
			toast.error(messageOf(e), { title: $t('standby.apps.returnFailed') });
			busy = false;
			await reload();
			return;
		}
		busy = false;
		const asked = await confirm({
			title: $t('standby.apps.returnTitle'),
			message: $t('standby.apps.returnMessage'),
			consequences: [
				$t('standby.apps.returnMib', { size: formatSize(mib * 1024 * 1024, $intlLocale, $t) }),
				$t('standby.apps.returnBackup'),
				$t('standby.apps.returnLeft')
			],
			confirmLabel: $t('standby.apps.returnConfirm'),
			tone: 'warning'
		});
		if (!asked) return;
		await act(async () => {
			const answer = await returnCriticalApps();
			return $t('standby.apps.returned', { n: answer.apps.length });
		}, 'standby.apps.returnFailed');
	}

	async function discardApps() {
		const model = card?.model ?? '';
		const asked = await confirm({
			title: $t('standby.apps.discardTitle'),
			message: $t('standby.apps.discardMessage'),
			confirmLabel: $t('standby.apps.discardConfirm'),
			typeToConfirm: model,
			focusCancel: true
		});
		if (!asked) return;
		await act(async () => {
			const answer = await discardCriticalApps(model);
			return $t('standby.apps.returned', { n: answer.apps.length });
		}, 'standby.apps.returnFailed');
	}

	async function remove() {
		const model = card?.model ?? '';
		const asked = await confirm({
			title: $t('standby.remove.question', { device: card?.device ?? '' }),
			message: $t('standby.remove.message'),
			confirmLabel: $t('standby.remove.confirm'),
			typeToConfirm: model || undefined,
			focusCancel: true
		});
		if (!asked) return;
		await act(async () => {
			await removeStandby(model);
			return $t('standby.remove.done');
		}, 'standby.remove.failed');
	}
</script>

{#snippet removeCard()}
	<Card title={$t('standby.remove.title')}>
		<HelpText text={$t('standby.remove.help')} />
		<div class="mt-3">
			<Button variant="danger" disabled={busy || Boolean(status?.syncing)} onclick={remove}>
				{$t('standby.remove.button')}
			</Button>
		</div>
	</Card>
{/snippet}

<div class="space-y-4">
	<div class="flex items-center justify-between gap-3">
		<div class="min-w-0">
			<h2 class="text-xl font-semibold">{$t('standby.title')}</h2>
			{#if zone}
				<p class="page-subtitle" data-testid="standby-zone">{$t('standby.zone', { zone })}</p>
			{/if}
		</div>
		<Button variant="ghost" size="icon" icon={RefreshCw} {loading} title={$t('standby.refresh')} onclick={reload}>
			<span class="sr-only">{$t('standby.refresh')}</span>
		</Button>
	</div>
	<HelpText text={$t('standby.intro')} />

	{#if !loaded}
		<!-- The conditions' shape: one card of divided rows, each a title over a line and a badge. -->
		<div class="card card-flush divide-y divide-border" aria-busy="true">
			{#each [0, 1, 2, 3, 4, 5] as row (row)}
				<div class="flex items-center justify-between gap-4 px-5 py-3">
					<div class="min-w-0 flex-1">
						<Skeleton size="base" class="w-48 max-w-full" />
						<Skeleton class="w-72 max-w-full" />
					</div>
					<Skeleton shape="block" class="h-6 w-20 shrink-0 rounded" />
				</div>
			{/each}
		</div>
	{:else if error || !status}
		<div class="card">
			<ErrorState title={$t('standby.loadError')} message={error} onretry={reload} retrying={loading} />
		</div>
	{:else}
		{#if status.unavailable_reason}
			<Alert variant="info" title={$t('standby.unavailable')}>{status.unavailable_reason}</Alert>
		{/if}
		{#if status.root === 'card'}
			<Alert variant="error" title={$t('standby.onCard.title')}>
				<p>{$t('standby.onCard.text')}</p>
				<p class="mt-1">{$t('standby.boot.card', { reason: reasonText($t, status.boot_reason) })}</p>
			</Alert>
		{:else if status.standby_ran}
			<Alert variant="warning" title={$t('standby.ran.title')}>
				{$t('standby.ran.text', { time: formatDateTime(status.standby_ran.at, $intlLocale, { zone }) })}
			</Alert>
		{/if}

		{#if status.armed}
			<StandbyStatusCard {status} {zone} {syncing} {savingTime} onsync={syncNow} onsavetime={saveTime} />
			<StandbyDrillsCard
				{status}
				{zone}
				{busy}
				ondrill={drill}
				onreturn={returnBack}
				onreturndiscard={returnDiscarding}
				oncheck={checkEmmc}
				ondiscard={discard}
			/>
			<StandbyAppsCard {status} {zone} {busy} onmove={moveApps} onreturn={returnApps} ondiscard={discardApps} />
			{#if status.root !== 'card'}
				{@render removeCard()}
			{/if}
		{:else if status.leftover}
			<Alert variant="warning" title={$t('standby.leftover.title')}>{$t('standby.leftover.text')}</Alert>
			{@render removeCard()}
		{:else if freeCard}
			<Card title={$t('standby.free.title')}>
				<p class="font-medium break-words" data-testid="standby-free-card">
					<span class="mono">{freeCard.device}</span> · {freeCard.model ?? ''}{freeCard.size
						? ` · ${formatSize(freeCard.size, $intlLocale, $t)}`
						: ''}
				</p>
				<HelpText class="mt-2" text={$t('standby.free.text')} />
				<div class="mt-4">
					<Button variant="primary" loading={busy} onclick={setUp}>{$t('standby.setup.button')}</Button>
				</div>
			</Card>
		{:else}
			<Alert variant="info" title={$t('standby.notOffered')}>{$t('standby.notOfferedText')}</Alert>
		{/if}

		<StandbyConditions conditions={status.conditions} />
	{/if}
</div>

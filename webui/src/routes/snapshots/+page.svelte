<script lang="ts">
	// The Snapshots page: the snapshots of a mounted pool or volume and the copies that restores
	// made in it, with the dialogs that take, restore and delete them. Its lists are SnapshotLists.
	// The pool or volume is chosen in the header; each card names its folder once, and the rules
	// that the About card listed are where they apply.
	import { onMount } from 'svelte';
	import { Archive, Copy, Plus, Trash2, RotateCcw, RefreshCw } from 'lucide-svelte';
	import { Alert, Button, Card, EmptyState, ErrorState, Select, Skeleton } from '$lib/components/ui';
	import {
		deleteRestoreCopy,
		deleteSnapshot,
		type RestoreCopy,
		type SnapshotInfo,
		type Subvolume
	} from '$lib/api/snapshots';
	import { intlLocale, t } from '$lib/i18n';
	import { DATE_TIME_MINUTES } from '$lib/format';
	import { formatDateTime } from '$lib/time';
	import {
		CreateSnapshotDialog,
		DeleteDialog,
		RestoreSnapshotDialog,
		SnapshotTable
	} from '$lib/components/snapshots';
	import { SnapshotLists } from '$lib/components/snapshots/lists.svelte';
	import { SNAPSHOTS_FOLDER } from '$lib/components/snapshots/names';

	const lists = new SnapshotLists();

	// Where the last restore put its copy.
	let restoredTo = $state('');

	// The dialogs: each is open while its target is set.
	let showCreate = $state(false);
	let snapshotToRestore = $state.raw<SnapshotInfo | null>(null);
	let snapshotToDelete = $state.raw<SnapshotInfo | null>(null);
	let copyToDelete = $state.raw<RestoreCopy | null>(null);

	// A pool or a volume as the picker and the list's heading name it. It reads the language where
	// it is called, as the dates below do, so that what shows them follows a change of the language.
	const label = (sv: Subvolume): string =>
		sv.volume
			? $t('snapshots.volume', { pool: sv.pool, volume: sv.volume })
			: $t('snapshots.pool', { pool: sv.pool });

	// The server gives UTC times with their offset; the browser shows them in its own time zone,
	// in the language of the page (ADR-0287).
	const formatDate = (value: string): string => (value ? formatDateTime(value, $intlLocale, DATE_TIME_MINUTES) : '-');

	let options = $derived(lists.subvolumes.map((sv) => ({ value: sv.path, label: label(sv) })));

	onMount(() => lists.refresh());

	function choose(path: string) {
		lists.selectedPath = path;
		restoredTo = '';
		lists.loadSnapshots();
		lists.loadCopies();
	}

	function openCreate() {
		if (!lists.selected) return;
		showCreate = true;
	}

	async function snapshotCreated() {
		showCreate = false;
		await lists.loadSnapshots();
	}

	async function restored(target: string) {
		restoredTo = target;
		snapshotToRestore = null;
		await lists.refresh();
	}

	async function snapshotDeleted() {
		snapshotToDelete = null;
		await lists.loadSnapshots();
	}

	// A copy of a pool's snapshot is a volume of the pool as well: the picker loses it.
	async function copyDeleted() {
		copyToDelete = null;
		await lists.refresh();
	}
</script>

<div class="page-container">
	<div class="page-header">
		<div class="min-w-0">
			<h1 class="page-title">{$t('snapshots.title')}</h1>
			<p class="page-subtitle">{$t('snapshots.subtitle')}</p>
		</div>
		<!-- The pool or volume, then Refresh and the page's one primary action, last (ADR-0428). -->
		<div class="page-actions items-end">
			{#if lists.subvolumes.length > 0}
				<Select
					label={$t('snapshots.picker')}
					{options}
					bind:value={lists.selectedPath}
					onchange={choose}
					class="w-64 max-w-full"
				/>
			{:else if lists.subvolumesLoading}
				<!-- The picker's label and field, in their place until the pools answer (ADR-0430). -->
				<div class="w-64 max-w-full" aria-hidden="true" data-skeleton>
					<Skeleton class="w-28 mb-1.5" />
					<Skeleton shape="block" class="h-9 w-full" />
				</div>
			{/if}
			<Button
				variant="secondary"
				size="icon"
				icon={RefreshCw}
				loading={lists.busy}
				title={$t('common.refresh')}
				onclick={() => lists.refresh()}
			>
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
			<!-- Without a mounted pool there is nothing to take a snapshot of: the button says why, once the
			     pools have answered (while they load, or when they could not be read, no reason is true). -->
			<Button
				icon={Plus}
				disabled={!lists.selected}
				disabledReason={lists.selected || lists.subvolumesLoading || lists.subvolumesError ? '' : $t('snapshots.createBlocked')}
				onclick={openCreate}>{$t('snapshots.create')}</Button
			>
		</div>
	</div>

	{#if lists.subvolumesError}
		<Card>
			<!-- The header's Refresh tries again: no Retry of its own (ADR-0431). -->
			<ErrorState title={$t('snapshots.loadPoolsError')} message={lists.subvolumesError} />
		</Card>
	{/if}

	{#each lists.unreadVolumes as sv (sv.path)}
		<Alert variant="warning" size="sm" title={$t('snapshots.volumesError', { pool: sv.pool })}>
			{sv.volumes_error}
			{$t('snapshots.volumesErrorHint')}
		</Alert>
	{/each}

	{#if lists.subvolumes.length === 0 && lists.subvolumesLoading}
		<div class="space-y-6" aria-busy="true">
			<Skeleton shape="table" columns={3} count={3} />
			<Skeleton shape="card" />
		</div>
	{:else if lists.subvolumes.length === 0 && !lists.subvolumesError}
		<Card>
			<EmptyState compact icon={Archive} title={$t('snapshots.noPoolTitle')} description={$t('snapshots.noPool')}>
				<a href="/storage" class="link text-sm">{$t('snapshots.storagePage')}</a>
			</EmptyState>
		</Card>
	{/if}

	{#if restoredTo}
		<!-- Not a toast: where the copy was made is to be read, and kept, before going on. -->
		<Alert
			variant="success"
			title={$t('snapshots.restored')}
			dismissible
			ondismiss={() => (restoredTo = '')}
		>
			{$t('snapshots.restoredAt')} <span class="mono-xs break-all">{restoredTo}</span>.
		</Alert>
	{/if}

	{#if lists.selected}
		<Card flush>
			<div class="card-header">
				<div class="min-w-0">
					<h2 class="section-title">
						<Archive class="w-5 h-5 text-content-secondary" aria-hidden="true" />
						{$t('snapshots.of', { name: label(lists.selected) })}
					</h2>
					<p class="card-description break-all">
						{$t('snapshots.keptIn')} <span class="mono-xs">{lists.selected.path}/{SNAPSHOTS_FOLDER}</span>
					</p>
				</div>
				<!-- Where they are taken on a schedule: the Scheduler's snapshot tasks, of pools only. -->
				<a href="/system#scheduler" class="link text-[13px] whitespace-nowrap">{$t('snapshots.schedule')}</a>
			</div>
			{#if lists.snapshots.loading && lists.snapshots.shownPath !== lists.selectedPath}
				<div class="px-5 pb-5" aria-busy="true">
					<Skeleton shape="row" columns={3} count={3} size="control" />
				</div>
			{:else if lists.snapshots.error}
				<div class="px-5 pb-5">
					<ErrorState title={$t('snapshots.loadError')} message={lists.snapshots.error} />
				</div>
			{:else if lists.snapshots.items.length === 0}
				<!-- Create snapshot is the header's: the empty state says where they come from. -->
				<EmptyState compact icon={Archive} title={$t('snapshots.empty')} description={$t('snapshots.emptyHint')} />
			{:else}
				<SnapshotTable
					rows={lists.snapshots.items}
					label={$t('snapshots.list')}
					dateHeading={$t('snapshots.created')}
					{formatDate}
				>
					{#snippet actions(snap)}
						<!-- Restore in words, so that its icon is never taken for Refresh; Delete an icon with
						     its tooltip. -->
						<Button
							variant="secondary"
							size="sm"
							icon={RotateCcw}
							aria-label={$t('snapshots.restoreOne', { name: snap.name })}
							onclick={() => (snapshotToRestore = snap)}
						>
							{$t('snapshots.restoreAction')}
						</Button>
						<Button
							variant="ghost-danger"
							size="icon-sm"
							title={$t('snapshots.deleteOne', { name: snap.name })}
							onclick={() => (snapshotToDelete = snap)}
						>
							<Trash2 class="w-4 h-4" aria-hidden="true" />
							<span class="sr-only">{$t('snapshots.deleteOne', { name: snap.name })}</span>
						</Button>
					{/snippet}
				</SnapshotTable>
			{/if}
		</Card>

		<Card flush>
			<div class="card-header">
				<div class="min-w-0">
					<h2 class="section-title">
						<Copy class="w-5 h-5 text-content-secondary" aria-hidden="true" />
						{$t('snapshots.copies.of', { name: label(lists.selected) })}
					</h2>
					<p class="card-description">{$t('snapshots.copies.hint')}</p>
				</div>
			</div>
			{#if lists.copies.loading && lists.copies.shownPath !== lists.selectedPath}
				<div class="px-5 pb-5" aria-busy="true"><Skeleton count={2} /></div>
			{:else if lists.copies.error}
				<div class="px-5 pb-5">
					<ErrorState title={$t('snapshots.copies.loadError')} message={lists.copies.error} />
				</div>
			{:else if lists.copies.items.length === 0}
				<EmptyState compact icon={Copy} title={$t('snapshots.copies.empty')} />
			{:else}
				<SnapshotTable
					rows={lists.copies.items}
					label={$t('snapshots.copies.list')}
					dateHeading={$t('snapshots.copies.made')}
					{formatDate}
				>
					{#snippet actions(copy)}
						<Button
							variant="ghost-danger"
							size="icon-sm"
							title={$t('snapshots.copies.deleteOne', { name: copy.name })}
							onclick={() => (copyToDelete = copy)}
						>
							<Trash2 class="w-4 h-4" aria-hidden="true" />
							<span class="sr-only">{$t('snapshots.copies.deleteOne', { name: copy.name })}</span>
						</Button>
					{/snippet}
				</SnapshotTable>
			{/if}
		</Card>
	{/if}
</div>

<CreateSnapshotDialog
	open={showCreate}
	selected={lists.selected}
	onclose={() => (showCreate = false)}
	oncreated={snapshotCreated}
/>

<RestoreSnapshotDialog
	snapshot={snapshotToRestore}
	subvolumes={lists.subvolumes}
	onclose={() => (snapshotToRestore = null)}
	onrestored={restored}
/>

<DeleteDialog
	item={snapshotToDelete}
	title={$t('snapshots.delete.title')}
	before={$t('snapshots.delete.before')}
	after={$t('snapshots.delete.after')}
	failed={$t('snapshots.delete.failed')}
	submit={$t('snapshots.delete.submit')}
	remove={(snap) => deleteSnapshot(snap.id, snap.subvolume)}
	onclose={() => (snapshotToDelete = null)}
	ondeleted={snapshotDeleted}
/>

<DeleteDialog
	item={copyToDelete}
	title={$t('snapshots.copies.delete.title')}
	before={$t('snapshots.copies.delete.before')}
	after={$t('snapshots.copies.delete.after')}
	note={$t('snapshots.copies.delete.inUse')}
	failed={$t('snapshots.copies.delete.failed')}
	submit={$t('snapshots.copies.delete.submit')}
	remove={(copy) => deleteRestoreCopy(copy.name, copy.subvolume)}
	onclose={() => (copyToDelete = null)}
	ondeleted={copyDeleted}
/>

<script lang="ts">
	// The Snapshots page: the snapshots of a mounted pool or volume and the copies that restores
	// made in it, with the dialogs that take, restore and delete them. Its lists are SnapshotLists.
	import { onMount } from 'svelte';
	import { Archive, Copy, Plus, Trash2, RotateCcw, RefreshCw } from 'lucide-svelte';
	import { Alert, Button, Card, EmptyState, Select, Skeleton } from '$lib/components/ui';
	import {
		deleteRestoreCopy,
		deleteSnapshot,
		type RestoreCopy,
		type SnapshotInfo,
		type Subvolume
	} from '$lib/api/snapshots';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import {
		CreateSnapshotDialog,
		DeleteDialog,
		RestoreSnapshotDialog,
		SnapshotTable
	} from '$lib/components/snapshots';
	import { SnapshotLists } from '$lib/components/snapshots/lists.svelte';
	import { AUTO_PREFIX, RESTORE_PREFIX, SNAP_PREFIX, SNAPSHOTS_FOLDER } from '$lib/components/snapshots/names';

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
	const formatDate = (value: string): string => (value ? formatDateTime(value, $intlLocale) : '-');

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
		<h1 class="page-title">{$t('snapshots.title')}</h1>
		<div class="flex flex-wrap items-center gap-2">
			<Button icon={Plus} disabled={!lists.selected} onclick={openCreate}>{$t('snapshots.create')}</Button>
			<Button variant="secondary" size="icon" disabled={lists.busy} onclick={() => lists.refresh()}>
				<RefreshCw class="w-4 h-4 {lists.busy ? 'animate-spin' : ''}" />
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
		</div>
	</div>

	{#if lists.subvolumesError}
		<Alert variant="error" title={$t('snapshots.loadPoolsError')}>{lists.subvolumesError}</Alert>
	{/if}

	{#if lists.subvolumes.length > 0}
		<Card>
			<Select
				label={$t('snapshots.picker')}
				{options}
				bind:value={lists.selectedPath}
				onchange={choose}
				class="max-w-md"
			/>
			{#each lists.unreadVolumes as sv (sv.path)}
				<Alert variant="warning" title={$t('snapshots.volumesError', { pool: sv.pool })} class="mt-4">
					{sv.volumes_error}
					{$t('snapshots.volumesErrorHint')}
				</Alert>
			{/each}
		</Card>
	{:else if lists.subvolumesLoading}
		<div class="space-y-6" aria-busy="true">
			<Skeleton shape="card" count={2} />
			<Skeleton shape="table" columns={4} count={3} />
		</div>
	{:else if !lists.subvolumesError}
		<Card>
			<p class="text-muted">
				{$t('snapshots.noPool')}
				<a href="/storage" class="link">{$t('snapshots.storagePage')}</a>.
			</p>
		</Card>
	{/if}

	{#if restoredTo}
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
		<Card>
			<h2 class="section-title mb-4">
				<Archive class="w-5 h-5 text-content-secondary" />
				{$t('snapshots.of', { name: label(lists.selected) })}
			</h2>
			{#if lists.snapshots.loading && lists.snapshots.shownPath !== lists.selectedPath}
				<div aria-busy="true">
					<Skeleton shape="row" columns={4} count={3} size="control" />
				</div>
			{:else if lists.snapshots.error}
				<Alert variant="error" title={$t('snapshots.loadError')}>{lists.snapshots.error}</Alert>
			{:else if lists.snapshots.items.length === 0}
				<EmptyState
					icon={Archive}
					title={$t('snapshots.empty')}
					description={$t('snapshots.emptyHint')}
				/>
			{:else}
				<SnapshotTable
					rows={lists.snapshots.items}
					label={$t('snapshots.list')}
					dateHeading={$t('snapshots.created')}
					{formatDate}
				>
					{#snippet actions(snap)}
						<Button variant="ghost" size="icon-sm" onclick={() => (snapshotToRestore = snap)}>
							<RotateCcw class="w-4 h-4" />
							<span class="sr-only">{$t('snapshots.restoreOne', { name: snap.name })}</span>
						</Button>
						<Button variant="ghost-danger" size="icon-sm" onclick={() => (snapshotToDelete = snap)}>
							<Trash2 class="w-4 h-4" />
							<span class="sr-only">{$t('snapshots.deleteOne', { name: snap.name })}</span>
						</Button>
					{/snippet}
				</SnapshotTable>
			{/if}
		</Card>
	{/if}

	{#if lists.selected}
		<Card>
			<h2 class="section-title mb-2">
				<Copy class="w-5 h-5 text-content-secondary" />
				{$t('snapshots.copies.of', { name: label(lists.selected) })}
			</h2>
			<p class="text-sm text-secondary mb-4">{$t('snapshots.copies.hint')}</p>
			{#if lists.copies.loading && lists.copies.shownPath !== lists.selectedPath}
				<div aria-busy="true"><Skeleton count={2} /></div>
			{:else if lists.copies.error}
				<Alert variant="error" title={$t('snapshots.copies.loadError')}>{lists.copies.error}</Alert>
			{:else if lists.copies.items.length === 0}
				<p class="text-sm text-muted">{$t('snapshots.copies.empty')}</p>
			{:else}
				<SnapshotTable
					rows={lists.copies.items}
					label={$t('snapshots.copies.list')}
					dateHeading={$t('snapshots.copies.made')}
					{formatDate}
				>
					{#snippet actions(copy)}
						<Button variant="ghost-danger" size="icon-sm" onclick={() => (copyToDelete = copy)}>
							<Trash2 class="w-4 h-4" />
							<span class="sr-only">{$t('snapshots.copies.deleteOne', { name: copy.name })}</span>
						</Button>
					{/snippet}
				</SnapshotTable>
			{/if}
		</Card>
	{/if}

	<!-- Sentences with a name, a path or a link inside are translated in parts around them, and a
	     part that starts with punctuation follows its element without a space (ADR-0296). -->
	<Card>
		<h2 class="section-title mb-2">{$t('snapshots.about.title')}</h2>
		<p class="text-sm text-secondary">
			{$t('snapshots.about.kept')}
			<span class="mono-xs">{SNAPSHOTS_FOLDER}</span>
			{$t('snapshots.about.folder')}
			{$t('snapshots.about.btrfs')}
			{$t('snapshots.about.volumes')}
			{$t('snapshots.about.restore')}
			{$t('snapshots.about.scheduled')}
			<span class="mono-xs">{AUTO_PREFIX}{$t('snapshots.dateTime')}</span>. {$t('snapshots.about.names')}
			<span class="mono-xs">{SNAP_PREFIX}</span>,
			<span class="mono-xs">{RESTORE_PREFIX}</span>
			{$t('snapshots.about.and')}
			<span class="mono-xs">{AUTO_PREFIX}</span>{$t('snapshots.about.utc')}
		</p>
	</Card>
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

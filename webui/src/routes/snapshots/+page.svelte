<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getSubvolumes,
		getSnapshots,
		createSnapshot,
		deleteSnapshot,
		restoreSnapshot,
		getRestoreCopies,
		deleteRestoreCopy,
		type RestoreCopy,
		type SnapshotInfo,
		type Subvolume
	} from '$lib/api/snapshots';
	import { Archive, Copy, Plus, Trash2, RotateCcw, RefreshCw } from 'lucide-svelte';
	import { Button, Card, Modal, Alert, Input, Select, EmptyState } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';

	// Names on the disk, the same in every language (ADR-0296): the folder of a pool or a volume
	// that holds its snapshots, and the prefixes of the names that UNOS gives, which a name's
	// pattern puts before its translated <date>_<time>. They are the server's
	// (supervisor/tests/test_snapshots.py), and no text of the catalog holds one.
	// i18n-ignore: the name of a folder on the disk
	const SNAPSHOTS_FOLDER = '.snapshots';
	const SNAP_PREFIX = 'snap_';
	const RESTORE_PREFIX = 'restore_';
	const AUTO_PREFIX = 'auto_';

	// The mounted pools and their volumes (ADR-0243), and the one whose snapshots are listed.
	let subvolumes: Subvolume[] = [];
	let subvolumesLoading = true;
	let subvolumesError = '';
	let selectedPath = '';

	let snapshots: SnapshotInfo[] = [];
	let snapshotsLoading = false;
	let snapshotsError = '';
	// Only the latest request updates the list: another pool or volume may have been chosen meanwhile.
	let snapshotsRequest = 0;

	// The copies that restores made right in the pool or volume chosen (ADR-0356); as for the
	// snapshots, only the latest request updates the list.
	let copies: RestoreCopy[] = [];
	let copiesLoading = false;
	let copiesError = '';
	let copiesRequest = 0;

	// Where the last restore put its copy.
	let restoredTo = '';

	let showCreate = false;
	let newSnapshotName = '';
	let creating = false;
	let createError = '';

	let snapshotToRestore: SnapshotInfo | null = null;
	let restoring = false;
	let restoreError = '';

	let snapshotToDelete: SnapshotInfo | null = null;
	let deleting = false;
	let deleteError = '';

	let copyToDelete: RestoreCopy | null = null;
	let deletingCopy = false;
	let deleteCopyError = '';

	// A pool or a volume as the picker and the list's heading name it; reactive, as are the dates
	// below, so that they follow a change of the language.
	$: label = (sv: Subvolume): string =>
		sv.volume
			? $t('snapshots.volume', { pool: sv.pool, volume: sv.volume })
			: $t('snapshots.pool', { pool: sv.pool });

	// The server gives UTC times with their offset; the browser shows them in its own time zone,
	// in the language of the page (ADR-0287).
	$: formatDate = (value: string): string => {
		if (!value) return '-';
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString($intlLocale);
	};

	$: options = subvolumes.map((sv) => ({ value: sv.path, label: label(sv) }));
	// Pools offered without their volumes, which could not be read (a disk that dropped off, say).
	$: unreadVolumes = subvolumes.filter((sv) => sv.volumes_error);
	$: selected = subvolumes.find((sv) => sv.path === selectedPath) ?? null;
	$: busy = subvolumesLoading || snapshotsLoading || copiesLoading;
	// The pool or volume that a restore makes its copy in.
	$: restoreInto = snapshotToRestore
		? (subvolumes.find((sv) => sv.path === snapshotToRestore?.subvolume) ?? null)
		: null;

	onMount(refresh);

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function refresh() {
		subvolumesLoading = true;
		subvolumesError = '';
		try {
			subvolumes = await getSubvolumes();
		} catch (e) {
			subvolumes = [];
			subvolumesError = messageOf(e);
		} finally {
			subvolumesLoading = false;
		}
		// The choice stays while it is offered.
		if (!subvolumes.some((sv) => sv.path === selectedPath)) {
			selectedPath = subvolumes[0]?.path ?? '';
		}
		await Promise.all([loadSnapshots(), loadCopies()]);
	}

	async function loadSnapshots() {
		const path = selectedPath;
		const request = ++snapshotsRequest;
		snapshotsError = '';
		if (!path) {
			snapshots = [];
			snapshotsLoading = false;
			return;
		}
		snapshotsLoading = true;
		try {
			const list = await getSnapshots(path);
			if (request === snapshotsRequest) snapshots = list;
		} catch (e) {
			if (request === snapshotsRequest) {
				snapshots = [];
				snapshotsError = messageOf(e);
			}
		} finally {
			if (request === snapshotsRequest) snapshotsLoading = false;
		}
	}

	async function loadCopies() {
		const path = selectedPath;
		const request = ++copiesRequest;
		copiesError = '';
		if (!path) {
			copies = [];
			copiesLoading = false;
			return;
		}
		copiesLoading = true;
		try {
			const list = await getRestoreCopies(path);
			if (request === copiesRequest) copies = list;
		} catch (e) {
			if (request === copiesRequest) {
				copies = [];
				copiesError = messageOf(e);
			}
		} finally {
			if (request === copiesRequest) copiesLoading = false;
		}
	}

	function choose(path: string) {
		selectedPath = path;
		restoredTo = '';
		loadSnapshots();
		loadCopies();
	}

	function openCreate() {
		if (!selected) return;
		newSnapshotName = '';
		createError = '';
		showCreate = true;
	}

	function closeCreate() {
		if (creating) return;
		showCreate = false;
	}

	async function handleCreate() {
		if (!selected || creating) return;
		creating = true;
		createError = '';
		try {
			// Without a name the server makes one (snap_<date>_<time>, in UTC).
			await createSnapshot(selected.path, newSnapshotName.trim() || undefined);
			creating = false;
			showCreate = false;
			await loadSnapshots();
		} catch (e) {
			// 400: the name is taken or invalid; 500: btrfs or the .snapshots folder failed
			createError = messageOf(e);
		} finally {
			creating = false;
		}
	}

	function askRestore(snap: SnapshotInfo) {
		snapshotToRestore = snap;
		restoreError = '';
	}

	function closeRestore() {
		if (restoring) return;
		snapshotToRestore = null;
	}

	async function handleRestore() {
		if (!snapshotToRestore || restoring) return;
		restoring = true;
		restoreError = '';
		try {
			const result = await restoreSnapshot(snapshotToRestore.path);
			restoredTo = result.target;
			restoring = false;
			snapshotToRestore = null;
			// The copy of a pool's snapshot is a new volume of the pool.
			await refresh();
		} catch (e) {
			restoreError = messageOf(e);
		} finally {
			restoring = false;
		}
	}

	function askDelete(snap: SnapshotInfo) {
		snapshotToDelete = snap;
		deleteError = '';
	}

	function closeDelete() {
		if (deleting) return;
		snapshotToDelete = null;
	}

	async function handleDelete() {
		if (!snapshotToDelete || deleting) return;
		deleting = true;
		deleteError = '';
		try {
			await deleteSnapshot(snapshotToDelete.id, snapshotToDelete.subvolume);
			deleting = false;
			snapshotToDelete = null;
			await loadSnapshots();
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}

	function askDeleteCopy(copy: RestoreCopy) {
		copyToDelete = copy;
		deleteCopyError = '';
	}

	function closeDeleteCopy() {
		if (deletingCopy) return;
		copyToDelete = null;
	}

	async function handleDeleteCopy() {
		if (!copyToDelete || deletingCopy) return;
		deletingCopy = true;
		deleteCopyError = '';
		try {
			await deleteRestoreCopy(copyToDelete.name, copyToDelete.subvolume);
			deletingCopy = false;
			copyToDelete = null;
			// A copy of a pool's snapshot is a volume of the pool as well: the picker loses it.
			await refresh();
		} catch (e) {
			// 409: in use, busy or holding subvolumes; 503: Docker did not answer
			deleteCopyError = messageOf(e);
		} finally {
			deletingCopy = false;
		}
	}
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">{$t('snapshots.title')}</h1>
		<div class="flex flex-wrap items-center gap-2">
			<Button icon={Plus} disabled={!selected} on:click={openCreate}>{$t('snapshots.create')}</Button>
			<Button variant="secondary" size="icon" disabled={busy} on:click={refresh}>
				<RefreshCw class="w-4 h-4 {busy ? 'animate-spin' : ''}" />
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
		</div>
	</div>

	{#if subvolumesError}
		<Alert variant="error" title={$t('snapshots.loadPoolsError')}>{subvolumesError}</Alert>
	{/if}

	{#if subvolumes.length > 0}
		<Card>
			<Select
				label={$t('snapshots.picker')}
				{options}
				bind:value={selectedPath}
				on:change={(e) => choose(e.detail.value)}
				class="max-w-md"
			/>
			{#each unreadVolumes as sv (sv.path)}
				<Alert variant="warning" title={$t('snapshots.volumesError', { pool: sv.pool })} class="mt-4">
					{sv.volumes_error}
					{$t('snapshots.volumesErrorHint')}
				</Alert>
			{/each}
		</Card>
	{:else if subvolumesLoading}
		<p class="loading-text">{$t('common.loading')}</p>
	{:else if !subvolumesError}
		<Card>
			<p class="text-muted">
				{$t('snapshots.noPool')}
				<a href="/storage" class="text-info hover:underline">{$t('snapshots.storagePage')}</a>.
			</p>
		</Card>
	{/if}

	{#if restoredTo}
		<Alert
			variant="success"
			title={$t('snapshots.restored')}
			dismissible
			on:dismiss={() => (restoredTo = '')}
		>
			{$t('snapshots.restoredAt')} <span class="mono-xs break-all">{restoredTo}</span>.
		</Alert>
	{/if}

	{#if selected}
		<Card>
			<h2 class="section-title mb-4">
				<Archive class="w-5 h-5" />
				{$t('snapshots.of', { name: label(selected) })}
			</h2>
			{#if snapshotsLoading}
				<p class="loading-text">{$t('snapshots.loading')}</p>
			{:else if snapshotsError}
				<Alert variant="error" title={$t('snapshots.loadError')}>{snapshotsError}</Alert>
			{:else if snapshots.length === 0}
				<EmptyState
					icon={Archive}
					title={$t('snapshots.empty')}
					description={$t('snapshots.emptyHint')}
				/>
			{:else}
				<div class="table-frame">
					<table class="table" aria-label={$t('snapshots.list')}>
						<thead class="table-header">
							<tr>
								<th>{$t('snapshots.name')}</th>
								<th>{$t('snapshots.created')}</th>
								<th><span class="sr-only">{$t('snapshots.actions')}</span></th>
							</tr>
						</thead>
						<tbody>
							{#each snapshots as snap (snap.path)}
								<tr class="table-row align-top">
									<td>
										<p class="font-medium break-all">{snap.name}</p>
										<p class="mono-xs text-muted break-all">{snap.path}</p>
									</td>
									<td class="whitespace-nowrap">{formatDate(snap.created)}</td>
									<td>
										<div class="flex justify-end gap-1">
											<Button variant="ghost" size="icon-sm" on:click={() => askRestore(snap)}>
												<RotateCcw class="w-4 h-4" />
												<span class="sr-only">{$t('snapshots.restoreOne', { name: snap.name })}</span>
											</Button>
											<Button variant="ghost" size="icon-sm" on:click={() => askDelete(snap)}>
												<Trash2 class="w-4 h-4 text-error" />
												<span class="sr-only">{$t('snapshots.deleteOne', { name: snap.name })}</span>
											</Button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card>
	{/if}

	{#if selected}
		<Card>
			<h2 class="section-title mb-2">
				<Copy class="w-5 h-5" />
				{$t('snapshots.copies.of', { name: label(selected) })}
			</h2>
			<p class="text-sm text-secondary mb-4">{$t('snapshots.copies.hint')}</p>
			{#if copiesLoading}
				<p class="loading-text">{$t('snapshots.copies.loading')}</p>
			{:else if copiesError}
				<Alert variant="error" title={$t('snapshots.copies.loadError')}>{copiesError}</Alert>
			{:else if copies.length === 0}
				<p class="text-sm text-muted">{$t('snapshots.copies.empty')}</p>
			{:else}
				<div class="table-frame">
					<table class="table" aria-label={$t('snapshots.copies.list')}>
						<thead class="table-header">
							<tr>
								<th>{$t('snapshots.name')}</th>
								<th>{$t('snapshots.copies.made')}</th>
								<th><span class="sr-only">{$t('snapshots.actions')}</span></th>
							</tr>
						</thead>
						<tbody>
							{#each copies as copy (copy.path)}
								<tr class="table-row align-top">
									<td>
										<p class="font-medium break-all">{copy.name}</p>
										<p class="mono-xs text-muted break-all">{copy.path}</p>
									</td>
									<td class="whitespace-nowrap">{formatDate(copy.created)}</td>
									<td>
										<div class="flex justify-end gap-1">
											<Button variant="ghost" size="icon-sm" on:click={() => askDeleteCopy(copy)}>
												<Trash2 class="w-4 h-4 text-error" />
												<span class="sr-only">{$t('snapshots.copies.deleteOne', { name: copy.name })}</span>
											</Button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
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

<Modal open={showCreate} title={$t('snapshots.new.title')} closable={!creating} on:close={closeCreate}>
	{#if selected}
		<form on:submit|preventDefault={handleCreate} class="space-y-4">
			<p class="text-sm text-secondary">
				{$t('snapshots.new.of')}
				<span class="mono-xs break-all">{selected.path}</span>{$t('snapshots.new.keptIn')}
				<span class="mono-xs break-all">{selected.path}/{SNAPSHOTS_FOLDER}</span>.
				{#if !selected.volume}
					{$t('snapshots.new.pool')}
				{/if}
			</p>
			<Input
				label={$t('snapshots.new.name')}
				bind:value={newSnapshotName}
				placeholder={$t('snapshots.new.namePlaceholder')}
				autocomplete="off"
				disabled={creating}
			/>
			{#if createError}
				<Alert variant="error" title={$t('snapshots.new.failed')}>{createError}</Alert>
			{/if}
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={creating} on:click={closeCreate}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" icon={Plus} loading={creating} on:click={handleCreate}>
			{$t('snapshots.new.submit')}
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={snapshotToRestore !== null}
	title={$t('snapshots.restore.title')}
	closable={!restoring}
	on:close={closeRestore}
>
	{#if snapshotToRestore}
		<div class="space-y-4">
			<p class="text-sm">
				{$t('snapshots.restore.before')}
				<span class="font-semibold break-all">{snapshotToRestore.name}</span>{$t('snapshots.restore.copy')}
				<span class="mono-xs">{RESTORE_PREFIX}{$t('snapshots.dateTime')}</span>
				{$t('snapshots.restore.madeIn')}
				<span class="mono-xs break-all">{snapshotToRestore.subvolume}</span>{$t('snapshots.restore.after')}
			</p>
			{#if restoreInto && !restoreInto.volume}
				<p class="text-sm text-secondary">{$t('snapshots.restore.poolCopy')}</p>
			{:else if restoreInto}
				<p class="text-sm text-secondary">{$t('snapshots.restore.volumeCopy')}</p>
			{/if}
			{#if restoreError}
				<Alert variant="error" title={$t('snapshots.restore.failed')}>{restoreError}</Alert>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={restoring} on:click={closeRestore}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" icon={RotateCcw} loading={restoring} on:click={handleRestore}>
			{$t('snapshots.restore.submit')}
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={snapshotToDelete !== null}
	title={$t('snapshots.delete.title')}
	danger
	closable={!deleting}
	on:close={closeDelete}
>
	{#if snapshotToDelete}
		<div class="space-y-4">
			<p class="text-sm">
				{$t('snapshots.delete.before')}
				<span class="font-semibold break-all">{snapshotToDelete.name}</span>
				(<span class="mono-xs break-all">{snapshotToDelete.path}</span>){$t('snapshots.delete.after')}
			</p>
			{#if deleteError}
				<Alert variant="error" title={$t('snapshots.delete.failed')}>{deleteError}</Alert>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={deleting} on:click={closeDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" icon={Trash2} loading={deleting} on:click={handleDelete}>
			{$t('snapshots.delete.submit')}
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={copyToDelete !== null}
	title={$t('snapshots.copies.delete.title')}
	danger
	closable={!deletingCopy}
	on:close={closeDeleteCopy}
>
	{#if copyToDelete}
		<div class="space-y-4">
			<p class="text-sm">
				{$t('snapshots.copies.delete.before')}
				<span class="font-semibold break-all">{copyToDelete.name}</span>
				(<span class="mono-xs break-all">{copyToDelete.path}</span>){$t('snapshots.copies.delete.after')}
			</p>
			<p class="text-sm text-secondary">{$t('snapshots.copies.delete.inUse')}</p>
			{#if deleteCopyError}
				<Alert variant="error" title={$t('snapshots.copies.delete.failed')}>{deleteCopyError}</Alert>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={deletingCopy} on:click={closeDeleteCopy}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" icon={Trash2} loading={deletingCopy} on:click={handleDeleteCopy}>
			{$t('snapshots.copies.delete.submit')}
		</Button>
	</svelte:fragment>
</Modal>

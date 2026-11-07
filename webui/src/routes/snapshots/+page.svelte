<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getSubvolumes,
		getSnapshots,
		createSnapshot,
		deleteSnapshot,
		restoreSnapshot,
		type SnapshotInfo,
		type Subvolume
	} from '$lib/api/snapshots';
	import { Archive, Plus, Trash2, RotateCcw, RefreshCw } from 'lucide-svelte';
	import { Button, Card, Modal, Alert, Input, Select, EmptyState } from '$lib/components/ui';

	// The mounted pools and their volumes (ADR-0234), and the one whose snapshots are listed.
	let subvolumes: Subvolume[] = [];
	let subvolumesLoading = true;
	let subvolumesError = '';
	let selectedPath = '';

	let snapshots: SnapshotInfo[] = [];
	let snapshotsLoading = false;
	let snapshotsError = '';
	// Only the latest request updates the list: another pool or volume may have been chosen meanwhile.
	let snapshotsRequest = 0;

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

	$: options = subvolumes.map((sv) => ({ value: sv.path, label: label(sv) }));
	// Pools offered without their volumes, which could not be read (a disk that dropped off, say).
	$: unreadVolumes = subvolumes.filter((sv) => sv.volumes_error);
	$: selected = subvolumes.find((sv) => sv.path === selectedPath) ?? null;
	$: busy = subvolumesLoading || snapshotsLoading;
	// The pool or volume that a restore makes its copy in.
	$: restoreInto = snapshotToRestore
		? (subvolumes.find((sv) => sv.path === snapshotToRestore?.subvolume) ?? null)
		: null;

	onMount(refresh);

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	function label(sv: Subvolume): string {
		return sv.volume ? `${sv.pool}/${sv.volume} (Volume)` : `${sv.pool} (Pool)`;
	}

	// The server gives UTC times with their offset; the browser shows them in its own time zone.
	function formatDate(value: string): string {
		if (!value) return '-';
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
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
		await loadSnapshots();
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

	function choose(path: string) {
		selectedPath = path;
		restoredTo = '';
		loadSnapshots();
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
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">Snapshots</h1>
		<div class="flex items-center gap-2">
			<Button icon={Plus} disabled={!selected} on:click={openCreate}>Create Snapshot</Button>
			<Button variant="secondary" size="icon" disabled={busy} on:click={refresh}>
				<RefreshCw class="w-4 h-4 {busy ? 'animate-spin' : ''}" />
				<span class="sr-only">Refresh</span>
			</Button>
		</div>
	</div>

	{#if subvolumesError}
		<Alert variant="error" title="Could not load the pools and volumes">{subvolumesError}</Alert>
	{/if}

	{#if subvolumes.length > 0}
		<Card>
			<Select
				label="Pool or volume"
				{options}
				bind:value={selectedPath}
				on:change={(e) => choose(e.detail.value)}
				class="max-w-md"
			/>
			{#each unreadVolumes as sv (sv.path)}
				<Alert variant="warning" title="The volumes of {sv.pool} could not be read" class="mt-4">
					{sv.volumes_error} The pool itself is offered; its volumes are missing from the list.
				</Alert>
			{/each}
		</Card>
	{:else if subvolumesLoading}
		<p class="loading-text">Loading...</p>
	{:else if !subvolumesError}
		<Card>
			<p class="text-muted">
				No pool is mounted. Mount a pool on the <a href="/storage" class="text-info hover:underline"
					>Storage page</a
				>.
			</p>
		</Card>
	{/if}

	{#if restoredTo}
		<Alert variant="success" title="Snapshot restored" dismissible on:dismiss={() => (restoredTo = '')}>
			The copy is at <span class="mono-xs break-all">{restoredTo}</span>.
		</Alert>
	{/if}

	{#if selected}
		<Card>
			<h2 class="section-title mb-4">
				<Archive class="w-5 h-5" />
				Snapshots of {label(selected)}
			</h2>
			{#if snapshotsLoading}
				<p class="loading-text">Loading snapshots...</p>
			{:else if snapshotsError}
				<Alert variant="error" title="Could not load the snapshots">{snapshotsError}</Alert>
			{:else if snapshots.length === 0}
				<EmptyState
					icon={Archive}
					title="No snapshots yet"
					description="A snapshot keeps the files as they are now, read-only."
				/>
			{:else}
				<div class="overflow-x-auto">
					<table class="table" aria-label="Snapshots">
						<thead class="table-header">
							<tr>
								<th>Name</th>
								<th>Created</th>
								<th><span class="sr-only">Actions</span></th>
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
												<span class="sr-only">Restore snapshot {snap.name}</span>
											</Button>
											<Button variant="ghost" size="icon-sm" on:click={() => askDelete(snap)}>
												<Trash2 class="w-4 h-4 text-error" />
												<span class="sr-only">Delete snapshot {snap.name}</span>
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

	<Card>
		<h2 class="section-title mb-2">About Snapshots</h2>
		<p class="text-sm text-secondary">
			A snapshot is a read-only copy of a pool or a volume at one point in time, kept in its
			<span class="mono-xs">.snapshots</span> folder. btrfs makes it at once and then stores only
			what changes. A snapshot of a pool does not include the files in its volumes: take
			snapshots of each volume as well. Restoring makes a writable copy next to the original and
			overwrites nothing. Scheduled snapshots (System, Scheduler) are made of pools only and are
			named <span class="mono-xs">auto_&lt;date&gt;_&lt;time&gt;</span>. The date and time in the
			names that UNOS gives, <span class="mono-xs">snap_</span>,
			<span class="mono-xs">restore_</span> and <span class="mono-xs">auto_</span>, are in UTC;
			Created shows the time in your browser's time zone.
		</p>
	</Card>
</div>

<Modal open={showCreate} title="Create Snapshot" closable={!creating} on:close={closeCreate}>
	{#if selected}
		<form on:submit|preventDefault={handleCreate} class="space-y-4">
			<p class="text-sm text-secondary">
				A read-only snapshot of <span class="mono-xs break-all">{selected.path}</span>, kept in
				<span class="mono-xs break-all">{selected.path}/.snapshots</span>.
				{#if !selected.volume}
					It does not include the files in the pool's volumes: take snapshots of each volume as
					well. Scheduled snapshot tasks take pools only.
				{/if}
			</p>
			<Input
				label="Snapshot name (optional)"
				bind:value={newSnapshotName}
				placeholder="Made from the date and time in UTC when empty"
				autocomplete="off"
				disabled={creating}
			/>
			{#if createError}
				<Alert variant="error" title="Snapshot was not created">{createError}</Alert>
			{/if}
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={creating} on:click={closeCreate}>
			Cancel
		</Button>
		<Button class="flex-1" icon={Plus} loading={creating} on:click={handleCreate}>Create</Button>
	</svelte:fragment>
</Modal>

<Modal open={snapshotToRestore !== null} title="Restore Snapshot" closable={!restoring} on:close={closeRestore}>
	{#if snapshotToRestore}
		<div class="space-y-4">
			<p class="text-sm">
				Restore snapshot <span class="font-semibold break-all">{snapshotToRestore.name}</span>? A
				writable copy of it, <span class="mono-xs">restore_&lt;date&gt;_&lt;time&gt;</span> with the
				date and time in UTC, is made in
				<span class="mono-xs break-all">{snapshotToRestore.subvolume}</span>. Nothing is overwritten.
			</p>
			{#if restoreInto && !restoreInto.volume}
				<p class="text-sm text-secondary">
					The copy is a volume of the pool: delete it with the pool's Volumes on the Storage page
					when it is no longer needed.
				</p>
			{:else if restoreInto}
				<p class="text-sm text-secondary">
					The copy is a subvolume inside the volume: it cannot be deleted in the web interface yet,
					and the volume cannot be deleted while it holds the copy.
				</p>
			{/if}
			{#if restoreError}
				<Alert variant="error" title="Snapshot was not restored">{restoreError}</Alert>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={restoring} on:click={closeRestore}>
			Cancel
		</Button>
		<Button class="flex-1" icon={RotateCcw} loading={restoring} on:click={handleRestore}>
			Restore
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={snapshotToDelete !== null}
	title="Delete Snapshot"
	danger
	closable={!deleting}
	on:close={closeDelete}
>
	{#if snapshotToDelete}
		<div class="space-y-4">
			<p class="text-sm">
				Delete snapshot <span class="font-semibold break-all">{snapshotToDelete.name}</span>
				(<span class="mono-xs break-all">{snapshotToDelete.path}</span>)? This cannot be undone.
			</p>
			{#if deleteError}
				<Alert variant="error" title="Snapshot was not deleted">{deleteError}</Alert>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={deleting} on:click={closeDelete}>
			Cancel
		</Button>
		<Button variant="danger" class="flex-1" icon={Trash2} loading={deleting} on:click={handleDelete}>
			Delete Snapshot
		</Button>
	</svelte:fragment>
</Modal>

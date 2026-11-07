<script lang="ts">
	import { onMount } from 'svelte';
	import { FolderOpen, Network, Plus, Trash2 } from 'lucide-svelte';
	import {
		clearImportReport,
		createNfsExport,
		deleteNfsExport,
		deleteSmbShare,
		getNfsExports,
		getSharePaths,
		getSmbShares,
		getSmbStatus,
		updateSmbShare,
		type NfsExport,
		type SharePath,
		type SmbServiceStatus,
		type SmbShare
	} from '$lib/api/shares';
	import { ApiError } from '$lib/api/client';
	import { Alert, Button, Card, EmptyState, Input, Modal, Select } from '$lib/components/ui';
	import { RepairPermissionsModal, ShareCard, ShareFormModal } from '$lib/components/shares';
	import { NETWORK_NOTE } from '$lib/components/shares/texts';

	const tabs = [
		{ id: 'smb', label: 'SMB' },
		{ id: 'nfs', label: 'NFS' }
	] as const;
	type TabId = (typeof tabs)[number]['id'];

	let activeTab: TabId = 'smb';
	// The NAS's address as this browser reaches it, for the connection hints.
	let host = '';

	let loading = true;
	let paths: SharePath[] = [];
	let pathsError = '';

	// ---- SMB ----
	let shares: SmbShare[] = [];
	let sharesError = '';
	let status: SmbServiceStatus | null = null;
	let statusError = '';

	// Outcome of the last change: shown until dismissed.
	let notice = '';
	let warnings: string[] = [];
	let actionError = '';

	// Create / edit dialog: mounted while open.
	let form: { share: SmbShare | null } | null = null;
	let repairing: string | null = null;
	let shareToDelete: SmbShare | null = null;
	let deleting = false;
	let deleteError = '';
	let dismissing: Record<string, boolean> = {};

	// ---- NFS ----
	let nfsExports: NfsExport[] = [];
	let nfsError = '';
	let nfsFormOpen = false;
	let nfsSaving = false;
	let nfsFormError = '';
	let nfsForm = { path: '', clients: '*', options: 'rw,sync,no_subtree_check' };
	let exportToDelete: NfsExport | null = null;
	let nfsDeleting = false;
	let nfsDeleteError = '';

	onMount(() => {
		host = window.location.hostname;
		void loadAll();
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function loadAll() {
		loading = true;
		// The share list imports the old smb.conf on first use, so the status
		// (with the import report) is read after it.
		await Promise.all([loadShares().then(loadStatus), loadPaths(), loadNfs()]);
		loading = false;
	}

	async function loadShares() {
		try {
			shares = await getSmbShares();
			sharesError = '';
		} catch (e) {
			sharesError = messageOf(e);
		}
	}

	async function loadStatus() {
		try {
			status = await getSmbStatus();
			statusError = '';
		} catch (e) {
			status = null;
			statusError = messageOf(e);
		}
	}

	async function loadPaths() {
		try {
			paths = await getSharePaths();
			pathsError = '';
		} catch (e) {
			paths = [];
			pathsError = messageOf(e);
		}
	}

	async function loadNfs() {
		try {
			nfsExports = await getNfsExports();
			nfsError = '';
		} catch (e) {
			nfsError = messageOf(e);
		}
	}

	async function reloadSmb() {
		await loadShares();
		await loadStatus();
	}

	/**
	 * Keyboard of the SMB / NFS tabs: the arrow keys move to the previous or
	 * next tab (wrapping around), Home and End to the first or last; the tab
	 * reached is selected and focused. Only the selected tab is in the Tab order.
	 */
	function tabKeydown(event: KeyboardEvent, from: TabId) {
		const index = tabs.findIndex((tab) => tab.id === from);
		let next: number;
		if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
		else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = tabs.length - 1;
		else return;
		event.preventDefault();
		activeTab = tabs[next].id;
		document.getElementById(`shares-tab-${activeTab}`)?.focus();
	}

	function showOutcome(message: string, newWarnings: string[] | undefined) {
		notice = message;
		warnings = newWarnings ?? [];
		actionError = '';
	}

	// ---- SMB shares ----

	function openCreate() {
		form = { share: null };
	}

	function openEdit(share: SmbShare) {
		form = { share };
	}

	function closeForm(event: CustomEvent<{ stale: boolean }>) {
		form = null;
		// The server refused a change (e.g. the share was changed meanwhile):
		// show the shares as they are now, so that the next edit starts from them.
		if (event.detail.stale) void reloadSmb();
	}

	async function handleSaved(event: CustomEvent<{ share: SmbShare; created: boolean }>) {
		const { share, created } = event.detail;
		form = null;
		showOutcome(created ? `Share "${share.name}" created` : `Share "${share.name}" saved`, share.warnings);
		await reloadSmb();
	}

	function openDelete(share: SmbShare) {
		shareToDelete = share;
		deleteError = '';
	}

	function closeDelete() {
		if (deleting) return;
		shareToDelete = null;
		deleteError = '';
	}

	async function confirmDelete() {
		if (!shareToDelete) return;
		const name = shareToDelete.name;
		deleting = true;
		deleteError = '';
		try {
			const result = await deleteSmbShare(name);
			deleting = false;
			closeDelete();
			showOutcome(`Share "${name}" deleted`, result.warnings);
			await reloadSmb();
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}

	async function dismissNotes(share: SmbShare) {
		dismissing = { ...dismissing, [share.name]: true };
		actionError = '';
		try {
			const updated = await updateSmbShare(share.name, {
				import_notes: [],
				expected_version: share.version
			});
			shares = shares.map((s) => (s.name === share.name ? updated : s));
			if (updated.warnings?.length) warnings = updated.warnings;
		} catch (e) {
			actionError = messageOf(e);
			// 409: the share was changed meanwhile; show it as it is now.
			if (e instanceof ApiError && e.status === 409) await loadShares();
		} finally {
			dismissing = { ...dismissing, [share.name]: false };
		}
	}

	async function dismissImportReport() {
		actionError = '';
		try {
			await clearImportReport();
			if (status) status = { ...status, import_report: [] };
		} catch (e) {
			actionError = messageOf(e);
		}
	}

	// ---- NFS exports ----

	$: pathOptions = paths.map((p) => ({ value: p.path, label: p.label }));

	function openNfsCreate() {
		nfsForm = { path: paths[0]?.path ?? '', clients: '*', options: 'rw,sync,no_subtree_check' };
		nfsFormError = '';
		nfsFormOpen = true;
	}

	function closeNfsCreate() {
		if (!nfsSaving) nfsFormOpen = false;
	}

	async function handleNfsCreate() {
		if (!nfsForm.path || nfsSaving) return;
		nfsSaving = true;
		nfsFormError = '';
		try {
			await createNfsExport(nfsForm);
			nfsSaving = false;
			nfsFormOpen = false;
			await loadNfs();
		} catch (e) {
			nfsFormError = messageOf(e);
		} finally {
			nfsSaving = false;
		}
	}

	function openNfsDelete(exp: NfsExport) {
		exportToDelete = exp;
		nfsDeleteError = '';
	}

	function closeNfsDelete() {
		if (!nfsDeleting) exportToDelete = null;
	}

	async function confirmNfsDelete() {
		if (!exportToDelete) return;
		nfsDeleting = true;
		nfsDeleteError = '';
		try {
			await deleteNfsExport(exportToDelete.path);
			nfsDeleting = false;
			exportToDelete = null;
			await loadNfs();
		} catch (e) {
			nfsDeleteError = messageOf(e);
		} finally {
			nfsDeleting = false;
		}
	}
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">Shares</h1>
		{#if activeTab === 'smb'}
			<Button icon={Plus} disabled={loading || paths.length === 0} on:click={openCreate}>
				Add SMB share
			</Button>
		{:else}
			<Button icon={Plus} disabled={loading || paths.length === 0} on:click={openNfsCreate}>
				Add NFS export
			</Button>
		{/if}
	</div>

	<div class="flex gap-1 p-1 bg-surface-elevated rounded-lg w-fit" role="tablist" aria-label="Share types">
		{#each tabs as tab (tab.id)}
			<button
				type="button"
				role="tab"
				id="shares-tab-{tab.id}"
				aria-selected={activeTab === tab.id}
				aria-controls="shares-panel"
				tabindex={activeTab === tab.id ? 0 : -1}
				class="px-4 py-2 rounded-md text-sm font-medium transition-colors {activeTab === tab.id
					? 'bg-blue-600 text-white'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = tab.id)}
				on:keydown={(event) => tabKeydown(event, tab.id)}
			>
				{tab.label}
			</button>
		{/each}
	</div>

	<!-- What the selected tab shows. -->
	<div
		role="tabpanel"
		id="shares-panel"
		aria-labelledby="shares-tab-{activeTab}"
		tabindex="0"
		class="space-y-6"
	>
		{#if !loading && pathsError}
			<Alert variant="error" title="The pools and volumes could not be read">{pathsError}</Alert>
		{:else if !loading && paths.length === 0}
			<Alert variant="warning">
				No storage pools available. Create a storage pool first before adding shares.
			</Alert>
		{/if}

		{#if notice}
			<Alert variant="success" dismissible on:dismiss={() => (notice = '')}>{notice}</Alert>
		{/if}
		{#if warnings.length > 0}
			<Alert variant="warning" dismissible on:dismiss={() => (warnings = [])}>
				{#each warnings as warning}
					<p>{warning}</p>
				{/each}
			</Alert>
		{/if}
		{#if actionError}
			<Alert variant="error" dismissible on:dismiss={() => (actionError = '')}>{actionError}</Alert>
		{/if}

		{#if loading}
			<p class="loading-text">Loading...</p>
		{:else if activeTab === 'smb'}
			{#if status}
				{#if status.problems.length > 0}
					<Alert variant="error">
						{#each status.problems as problem}
							<p>{problem}</p>
						{/each}
					</Alert>
				{/if}
				{#if status.samba_tools_missing && status.samba_tools_missing.length > 0}
					<Alert variant="warning">
						<p>Samba is not installed on the host: shares and SMB accounts cannot be used</p>
						<p class="text-sm mt-1">Missing: {status.samba_tools_missing.join(', ')}</p>
					</Alert>
				{/if}
				{#if status.guest_shares.length > 0}
					<Alert variant="warning">
						Guest access is on for: {status.guest_shares.join(', ')}. Anyone on your network can read
						these shares without a password, and connections to this server are not required to be
						signed.
					</Alert>
				{/if}
				{#if status.import_report.length > 0}
					<Alert
						variant="info"
						title="Shares imported from the previous smb.conf"
						dismissible
						on:dismiss={dismissImportReport}
					>
						{#each status.import_report as line}
							<p>{line}</p>
						{/each}
					</Alert>
				{/if}
				{#if status.unmanaged_samba_users.length > 0}
					<Alert variant="info">
						These Samba accounts were not created by UNOS and cannot use UNOS shares: {status.unmanaged_samba_users.join(
							', '
						)}
					</Alert>
				{/if}
				{#if status.active_smb_users === 0}
					<Alert variant="info">
						Nobody can connect yet: allow SMB on the Users page, then grant access here.
					</Alert>
				{/if}
			{:else if statusError}
				<Alert variant="error" title="The SMB status could not be read">{statusError}</Alert>
			{/if}

			<Card>
				<h2 class="section-title mb-4">
					<FolderOpen class="w-5 h-5" />
					SMB shares
				</h2>

				{#if sharesError}
					<Alert variant="error" title="The shares could not be loaded">{sharesError}</Alert>
				{:else if shares.length === 0}
					<EmptyState
						icon={FolderOpen}
						title="No SMB shares yet"
						description="Share a folder of a pool, or a volume, with the users you choose."
					/>
				{:else}
					<div class="space-y-4">
						{#each shares as share (share.name)}
							<ShareCard
								{share}
								{host}
								dismissing={dismissing[share.name] ?? false}
								on:edit={(event) => openEdit(event.detail)}
								on:repair={(event) => (repairing = event.detail.name)}
								on:delete={(event) => openDelete(event.detail)}
								on:dismissNotes={(event) => dismissNotes(event.detail)}
							/>
						{/each}
					</div>
				{/if}

				<p class="form-hint mt-4">{NETWORK_NOTE}</p>
			</Card>
		{:else}
			<Card>
				<h2 class="section-title mb-4">
					<Network class="w-5 h-5" />
					NFS exports
				</h2>

				{#if nfsError}
					<Alert variant="error" title="The NFS exports could not be loaded">{nfsError}</Alert>
				{:else if nfsExports.length === 0}
					<p class="text-muted">No NFS exports configured</p>
				{:else}
					<ul class="space-y-2">
						{#each nfsExports as exp (exp.path)}
							<li class="card-nested flex items-center justify-between gap-2">
								<div class="min-w-0">
									<p class="mono break-all">{exp.path}</p>
									<p class="text-sm text-secondary">{exp.clients} ({exp.options})</p>
								</div>
								<Button variant="ghost" size="icon-sm" on:click={() => openNfsDelete(exp)}>
									<Trash2 class="w-4 h-4 text-error" />
									<span class="sr-only">Delete export {exp.path}</span>
								</Button>
							</li>
						{/each}
					</ul>
				{/if}
			</Card>
		{/if}
	</div>
</div>

{#if form}
	<ShareFormModal
		share={form.share}
		{paths}
		on:close={closeForm}
		on:saved={handleSaved}
	/>
{/if}

{#if repairing}
	<RepairPermissionsModal name={repairing} on:close={() => (repairing = null)} on:repaired={reloadSmb} />
{/if}

<Modal
	open={shareToDelete !== null}
	title="Delete share"
	size="md"
	danger
	closable={!deleting}
	on:close={closeDelete}
>
	{#if shareToDelete}
		<p>
			Delete share <strong class="text-content">{shareToDelete.name}</strong>? The folder and its
			files are kept. Open connections to the share are closed.
		</p>
		{#if deleteError}
			<Alert variant="error" title="Share was not deleted">{deleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={deleting} on:click={closeDelete}>
			Cancel
		</Button>
		<Button variant="danger" class="flex-1" loading={deleting} on:click={confirmDelete}>
			Delete share
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={nfsFormOpen}
	title="Add NFS export"
	size="md"
	closable={!nfsSaving}
	on:close={closeNfsCreate}
>
	<form on:submit|preventDefault={handleNfsCreate} class="space-y-4">
		<Select id="nfs-path" label="Path" bind:value={nfsForm.path} options={pathOptions} required />
		<Input
			id="nfs-clients"
			label="Allowed clients"
			bind:value={nfsForm.clients}
			placeholder="* or 192.168.1.0/24"
		/>
		<Input
			id="nfs-options"
			label="Options"
			bind:value={nfsForm.options}
			placeholder="rw,sync,no_subtree_check"
		/>
		{#if nfsFormError}
			<Alert variant="error" title="Export was not created">{nfsFormError}</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={nfsSaving} on:click={closeNfsCreate}>
			Cancel
		</Button>
		<Button
			class="flex-1"
			loading={nfsSaving}
			disabled={!nfsForm.path}
			on:click={handleNfsCreate}
		>
			Create export
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={exportToDelete !== null}
	title="Delete NFS export"
	size="md"
	danger
	closable={!nfsDeleting}
	on:close={closeNfsDelete}
>
	{#if exportToDelete}
		<p>
			Delete the export of <span class="mono break-all">{exportToDelete.path}</span>? NFS clients
			lose access to it.
		</p>
		{#if nfsDeleteError}
			<Alert variant="error" title="Export was not deleted">{nfsDeleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={nfsDeleting} on:click={closeNfsDelete}>
			Cancel
		</Button>
		<Button variant="danger" class="flex-1" loading={nfsDeleting} on:click={confirmNfsDelete}>
			Delete export
		</Button>
	</svelte:fragment>
</Modal>

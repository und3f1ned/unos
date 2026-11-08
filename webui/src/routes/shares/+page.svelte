<script lang="ts">
	import { onMount } from 'svelte';
	import { FolderOpen, Network, Plus, Trash2 } from 'lucide-svelte';
	import {
		clearImportReport,
		deleteNfsExport,
		deleteSmbShare,
		getNfsExports,
		getNfsStatus,
		getSharePaths,
		getSmbShares,
		getSmbStatus,
		updateSmbShare,
		type NfsExport,
		type NfsExportClient,
		type NfsServiceStatus,
		type SharePath,
		type SmbServiceStatus,
		type SmbShare
	} from '$lib/api/shares';
	import { ApiError } from '$lib/api/client';
	import { Alert, Button, Card, EmptyState, Modal } from '$lib/components/ui';
	import {
		NfsExportFormModal,
		RepairPermissionsModal,
		ShareCard,
		ShareFormModal
	} from '$lib/components/shares';
	import { t } from '$lib/i18n';

	// i18n-ignore: the names of the protocols, the same in every language
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
	let nfsStatus: NfsServiceStatus | null = null;
	let nfsStatusError = '';
	// The Add NFS export dialog: mounted while open.
	let nfsFormOpen = false;
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
		await Promise.all([loadNfsExports(), loadNfsStatus()]);
	}

	async function loadNfsExports() {
		try {
			nfsExports = await getNfsExports();
			nfsError = '';
		} catch (e) {
			nfsError = messageOf(e);
		}
	}

	async function loadNfsStatus() {
		try {
			nfsStatus = await getNfsStatus();
			nfsStatusError = '';
		} catch (e) {
			nfsStatus = null;
			nfsStatusError = messageOf(e);
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
		showOutcome(
			created
				? $t('shares.smb.created', { name: share.name })
				: $t('shares.smb.saved', { name: share.name }),
			share.warnings
		);
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
			showOutcome($t('shares.smb.deleted', { name }), result.warnings);
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

	/** What a client may do, in the words of the SMB access levels. */
	function nfsAccessLabel(client: NfsExportClient): string {
		if (client.access === 'write') return $t('shares.level.write');
		if (client.access === 'read') return $t('shares.level.read');
		return $t('shares.nfs.perFlavor');
	}

	async function handleNfsSaved(event: CustomEvent<{ exp: NfsExport }>) {
		const { exp } = event.detail;
		nfsFormOpen = false;
		showOutcome($t('shares.nfs.created', { path: exp.path }), exp.warnings);
		await loadNfs();
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
		const path = exportToDelete.path;
		nfsDeleting = true;
		nfsDeleteError = '';
		try {
			const result = await deleteNfsExport(path);
			nfsDeleting = false;
			exportToDelete = null;
			showOutcome($t('shares.nfs.deleted', { path }), result.warnings);
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
		<h1 class="page-title">{$t('shares.title')}</h1>
		{#if activeTab === 'smb'}
			<Button icon={Plus} disabled={loading || paths.length === 0} on:click={openCreate}>
				{$t('shares.smb.add')}
			</Button>
		{:else}
			<Button icon={Plus} disabled={loading || paths.length === 0} on:click={() => (nfsFormOpen = true)}>
				{$t('shares.nfs.add')}
			</Button>
		{/if}
	</div>

	<div
		class="flex gap-1 p-1 bg-surface-elevated rounded-lg w-fit"
		role="tablist"
		aria-label={$t('shares.tabs.label')}
	>
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
			<Alert variant="error" title={$t('shares.pathsError')}>{pathsError}</Alert>
		{:else if !loading && paths.length === 0}
			<Alert variant="warning">{$t('shares.noPools')}</Alert>
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
			<p class="loading-text">{$t('common.loading')}</p>
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
						<p>{$t('shares.status.sambaMissing')}</p>
						<p class="text-sm mt-1">
							{$t('shares.status.missing', { tools: status.samba_tools_missing.join(', ') })}
						</p>
					</Alert>
				{/if}
				{#if status.guest_shares.length > 0}
					<Alert variant="warning">
						{$t('shares.status.guestShares', { shares: status.guest_shares.join(', ') })}
					</Alert>
				{/if}
				{#if status.import_report.length > 0}
					<Alert
						variant="info"
						title={$t('shares.status.imported')}
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
						{$t('shares.status.unmanagedUsers', { users: status.unmanaged_samba_users.join(', ') })}
					</Alert>
				{/if}
				{#if status.active_smb_users === 0}
					<Alert variant="info">{$t('shares.status.nobody')}</Alert>
				{/if}
			{:else if statusError}
				<Alert variant="error" title={$t('shares.status.error')}>{statusError}</Alert>
			{/if}

			<Card>
				<h2 class="section-title mb-4">
					<FolderOpen class="w-5 h-5" />
					{$t('shares.smb.title')}
				</h2>

				{#if sharesError}
					<Alert variant="error" title={$t('shares.smb.loadError')}>{sharesError}</Alert>
				{:else if shares.length === 0}
					<EmptyState
						icon={FolderOpen}
						title={$t('shares.smb.empty')}
						description={$t('shares.smb.emptyHint')}
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

				<p class="form-hint mt-4">{$t('shares.smb.networkNote')}</p>
			</Card>
		{:else}
			{#if nfsStatus}
				{#if nfsStatus.server_missing}
					<Alert variant="warning">
						<p>{$t('shares.nfs.serverMissing')}</p>
						<p class="text-sm mt-1">{$t('shares.nfs.installServer')}</p>
					</Alert>
				{/if}
				{#if nfsStatus.problems.length > 0}
					<!-- Lines that exportfs cannot read, and exports of UNOS below folders that other
					     accounts can change: each text names the file or the folder. -->
					<Alert variant="error">
						{#each nfsStatus.problems as problem}
							<p>{problem}</p>
						{/each}
					</Alert>
				{/if}
			{:else if nfsStatusError}
				<Alert variant="error" title={$t('shares.nfs.statusError')}>{nfsStatusError}</Alert>
			{/if}

			<Card>
				<h2 class="section-title mb-4">
					<Network class="w-5 h-5" />
					{$t('shares.nfs.title')}
				</h2>

				{#if nfsError}
					<Alert variant="error" title={$t('shares.nfs.loadError')}>{nfsError}</Alert>
				{:else if nfsExports.length === 0}
					<EmptyState
						icon={Network}
						title={$t('shares.nfs.empty')}
						description={$t('shares.nfs.emptyHint')}
					/>
				{:else}
					<ul class="space-y-2" aria-label={$t('shares.nfs.title')}>
						<!-- Not keyed: two exports can start on the same line of a file, and nothing
						     else of an entry is unique. -->
						{#each nfsExports as exp}
							<li class="card-nested flex items-start justify-between gap-2">
								<div class="min-w-0 space-y-1">
									<p class="mono break-all">{exp.path}</p>
									<ul class="space-y-0.5" aria-label={$t('shares.nfs.clientsOf', { path: exp.path })}>
										{#each exp.clients as client}
											<li class="text-sm">
												<span class="mono break-all">{client.client}</span>
												<span class="text-secondary">· {nfsAccessLabel(client)}</span>
												{#if client.options.length > 0}
													<span class="mono-xs text-muted break-all">({client.options.join(',')})</span>
												{/if}
											</li>
										{/each}
									</ul>
									{#if !exp.managed}
										<p class="text-xs text-muted">
											{$t('shares.nfs.notAddedInUnos', { file: exp.file, line: exp.line })}
										</p>
									{/if}
								</div>
								{#if exp.managed}
									<Button variant="ghost" size="icon-sm" on:click={() => openNfsDelete(exp)}>
										<Trash2 class="w-4 h-4 text-error" />
										<span class="sr-only">{$t('shares.nfs.deleteOne', { path: exp.path })}</span>
									</Button>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}

				<p class="form-hint mt-4">{$t('shares.nfs.note')}</p>
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
	title={$t('shares.smb.deleteTitle')}
	size="md"
	danger
	closable={!deleting}
	on:close={closeDelete}
>
	{#if shareToDelete}
		<p>
			{$t('shares.smb.deleteQuestion')}
			<strong class="text-content">{shareToDelete.name}</strong>? {$t('shares.smb.deleteNote')}
		</p>
		{#if deleteError}
			<Alert variant="error" title={$t('shares.smb.notDeleted')}>{deleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={deleting} on:click={closeDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={deleting} on:click={confirmDelete}>
			{$t('shares.smb.delete')}
		</Button>
	</svelte:fragment>
</Modal>

{#if nfsFormOpen}
	<NfsExportFormModal {paths} on:close={() => (nfsFormOpen = false)} on:saved={handleNfsSaved} />
{/if}

<Modal
	open={exportToDelete !== null}
	title={$t('shares.nfs.deleteTitle')}
	size="md"
	danger
	closable={!nfsDeleting}
	on:close={closeNfsDelete}
>
	{#if exportToDelete}
		<p>
			{$t('shares.nfs.deleteQuestion')}
			<span class="mono break-all">{exportToDelete.path}</span>? {$t('shares.nfs.deleteNote')}
		</p>
		{#if nfsDeleteError}
			<Alert variant="error" title={$t('shares.nfs.notDeleted')}>{nfsDeleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={nfsDeleting} on:click={closeNfsDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={nfsDeleting} on:click={confirmNfsDelete}>
			{$t('shares.nfs.delete')}
		</Button>
	</svelte:fragment>
</Modal>

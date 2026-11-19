<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { FolderOpen, Network, Plus, Trash2 } from 'lucide-svelte';
	import {
		clearImportReport,
		deleteNfsExport,
		deleteSmbShare,
		deleteSmbShares,
		getNfsExports,
		getNfsStatus,
		getSharePaths,
		getSmbShares,
		getSmbStatus,
		updateSmbShare,
		type NfsExport,
		type NfsExportClient,
		type NfsServiceStatus,
		type RepairResult,
		type SharePath,
		type SmbServiceStatus,
		type SmbShare,
		type SmbShareDeleteResult
	} from '$lib/api/shares';
	import { ApiError } from '$lib/api/client';
	import { Alert, Button, Card, EmptyState, Modal } from '$lib/components/ui';
	import {
		IscsiTab,
		NfsExportFormModal,
		RepairPermissionsModal,
		ShareCard,
		ShareFormModal,
		SmbInterfacesCard
	} from '$lib/components/shares';
	import { t } from '$lib/i18n';
	import { isRepairOf, repairJob } from '$lib/stores/repair';

	// i18n-ignore: the names of the protocols, the same in every language
	const tabs = [
		{ id: 'smb', label: 'SMB' },
		{ id: 'nfs', label: 'NFS' },
		{ id: 'iscsi', label: 'iSCSI' }
	] as const;
	type TabId = (typeof tabs)[number]['id'];

	let activeTab: TabId = $state('smb');
	// The NAS's address as this browser reaches it, for the connection hints.
	let host = $state('');

	let loading = $state(true);
	let paths: SharePath[] = $state([]);
	let pathsError = $state('');

	// ---- SMB ----
	let shares: SmbShare[] = $state([]);
	let sharesError = $state('');
	let status: SmbServiceStatus | null = $state(null);
	let statusError = $state('');

	// Outcome of the last change: shown until dismissed.
	let notice = $state('');
	let warnings: string[] = $state([]);
	let actionError = $state('');

	// Create / edit dialog: mounted while open.
	let form: { share: SmbShare | null } | null = $state(null);
	let repairing: string | null = $state(null);
	let shareToDelete: SmbShare | null = $state(null);
	let deleting = $state(false);
	let deleteError = $state('');
	let dismissing: Record<string, boolean> = $state({});

	// Several shares at once (ADR-0389): the selected shares, each by its name and its folder
	// (keyOf). A share the list no longer has with both leaves the selection whenever the list is
	// loaded again: a selection never moves to a share made again under the same name in another
	// folder.
	let selected = $state(new Set<string>());
	// The most shares one deletion takes (BULK_DELETE_MAX of supervisor/src/shares/schemas.py).
	const BULK_DELETE_MAX = 200;
	// The one question of a deletion of several: the shares as the page had them when it was asked,
	// each with the version and the folder that the request sends (a share changed since is not
	// deleted).
	let bulkToDelete: SmbShare[] | null = $state(null);
	let bulkDeleting = $state(false);
	let bulkError = $state('');
	let bulkErrorTitle = $state('');
	// The shares of the last deletion of several that were not deleted, each with the reason.
	let bulkNotDeleted: SmbShareDeleteResult[] = $state([]);

	// ---- NFS ----
	let nfsExports: NfsExport[] = $state([]);
	let nfsError = $state('');
	let nfsStatus: NfsServiceStatus | null = $state(null);
	let nfsStatusError = $state('');
	// The Add NFS export dialog: mounted while open.
	let nfsFormOpen = $state(false);
	let exportToDelete: NfsExport | null = $state(null);
	let nfsDeleting = $state(false);
	let nfsDeleteError = $state('');

	onMount(() => {
		host = window.location.hostname;
		void loadAll();
	});

	// A repair of a share's permissions runs in the background (ADR-0363): its card says so, and
	// when the events socket says that it ended, the shares are read again (their permissions).
	// The repair seen last, which only followRepair reads.
	let followedRepair: RepairResult | null = null;
	// Runs for each new value of the store; what followRepair reads and writes does not make it
	// run again.
	$effect(() => {
		const job = $repairJob;
		untrack(() => followRepair(job));
	});

	function followRepair(job: RepairResult | null) {
		const ended =
			followedRepair?.state === 'running' &&
			job?.state !== 'running' &&
			isRepairOf(job, followedRepair.share);
		followedRepair = job;
		if (ended) void reloadSmb();
	}

	/** A share as a selection holds it: share names cannot hold a line break. */
	function keyOf(share: SmbShare): string {
		return `${share.name}\n${share.path}`;
	}

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
			const listed = new Set(shares.map(keyOf));
			selected = new Set([...selected].filter((key) => listed.has(key)));
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
		bulkNotDeleted = [];
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

	function closeForm({ stale }: { stale: boolean }) {
		form = null;
		// The server refused a change (e.g. the share was changed meanwhile):
		// show the shares as they are now, so that the next edit starts from them.
		if (stale) void reloadSmb();
	}

	async function handleSaved({ share, created }: { share: SmbShare; created: boolean }) {
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

	// ---- Several shares at once (ADR-0389) ----

	const allSelected = $derived(shares.length > 0 && shares.every((share) => selected.has(keyOf(share))));

	function selectShare({ share, selected: on }: { share: SmbShare; selected: boolean }) {
		// A new set: the page reacts to `selected` being replaced, not to a change inside it.
		const next = new Set(selected);
		if (on) next.add(keyOf(share));
		else next.delete(keyOf(share));
		selected = next;
	}

	function selectAll(event: Event) {
		const on = (event.currentTarget as HTMLInputElement).checked;
		selected = on ? new Set(shares.map(keyOf)) : new Set();
	}

	function openBulkDelete() {
		const chosen = shares.filter((share) => selected.has(keyOf(share)));
		if (chosen.length === 0 || chosen.length > BULK_DELETE_MAX) return;
		bulkToDelete = chosen;
		bulkError = '';
		bulkNotDeleted = [];
	}

	function closeBulkDelete() {
		if (bulkDeleting) return;
		bulkToDelete = null;
		bulkError = '';
	}

	// The status the server answers for a share that was not deleted, through a key (ADR-0287).
	const NOT_DELETED: Record<string, string> = {
		not_found: 'shares.bulk.status.notFound',
		changed: 'shares.bulk.status.changed'
	};

	/** Why a share was not deleted, in the page's language; a status without a key as the server words it. */
	function notDeletedReason(result: SmbShareDeleteResult): string {
		return Object.prototype.hasOwnProperty.call(NOT_DELETED, result.status)
			? $t(NOT_DELETED[result.status])
			: (result.detail ?? result.status);
	}

	async function confirmBulkDelete() {
		const asked = bulkToDelete;
		if (!asked || bulkDeleting) return;
		bulkDeleting = true;
		bulkError = '';
		try {
			const answer = await deleteSmbShares(
				asked.map((share) => ({
					name: share.name,
					expected_version: share.version,
					expected_path: share.path
				}))
			);
			const deleted = answer.results.filter((result) => result.status === 'deleted');
			bulkNotDeleted = answer.results.filter((result) => result.status !== 'deleted');
			// What was not deleted stays selected, for another look; the list drops what is gone.
			const kept = new Set(bulkNotDeleted.map((result) => result.name));
			selected = new Set(asked.filter((share) => kept.has(share.name)).map(keyOf));
			bulkDeleting = false;
			closeBulkDelete();
			notice =
				deleted.length > 0
					? $t('shares.bulk.deleted', {
							n: deleted.length,
							names: deleted.map((result) => result.name).join(', ')
						})
					: '';
			warnings = answer.warnings;
			actionError = '';
			await reloadSmb();
		} catch (e) {
			// The session ended: the sign-in page is on its way.
			if (e instanceof ApiError && e.status === 401) return;
			// The dialog stays open with the reason. Whether nothing was deleted, the list read again
			// says: a refusal of the apply deletes none, but an answer that never came (a lost
			// connection) or an error after the deletion (its audit entry) does not say so.
			const reason = messageOf(e);
			await reloadSmb();
			const listed = new Set(shares.map(keyOf));
			if (sharesError) bulkErrorTitle = $t('shares.bulk.notKnown');
			else if (asked.every((share) => listed.has(keyOf(share))))
				bulkErrorTitle = $t('shares.bulk.noneDeleted');
			else bulkErrorTitle = $t('shares.bulk.someGone');
			bulkError = reason;
		} finally {
			bulkDeleting = false;
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

	async function handleNfsSaved({ exp }: { exp: NfsExport }) {
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
			<Button icon={Plus} disabled={loading || paths.length === 0} onclick={openCreate}>
				{$t('shares.smb.add')}
			</Button>
		{:else if activeTab === 'nfs'}
			<Button icon={Plus} disabled={loading || paths.length === 0} onclick={() => (nfsFormOpen = true)}>
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
			<Button variant="plain"
				type="button"
				role="tab"
				id="shares-tab-{tab.id}"
				aria-selected={activeTab === tab.id}
				aria-controls="shares-panel"
				tabindex={activeTab === tab.id ? 0 : -1}
			 class="tab-pill {activeTab === tab.id ? 'tab-pill-active' : ''}"
				onclick={() => (activeTab = tab.id)}
				onkeydown={(event) => tabKeydown(event, tab.id)}
			>
				{tab.label}
			</Button>
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
			<Alert variant="success" dismissible ondismiss={() => (notice = '')}>{notice}</Alert>
		{/if}
		{#if warnings.length > 0}
			<Alert variant="warning" dismissible ondismiss={() => (warnings = [])}>
				{#each warnings as warning}
					<p>{warning}</p>
				{/each}
			</Alert>
		{/if}
		{#if actionError}
			<Alert variant="error" dismissible ondismiss={() => (actionError = '')}>{actionError}</Alert>
		{/if}
		{#if bulkNotDeleted.length > 0}
			{@const title = $t('shares.bulk.notDeleted', { n: bulkNotDeleted.length })}
			<Alert variant="warning" {title} dismissible ondismiss={() => (bulkNotDeleted = [])}>
				<ul class="space-y-1" aria-label={title}>
					{#each bulkNotDeleted as result (result.name)}
						<li class="break-words">
							{$t('shares.bulk.notDeletedItem', { name: result.name, reason: notDeletedReason(result) })}
						</li>
					{/each}
				</ul>
			</Alert>
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
						ondismiss={dismissImportReport}
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
					<!-- The selection for an action on several shares (ADR-0389); it wraps on a phone. -->
					<div
						class="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2"
						role="toolbar"
						aria-label={$t('shares.bulk.toolbar')}
					>
						<label class="flex items-center gap-2 text-sm">
							<input
								type="checkbox"
								class="form-checkbox"
								checked={allSelected}
								disabled={bulkDeleting}
								onchange={selectAll}
							/>
							{$t('shares.bulk.selectAll')}
						</label>
						<span class="text-sm text-content-secondary">
							{$t('shares.bulk.selected', { n: selected.size })}
						</span>
						<Button
							variant="danger"
							size="sm"
							icon={Trash2}
							disabled={selected.size === 0 || selected.size > BULK_DELETE_MAX || bulkDeleting}
							onclick={openBulkDelete}
						>
							{$t('shares.bulk.delete')}
						</Button>
						{#if selected.size > BULK_DELETE_MAX}
							<p class="w-full text-sm text-warning">
								{$t('shares.bulk.tooMany', { max: BULK_DELETE_MAX })}
							</p>
						{/if}
					</div>
					<div class="space-y-4">
						{#each shares as share (share.name)}
							<ShareCard
								{share}
								{host}
								selected={selected.has(keyOf(share))}
								selectDisabled={bulkDeleting}
								onselect={selectShare}
								dismissing={dismissing[share.name] ?? false}
								repairing={$repairJob?.state === 'running' && isRepairOf($repairJob, share.name)}
								onedit={openEdit}
								onrepair={(share) => (repairing = share.name)}
								ondelete={openDelete}
								ondismissnotes={dismissNotes}
							/>
						{/each}
					</div>
				{/if}

				<p class="form-hint mt-4">{$t('shares.smb.networkNote')}</p>
			</Card>

			<SmbInterfacesCard />
		{:else if activeTab === 'iscsi'}
			<IscsiTab {paths} />
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
									<Button variant="ghost" size="icon-sm" onclick={() => openNfsDelete(exp)}>
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
		onclose={closeForm}
		onsaved={handleSaved}
	/>
{/if}

{#if repairing}
	<RepairPermissionsModal name={repairing} onclose={() => (repairing = null)} />
{/if}

<Modal
	open={shareToDelete !== null}
	title={$t('shares.smb.deleteTitle')}
	size="md"
	danger
	closable={!deleting}
	onclose={closeDelete}
>
	{#if shareToDelete}
		<!-- In parts around the share's name: ADR-0296. -->
		<p>
			{$t('shares.smb.deleteBefore')}
			<strong class="text-content">{shareToDelete.name}</strong>{$t('shares.smb.deleteAfter')}
		</p>
		{#if deleteError}
			<Alert variant="error" title={$t('shares.smb.notDeleted')}>{deleteError}</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={deleting} onclick={closeDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={deleting} onclick={confirmDelete}>
			{$t('shares.smb.delete')}
		</Button>
	{/snippet}
</Modal>

<Modal
	open={bulkToDelete !== null}
	title={$t('shares.bulk.deleteTitle', { n: bulkToDelete?.length ?? 0 })}
	size="md"
	danger
	closable={!bulkDeleting}
	onclose={closeBulkDelete}
>
	{#if bulkToDelete}
		<!-- One question that names every share and its folder (ADR-0389). -->
		<div class="space-y-3">
			<p>{$t('shares.bulk.question', { n: bulkToDelete.length })}</p>
			<ul class="space-y-1" aria-label={$t('shares.bulk.list')}>
				{#each bulkToDelete as share (share.name)}
					<li class="min-w-0">
						<strong class="text-content break-all">{share.name}</strong>{' '}<span
							class="mono-xs text-muted break-all">{share.path}</span
						>
					</li>
				{/each}
			</ul>
			<p class="text-sm text-secondary">{$t('shares.bulk.kept', { n: bulkToDelete.length })}</p>
			{#if bulkError}
				<Alert variant="error" title={bulkErrorTitle}>{bulkError}</Alert>
			{/if}
		</div>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={bulkDeleting} onclick={closeBulkDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={bulkDeleting} onclick={confirmBulkDelete}>
			{$t('shares.bulk.confirm', { n: bulkToDelete?.length ?? 0 })}
		</Button>
	{/snippet}
</Modal>

{#if nfsFormOpen}
	<NfsExportFormModal {paths} onclose={() => (nfsFormOpen = false)} onsaved={handleNfsSaved} />
{/if}

<Modal
	open={exportToDelete !== null}
	title={$t('shares.nfs.deleteTitle')}
	size="md"
	danger
	closable={!nfsDeleting}
	onclose={closeNfsDelete}
>
	{#if exportToDelete}
		<!-- In parts around the folder's path: ADR-0296. -->
		<p>
			{$t('shares.nfs.deleteBefore')}
			<span class="mono break-all">{exportToDelete.path}</span>{$t('shares.nfs.deleteAfter')}
		</p>
		{#if nfsDeleteError}
			<Alert variant="error" title={$t('shares.nfs.notDeleted')}>{nfsDeleteError}</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={nfsDeleting} onclick={closeNfsDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={nfsDeleting} onclick={confirmNfsDelete}>
			{$t('shares.nfs.delete')}
		</Button>
	{/snippet}
</Modal>

<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { Plus } from 'lucide-svelte';
	import { getSharePaths, type NfsExport, type RepairResult, type SharePath, type SmbShare } from '$lib/api/shares';
	import { Alert, Button } from '$lib/components/ui';
	import {
		BulkDeleteDialog,
		DeleteNfsExportDialog,
		DeleteShareDialog,
		IscsiTab,
		NfsExportFormModal,
		NfsExportList,
		NfsStatusAlerts,
		RepairPermissionsModal,
		ShareFormModal,
		ShareOutcomeAlerts,
		ShareTabs,
		SmbInterfacesCard,
		SmbShareList,
		SmbStatusAlerts
	} from '$lib/components/shares';
	import { messageOf } from '$lib/components/shares/errors';
	import { NfsExports } from '$lib/components/shares/nfs.svelte';
	import { ShareOutcome } from '$lib/components/shares/outcome.svelte';
	import { SmbShares } from '$lib/components/shares/smb.svelte';
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

	// Outcome of the last change: shown until dismissed.
	const outcome = new ShareOutcome();
	const smb = new SmbShares(outcome);
	const nfs = new NfsExports();

	// ---- SMB dialogs ----
	// Create / edit dialog: mounted while open.
	let form: { share: SmbShare | null } | null = $state(null);
	let repairing: string | null = $state(null);
	// The questions before a deletion, always mounted, which the page opens.
	let deleteDialog: ReturnType<typeof DeleteShareDialog> | undefined;
	let bulkDeleteDialog: ReturnType<typeof BulkDeleteDialog> | undefined;

	// ---- NFS dialogs ----
	// The Add NFS export dialog: mounted while open.
	let nfsFormOpen = $state(false);
	let nfsDeleteDialog: ReturnType<typeof DeleteNfsExportDialog> | undefined;

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
		if (ended) void smb.reload();
	}

	async function loadAll() {
		loading = true;
		// The share list imports the old smb.conf on first use, so the status
		// (with the import report) is read after it.
		await Promise.all([smb.load().then(() => smb.loadStatus()), loadPaths(), nfs.load()]);
		loading = false;
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
		if (stale) void smb.reload();
	}

	async function handleSaved({ share, created }: { share: SmbShare; created: boolean }) {
		form = null;
		outcome.show(
			created
				? $t('shares.smb.created', { name: share.name })
				: $t('shares.smb.saved', { name: share.name }),
			share.warnings
		);
		await smb.reload();
	}

	// ---- NFS exports ----

	async function handleNfsSaved({ exp }: { exp: NfsExport }) {
		nfsFormOpen = false;
		outcome.show($t('shares.nfs.created', { path: exp.path }), exp.warnings);
		await nfs.load();
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

	<ShareTabs {tabs} active={activeTab} onselect={(id) => (activeTab = id)} />

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

		<ShareOutcomeAlerts {outcome} />

		{#if loading}
			<p class="loading-text">{$t('common.loading')}</p>
		{:else if activeTab === 'smb'}
			<SmbStatusAlerts
				status={smb.status}
				error={smb.statusError}
				ondismissimport={() => smb.dismissImportReport()}
			/>
			<SmbShareList
				{smb}
				{host}
				onedit={openEdit}
				onrepair={(share) => (repairing = share.name)}
				ondelete={(share) => deleteDialog?.open(share)}
				onbulkdelete={() => bulkDeleteDialog?.open()}
			/>
			<SmbInterfacesCard />
		{:else if activeTab === 'iscsi'}
			<IscsiTab {paths} />
		{:else}
			<NfsStatusAlerts status={nfs.status} error={nfs.statusError} />
			<NfsExportList exports={nfs.exports} error={nfs.error} ondelete={(exp) => nfsDeleteDialog?.open(exp)} />
		{/if}
	</div>
</div>

{#if form}
	<ShareFormModal share={form.share} {paths} onclose={closeForm} onsaved={handleSaved} />
{/if}

{#if repairing}
	<RepairPermissionsModal name={repairing} onclose={() => (repairing = null)} />
{/if}

<DeleteShareDialog bind:this={deleteDialog} {smb} {outcome} />

<BulkDeleteDialog bind:this={bulkDeleteDialog} {smb} {outcome} />

{#if nfsFormOpen}
	<NfsExportFormModal {paths} onclose={() => (nfsFormOpen = false)} onsaved={handleNfsSaved} />
{/if}

<DeleteNfsExportDialog bind:this={nfsDeleteDialog} {nfs} {outcome} />

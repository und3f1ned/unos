<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { Plus } from 'lucide-svelte';
	import type { IscsiState } from '$lib/api/iscsi';
	import type { NfsExport, RepairResult, SharePath, SmbInterfaces, SmbShare } from '$lib/api/shares';
	import { Alert, Button, Skeleton, Tabs } from '$lib/components/ui';
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
		SmbInterfacesCard,
		SmbShareList,
		SmbStatusAlerts
	} from '$lib/components/shares';
	import { NfsExports } from '$lib/components/shares/nfs.svelte';
	import { ShareOutcome } from '$lib/components/shares/outcome.svelte';
	import { SmbShares } from '$lib/components/shares/smb.svelte';
	import { t } from '$lib/i18n';
	import { followPage } from '$lib/pages/follow.svelte';
	import { errorOf, valueOr, type Settled } from '$lib/pages/read';
	import type { SharesSnapshot } from '$lib/pages/shares';
	import { isRepairOf, repairJob } from '$lib/stores/repair';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

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

	// Until the page's load has answered: only on the tab's first page, when it takes a while.
	let loading = $state(true);
	let paths: SharePath[] = $state([]);
	let pathsError = $state('');
	// The interfaces SMB listens on, which SmbInterfacesCard shows and changes.
	let interfaces = $state.raw<Settled<SmbInterfaces> | null>(null);

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

	// ---- iSCSI ----
	// The tab's targets and the host's support, as the tab last read them, and the tab, whose form
	// the header's Add target opens.
	let iscsiState = $state<IscsiState | null>(null);
	let iscsiTab: ReturnType<typeof IscsiTab> | undefined = $state();

	// Why a share, an export or a target cannot be added now; '' when it can, or while the page
	// still reads what it needs (then the button only waits). Said under the button.
	const noPool = $derived(
		loading ? '' : pathsError ? $t('shares.pathsError') : paths.length === 0 ? $t('shares.noPoolsShort') : ''
	);
	const iscsiBlocked = $derived(
		!iscsiState
			? ''
			: !iscsiState.kernel_support
				? $t('iscsi.noKernelShort')
				: loading
					? ''
					: pathsError
						? $t('shares.pathsError')
						: !paths.some((p) => p.type === 'volume' && p.managed)
							? $t('iscsi.noVolumesShort')
							: ''
	);

	// ---- NFS dialogs ----
	// The Add NFS export dialog: mounted while open.
	let nfsFormOpen = $state(false);
	let nfsDeleteDialog: ReturnType<typeof DeleteNfsExportDialog> | undefined;

	// What the page's load read ($lib/pages/shares): shown before the page is drawn, and replaced by
	// a fresh read when the load gave the last visit's.
	followPage(
		() => data,
		(snapshot) => applySnapshot(snapshot)
	);

	function applySnapshot(snapshot: SharesSnapshot) {
		smb.apply(snapshot.shares, snapshot.smbStatus);
		nfs.apply(snapshot.nfsExports, snapshot.nfsStatus);
		paths = valueOr(snapshot.paths, []);
		pathsError = errorOf(snapshot.paths);
		interfaces = snapshot.interfaces;
		loading = false;
	}

	onMount(() => {
		host = window.location.hostname;
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
			followedRepair?.state === 'running' && job?.state !== 'running' && isRepairOf(job, followedRepair.share);
		followedRepair = job;
		if (ended) void smb.reload();
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
			created ? $t('shares.smb.created', { name: share.name }) : $t('shares.smb.saved', { name: share.name }),
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

{#snippet addButton(label: string, waiting: boolean, blocked: string, onclick: () => void)}
	<div class="flex flex-col items-end gap-1">
		<Button
			icon={Plus}
			disabled={waiting || blocked !== ''}
			aria-describedby={blocked ? 'shares-add-blocked' : undefined}
			{onclick}
		>
			{label}
		</Button>
		{#if blocked}
			<p id="shares-add-blocked" class="text-xs text-content-muted text-right max-w-[16rem]">{blocked}</p>
		{/if}
	</div>
{/snippet}

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">{$t('shares.title')}</h1>
		<!-- The tab's one main action; when it cannot be taken, why, under it. -->
		{#if activeTab === 'smb'}
			{@render addButton($t('shares.smb.add'), loading, noPool, openCreate)}
		{:else if activeTab === 'nfs'}
			{@render addButton($t('shares.nfs.add'), loading, noPool, () => (nfsFormOpen = true))}
		{:else}
			{@render addButton($t('iscsi.add'), !iscsiState || loading, iscsiBlocked, () => iscsiTab?.openCreate())}
		{/if}
	</div>

	<Tabs
		{tabs}
		active={activeTab}
		label={$t('shares.tabs.label')}
		tabId={(id) => `shares-tab-${id}`}
		panelId={() => 'shares-panel'}
		onselect={(id) => (activeTab = id)}
	/>

	<!-- What the selected tab shows. -->
	<div role="tabpanel" id="shares-panel" aria-labelledby="shares-tab-{activeTab}" tabindex="0" class="space-y-6">
		{#if !loading && pathsError}
			<Alert variant="error" title={$t('shares.pathsError')}>{pathsError}</Alert>
		{/if}

		<ShareOutcomeAlerts {outcome} />

		{#if loading}
			<!-- The tab's first page, when its data take longer than a moment: the shares' table and the
			     card under it. A navigation keeps the page before until they are in. -->
			<div class="space-y-6" aria-busy="true">
				<Skeleton shape="table" columns={5} count={3} />
				<Skeleton shape="card" count={2} />
			</div>
		{:else if activeTab === 'smb'}
			<SmbStatusAlerts
				status={smb.status}
				error={smb.statusError}
				shares={smb.shares}
				ondismissimport={() => smb.dismissImportReport()}
			/>
			<SmbShareList
				{smb}
				{host}
				noPool={!loading && !pathsError && paths.length === 0}
				onretry={() => smb.reload()}
				onedit={openEdit}
				onrepair={(share) => (repairing = share.name)}
				ondelete={(share) => deleteDialog?.open(share)}
				onbulkdelete={() => bulkDeleteDialog?.open()}
			/>
			<SmbInterfacesCard {interfaces} onchange={(answer) => (interfaces = answer)} />
		{:else if activeTab === 'iscsi'}
			<IscsiTab bind:this={iscsiTab} bind:iscsiState {paths} />
		{:else}
			<NfsStatusAlerts status={nfs.status} error={nfs.statusError} />
			<NfsExportList
				exports={nfs.exports}
				error={nfs.error}
				noPool={!loading && !pathsError && paths.length === 0}
				ondelete={(exp) => nfsDeleteDialog?.open(exp)}
				onretry={() => nfs.load()}
			/>
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

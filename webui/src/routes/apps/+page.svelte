<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import {
		uninstallApp,
		startApp,
		stopApp,
		restartApp,
		dismissInstallJob,
		cancelInstallJob,
		rollbackApp,
		removeEarlierVersion,
		type AppTemplate,
		type AppInstallJob,
		type ContainerResources,
		type InstalledApp
	} from '$lib/api/apps';
	import { Package, RefreshCw, Grid3X3, CheckCircle2 } from 'lucide-svelte';
	import { Button, Alert, EmptyState, Skeleton } from '$lib/components/ui';
	import {
		AppCatalog,
		AppDetailModal,
		AppInstallJobs,
		AppLimitsDialog,
		AppUpdateDialog,
		InstalledAppCard
	} from '$lib/components/apps';
	import { AppsData, reasonOf } from '$lib/components/apps/appsData.svelte';
	import { containerNames, installedOf } from '$lib/components/apps/installed';
	import { JobEnds } from '$lib/components/apps/jobEnds';
	import { t } from '$lib/i18n';
	import { ApiError } from '$lib/api/client';
	import { appInstalls } from '$lib/stores/appInstalls';

	// The catalog and the installed apps, each with its load (AppsData). `error` is for the
	// actions on an app.
	const data = new AppsData();
	let error = $state('');
	let searchQuery = $state('');
	let selectedCategory = $state('');
	let activeTab = $state<'catalog' | 'installed'>('catalog');

	// Detail modal state
	let selectedApp = $state<AppTemplate | null>(null);
	let showDetailModal = $state(false);

	// The container whose limits the dialog changes (ADR-0390), with what the list said of it.
	let limitsOf = $state<{ container: string; resources: ContainerResources } | null>(null);

	function openLimits(target: { container: string; resources: ContainerResources }) {
		limitsOf = target;
	}

	async function limitsSaved() {
		limitsOf = null;
		await data.loadInstalled();
	}

	// The installs that ended or were forgotten since the page was made (JobEnds, ADR-0384).
	const ends = new JobEnds($appInstalls);
	// The app whose install runs: its card offers no Install.
	const installing = $derived(
		new Set($appInstalls.filter((job) => job.state === 'running').map((job) => job.template_id))
	);
	// The running installs whose cancel was asked, until they end.
	let cancelling = $state<ReadonlySet<string>>(new Set());

	// Each message of the events socket: an install that ended reads the installed apps again,
	// and the cancels asked of installs that no longer run are dropped. Before the markup updates,
	// as Svelte 4's statements did; only the store makes it run again.
	$effect.pre(() => {
		const jobs = $appInstalls;
		untrack(() => {
			if (ends.note(jobs)) void data.loadInstalled();
			cancelling = new Set([...cancelling].filter((id) => jobs.some((job) => job.id === id && job.state === 'running')));
		});
	});

	async function dismiss(job: AppInstallJob) {
		try {
			await dismissInstallJob(job.id);
		} catch (e) {
			// Gone already (dismissed in another tab, or the supervisor restarted): done.
			if (e instanceof ApiError && e.status === 404) return;
			error = reasonOf(e);
		}
	}

	// The installed app whose update the dialog shows (ADR-0408).
	let updating = $state<InstalledApp | null>(null);

	/** The app's name in the catalog, or its own. */
	function nameOf(app: InstalledApp): string {
		return data.templateOf(app)?.name ?? app.name;
	}

	async function handleRollback(app: InstalledApp) {
		if (!confirm($t('appUpdates.confirmRollback', { name: nameOf(app) }))) return;
		error = '';
		try {
			await rollbackApp(app.container_id);
		} catch (e) {
			error = $t('appUpdates.rollbackFailed', { error: reasonOf(e) });
		}
	}

	async function handleRemoveEarlier(app: InstalledApp) {
		if (!confirm($t('appUpdates.confirmRemove', { name: nameOf(app) }))) return;
		error = '';
		try {
			await removeEarlierVersion(app.container_id);
		} catch (e) {
			error = $t('appUpdates.removeFailed', { error: reasonOf(e) });
		}
		await data.loadInstalled();
	}

	async function cancel(job: AppInstallJob) {
		const question = job.kind === 'update' ? 'appUpdates.confirmCancel' : 'apps.job.confirmCancel';
		if (!confirm($t(question, { name: job.name }))) return;
		cancelling = new Set([...cancelling, job.id]);
		try {
			await cancelInstallJob(job.id);
		} catch (e) {
			cancelling = new Set([...cancelling].filter((id) => id !== job.id));
			// It ended meanwhile: its end shows on its card.
			if (e instanceof ApiError && e.status === 404) return;
			error = reasonOf(e);
		}
	}

	// The first load draws the page once (ADR-0430): the catalog with the installed apps' answer, when
	// it comes within a moment of the catalog's; a Docker that takes longer does not hold the catalog
	// (ADR-0327), which is then listed alone. Until then the page is its skeleton.
	const INSTALLED_GRACE_MS = 1000;
	let drawn = $state(false);

	onMount(async () => {
		const installed = data.loadInstalled();
		await data.loadCatalog();
		await Promise.race([installed, new Promise((resolve) => setTimeout(resolve, INSTALLED_GRACE_MS))]);
		drawn = true;
	});

	async function refresh() {
		error = '';
		await Promise.all([data.loadCatalog(), data.loadInstalled()]);
	}

	function openDetail(app: AppTemplate) {
		selectedApp = app;
		showDetailModal = true;
	}

	function closeDetail() {
		showDetailModal = false;
		selectedApp = null;
	}

	/** Start, stop, restart or uninstall an app, then read both lists again; a failure says why. */
	async function act(call: (containerId: string) => Promise<void>, app: InstalledApp, failedKey: string) {
		try {
			await call(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || $t(failedKey);
		}
	}

	async function handleUninstall(app: InstalledApp) {
		const question = $t('apps.installed.confirmUninstall', { name: app.name, n: containerNames(app).length });
		if (!confirm(question)) return;
		await act(uninstallApp, app, 'apps.installed.uninstallFailed');
	}

	// It reads `data.installed`, so that the markup that calls it follows the installed list, which
	// can answer after the catalog.
	function getInstalledInfo(appId: string): InstalledApp | undefined {
		return installedOf(data.installed, appId);
	}
</script>

<div class="page-container">
	<!-- Page Header -->
	<div class="page-header">
		<div class="min-w-0">
			<h1 class="page-title">{$t('apps.title')}</h1>
			<!-- One line, always: the count comes without moving the page; a refresh keeps it. The
			     installed apps are counted by their tab (ADR-0431). -->
			<p class="page-subtitle min-h-5">
				{#if !drawn}
					<Skeleton class="w-56 max-w-full" />
				{:else if data.catalogKnown}
					{$t('apps.summaryAvailable', { available: data.availableCount })}
				{/if}
			</p>
		</div>
		<Button variant="secondary" size="icon" onclick={refresh} disabled={data.loading} title={$t('common.refresh')}>
			<RefreshCw class="w-4 h-4 {data.loading ? 'animate-spin' : ''}" aria-hidden="true" />
			<span class="sr-only">{$t('common.refresh')}</span>
		</Button>
	</div>

	{#if error}
		<Alert variant="error">{error}</Alert>
	{/if}


	<!-- The installs that run in the background and the last of each app, with their ends (ADR-0384). -->
	<AppInstallJobs jobs={$appInstalls} catalog={data.catalog} {cancelling} ondismiss={dismiss} oncancel={cancel} />

	<!-- Page tabs: a line under the row (ADR-0428). -->
	<div class="tab-bar">
		<Button variant="plain"
		 class="tab-underline {activeTab === 'catalog' ? 'tab-underline-active' : ''}"
			onclick={() => (activeTab = 'catalog')}
		>
			<span class="flex items-center gap-2">
				<Grid3X3 class="w-4 h-4" />
				{$t('apps.tabs.catalog')}
			</span>
		</Button>
		<Button variant="plain"
		 class="tab-underline {activeTab === 'installed' ? 'tab-underline-active' : ''}"
			onclick={() => (activeTab = 'installed')}
		>
			<span class="flex items-center gap-2">
				<CheckCircle2 class="w-4 h-4" />
				{data.installedKnown
					? $t('apps.tabs.installed', { count: data.installed.length })
					: $t('apps.tabs.installedUnknown')}
			</span>
		</Button>
	</div>

	<!-- The lists' errors under the tabs, drawn with the lists: they push nothing that was shown
	     (ADR-0430). -->
	{#if drawn && data.catalogError}
		<Alert variant="error">{$t('apps.catalogLoadError', { error: data.catalogError })}</Alert>
	{/if}

	{#if drawn && data.installedError}
		<Alert variant="error">{$t('apps.installed.loadError', { error: data.installedError })}</Alert>
	{/if}

	{#if activeTab === 'catalog' && !drawn}
		<!-- The first load: the featured apps, the categories and the grid of apps (ADR-0430). -->
		<div class="space-y-6" aria-busy="true">
			<div class="card">
				<Skeleton size="base" class="w-36 mb-5" />
				<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
					{#each [0, 1, 2] as tile (tile)}
						<Skeleton shape="block" class="h-[118px] w-full rounded-card" />
					{/each}
				</div>
			</div>
			<div class="flex flex-col lg:flex-row gap-6">
				<div class="lg:w-56 flex-shrink-0"><Skeleton shape="card" count={6} size="control" /></div>
				<div class="flex-1 min-w-0 space-y-5">
					<Skeleton shape="block" class="h-9 w-full" />
					<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
						{#each [0, 1, 2, 3, 4, 5] as tile (tile)}
							<Skeleton shape="block" class="h-40 w-full rounded-card" />
						{/each}
					</div>
				</div>
			</div>
		</div>
	{:else if activeTab === 'catalog' && data.catalogState === 'failed' && data.catalog.length === 0}
		<!-- The error above says why nothing is listed. -->
	{:else if activeTab === 'catalog'}
		<AppCatalog
			catalog={data.catalog}
			installed={data.installed}
			{installing}
			bind:search={searchQuery}
			bind:category={selectedCategory}
			onopen={openDetail}
		/>
	{:else}
		<!-- Installed Apps -->
		{#if !drawn || (data.installedState === 'loading' && !data.installedKnown && !data.installedError)}
			<!-- Not answered yet (ADR-0430); a refresh keeps the list. -->
			<div class="space-y-4" aria-busy="true">
				<Skeleton shape="card" count={2} />
				<Skeleton shape="card" count={2} />
			</div>
		{:else if data.installedState === 'failed' || (data.installedState === 'loading' && !data.installedKnown)}
			<!-- The error above says why nothing is listed. -->
		{:else if data.installed.length === 0}
			<!-- The Catalog is the tab beside this one: no second way there (ADR-0431). -->
			<EmptyState icon={Package} title={$t('apps.noneInstalled')} description={$t('apps.noneInstalledHint')} />
		{:else}
			<div class="space-y-4">
				{#each data.installed as app}
					<InstalledAppCard
						{app}
						template={data.templateOf(app)}
						installing={installing.has(app.template_id)}
						onupdate={(target) => (updating = target)}
						onstart={(target) => act(startApp, target, 'apps.installed.startFailed')}
						onstop={(target) => act(stopApp, target, 'apps.installed.stopFailed')}
						onrestart={(target) => act(restartApp, target, 'apps.installed.restartFailed')}
						onuninstall={handleUninstall}
						onrollback={handleRollback}
						onremoveearlier={handleRemoveEarlier}
						onlimits={openLimits}
					/>
				{/each}
			</div>
		{/if}
	{/if}
</div>

<!-- What the update of an installed app does, and its start (ADR-0408) -->
<AppUpdateDialog
	open={updating !== null}
	app={updating}
	name={updating ? nameOf(updating) : ''}
	onclose={() => (updating = null)}
	onstarted={() => (updating = null)}
/>

<!-- The limits of one container of an installed app (ADR-0390) -->
<AppLimitsDialog
	open={limitsOf !== null}
	container={limitsOf?.container ?? ''}
	resources={limitsOf?.resources ?? null}
	onclose={() => (limitsOf = null)}
	onsaved={limitsSaved}
/>

<!-- App Detail Modal -->
<AppDetailModal
	bind:open={showDetailModal}
	app={selectedApp}
	isInstalled={selectedApp ? getInstalledInfo(selectedApp.id) !== undefined : false}
	installedApp={selectedApp ? (getInstalledInfo(selectedApp.id) ?? null) : null}
	onclose={closeDetail}
/>

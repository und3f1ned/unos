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
	import { Package, RefreshCw, RotateCw, Grid3X3, CheckCircle2 } from 'lucide-svelte';
	import { Button, Alert, EmptyState, LoadState, Skeleton, Tabs, confirm } from '$lib/components/ui';
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
	import { appsReader } from '$lib/pages/apps';
	import { followPage } from '$lib/pages/follow.svelte';
	import { refreshPage } from '$lib/pages/load';
	import type { PageProps } from './$types';

	// What the page's load read ($lib/pages/apps); `data` is the lists'.
	let { data: pageData }: PageProps = $props();

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
			cancelling = new Set(
				[...cancelling].filter((id) => jobs.some((job) => job.id === id && job.state === 'running'))
			);
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
		const name = nameOf(app);
		const yes = await confirm({
			title: $t('appUpdates.rollbackTitle', { name }),
			message: $t('appUpdates.confirmRollback', { name }),
			confirmLabel: $t('appUpdates.rollbackAction')
		});
		if (!yes) return;
		error = '';
		try {
			await rollbackApp(app.container_id);
		} catch (e) {
			error = $t('appUpdates.rollbackFailed', { error: reasonOf(e) });
		}
	}

	async function handleRemoveEarlier(app: InstalledApp) {
		const yes = await confirm({
			title: $t('appUpdates.removeTitle', { name: nameOf(app) }),
			message: $t('appUpdates.confirmRemove'),
			confirmLabel: $t('appUpdates.removeAction')
		});
		if (!yes) return;
		error = '';
		try {
			await removeEarlierVersion(app.container_id);
		} catch (e) {
			error = $t('appUpdates.removeFailed', { error: reasonOf(e) });
		}
		await data.loadInstalled();
	}

	async function cancel(job: AppInstallJob) {
		const update = job.kind === 'update';
		const yes = await confirm({
			title: $t(update ? 'appUpdates.cancelTitle' : 'apps.job.cancelTitle', { name: job.name }),
			message: $t(update ? 'appUpdates.confirmCancel' : 'apps.job.confirmCancel'),
			confirmLabel: $t(update ? 'appUpdates.cancelAction' : 'apps.job.cancelAction'),
			cancelLabel: $t(update ? 'appUpdates.keepGoing' : 'apps.job.keepGoing'),
			tone: 'warning'
		});
		if (!yes) return;
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

	// The page's load draws it once: the catalog with the installed apps' answer when it comes within
	// a moment of the catalog's (ADR-0430); a Docker that takes longer does not hold the catalog
	// (ADR-0327), which is then listed alone and the installed apps when they come. Until the load
	// has answered, only on the tab's first page when it takes a while, the page is its skeleton.
	let drawn = $state(false);

	// Shown before the page is drawn, and replaced by a fresh read when the load gave the last visit's.
	followPage(
		() => pageData,
		(snapshot) => {
			data.apply(snapshot);
			drawn = true;
		}
	);

	onMount(() => {
		// The Containers page links an app's container to its card here (webui.ux-apps).
		if (window.location.hash === '#installed') activeTab = 'installed';
	});

	// Refresh: the page's load again.
	let refreshing = $state(false);
	async function refresh() {
		error = '';
		data.expectSnapshot();
		refreshing = true;
		try {
			await refreshPage(appsReader.key);
		} finally {
			refreshing = false;
		}
	}

	// Both lists again, after an action on an app.
	async function reload() {
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
			await reload();
		} catch (e) {
			error = (e as Error).message || $t(failedKey);
		}
	}

	async function handleUninstall(app: InstalledApp) {
		const yes = await confirm({
			title: $t('apps.installed.uninstallTitle', { name: app.name }),
			message: $t('apps.installed.confirmUninstall', { n: containerNames(app).length }),
			confirmLabel: $t('apps.installed.uninstallAction')
		});
		if (!yes) return;
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
		<Button
			variant="secondary"
			size="icon"
			icon={RefreshCw}
			onclick={refresh}
			loading={data.loading || refreshing}
			title={$t('common.refresh')}
		>
			<span class="sr-only">{$t('common.refresh')}</span>
		</Button>
	</div>

	{#if error}
		<Alert variant="error">{error}</Alert>
	{/if}

	<!-- The installs that run in the background and the last of each app, with their ends (ADR-0384). -->
	<AppInstallJobs jobs={$appInstalls} catalog={data.catalog} {cancelling} ondismiss={dismiss} oncancel={cancel} />

	<!-- Page tabs (Tabs, ADR-0432); the Installed tab counts the installed apps (ADR-0431). -->
	<Tabs
		tabs={[
			{ id: 'catalog', label: $t('apps.tabs.catalog'), icon: Grid3X3 },
			{
				id: 'installed',
				label: data.installedKnown
					? $t('apps.tabs.installed', { count: data.installed.length })
					: $t('apps.tabs.installedUnknown'),
				icon: CheckCircle2
			}
		]}
		active={activeTab}
		label={$t('apps.title')}
		tabId={(id) => `apps-tab-${id}`}
		panelId={(id) => `apps-panel-${id}`}
		onselect={(id) => (activeTab = id)}
	/>

	<div role="tabpanel" id="apps-panel-{activeTab}" aria-labelledby="apps-tab-{activeTab}" class="space-y-6">
		{#if activeTab === 'catalog'}
			<!-- The catalog: its skeleton until the first load has drawn (ADR-0430), a failed load in
			     its place, or above the apps of an earlier load. -->
			<LoadState
				loading={!drawn}
				error={data.catalogError}
				errorTitle={$t('apps.catalogLoadError')}
				keepOnError={data.catalog.length > 0}
				onretry={() => data.loadCatalog()}
				retrying={data.catalogState === 'loading'}
				class="space-y-6"
			>
				{#snippet skeleton()}
					<!-- The categories and the grid of apps, as the catalog draws them at each width (no
					     featured apps: ADR-0431): the list of categories from lg, below it the category
					     menu beside the search (on a phone under it). -->
					<div class="flex flex-col lg:flex-row gap-6">
						<div class="hidden lg:block lg:w-56 flex-shrink-0"><Skeleton shape="card" count={6} size="control" /></div>
						<div class="flex-1 min-w-0">
							<div class="mb-5 flex flex-col sm:flex-row gap-3">
								<Skeleton shape="block" class="h-9 w-full sm:flex-1" />
								<Skeleton shape="block" class="h-9 w-full sm:w-60 lg:hidden" />
							</div>
							<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
								{#each [0, 1, 2, 3, 4, 5] as tile (tile)}
									<Skeleton shape="block" class="h-40 w-full rounded-card" />
								{/each}
							</div>
						</div>
					</div>
				{/snippet}
				<!-- Docker's answer is the installed apps': without it no app is installed, started or
				     stopped, which the catalog says above its apps (webui.ux-apps). "Docker is not
				     answering" only for Docker's own 503 (ADR-0323); any other failure says that the
				     installed apps could not be loaded. Drawn with the list, inside its LoadState, so
				     that it pushes nothing that was shown (ADR-0430). -->
				{#if drawn && data.installedError}
					<Alert
						variant="error"
						title={$t(data.installedDockerDown ? 'apps.installed.loadErrorTitle' : 'apps.installed.loadFailedTitle')}
					>
						<p>{$t(data.installedDockerDown ? 'apps.installed.loadError' : 'apps.installed.loadFailed')}</p>
						<p class="mt-1 mono-xs break-words text-content-secondary">{data.installedError}</p>
						<Button
							variant="secondary"
							size="sm"
							icon={RotateCw}
							class="mt-3"
							loading={data.installedState === 'loading'}
							onclick={() => data.loadInstalled()}
						>
							{$t('common.retry')}
						</Button>
					</Alert>
				{/if}
				<AppCatalog
					catalog={data.catalog}
					canInstall={data.installedKnown}
					installed={data.installed}
					{installing}
					bind:search={searchQuery}
					bind:category={selectedCategory}
					onopen={openDetail}
				/>
			</LoadState>
		{:else}
			<!-- Installed Apps: their skeleton until Docker's first answer (ADR-0430), a failed load in
			     their place with what it means, the server's reason and Retry (webui.ux-apps), or none.
			     The Catalog is the tab beside this one: no second way there (ADR-0431). A refresh keeps
			     the list. -->
			<LoadState
				loading={!drawn || (!data.installedKnown && !data.installedError)}
				error={data.installedError}
				errorTitle={$t(data.installedDockerDown ? 'apps.installed.loadErrorTitle' : 'apps.installed.loadFailedTitle')}
				errorDescription={$t(data.installedDockerDown ? 'apps.installed.loadError' : 'apps.installed.loadFailed')}
				empty={data.installed.length === 0}
				onretry={() => data.loadInstalled()}
				retrying={data.installedState === 'loading'}
			>
				{#snippet skeleton()}
					<div class="space-y-4">
						<Skeleton shape="card" count={2} />
						<Skeleton shape="card" count={2} />
					</div>
				{/snippet}
				{#snippet emptyState()}
					<EmptyState icon={Package} title={$t('apps.noneInstalled')} description={$t('apps.noneInstalledHint')} />
				{/snippet}
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
			</LoadState>
		{/if}
	</div>
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

<!-- An app's details and install form (a Sheet) -->
<AppDetailModal
	bind:open={showDetailModal}
	app={selectedApp}
	isInstalled={selectedApp ? getInstalledInfo(selectedApp.id) !== undefined : false}
	installedApp={selectedApp ? (getInstalledInfo(selectedApp.id) ?? null) : null}
	installBlocked={data.installedKnown
		? ''
		: $t(data.installedDockerDown ? 'apps.detail.dockerDown' : 'apps.detail.installedUnknown')}
	onclose={closeDetail}
/>

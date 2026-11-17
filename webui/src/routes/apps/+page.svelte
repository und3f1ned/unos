<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getAppCatalog,
		getInstalledApps,
		uninstallApp,
		startApp,
		stopApp,
		restartApp,
		dismissInstallJob,
		cancelInstallJob,
		allProfiles,
		missingHardware,
		type AppTemplate,
		type AppInstallJob,
		type InstalledApp
	} from '$lib/api/apps';
	import {
		Package,
		Download,
		Play,
		Square,
		RotateCw,
		Trash2,
		ExternalLink,
		Search,
		RefreshCw,
		Grid3X3,
		CheckCircle2,
		Film,
		FileText,
		Download as DownloadIcon,
		Database,
		Activity,
		Globe,
		Code,
		Lock,
		LayoutGrid,
		Sparkles,
		Home,
		Cpu
	} from 'lucide-svelte';
	import { Button, Card, Badge, Alert, EmptyState } from '$lib/components/ui';
	import { AppCategory, AppDetailModal, AppInstallJobs, AppStorageWarning } from '$lib/components/apps';
	import { t } from '$lib/i18n';
	import { ApiError } from '$lib/api/client';
	import { appInstalls } from '$lib/stores/appInstalls';

	let catalog: AppTemplate[] = [];
	let installed: InstalledApp[] = [];
	// The catalog and the installed apps load each on its own: the catalog needs no Docker, and is
	// listed as soon as it came, while the installed apps wait for Docker's answer, or fail as when
	// Docker cannot be reached (503, ADR-0323). A count is shown only from a load that succeeded,
	// and each failed load says so (ADR-0327). `error` is for the actions on an app.
	type LoadState = 'loading' | 'loaded' | 'failed';
	let catalogState: LoadState = 'loading';
	let installedState: LoadState = 'loading';
	let catalogError = '';
	let installedError = '';
	let error = '';
	$: loading = catalogState === 'loading' || installedState === 'loading';
	let searchQuery = '';
	let selectedCategory = '';
	let activeTab: 'catalog' | 'installed' = 'catalog';

	// Detail modal state
	let selectedApp: AppTemplate | null = null;
	let showDetailModal = false;

	// The category list; AppCategory names each category.
	const categories = [
		{ value: '', icon: LayoutGrid, count: 0 },
		{ value: 'home_automation', icon: Home, count: 0 },
		{ value: 'media', icon: Film, count: 0 },
		{ value: 'ai', icon: Cpu, count: 0 },
		{ value: 'productivity', icon: FileText, count: 0 },
		{ value: 'download', icon: DownloadIcon, count: 0 },
		{ value: 'backup', icon: Database, count: 0 },
		{ value: 'monitoring', icon: Activity, count: 0 },
		{ value: 'network', icon: Globe, count: 0 },
		{ value: 'documents', icon: FileText, count: 0 },
		{ value: 'development', icon: Code, count: 0 },
		{ value: 'security', icon: Lock, count: 0 }
	];

	// The group of the apps that are given share folders (ADR-0274): a name, the same in every language.
	const UNOS_SMB = 'unos-smb';

	// The installs that ended, as the events socket said them (ADR-0384): when another one ends,
	// the installed apps are read again. Those already in the store when the page is made count as
	// seen, since the page's first load reads the apps they made; after a reload the store is
	// empty, and the socket's first message reads them once more.
	const ended = new Set($appInstalls.filter((job) => job.state !== 'running').map((job) => job.id));
	// The installs seen running: one that the store no longer has (a socket that signed in again to
	// a supervisor that restarted, and forgot it) may have left containers, so the installed apps
	// are read again too.
	const running = new Set($appInstalls.filter((job) => job.state === 'running').map((job) => job.id));
	$: noteEnds($appInstalls);
	// The app whose install runs: its card offers no Install.
	$: installing = new Set($appInstalls.filter((job) => job.state === 'running').map((job) => job.template_id));
	// The running installs whose cancel was asked, until they end.
	let cancelling: ReadonlySet<string> = new Set();
	$: cancelling = new Set([...cancelling].filter((id) => $appInstalls.some((job) => job.id === id && job.state === 'running')));

	function noteEnds(jobs: AppInstallJob[]) {
		let changed = false;
		for (const job of jobs) {
			if (job.state === 'running') {
				running.add(job.id);
			} else if (!ended.has(job.id)) {
				ended.add(job.id);
				running.delete(job.id);
				changed = true;
			}
		}
		for (const id of [...running]) {
			if (!jobs.some((job) => job.id === id)) {
				running.delete(id);
				changed = true;
			}
		}
		if (changed) void loadInstalled();
	}

	async function dismiss(event: CustomEvent<AppInstallJob>) {
		try {
			await dismissInstallJob(event.detail.id);
		} catch (e) {
			// Gone already (dismissed in another tab, or the supervisor restarted): done.
			if (e instanceof ApiError && e.status === 404) return;
			error = reasonOf(e);
		}
	}

	async function cancel(event: CustomEvent<AppInstallJob>) {
		const job = event.detail;
		if (!confirm($t('apps.job.confirmCancel', { name: job.name }))) return;
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

	// Docker's health of a running app's main container: its word, and its badge.
	const HEALTH: Record<string, { key: string; variant: 'info' | 'success' | 'error' }> = {
		starting: { key: 'apps.health.starting', variant: 'info' },
		healthy: { key: 'apps.health.healthy', variant: 'success' },
		unhealthy: { key: 'apps.health.unhealthy', variant: 'error' }
	};

	// Update category counts
	$: categoriesWithCounts = categories.map((cat) => ({
		...cat,
		count:
			cat.value === ''
				? catalog.length
				: catalog.filter((app) => app.category === cat.value).length
	}));

	// Filtered catalog
	$: filteredCatalog = catalog.filter((app) => {
		const matchesSearch =
			searchQuery === '' ||
			app.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
			app.description.toLowerCase().includes(searchQuery.toLowerCase());
		const matchesCategory = selectedCategory === '' || app.category === selectedCategory;
		return matchesSearch && matchesCategory;
	});

	// Featured apps - popular apps that users actually use
	$: featuredApps = catalog
		.filter((app) =>
			['homeassistant', 'frigate', 'jellyfin', 'nextcloud', 'immich', 'ollama'].includes(app.id)
		)
		.slice(0, 6);

	onMount(async () => {
		await refresh();
	});

	const reasonOf = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

	async function loadCatalog() {
		catalogState = 'loading';
		try {
			catalog = await getAppCatalog();
			catalogError = '';
			catalogState = 'loaded';
		} catch (e) {
			catalogError = reasonOf(e);
			catalogState = 'failed';
		}
	}

	async function loadInstalled() {
		installedState = 'loading';
		try {
			installed = await getInstalledApps();
			installedError = '';
			installedState = 'loaded';
		} catch (e) {
			installed = [];
			installedError = reasonOf(e);
			installedState = 'failed';
		}
	}

	async function refresh() {
		error = '';
		await Promise.all([loadCatalog(), loadInstalled()]);
	}

	function openDetail(app: AppTemplate) {
		selectedApp = app;
		showDetailModal = true;
	}

	function closeDetail() {
		showDetailModal = false;
		selectedApp = null;
	}

	async function handleUninstall(app: InstalledApp) {
		const question = $t('apps.installed.confirmUninstall', { name: app.name, n: containerNames(app).length });
		if (!confirm(question)) return;
		try {
			await uninstallApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || $t('apps.installed.uninstallFailed');
		}
	}

	async function handleStart(app: InstalledApp) {
		try {
			await startApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || $t('apps.installed.startFailed');
		}
	}

	async function handleStop(app: InstalledApp) {
		try {
			await stopApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || $t('apps.installed.stopFailed');
		}
	}

	async function handleRestart(app: InstalledApp) {
		try {
			await restartApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || $t('apps.installed.restartFailed');
		}
	}

	// Reactive, so that the markup that calls them follows the installed list, which can answer
	// after the catalog.
	$: isInstalled = (appId: string): boolean => installed.some((i) => i.template_id === appId);
	$: getInstalledInfo = (appId: string): InstalledApp | undefined =>
		installed.find((i) => i.template_id === appId);

	/** The badge of the source of an app's image (AppSource), with the key of its label. */
	function getSourceBadge(
		source: string | undefined
	): { variant: 'success' | 'info' | 'purple' | 'warning'; key: string } {
		switch (source?.toUpperCase()) {
			case 'OFFICIAL':
				return { variant: 'success', key: 'apps.source.official' };
			case 'LINUXSERVER':
			case 'LINUXSERVER.IO':
				return { variant: 'purple', key: 'apps.source.linuxserver' };
			case 'VERIFIED':
				return { variant: 'info', key: 'apps.source.verified' };
			default:
				return { variant: 'warning', key: 'apps.source.community' };
		}
	}

	/** The state of an installed app: incomplete while Docker lacks one of its containers (ADR-0381). */
	function stateOf(app: InstalledApp): { key: string; variant: 'success' | 'neutral' | 'warning' } {
		if (app.missing.length > 0) return { key: 'apps.state.incomplete', variant: 'warning' };
		if (app.status === 'running') return { key: 'apps.state.running', variant: 'success' };
		return { key: 'apps.state.stopped', variant: 'neutral' };
	}

	/** The names of the containers of an installed app that Docker has, its main container first. */
	function containerNames(app: InstalledApp): string[] {
		return [...(app.status === 'missing' ? [] : [app.name]), ...app.companions.map((c) => c.container)];
	}

	/** The names of the companions that do not run while the app's main container runs. */
	function stoppedCompanions(app: InstalledApp): string[] {
		if (app.status !== 'running') return [];
		return app.companions.filter((c) => c.status !== 'running').map((c) => c.container);
	}

	function getWebUrl(app: InstalledApp): string | null {
		if (app.web_port) {
			return `http://${window.location.hostname}:${app.web_port}`;
		}
		return null;
	}
</script>

<div class="page-container">
	<!-- Page Header -->
	<div class="page-header">
		<div class="flex items-center gap-3">
			<div class="w-10 h-10 rounded-lg bg-surface-elevated flex items-center justify-center">
				<Package class="w-5 h-5 text-info" />
			</div>
			<div>
				<h1 class="page-title">{$t('apps.title')}</h1>
				<p class="text-sm text-secondary">
					{#if catalogState === 'loaded' && installedState === 'loaded'}
						{$t('apps.summary', { available: catalog.length, installed: installed.length })}
					{:else if catalogState === 'loaded'}
						{$t('apps.summaryAvailable', { available: catalog.length })}
					{/if}
				</p>
			</div>
		</div>
		<Button variant="secondary" size="icon" on:click={refresh} disabled={loading}>
			<RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
		</Button>
	</div>

	{#if error}
		<Alert variant="error">{error}</Alert>
	{/if}

	{#if catalogError}
		<Alert variant="error">{$t('apps.catalogLoadError', { error: catalogError })}</Alert>
	{/if}

	{#if installedError}
		<Alert variant="error">{$t('apps.installed.loadError', { error: installedError })}</Alert>
	{/if}

	<!-- The installs that run in the background and the last of each app, with their ends (ADR-0384). -->
	<AppInstallJobs jobs={$appInstalls} {catalog} {cancelling} on:dismiss={dismiss} on:cancel={cancel} />

	<!-- Tabs -->
	<div class="flex gap-1 p-1 bg-surface-elevated rounded-lg w-fit">
		<button
			class="px-4 py-2 rounded-md text-sm font-medium transition-colors {activeTab === 'catalog'
				? 'bg-blue-600 text-white'
				: 'text-secondary hover:text-content'}"
			on:click={() => (activeTab = 'catalog')}
		>
			<span class="flex items-center gap-2">
				<Grid3X3 class="w-4 h-4" />
				{$t('apps.tabs.catalog')}
			</span>
		</button>
		<button
			class="px-4 py-2 rounded-md text-sm font-medium transition-colors {activeTab === 'installed'
				? 'bg-blue-600 text-white'
				: 'text-secondary hover:text-content'}"
			on:click={() => (activeTab = 'installed')}
		>
			<span class="flex items-center gap-2">
				<CheckCircle2 class="w-4 h-4" />
				{installedState === 'loaded'
					? $t('apps.tabs.installed', { count: installed.length })
					: $t('apps.tabs.installedUnknown')}
			</span>
		</button>
	</div>

	{#if activeTab === 'catalog' && catalogState === 'loading' && catalog.length === 0}
		<p class="loading-text">{$t('apps.loading')}</p>
	{:else if activeTab === 'catalog' && catalogState === 'failed' && catalog.length === 0}
		<!-- The error above says why nothing is listed. -->
	{:else if activeTab === 'catalog'}
		<!-- Featured Apps Section -->
		{#if featuredApps.length > 0 && !searchQuery && !selectedCategory}
			<Card>
				<div class="flex items-center gap-2 mb-5">
					<Sparkles class="w-5 h-5 text-yellow-400" />
					<h2 class="text-lg font-semibold">{$t('apps.featured')}</h2>
				</div>
				<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
					{#each featuredApps as app}
						{@const installedInfo = getInstalledInfo(app.id)}
						<button
							class="bg-surface-muted rounded-xl p-4 text-left hover:bg-surface-overlay transition-colors"
							on:click={() => openDetail(app)}
						>
							<div class="flex items-center gap-4 mb-3">
								<div
									class="w-14 h-14 rounded-xl bg-surface-overlay flex items-center justify-center text-3xl flex-shrink-0"
								>
									{app.icon}
								</div>
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-2">
										<h3 class="font-semibold text-lg truncate">{app.name}</h3>
										{#if installedInfo}
											<div
												class="w-2 h-2 rounded-full flex-shrink-0 {installedInfo.status ===
												'running'
													? 'bg-green-500'
													: 'bg-slate-500'}"
											></div>
										{/if}
									</div>
									<p class="text-sm text-secondary"><AppCategory category={app.category} /></p>
								</div>
							</div>
							<p class="text-sm text-secondary line-clamp-2">{app.description}</p>
						</button>
					{/each}
				</div>
			</Card>
		{/if}

		<!-- Search and Categories -->
		<div class="flex flex-col lg:flex-row gap-6">
			<!-- Category Sidebar -->
			<div class="lg:w-56 flex-shrink-0">
				<Card>
					<h3 class="font-semibold mb-4">{$t('apps.categories')}</h3>
					<nav class="space-y-1">
						{#each categoriesWithCounts as cat}
							{#if cat.count > 0 || cat.value === ''}
								<button
									class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-sm transition-colors
									{selectedCategory === cat.value
										? 'bg-blue-600 text-white'
										: 'text-secondary hover:text-content hover:bg-surface-overlay'}"
									on:click={() => (selectedCategory = cat.value)}
								>
									<span class="flex items-center gap-2.5">
										<svelte:component this={cat.icon} class="w-4 h-4" />
										<AppCategory category={cat.value} />
									</span>
									<span
										class="text-xs px-1.5 py-0.5 rounded {selectedCategory === cat.value
											? 'bg-white/20 text-white'
											: 'bg-surface-overlay text-muted'}"
									>
										{cat.count}
									</span>
								</button>
							{/if}
						{/each}
					</nav>
				</Card>
			</div>

			<!-- App Grid -->
			<div class="flex-1 min-w-0">
				<!-- Search -->
				<div class="mb-5">
					<div class="relative">
						<Search
							class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-content-muted pointer-events-none"
						/>
						<input
							type="text"
							placeholder={$t('apps.search')}
							bind:value={searchQuery}
							class="form-input pl-10"
						/>
					</div>
				</div>

				{#if filteredCatalog.length === 0}
					<EmptyState
						icon={Package}
						title={$t('apps.noneFound')}
						description={$t('apps.noneFoundHint')}
					>
						<Button
							variant="secondary"
							on:click={() => {
								searchQuery = '';
								selectedCategory = '';
							}}>{$t('apps.clearFilters')}</Button
						>
					</EmptyState>
				{:else}
					<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
						{#each filteredCatalog as app}
							{@const installedInfo = getInstalledInfo(app.id)}
							{@const source = getSourceBadge(app.source)}
							<div
								class="card hover:bg-surface-overlay transition-colors cursor-pointer"
								on:click={() => openDetail(app)}
								role="button"
								tabindex="0"
								on:keypress={(e) => e.key === 'Enter' && openDetail(app)}
							>
								<div class="flex items-start gap-3 mb-3">
									<div
										class="w-12 h-12 rounded-xl bg-surface-muted flex items-center justify-center text-2xl flex-shrink-0"
									>
										{app.icon}
									</div>
									<div class="flex-1 min-w-0">
										<div class="flex items-center gap-2 mb-0.5">
											<h3 class="font-semibold truncate">{app.name}</h3>
											{#if installedInfo}
												<div
													class="w-2 h-2 rounded-full flex-shrink-0 {installedInfo.status ===
													'running'
														? 'bg-green-500'
														: 'bg-slate-500'}"
												></div>
											{/if}
										</div>
										<div class="flex flex-wrap items-center gap-2">
											<Badge variant={source.variant} class="text-xs">{$t(source.key)}</Badge>
											<span class="text-xs text-muted"><AppCategory category={app.category} /></span>
											{#if installedInfo && missingHardware(app, installedInfo).length > 0}
												<!-- A container of it did not get the hardware: its details say how to give it. -->
												<Badge variant="warning" class="text-xs">
													<Cpu class="w-3 h-3 mr-1" />
													{$t('apps.hardware.badgeMissing')}
												</Badge>
											{:else if allProfiles(app).length > 0}
												<Badge variant="info" class="text-xs">
													<Cpu class="w-3 h-3 mr-1" />
													{$t('apps.hardware.badge')}
												</Badge>
											{/if}
										</div>
									</div>
								</div>
								<p class="text-sm text-secondary line-clamp-2 mb-3">{app.description}</p>
								<div class="flex items-center justify-between pt-3 border-t border-border">
									{#if installedInfo}
										{@const state = stateOf(installedInfo)}
										<Badge variant={state.variant}>{$t(state.key)}</Badge>
										<div class="flex items-center gap-1">
											{#if getWebUrl(installedInfo) && installedInfo.missing.length === 0}
												<a
													href={getWebUrl(installedInfo)}
													target="_blank"
													rel="noopener"
													on:click|stopPropagation
													class="p-1.5 rounded hover:bg-surface-muted text-info transition-colors"
													title={$t('apps.open')}
												>
													<ExternalLink class="w-4 h-4" />
												</a>
											{/if}
										</div>
									{:else if installing.has(app.id)}
										<span class="text-xs text-muted mono">{app.version || ''}</span>
										<Badge variant="info">{$t('apps.job.state.running')}</Badge>
									{:else}
										<span class="text-xs text-muted mono">{app.version || ''}</span>
										<Button variant="primary" size="sm" icon={Download}>{$t('apps.install')}</Button>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{:else}
		<!-- Installed Apps -->
		{#if installedState === 'loading'}
			<p class="loading-text">{$t('apps.loading')}</p>
		{:else if installedState === 'failed'}
			<!-- The error above says why nothing is listed. -->
		{:else if installed.length === 0}
			<EmptyState
				icon={Package}
				title={$t('apps.noneInstalled')}
				description={$t('apps.noneInstalledHint')}
			>
				<Button variant="primary" on:click={() => (activeTab = 'catalog')} icon={Download}>
					{$t('apps.browseCatalog')}
				</Button>
			</EmptyState>
		{:else}
			<div class="space-y-4">
				{#each installed as app}
					{@const template = catalog.find((c) => c.id === app.template_id)}
					{@const state = stateOf(app)}
					{@const health = app.status === 'running' && app.health ? HEALTH[app.health] : undefined}
					{@const stopped = app.missing.length === 0 ? stoppedCompanions(app) : []}
					<Card>
						<!-- On a narrow screen the buttons wrap below the app (ADR-0302). -->
						<div class="flex flex-wrap items-center gap-4">
							<div
								class="w-14 h-14 rounded-xl bg-surface-muted flex items-center justify-center text-3xl flex-shrink-0"
							>
								{template?.icon || '📦'}
							</div>
							<div class="flex-1 min-w-0">
								<div class="flex flex-wrap items-center gap-2 mb-1">
									<h3 class="font-semibold text-lg break-all">{app.name}</h3>
									<Badge variant={state.variant}>{$t(state.key)}</Badge>
									{#if health}
										<Badge variant={health.variant}>{$t(health.key)}</Badge>
									{/if}
								</div>
								<p class="text-sm text-secondary truncate">
									{template?.description || app.template_id}
								</p>
								{#if app.web_port}
									<p class="text-xs text-muted mt-1">
										{$t('apps.installed.port')} <span class="mono">{app.web_port}</span>
									</p>
								{/if}
								{#if app.pool}
									<p class="text-xs text-muted mt-1">
										{$t('apps.installed.pool')} <span class="mono">{app.pool}</span>
									</p>
								{/if}
								{#if app.runs_in_unos_smb}
									<!-- Only such an app reads share folders, also after a Repair (ADR-0274). -->
									<p class="text-xs text-muted mt-1">
										{$t('apps.installed.inGroup')} <span class="mono">{UNOS_SMB}</span>
									</p>
								{/if}
								{#if app.companions.length > 0 || app.missing.length > 0}
									<!-- An app of several containers (ADR-0381): those that Docker has. -->
									<p class="text-xs text-muted mt-1">
										{$t('apps.installed.containers')}
										<span class="mono break-words">{containerNames(app).join(', ')}</span>
									</p>
								{/if}
							</div>
							<div class="flex flex-wrap items-center gap-2">
								{#if getWebUrl(app) && app.missing.length === 0}
									<a
										href={getWebUrl(app)}
										target="_blank"
										rel="noopener"
										class="btn btn-secondary btn-md"
									>
										<ExternalLink class="w-4 h-4" />
										{$t('apps.open')}
									</a>
								{/if}
								<!-- An incomplete app does not start (409): a stop of what runs, and Uninstall. -->
								{#if app.status === 'running'}
									<Button
										variant="warning"
										size="sm"
										icon={Square}
										on:click={() => handleStop(app)}
									>
										{$t('apps.installed.stop')}
									</Button>
									{#if app.missing.length === 0}
										<Button
											variant="secondary"
											size="sm"
											icon={RotateCw}
											on:click={() => handleRestart(app)}
										>
											{$t('apps.installed.restart')}
										</Button>
									{/if}
								{:else if app.missing.length === 0}
									<Button
										variant="success"
										size="sm"
										icon={Play}
										on:click={() => handleStart(app)}
									>
										{$t('apps.installed.start')}
									</Button>
								{/if}
								<Button
									variant="danger"
									size="sm"
									icon={Trash2}
									on:click={() => handleUninstall(app)}
								>
									{$t('apps.installed.uninstall')}
								</Button>
							</div>
						</div>
						{#if app.missing.length > 0}
							<Alert variant="warning" class="mt-4">
								<p>
									{$t('apps.installed.missing', {
										name: template?.name ?? app.name,
										containers: app.missing.join(', ')
									})}
								</p>
							</Alert>
						{:else if stopped.length > 0}
							<Alert variant="warning" class="mt-4">
								<p>
									{$t('apps.installed.companionStopped', {
										name: template?.name ?? app.name,
										containers: stopped.join(', ')
									})}
								</p>
							</Alert>
						{/if}
						<AppStorageWarning {app} class="mt-4" />
					</Card>
				{/each}
			</div>
		{/if}
	{/if}
</div>

<!-- App Detail Modal -->
<AppDetailModal
	bind:open={showDetailModal}
	app={selectedApp}
	isInstalled={selectedApp ? isInstalled(selectedApp.id) : false}
	installedApp={selectedApp ? (getInstalledInfo(selectedApp.id) ?? null) : null}
	on:close={closeDetail}
/>

<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getAppCatalog,
		getInstalledApps,
		uninstallApp,
		startApp,
		stopApp,
		restartApp,
		type AppTemplate,
		type AppInstallResponse,
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
	import { AppDetailModal, AppStorageWarning } from '$lib/components/apps';

	let catalog: AppTemplate[] = [];
	let installed: InstalledApp[] = [];
	let loading = true;
	let error = '';
	let notice = '';
	let searchQuery = '';
	let selectedCategory = '';
	let activeTab: 'catalog' | 'installed' = 'catalog';

	// Detail modal state
	let selectedApp: AppTemplate | null = null;
	let showDetailModal = false;

	const categories = [
		{ value: '', label: 'All Apps', icon: LayoutGrid, count: 0 },
		{ value: 'home_automation', label: 'Smart Home', icon: Home, count: 0 },
		{ value: 'media', label: 'Media', icon: Film, count: 0 },
		{ value: 'ai', label: 'AI & LLMs', icon: Cpu, count: 0 },
		{ value: 'productivity', label: 'Productivity', icon: FileText, count: 0 },
		{ value: 'download', label: 'Downloads', icon: DownloadIcon, count: 0 },
		{ value: 'backup', label: 'Backup', icon: Database, count: 0 },
		{ value: 'monitoring', label: 'Monitoring', icon: Activity, count: 0 },
		{ value: 'network', label: 'Network', icon: Globe, count: 0 },
		{ value: 'documents', label: 'Documents', icon: FileText, count: 0 },
		{ value: 'development', label: 'Development', icon: Code, count: 0 },
		{ value: 'security', label: 'Security', icon: Lock, count: 0 }
	];

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

	async function refresh() {
		loading = true;
		error = '';
		try {
			[catalog, installed] = await Promise.all([getAppCatalog(), getInstalledApps()]);
		} catch (e: any) {
			error = e.message;
		}
		loading = false;
	}

	function openDetail(app: AppTemplate) {
		selectedApp = app;
		showDetailModal = true;
	}

	function closeDetail() {
		showDetailModal = false;
		selectedApp = null;
	}

	async function handleInstalled(
		event: CustomEvent<{ app: AppTemplate; result: AppInstallResponse }>
	) {
		const { app, result } = event.detail;
		notice = result.pool
			? `${app.name} was installed. Its data is stored on the "${result.pool}" pool.`
			: `${app.name} was installed.`;
		await refresh();
	}

	async function handleUninstall(app: InstalledApp) {
		if (!confirm(`Uninstall ${app.name}? This removes its container and keeps its data folders.`))
			return;
		try {
			await uninstallApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || 'Uninstall failed';
		}
	}

	async function handleStart(app: InstalledApp) {
		try {
			await startApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || 'Start failed';
		}
	}

	async function handleStop(app: InstalledApp) {
		try {
			await stopApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || 'Stop failed';
		}
	}

	async function handleRestart(app: InstalledApp) {
		try {
			await restartApp(app.container_id);
			await refresh();
		} catch (e: any) {
			error = e.message || 'Restart failed';
		}
	}

	function isInstalled(appId: string): boolean {
		return installed.some((i) => i.template_id === appId);
	}

	function getInstalledInfo(appId: string): InstalledApp | undefined {
		return installed.find((i) => i.template_id === appId);
	}

	function getSourceBadge(
		source: string | undefined
	): { variant: 'success' | 'info' | 'purple' | 'warning'; label: string } {
		switch (source?.toUpperCase()) {
			case 'OFFICIAL':
				return { variant: 'success', label: 'Official' };
			case 'LINUXSERVER':
			case 'LINUXSERVER.IO':
				return { variant: 'purple', label: 'LinuxServer' };
			case 'VERIFIED':
				return { variant: 'info', label: 'Verified' };
			default:
				return { variant: 'warning', label: 'Community' };
		}
	}

	function getWebUrl(app: InstalledApp): string | null {
		if (app.web_port) {
			return `http://${window.location.hostname}:${app.web_port}`;
		}
		return null;
	}

	function formatCategory(category: string): string {
		return category.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
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
				<h1 class="page-title">App Store</h1>
				<p class="text-sm text-secondary">
					{catalog.length} apps available, {installed.length} installed
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

	{#if notice}
		<Alert variant="success" dismissible on:dismiss={() => (notice = '')}>{notice}</Alert>
	{/if}

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
				Catalog
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
				Installed ({installed.length})
			</span>
		</button>
	</div>

	{#if loading}
		<p class="loading-text">Loading apps...</p>
	{:else if activeTab === 'catalog'}
		<!-- Featured Apps Section -->
		{#if featuredApps.length > 0 && !searchQuery && !selectedCategory}
			<Card>
				<div class="flex items-center gap-2 mb-5">
					<Sparkles class="w-5 h-5 text-yellow-400" />
					<h2 class="text-lg font-semibold">Featured Apps</h2>
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
									<p class="text-sm text-secondary">{formatCategory(app.category)}</p>
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
					<h3 class="font-semibold mb-4">Categories</h3>
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
										{cat.label}
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
							placeholder="Search apps..."
							bind:value={searchQuery}
							class="form-input pl-10"
						/>
					</div>
				</div>

				{#if filteredCatalog.length === 0}
					<EmptyState
						icon={Package}
						title="No apps found"
						description="Try adjusting your search or category filter"
					>
						<Button
							variant="secondary"
							on:click={() => {
								searchQuery = '';
								selectedCategory = '';
							}}>Clear Filters</Button
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
										<div class="flex items-center gap-2">
											<Badge variant={source.variant} class="text-xs">{source.label}</Badge>
											<span class="text-xs text-muted">{formatCategory(app.category)}</span>
										</div>
									</div>
								</div>
								<p class="text-sm text-secondary line-clamp-2 mb-3">{app.description}</p>
								<div class="flex items-center justify-between pt-3 border-t border-border">
									{#if installedInfo}
										<Badge variant={installedInfo.status === 'running' ? 'success' : 'neutral'}>
											{installedInfo.status === 'running' ? 'Running' : 'Stopped'}
										</Badge>
										<div class="flex items-center gap-1">
											{#if getWebUrl(installedInfo)}
												<a
													href={getWebUrl(installedInfo)}
													target="_blank"
													rel="noopener"
													on:click|stopPropagation
													class="p-1.5 rounded hover:bg-surface-muted text-info transition-colors"
													title="Open"
												>
													<ExternalLink class="w-4 h-4" />
												</a>
											{/if}
										</div>
									{:else}
										<span class="text-xs text-muted mono">{app.version || ''}</span>
										<Button variant="primary" size="sm" icon={Download}>Install</Button>
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
		{#if installed.length === 0}
			<EmptyState
				icon={Package}
				title="No apps installed"
				description="Install apps from the catalog to get started"
			>
				<Button variant="primary" on:click={() => (activeTab = 'catalog')} icon={Download}>
					Browse Catalog
				</Button>
			</EmptyState>
		{:else}
			<div class="space-y-4">
				{#each installed as app}
					{@const template = catalog.find((c) => c.id === app.template_id)}
					<Card>
						<div class="flex items-center gap-4">
							<div
								class="w-14 h-14 rounded-xl bg-surface-muted flex items-center justify-center text-3xl flex-shrink-0"
							>
								{template?.icon || '📦'}
							</div>
							<div class="flex-1 min-w-0">
								<div class="flex items-center gap-2 mb-1">
									<h3 class="font-semibold text-lg">{app.name}</h3>
									<Badge variant={app.status === 'running' ? 'success' : 'neutral'}>
										{app.status === 'running' ? 'Running' : 'Stopped'}
									</Badge>
								</div>
								<p class="text-sm text-secondary truncate">
									{template?.description || app.template_id}
								</p>
								{#if app.web_port}
									<p class="text-xs text-muted mt-1">
										Port: <span class="mono">{app.web_port}</span>
									</p>
								{/if}
								{#if app.pool}
									<p class="text-xs text-muted mt-1">
										Data on pool: <span class="mono">{app.pool}</span>
									</p>
								{/if}
							</div>
							<div class="flex items-center gap-2">
								{#if getWebUrl(app)}
									<a
										href={getWebUrl(app)}
										target="_blank"
										rel="noopener"
										class="btn btn-secondary btn-md"
									>
										<ExternalLink class="w-4 h-4" />
										Open
									</a>
								{/if}
								{#if app.status === 'running'}
									<Button
										variant="warning"
										size="sm"
										icon={Square}
										on:click={() => handleStop(app)}
									>
										Stop
									</Button>
									<Button
										variant="secondary"
										size="sm"
										icon={RotateCw}
										on:click={() => handleRestart(app)}
									>
										Restart
									</Button>
								{:else}
									<Button
										variant="success"
										size="sm"
										icon={Play}
										on:click={() => handleStart(app)}
									>
										Start
									</Button>
								{/if}
								<Button
									variant="danger"
									size="sm"
									icon={Trash2}
									on:click={() => handleUninstall(app)}
								>
									Uninstall
								</Button>
							</div>
						</div>
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
	on:installed={handleInstalled}
/>

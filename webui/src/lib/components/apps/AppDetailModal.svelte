<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import {
		Globe,
		Github,
		BookOpen,
		Network,
		HardDrive,
		Settings,
		Download,
		Check,
		Package,
		Tag,
		Shield,
		Database
	} from 'lucide-svelte';
	import { Button, Badge, Modal, Alert, Input, Select } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import AppCategory from './AppCategory.svelte';
	import AppStorageWarning from './AppStorageWarning.svelte';
	import {
		type AppTemplate,
		type AppPool,
		type AppInstallRequest,
		type AppInstallResponse,
		type InstalledApp,
		type VolumeMapping,
		type EnvVar,
		getAppPools,
		installApp,
		poolHostPath,
		isInsidePools,
		describeApiError,
		isSetByUnos,
		UNOS_SMB_IDS
	} from '$lib/api/apps';

	export let app: AppTemplate | null = null;
	export let open = false;
	export let isInstalled = false;
	/** The installed instance of the app, if any: its real host folders are shown. */
	export let installedApp: InstalledApp | null = null;

	const dispatch = createEventDispatcher<{
		close: null;
		installed: { app: AppTemplate; result: AppInstallResponse };
	}>();

	let installing = false;
	let error = '';
	let activeTab: 'overview' | 'ports' | 'volumes' | 'environment' = 'overview';

	// Form state, reset whenever an app is shown
	let portValues: Record<number, string> = {};
	let volumeOverrides: Record<string, string> = {};
	let envValues: Record<string, string> = {};
	// Run the app in the group unos-smb, the only way to give it a share's folder (ADR-0274).
	let runInUnosSmb = false;

	// Storage pools, reloaded every time the modal opens
	let pools: AppPool[] = [];
	let poolsLoading = false;
	let poolsError = '';
	let selectedPoolName = '';
	let poolsRequest = 0;
	let wasOpen = false;

	$: sourceBadge = getSourceBadge(app?.source);
	$: if (app) resetForm(app);
	$: handleOpenChange(open);
	// Whether the app runs (installed) or is to run (the form) in the group unos-smb.
	$: inUnosSmb = isInstalled ? (installedApp?.runs_in_unos_smb ?? false) : runInUnosSmb;

	$: selectedPool = pools.find((pool) => pool.name === selectedPoolName) ?? null;
	$: poolOptions = pools.map((pool) => ({
		value: pool.name,
		label: $t('apps.detail.poolOption', {
			name: pool.name,
			free: formatSize(pool.free_bytes),
			total: formatSize(pool.total_bytes)
		})
	}));
	$: poolVolumes = app ? app.volumes.filter((volume) => volume.on_pool) : [];
	$: needsPool = poolVolumes.length > 0;
	// An installed app lists the folders its container really uses.
	$: volumeCount =
		isInstalled && installedApp ? installedApp.volumes.length : (app?.volumes.length ?? 0);

	// Client-side checks for a clear form; the server validates everything again.
	$: missingRequired = app
		? app.environment
				.filter((env) => env.required && !(envValues[env.name] ?? '').trim())
				.map((env) => env.name)
		: [];
	$: invalidPorts = app
		? app.ports
				.filter((port) => !isValidPort(portValues[port.container]))
				.map((port) => port.container)
		: [];
	$: invalidVolumes = Object.entries(volumeOverrides)
		.filter(([, path]) => !isInsidePools(path, pools))
		.map(([container]) => container);
	$: blockers = [
		...(missingRequired.length > 0
			? [
					$t(missingRequired.length > 1 ? 'apps.detail.missingVariables' : 'apps.detail.missingVariable', {
						names: missingRequired.join(', ')
					})
				]
			: []),
		...(invalidPorts.length > 0 ? [$t('apps.detail.invalidPorts')] : []),
		...(invalidVolumes.length > 0
			? [$t('apps.detail.invalidVolumes', { paths: invalidVolumes.join(', ') })]
			: [])
	];
	$: canInstall =
		!!app &&
		!isInstalled &&
		!installing &&
		!poolsLoading &&
		(!needsPool || selectedPool !== null) &&
		blockers.length === 0;

	// The units of a size, by powers of 1024, as formatBytes of $lib/stores/stats writes them.
	const SIZE_UNITS = ['apps.size.bytes', 'apps.size.kb', 'apps.size.mb', 'apps.size.gb', 'apps.size.tb'];

	/** A size in the language of the page: the number in its format with the unit of the catalog. */
	$: formatSize = (bytes: number): string => {
		const power = Math.max(0, Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), SIZE_UNITS.length - 1));
		const size = new Intl.NumberFormat($intlLocale, { maximumFractionDigits: 2, useGrouping: false }).format(
			bytes / 1024 ** power
		);
		return $t(SIZE_UNITS[power], { size });
	};

	function resetForm(template: AppTemplate) {
		portValues = Object.fromEntries(
			template.ports.map((port) => [port.container, String(port.host)])
		);
		volumeOverrides = {};
		envValues = Object.fromEntries(template.environment.map((env) => [env.name, env.value]));
		runInUnosSmb = false;
		error = '';
		activeTab = 'overview';
	}

	function handleOpenChange(isOpen: boolean) {
		if (isOpen && !wasOpen) {
			selectedPoolName = '';
			// An installed app already has its folders; no pool is chosen for it.
			if (isInstalled) {
				poolsRequest++;
				pools = [];
				poolsLoading = false;
				poolsError = '';
			} else {
				void loadPools();
			}
		}
		wasOpen = isOpen;
	}

	async function loadPools() {
		const request = ++poolsRequest;
		poolsLoading = true;
		poolsError = '';
		try {
			const result = await getAppPools();
			if (request !== poolsRequest) return;
			pools = result;
			// The server sorts pools by name and uses the first one by default.
			if (!pools.some((pool) => pool.name === selectedPoolName)) {
				selectedPoolName = pools[0]?.name ?? '';
			}
		} catch (e) {
			if (request !== poolsRequest) return;
			pools = [];
			poolsError = describeApiError(e, $t('apps.detail.poolsError'));
		} finally {
			if (request === poolsRequest) poolsLoading = false;
		}
	}

	function isValidPort(value: string | undefined): boolean {
		if (value === undefined || !/^\d{1,5}$/.test(value.trim())) return false;
		const port = Number(value);
		return port >= 1 && port <= 65535;
	}

	/** Host path shown for a volume: the override, else the path on the selected pool. */
	function effectiveHostPath(
		volume: VolumeMapping,
		pool: AppPool | null,
		overrides: Record<string, string>
	): string {
		const override = overrides[volume.container];
		if (volume.on_pool && override !== undefined) return override;
		return pool ? poolHostPath(volume, pool.path) : volume.host;
	}

	function setVolumePath(volume: VolumeMapping, value: string) {
		const defaultPath = selectedPool ? poolHostPath(volume, selectedPool.path) : volume.host;
		const next = { ...volumeOverrides };
		if (value === defaultPath) {
			delete next[volume.container];
		} else {
			next[volume.container] = value;
		}
		volumeOverrides = next;
	}

	function inputValue(event: Event): string {
		return (event.target as HTMLInputElement).value;
	}

	function changedPorts(template: AppTemplate): Record<number, number> {
		const result: Record<number, number> = {};
		for (const port of template.ports) {
			const value = Number(portValues[port.container]);
			if (value !== port.host) result[port.container] = value;
		}
		return result;
	}

	function changedEnv(template: AppTemplate): Record<string, string> {
		const result: Record<string, string> = {};
		for (const env of template.environment) {
			// The server sets PUID and PGID: the template's values, or the group's with the option.
			if (isSetByUnos(env.name)) continue;
			const value = envValues[env.name] ?? env.value;
			if (value !== env.value) result[env.name] = value;
		}
		return result;
	}

	/** PUID or PGID as UNOS sets it: the group's in unos-smb, otherwise the template's. */
	function setByUnosValue(env: EnvVar, inGroup: boolean): string {
		return inGroup && isSetByUnos(env.name) ? String(UNOS_SMB_IDS[env.name]) : env.value;
	}

	// Reactive: the hint follows the language of the page.
	$: setByUnosHint = (template: AppTemplate, env: EnvVar, installed: boolean, inGroup: boolean): string => {
		if (installed) {
			return $t(inGroup ? 'apps.detail.setByUnosIn' : 'apps.detail.setByUnosOutside', { name: template.name });
		}
		if (!template.can_run_in_unos_smb || !isSetByUnos(env.name)) return $t('apps.detail.setByUnos');
		return $t(env.name === 'PGID' ? 'apps.detail.setByUnosOptionGroup' : 'apps.detail.setByUnosOption', {
			id: UNOS_SMB_IDS[env.name],
			value: env.value
		});
	};

	function close() {
		dispatch('close');
	}

	async function handleInstall() {
		if (!app || !canInstall) return;
		const template = app;

		installing = true;
		error = '';

		try {
			const req: AppInstallRequest = { template_id: template.id };
			if (needsPool && selectedPool) req.pool = selectedPool.name;
			const ports = changedPorts(template);
			if (Object.keys(ports).length > 0) req.port_overrides = ports;
			const volumes = Object.fromEntries(
				Object.entries(volumeOverrides).map(([container, path]) => [container, path.trim()])
			);
			if (Object.keys(volumes).length > 0) req.volume_overrides = volumes;
			const env = changedEnv(template);
			if (Object.keys(env).length > 0) req.env_overrides = env;
			if (template.can_run_in_unos_smb && runInUnosSmb) req.run_in_unos_smb = true;

			const result = await installApp(req);
			dispatch('installed', { app: template, result });
			close();
		} catch (e) {
			error = describeApiError(e, $t('apps.detail.installFailed'));
		} finally {
			installing = false;
		}
	}

	/** The badge of the source of an app's image (AppSource), with the key of its label. */
	function getSourceBadge(
		source: string | undefined
	): { variant: 'success' | 'info' | 'purple' | 'warning'; key: string } {
		switch (source?.toUpperCase()) {
			case 'OFFICIAL':
				return { variant: 'success', key: 'apps.source.official' };
			case 'LINUXSERVER':
			case 'LINUXSERVER.IO':
				return { variant: 'purple', key: 'apps.source.linuxserverIo' };
			case 'VERIFIED':
				return { variant: 'info', key: 'apps.source.verified' };
			default:
				return { variant: 'warning', key: 'apps.source.community' };
		}
	}

	function getCategoryIcon(category: string): string {
		const icons: Record<string, string> = {
			media: '🎬',
			productivity: '📝',
			download: '📥',
			backup: '💾',
			monitoring: '📊',
			network: '🌐',
			development: '💻',
			security: '🔒'
		};
		return icons[category] || '📦';
	}
</script>

<Modal bind:open size="xl" title="" on:close={close}>
	{#if app}
		<!-- Custom Header -->
		<div class="-mx-6 -mt-6 mb-6 p-6 bg-gradient-to-b from-surface-overlay to-transparent">
			<div class="flex items-start gap-4">
				<div
					class="w-16 h-16 rounded-xl bg-surface-elevated flex items-center justify-center text-4xl flex-shrink-0 shadow-lg"
				>
					{app.icon}
				</div>
				<div class="flex-1 min-w-0">
					<div class="flex items-center gap-2 mb-1">
						<h2 class="text-2xl font-bold truncate">{app.name}</h2>
						{#if isInstalled}
							<Badge variant="success">{$t('apps.detail.installed')}</Badge>
						{/if}
					</div>
					<p class="text-secondary mb-2">{app.description}</p>
					<div class="flex flex-wrap items-center gap-2">
						<Badge variant="neutral">{getCategoryIcon(app.category)} <AppCategory category={app.category} /></Badge>
						{#if app.version}
							<Badge variant="info">
								<Tag class="w-3 h-3 mr-1" />
								{app.version}
							</Badge>
						{/if}
						<Badge variant={sourceBadge.variant}>
							<Shield class="w-3 h-3 mr-1" />
							{$t(sourceBadge.key)}
						</Badge>
					</div>
				</div>
			</div>

			<!-- External Links -->
			{#if app.website || app.github || app.documentation}
				<div class="flex items-center gap-3 mt-4 pt-4 border-t border-border">
					{#if app.website}
						<a
							href={app.website}
							target="_blank"
							rel="noopener"
							class="flex items-center gap-1.5 text-sm text-info hover:text-blue-300 transition-colors"
						>
							<Globe class="w-4 h-4" />
							{$t('apps.detail.website')}
						</a>
					{/if}
					{#if app.github}
						<a
							href={app.github}
							target="_blank"
							rel="noopener"
							class="flex items-center gap-1.5 text-sm text-secondary hover:text-content transition-colors"
						>
							<Github class="w-4 h-4" />
							{$t('apps.detail.github')}
						</a>
					{/if}
					{#if app.documentation}
						<a
							href={app.documentation}
							target="_blank"
							rel="noopener"
							class="flex items-center gap-1.5 text-sm text-secondary hover:text-content transition-colors"
						>
							<BookOpen class="w-4 h-4" />
							{$t('apps.detail.docs')}
						</a>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Storage pool for the app's data -->
		{#if needsPool && !isInstalled}
			<div class="mb-4">
				{#if poolsLoading}
					<p class="text-sm text-muted">{$t('apps.detail.loadingPools')}</p>
				{:else if poolsError}
					<Alert variant="error" title={$t('apps.detail.poolsError')}>{poolsError}</Alert>
				{:else if pools.length === 0}
					<Alert variant="warning" title={$t('apps.detail.noPoolTitle')}>
						<p>{$t('apps.detail.noPool', { name: app.name })}</p>
						<Button variant="secondary" size="sm" href="/storage" icon={HardDrive} class="mt-3">
							{$t('apps.detail.goToStorage')}
						</Button>
					</Alert>
				{:else}
					<Select
						label={$t('apps.detail.pool')}
						options={poolOptions}
						bind:value={selectedPoolName}
						hint={$t('apps.detail.poolHint')}
					/>
				{/if}
			</div>
		{/if}
		{#if app.can_run_in_unos_smb && !isInstalled}
			<div class="mb-4">
				<label class="flex items-center gap-2 cursor-pointer">
					<input
						type="checkbox"
						class="form-checkbox"
						bind:checked={runInUnosSmb}
						aria-describedby="app-unos-smb-hint"
					/>
					{$t('apps.detail.unosSmb')}
				</label>
				<p id="app-unos-smb-hint" class="form-hint">
					{$t('apps.detail.unosSmbHint', { puid: UNOS_SMB_IDS.PUID, pgid: UNOS_SMB_IDS.PGID })}
				</p>
				{#if runInUnosSmb}
					<Alert variant="warning" class="mt-2">
						{$t('apps.detail.unosSmbWarning', { name: app.name })}
					</Alert>
				{/if}
			</div>
		{/if}
		{#if isInstalled && installedApp?.runs_in_unos_smb}
			<Alert variant="info" class="mb-4">
				{$t('apps.detail.runsInUnosSmb', { name: app.name, puid: UNOS_SMB_IDS.PUID, pgid: UNOS_SMB_IDS.PGID })}
			</Alert>
		{/if}
		{#if isInstalled && installedApp}
			<AppStorageWarning app={installedApp} class="mb-4" />
		{/if}

		<!-- Tabs: they wrap on a narrow screen (ADR-0302) -->
		<div class="flex flex-wrap border-b border-border -mx-6 px-6 mb-4">
			<button
				class="flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors {activeTab ===
				'overview'
					? 'text-info border-b-2 border-blue-500'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = 'overview')}
			>
				<Package class="w-4 h-4" />
				{$t('apps.detail.tabs.overview')}
			</button>
			{#if app.ports?.length > 0}
				<button
					class="flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors {activeTab ===
					'ports'
						? 'text-info border-b-2 border-blue-500'
						: 'text-secondary hover:text-content'}"
					on:click={() => (activeTab = 'ports')}
				>
					<Network class="w-4 h-4" />
					{$t('apps.detail.tabs.ports', { count: app.ports.length })}
				</button>
			{/if}
			{#if volumeCount > 0}
				<button
					class="flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors {activeTab ===
					'volumes'
						? 'text-info border-b-2 border-blue-500'
						: 'text-secondary hover:text-content'}"
					on:click={() => (activeTab = 'volumes')}
				>
					<HardDrive class="w-4 h-4" />
					{$t('apps.detail.tabs.volumes', { count: volumeCount })}
				</button>
			{/if}
			{#if app.environment?.length > 0}
				<button
					class="flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors {activeTab ===
					'environment'
						? 'text-info border-b-2 border-blue-500'
						: 'text-secondary hover:text-content'}"
					on:click={() => (activeTab = 'environment')}
				>
					<Settings class="w-4 h-4" />
					{$t('apps.detail.tabs.environment', { count: app.environment.length })}
				</button>
			{/if}
		</div>

		<!-- Tab Content -->
		<div class="min-h-[200px]">
			{#if activeTab === 'overview'}
				<div class="space-y-4">
					<!-- Docker Image: on a phone it goes below its label and breaks (ADR-0302). -->
					<div class="card-nested">
						<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
							<span class="text-secondary text-sm">{$t('apps.detail.image')}</span>
							<code class="mono text-sm min-w-0 break-all">{app.image}</code>
						</div>
					</div>

					<!-- Where the data goes on the selected pool -->
					{#if needsPool && selectedPool && !isInstalled}
						<div class="card-nested">
							<div class="flex items-center gap-2 mb-3">
								<Database class="w-4 h-4 icon-storage" />
								<h4 class="text-sm font-medium">
									{$t('apps.detail.dataLocations', { pool: selectedPool.name })}
								</h4>
							</div>
							<ul class="space-y-2">
								{#each poolVolumes as volume}
									<li class="text-sm">
										<p class="mono break-all">
											{effectiveHostPath(volume, selectedPool, volumeOverrides)}
										</p>
										<p class="text-xs text-muted">
											{$t(volume.readonly ? 'apps.detail.mountedAtReadOnly' : 'apps.detail.mountedAt', {
												path: volume.container
											})}
										</p>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					<!-- Resource Recommendations -->
					{#if app.min_memory || app.recommended_memory}
						<div class="card-nested">
							<h4 class="text-sm font-medium mb-3">{$t('apps.detail.resources')}</h4>
							<div class="grid grid-cols-2 gap-4">
								{#if app.min_memory}
									<div>
										<span class="text-secondary text-sm">{$t('apps.detail.minMemory')}</span>
										<p class="font-medium">{app.min_memory}</p>
									</div>
								{/if}
								{#if app.recommended_memory}
									<div>
										<span class="text-secondary text-sm">{$t('apps.detail.recommendedMemory')}</span>
										<p class="font-medium">{app.recommended_memory}</p>
									</div>
								{/if}
							</div>
						</div>
					{/if}

					<!-- Quick Summary -->
					<div class="grid grid-cols-3 gap-4">
						<div class="card-nested text-center">
							<Network class="w-5 h-5 mx-auto mb-1 text-info" />
							<p class="text-2xl font-bold">{app.ports?.length || 0}</p>
							<p class="text-xs text-secondary">{$t('apps.detail.ports')}</p>
						</div>
						<div class="card-nested text-center">
							<HardDrive class="w-5 h-5 mx-auto mb-1 text-purple-400" />
							<p class="text-2xl font-bold">{volumeCount}</p>
							<p class="text-xs text-secondary">{$t('apps.detail.volumes')}</p>
						</div>
						<div class="card-nested text-center">
							<Settings class="w-5 h-5 mx-auto mb-1 text-orange-400" />
							<p class="text-2xl font-bold">{app.environment?.length || 0}</p>
							<p class="text-xs text-secondary">{$t('apps.detail.variables')}</p>
						</div>
					</div>
				</div>
			{:else if activeTab === 'ports'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">{$t('apps.detail.portsHint')}</p>
					{#each app.ports as port}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<Input
									class="flex-1"
									type="number"
									label={$t('apps.detail.hostPort')}
									bind:value={portValues[port.container]}
									disabled={isInstalled}
									error={invalidPorts.includes(port.container) ? $t('apps.detail.portRange') : ''}
								/>
								<span class="text-secondary pt-8">→</span>
								<Input
									class="flex-1"
									type="number"
									label={$t('apps.detail.containerPort')}
									value={String(port.container)}
									disabled
								/>
								<div class="pt-8">
									<Badge variant="info">{port.protocol.toUpperCase()}</Badge>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{:else if activeTab === 'volumes' && isInstalled}
				<!-- The folders the installed container really uses, not paths on a pool -->
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">{$t('apps.detail.installedVolumesHint')}</p>
					{#if installedApp && installedApp.volumes.length > 0}
						{#each installedApp.volumes as volume}
							<div class="card-nested">
								<div class="flex items-start gap-4">
									<div class="flex-1 min-w-0">
										<p class="form-label">{$t('apps.detail.hostPath')}</p>
										<p class="mono text-sm break-all py-2">{volume.host}</p>
									</div>
									<span class="text-secondary pt-8">→</span>
									<div class="flex-1 min-w-0">
										<p class="form-label">{$t('apps.detail.containerPath')}</p>
										<p class="mono text-sm break-all py-2">{volume.container}</p>
									</div>
									<div class="pt-8 flex flex-col items-end gap-1">
										{#if volume.readonly}
											<Badge variant="warning">{$t('apps.detail.readOnly')}</Badge>
										{:else}
											<Badge variant="neutral">{$t('apps.detail.readWrite')}</Badge>
										{/if}
										{#if installedApp.data_outside_pools.includes(volume.host)}
											<Badge variant="error">{$t('apps.detail.notOnPool')}</Badge>
										{/if}
									</div>
								</div>
							</div>
						{/each}
					{:else}
						<p class="text-sm text-muted">{$t('apps.detail.noVolumes')}</p>
					{/if}
				</div>
			{:else if activeTab === 'volumes'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">
						{#if needsPool}
							{$t('apps.detail.volumesPoolHint')}
						{/if}
						{$t('apps.detail.volumesSystemHint')}
					</p>
					{#each app.volumes as volume}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<div class="flex-1 min-w-0">
									{#if volume.on_pool}
										<Input
											class="font-mono text-sm"
											label={$t('apps.detail.hostPath')}
											value={effectiveHostPath(volume, selectedPool, volumeOverrides)}
											on:input={(e) => setVolumePath(volume, inputValue(e))}
											disabled={isInstalled || selectedPool === null}
											autocomplete="off"
											error={invalidVolumes.includes(volume.container)
												? $t('apps.detail.insidePool')
												: ''}
										/>
									{:else}
										<p class="form-label">{$t('apps.detail.hostPath')}</p>
										<p class="mono text-sm break-all py-2">{volume.host}</p>
									{/if}
								</div>
								<span class="text-secondary pt-8">→</span>
								<div class="flex-1 min-w-0">
									<p class="form-label">{$t('apps.detail.containerPath')}</p>
									<p class="mono text-sm break-all py-2">{volume.container}</p>
								</div>
								<div class="pt-8 flex flex-col items-end gap-1">
									{#if volume.readonly}
										<Badge variant="warning">{$t('apps.detail.readOnly')}</Badge>
									{:else}
										<Badge variant="neutral">{$t('apps.detail.readWrite')}</Badge>
									{/if}
									{#if !volume.on_pool}
										<Badge variant="info">{$t('apps.detail.system')}</Badge>
									{/if}
									{#if volume.is_file}
										<Badge variant="neutral">{$t('apps.detail.file')}</Badge>
									{/if}
								</div>
							</div>
						</div>
					{/each}
				</div>
			{:else if activeTab === 'environment'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">{$t('apps.detail.environmentHint')}</p>
					{#each app.environment as env}
						<div class="card-nested">
							{#if isSetByUnos(env.name)}
								<!-- The server sets PUID and PGID; the install option decides them (ADR-0274). -->
								<Input
									class="font-mono text-sm"
									label={env.name}
									value={setByUnosValue(env, inUnosSmb)}
									disabled
									hint={setByUnosHint(app, env, isInstalled, inUnosSmb)}
								/>
							{:else}
								<Input
									class="font-mono text-sm"
									label={env.name}
									type={env.secret ? 'password' : 'text'}
									bind:value={envValues[env.name]}
									required={env.required}
									disabled={isInstalled}
									hint={env.description}
									placeholder={env.secret
										? $t(env.required ? 'apps.detail.secretPlaceholder' : 'apps.detail.optional')
										: $t('apps.detail.enter', { name: env.name })}
									autocomplete={env.secret ? 'new-password' : 'off'}
									error={missingRequired.includes(env.name) ? $t('apps.detail.required') : ''}
								/>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>

		{#if !isInstalled && blockers.length > 0}
			<Alert variant="warning" class="mt-4">
				{#each blockers as blocker}
					<p>{blocker}</p>
				{/each}
			</Alert>
		{/if}

		{#if error}
			<Alert variant="error" title={$t('apps.detail.installFailed')} class="mt-4">{error}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" on:click={close}>{$t('common.cancel')}</Button>
		{#if isInstalled}
			<Button variant="success" class="flex-1" disabled>
				<Check class="w-4 h-4" />
				{$t('apps.detail.alreadyInstalled')}
			</Button>
		{:else}
			<Button
				variant="primary"
				class="flex-1"
				loading={installing}
				disabled={!canInstall}
				on:click={handleInstall}
				icon={Download}
			>
				{$t('apps.detail.install', { name: app?.name ?? '' })}
			</Button>
		{/if}
	</svelte:fragment>
</Modal>

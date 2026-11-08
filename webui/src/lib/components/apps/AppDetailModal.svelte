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
	import { formatBytes } from '$lib/stores/stats';

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
	// Run the app in the group unos-smb, the only way to give it a share's folder (ADR-0261).
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
		label: `${pool.name} — ${formatBytes(pool.free_bytes)} free of ${formatBytes(pool.total_bytes)}`
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
			? [`Set the required variable${missingRequired.length > 1 ? 's' : ''} ${missingRequired.join(', ')}.`]
			: []),
		...(invalidPorts.length > 0 ? ['Host ports must be numbers between 1 and 65535.'] : []),
		...(invalidVolumes.length > 0
			? [`Paths for ${invalidVolumes.join(', ')} must be inside a mounted storage pool.`]
			: [])
	];
	$: canInstall =
		!!app &&
		!isInstalled &&
		!installing &&
		!poolsLoading &&
		(!needsPool || selectedPool !== null) &&
		blockers.length === 0;

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
			poolsError = describeApiError(e, 'Could not load storage pools');
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

	function setByUnosHint(
		template: AppTemplate,
		env: EnvVar,
		installed: boolean,
		inGroup: boolean
	): string {
		if (installed) {
			return `Set by UNOS: ${template.name} runs ${inGroup ? 'in' : 'outside'} the group unos-smb.`;
		}
		if (!template.can_run_in_unos_smb || !isSetByUnos(env.name)) return 'Set by UNOS.';
		const group = env.name === 'PGID' ? ' (unos-smb)' : '';
		return `Set by UNOS: ${UNOS_SMB_IDS[env.name]}${group} with "Run in the group unos-smb", otherwise ${env.value}.`;
	}

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
			error = describeApiError(e, 'Installation failed');
		} finally {
			installing = false;
		}
	}

	function getSourceBadge(
		source: string | undefined
	): { variant: 'success' | 'info' | 'purple' | 'warning'; label: string } {
		switch (source?.toUpperCase()) {
			case 'OFFICIAL':
				return { variant: 'success', label: 'Official' };
			case 'LINUXSERVER':
				return { variant: 'purple', label: 'LinuxServer.io' };
			case 'VERIFIED':
				return { variant: 'info', label: 'Verified' };
			default:
				return { variant: 'warning', label: 'Community' };
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
							<Badge variant="success">Installed</Badge>
						{/if}
					</div>
					<p class="text-secondary mb-2">{app.description}</p>
					<div class="flex flex-wrap items-center gap-2">
						<Badge variant="neutral">{getCategoryIcon(app.category)} {app.category}</Badge>
						{#if app.version}
							<Badge variant="info">
								<Tag class="w-3 h-3 mr-1" />
								{app.version}
							</Badge>
						{/if}
						<Badge variant={sourceBadge.variant}>
							<Shield class="w-3 h-3 mr-1" />
							{sourceBadge.label}
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
							Website
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
							GitHub
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
							Docs
						</a>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Storage pool for the app's data -->
		{#if needsPool && !isInstalled}
			<div class="mb-4">
				{#if poolsLoading}
					<p class="text-sm text-muted">Loading storage pools...</p>
				{:else if poolsError}
					<Alert variant="error" title="Could not load storage pools">{poolsError}</Alert>
				{:else if pools.length === 0}
					<Alert variant="warning" title="No mounted storage pool">
						<p>
							{app.name} keeps its data on a storage pool, and no pool is mounted. Create or mount
							a pool on the Storage page, then install the app.
						</p>
						<Button variant="secondary" size="sm" href="/storage" icon={HardDrive} class="mt-3">
							Go to Storage
						</Button>
					</Alert>
				{:else}
					<Select
						label="Storage pool"
						options={poolOptions}
						bind:value={selectedPoolName}
						hint="The app's data is stored on this pool."
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
					Run in the group unos-smb
				</label>
				<p id="app-unos-smb-hint" class="form-hint">
					Sets PUID to {UNOS_SMB_IDS.PUID}, PGID to {UNOS_SMB_IDS.PGID} (unos-smb) and UMASK to 007.
					Only an app in this group can be given a share's folder on the Volumes tab: it reads and
					changes every file there, and SMB users can change what it writes.
				</p>
				{#if runInUnosSmb}
					<Alert variant="warning" class="mt-2">
						{app.name} can read and change the files of every share folder you give it. If it can be
						reached from the internet, whoever takes it over gets those files too.
					</Alert>
				{/if}
			</div>
		{/if}
		{#if isInstalled && installedApp?.runs_in_unos_smb}
			<Alert variant="info" class="mb-4">
				{app.name} runs in the group unos-smb (PUID {UNOS_SMB_IDS.PUID}, PGID {UNOS_SMB_IDS.PGID}): it
				reads and changes the files of the share folders it was given.
			</Alert>
		{/if}
		{#if isInstalled && installedApp}
			<AppStorageWarning app={installedApp} class="mb-4" />
		{/if}

		<!-- Tabs -->
		<div class="flex border-b border-border -mx-6 px-6 mb-4">
			<button
				class="flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors {activeTab ===
				'overview'
					? 'text-info border-b-2 border-blue-500'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = 'overview')}
			>
				<Package class="w-4 h-4" />
				Overview
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
					Ports ({app.ports.length})
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
					Volumes ({volumeCount})
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
					Environment ({app.environment.length})
				</button>
			{/if}
		</div>

		<!-- Tab Content -->
		<div class="min-h-[200px]">
			{#if activeTab === 'overview'}
				<div class="space-y-4">
					<!-- Docker Image -->
					<div class="card-nested">
						<div class="flex items-center justify-between">
							<span class="text-secondary text-sm">Docker Image</span>
							<code class="mono text-sm">{app.image}</code>
						</div>
					</div>

					<!-- Where the data goes on the selected pool -->
					{#if needsPool && selectedPool && !isInstalled}
						<div class="card-nested">
							<div class="flex items-center gap-2 mb-3">
								<Database class="w-4 h-4 icon-storage" />
								<h4 class="text-sm font-medium">Data locations on {selectedPool.name}</h4>
							</div>
							<ul class="space-y-2">
								{#each poolVolumes as volume}
									<li class="text-sm">
										<p class="mono break-all">
											{effectiveHostPath(volume, selectedPool, volumeOverrides)}
										</p>
										<p class="text-xs text-muted">
											mounted at {volume.container}{volume.readonly ? ' (read-only)' : ''}
										</p>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					<!-- Resource Recommendations -->
					{#if app.min_memory || app.recommended_memory}
						<div class="card-nested">
							<h4 class="text-sm font-medium mb-3">Resource Recommendations</h4>
							<div class="grid grid-cols-2 gap-4">
								{#if app.min_memory}
									<div>
										<span class="text-secondary text-sm">Minimum Memory</span>
										<p class="font-medium">{app.min_memory}</p>
									</div>
								{/if}
								{#if app.recommended_memory}
									<div>
										<span class="text-secondary text-sm">Recommended Memory</span>
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
							<p class="text-xs text-secondary">Ports</p>
						</div>
						<div class="card-nested text-center">
							<HardDrive class="w-5 h-5 mx-auto mb-1 text-purple-400" />
							<p class="text-2xl font-bold">{volumeCount}</p>
							<p class="text-xs text-secondary">Volumes</p>
						</div>
						<div class="card-nested text-center">
							<Settings class="w-5 h-5 mx-auto mb-1 text-orange-400" />
							<p class="text-2xl font-bold">{app.environment?.length || 0}</p>
							<p class="text-xs text-secondary">Variables</p>
						</div>
					</div>
				</div>
			{:else if activeTab === 'ports'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">
						Configure port mappings. Modify host ports if they conflict with existing services.
					</p>
					{#each app.ports as port}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<Input
									class="flex-1"
									type="number"
									label="Host Port"
									bind:value={portValues[port.container]}
									disabled={isInstalled}
									error={invalidPorts.includes(port.container) ? 'Use a port from 1 to 65535' : ''}
								/>
								<span class="text-secondary pt-8">→</span>
								<Input
									class="flex-1"
									type="number"
									label="Container Port"
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
					<p class="text-sm text-secondary mb-4">Host folders this installed app uses.</p>
					{#if installedApp && installedApp.volumes.length > 0}
						{#each installedApp.volumes as volume}
							<div class="card-nested">
								<div class="flex items-start gap-4">
									<div class="flex-1 min-w-0">
										<p class="form-label">Host Path</p>
										<p class="mono text-sm break-all py-2">{volume.host}</p>
									</div>
									<span class="text-secondary pt-8">→</span>
									<div class="flex-1 min-w-0">
										<p class="form-label">Container Path</p>
										<p class="mono text-sm break-all py-2">{volume.container}</p>
									</div>
									<div class="pt-8 flex flex-col items-end gap-1">
										{#if volume.readonly}
											<Badge variant="warning">RO</Badge>
										{:else}
											<Badge variant="neutral">RW</Badge>
										{/if}
										{#if installedApp.data_outside_pools.includes(volume.host)}
											<Badge variant="error">Not on a pool</Badge>
										{/if}
									</div>
								</div>
							</div>
						{/each}
					{:else}
						<p class="text-sm text-muted">No host folders are mounted into this app.</p>
					{/if}
				</div>
			{:else if activeTab === 'volumes'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">
						{#if needsPool}
							Data folders are created on the selected storage pool. A data folder can point to
							another folder inside a mounted pool, never into a snapshot or the trash, and to a
							share's folder only for an app that runs in the group unos-smb.
						{/if}
						System paths marked "System" are mounted from the host as they are.
					</p>
					{#each app.volumes as volume}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<div class="flex-1 min-w-0">
									{#if volume.on_pool}
										<Input
											class="font-mono text-sm"
											label="Host Path"
											value={effectiveHostPath(volume, selectedPool, volumeOverrides)}
											on:input={(e) => setVolumePath(volume, inputValue(e))}
											disabled={isInstalled || selectedPool === null}
											autocomplete="off"
											error={invalidVolumes.includes(volume.container)
												? 'Must be inside a mounted storage pool'
												: ''}
										/>
									{:else}
										<p class="form-label">Host Path</p>
										<p class="mono text-sm break-all py-2">{volume.host}</p>
									{/if}
								</div>
								<span class="text-secondary pt-8">→</span>
								<div class="flex-1 min-w-0">
									<p class="form-label">Container Path</p>
									<p class="mono text-sm break-all py-2">{volume.container}</p>
								</div>
								<div class="pt-8 flex flex-col items-end gap-1">
									{#if volume.readonly}
										<Badge variant="warning">RO</Badge>
									{:else}
										<Badge variant="neutral">RW</Badge>
									{/if}
									{#if !volume.on_pool}
										<Badge variant="info">System</Badge>
									{/if}
									{#if volume.is_file}
										<Badge variant="neutral">File</Badge>
									{/if}
								</div>
							</div>
						</div>
					{/each}
				</div>
			{:else if activeTab === 'environment'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">
						Configure environment variables. Required variables must be set before installation.
					</p>
					{#each app.environment as env}
						<div class="card-nested">
							{#if isSetByUnos(env.name)}
								<!-- The server sets PUID and PGID; the install option decides them (ADR-0261). -->
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
									placeholder={env.secret ? (env.required ? 'Choose a unique value' : 'Optional') : `Enter ${env.name}`}
									autocomplete={env.secret ? 'new-password' : 'off'}
									error={missingRequired.includes(env.name) ? 'Required' : ''}
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
			<Alert variant="error" title="Installation failed" class="mt-4">{error}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" on:click={close}>Cancel</Button>
		{#if isInstalled}
			<Button variant="success" class="flex-1" disabled>
				<Check class="w-4 h-4" />
				Already Installed
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
				Install {app?.name ?? ''}
			</Button>
		{/if}
	</svelte:fragment>
</Modal>

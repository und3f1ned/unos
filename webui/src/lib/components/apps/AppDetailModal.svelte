<script lang="ts">
	import { untrack } from 'svelte';
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
	import { appInstalls } from '$lib/stores/appInstalls';
	import { wsConnected } from '$lib/stores/stats';
	import { ApiError } from '$lib/api/client';
	import AppCategory from './AppCategory.svelte';
	import AppStorageWarning from './AppStorageWarning.svelte';
	import AppHardwareHint from './AppHardwareHint.svelte';
	import {
		type AppContainer,
		type AppTemplate,
		type AppPool,
		type AppInstallJob,
		type AppInstallRequest,
		type AppVolume,
		type InstalledApp,
		type PortMapping,
		type VolumeMapping,
		type EnvVar,
		appContainers,
		getAppPools,
		getInstallJob,
		installApp,
		poolHostPath,
		isInsidePools,
		describeApiError,
		isSetByUnos,
		isUserOrGroup,
		UNOS_SMB_IDS
	} from '$lib/api/apps';

	type Props = {
		app?: AppTemplate | null;
		open?: boolean;
		isInstalled?: boolean;
		/** The installed instance of the app, if any: its real host folders are shown. */
		installedApp?: InstalledApp | null;
		/** Cancel, Escape or the backdrop asked to close the dialog, or its install was handed on. */
		onclose?: () => void;
		/** The install's checks have passed (or it ended): the App Store's card follows its job. */
		onstarted?: (started: { app: AppTemplate; job: AppInstallJob }) => void;
	};

	let {
		app = null,
		open = $bindable(false),
		isInstalled = false,
		installedApp = null,
		onclose,
		onstarted
	}: Props = $props();

	let installing = $state(false);
	let error = $state('');
	// The install that this dialog started, while its job checks (ADR-0384): the dialog stays open
	// until the checks have passed, so that a refusal (a port in use, too little space, a share's
	// folder) shows here, where the settings can be changed; then the App Store's card follows it.
	let pending = $state<AppInstallJob | null>(null);
	// Counts the installs asked and the closes: an answer that comes after the dialog was closed
	// belongs to no dialog any more.
	let installRequest = 0;
	const watched = $derived(pending ? ($appInstalls.find((job) => job.id === pending?.id) ?? pending) : null);
	// Each news of the job; only `watched` makes it run again. When follow drops the job, it runs
	// once more and finds none.
	$effect.pre(() => {
		const job = watched;
		untrack(() => {
			if (job) follow(job);
		});
	});
	// The events socket signed in again while the job checked: maybe to a supervisor that restarted
	// and forgot the job, whose end then never comes. The job is asked for once (ADR-0384).
	let wasConnected = false;
	$effect.pre(() => {
		const connected = $wsConnected;
		untrack(() => noteConnection(connected));
	});

	function noteConnection(connected: boolean) {
		if (connected && !wasConnected && pending) void askAgain(pending.id);
		wasConnected = connected;
	}

	async function askAgain(id: string) {
		try {
			const job = await getInstallJob(id);
			if (pending?.id === id) pending = job;
		} catch (e) {
			if (pending?.id !== id) return;
			pending = null;
			installing = false;
			error =
				e instanceof ApiError && e.status === 404
					? $t('apps.detail.jobLost')
					: describeApiError(e, $t('apps.detail.installFailed'));
		}
	}
	// Another install that runs: one install at a time (ADR-0384).
	const otherInstall = $derived(
		$appInstalls.find((job) => job.state === 'running' && job.id !== pending?.id) ?? null
	);
	let activeTab = $state<'overview' | 'ports' | 'volumes' | 'environment'>('overview');

	// Form state, reset whenever an app is shown
	let portValues = $state<Record<number, string>>({});
	let volumeOverrides = $state<Record<string, string>>({});
	let envValues = $state<Record<string, string>>({});
	// Run the app in the group unos-smb, the only way to give it a share's folder (ADR-0274).
	let runInUnosSmb = $state(false);

	// Storage pools, reloaded every time the modal opens
	let pools = $state<AppPool[]>([]);
	let poolsLoading = $state(false);
	let poolsError = $state('');
	let selectedPoolName = $state('');
	let poolsRequest = 0;
	let wasOpen = false;

	const sourceBadge = $derived(getSourceBadge(app?.source));
	// Another app shown: the form starts again. Before the markup updates, as Svelte 4's statement
	// did, so that the dialog never shows the last app's values; only `app` makes it run again.
	$effect.pre(() => {
		const template = app;
		untrack(() => {
			if (template) resetForm(template);
		});
	});
	// Opened or closed, before the markup updates; only `open` makes it run again.
	$effect.pre(() => {
		const isOpen = open;
		untrack(() => handleOpenChange(isOpen));
	});
	// Whether the app runs (installed) or is to run (the form) in the group unos-smb.
	const inUnosSmb = $derived(isInstalled ? (installedApp?.runs_in_unos_smb ?? false) : runInUnosSmb);

	// An app in the host's network binds its container ports on the host: they cannot be moved (ADR-0381).
	const inHostNetwork = $derived(app ? isInHostNetwork(app) : false);

	// The app's containers, its main container first (ADR-0381): an app of several lists each.
	const containers = $derived(app ? appContainers(app) : []);
	const several = $derived(containers.length > 1);

	const selectedPool = $derived(pools.find((pool) => pool.name === selectedPoolName) ?? null);
	const poolOptions = $derived(
		pools.map((pool) => ({
			value: pool.name,
			label: $t('apps.detail.poolOption', {
				name: pool.name,
				free: formatSize(pool.free_bytes),
				total: formatSize(pool.total_bytes)
			})
		}))
	);
	const needsPool = $derived(containers.some((container) => container.spec.volumes.some((volume) => volume.on_pool)));
	const dataLocations = $derived(selectedPool ? locationsOf(containers, selectedPool, volumeOverrides) : []);
	// An installed app lists the folders its containers really use.
	const volumeCount = $derived(
		isInstalled && installedApp
			? installedApp.volumes.length
			: containers.reduce((sum, container) => sum + container.spec.volumes.length, 0)
	);
	const variableCount = $derived(containers.reduce((sum, container) => sum + container.spec.environment.length, 0));
	const installedVolumes = $derived(isInstalled && installedApp ? volumesPerContainer(installedApp) : []);

	// Client-side checks for a clear form; the server validates everything again.
	const missingRequired = $derived(
		app
			? app.environment
					.filter((env) => env.required && !(envValues[env.name] ?? '').trim())
					.map((env) => env.name)
			: []
	);
	const invalidPorts = $derived(
		app
			? app.ports
					.filter((port) => !isValidPort(portValues[port.container]))
					.map((port) => port.container)
			: []
	);
	const invalidVolumes = $derived(
		Object.entries(volumeOverrides)
			.filter(([, path]) => !isInsidePools(path, pools))
			.map(([container]) => container)
	);
	const blockers = $derived([
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
			: []),
		...(app && !app.available ? [$t('apps.detail.notAvailable', { name: app.name, platform: app.host_platform ?? '' })] : []),
		...(otherInstall ? [$t('apps.detail.installRunning', { name: otherInstall.name })] : [])
	]);
	const canInstall = $derived(
		!!app &&
			!isInstalled &&
			!installing &&
			!poolsLoading &&
			(!needsPool || selectedPool !== null) &&
			blockers.length === 0
	);

	// The apps whose database and jobs keep the pool's hard disks awake while they run (ADR-0381, ADR-0415).
	const DISK_AWAKE: ReadonlySet<string> = new Set(['immich', 'bookstack', 'paperless-ngx', 'linkwarden']);

	// The units of a size, by powers of 1024, as formatBytes of $lib/stores/stats writes them.
	const SIZE_UNITS = ['apps.size.bytes', 'apps.size.kb', 'apps.size.mb', 'apps.size.gb', 'apps.size.tb'];

	/** A size in the language of the page: the number in its format with the unit of the catalog. */
	function formatSize(bytes: number): string {
		const power = Math.max(0, Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), SIZE_UNITS.length - 1));
		const size = new Intl.NumberFormat($intlLocale, { maximumFractionDigits: 2, useGrouping: false }).format(
			bytes / 1024 ** power
		);
		return $t(SIZE_UNITS[power], { size });
	}

	/** Whether `template` runs in the host's network, where Docker discards its port bindings. */
	function isInHostNetwork(template: AppTemplate): boolean {
		return template.network_mode === 'host';
	}

	/** The host port of `port`: the container port itself for an app in the host's network. */
	function hostPortOf(template: AppTemplate, port: PortMapping): number {
		return isInHostNetwork(template) ? port.container : port.host;
	}

	function resetForm(template: AppTemplate) {
		portValues = Object.fromEntries(
			template.ports.map((port) => [port.container, String(hostPortOf(template, port))])
		);
		volumeOverrides = {};
		envValues = Object.fromEntries(template.environment.map((env) => [env.name, env.value]));
		runInUnosSmb = false;
		error = '';
		activeTab = 'overview';
	}

	function handleOpenChange(isOpen: boolean) {
		if (!isOpen && wasOpen) {
			// Closed while its install checked: the job goes on, and the App Store's card shows it.
			installRequest++;
			pending = null;
			installing = false;
		}
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

	/** A folder or file on the selected pool, with where each of the app's containers mounts it. */
	interface DataLocation {
		host: string;
		/** A password file that UNOS generates (ADR-0381). */
		secret: boolean;
		mounts: { container: string; path: string; readonly: boolean }[];
	}

	/**
	 * Where the app's data goes on `pool`: each source once, with its mounts. Only the main
	 * container's folders take an override; a password file has a fixed path.
	 */
	function locationsOf(
		ofApp: AppContainer[],
		pool: AppPool,
		overrides: Record<string, string>
	): DataLocation[] {
		const byHost = new Map<string, DataLocation>();
		for (const container of ofApp) {
			for (const volume of container.spec.volumes) {
				if (!volume.on_pool) continue;
				const host =
					container.companion === null && !volume.generated_secret
						? effectiveHostPath(volume, pool, overrides)
						: poolHostPath(volume, pool.path);
				const location = byHost.get(host) ?? { host, secret: volume.generated_secret, mounts: [] };
				location.mounts.push({ container: container.name, path: volume.container, readonly: volume.readonly });
				byHost.set(host, location);
			}
		}
		return [...byHost.values()];
	}

	/**
	 * The folders of an installed app per container, as its containers really use them: the main
	 * container's first, then each companion's in the catalog's order.
	 */
	function volumesPerContainer(installed: InstalledApp): { name: string; volumes: AppVolume[] }[] {
		const groups = [
			{ companion: null as string | null, name: installed.name },
			...installed.companions.map((companion) => ({ companion: companion.name as string | null, name: companion.container }))
		];
		return groups
			.map((group) => ({
				name: group.name,
				volumes: installed.volumes.filter((volume) => volume.companion === group.companion)
			}))
			.filter((group) => group.volumes.length > 0);
	}

	/** Whether an installed app's folder is a password file that UNOS generated (ADR-0381). */
	function isGeneratedSecret(template: AppTemplate, volume: AppVolume): boolean {
		const spec =
			volume.companion === null ? template : template.companions.find((companion) => companion.name === volume.companion);
		return spec?.volumes.some((mapping) => mapping.generated_secret && mapping.container === volume.container) ?? false;
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
		// The server refuses to move a port of an app in the host's network: none is sent.
		if (isInHostNetwork(template)) return result;
		for (const port of template.ports) {
			const value = Number(portValues[port.container]);
			if (value !== port.host) result[port.container] = value;
		}
		return result;
	}

	function changedEnv(template: AppTemplate): Record<string, string> {
		const result: Record<string, string> = {};
		for (const env of template.environment) {
			// The server sets PUID and PGID (the template's values, or the group's with the option) and
			// the wiring between the app's containers.
			if (isSetByUnos(env)) continue;
			const value = envValues[env.name] ?? env.value;
			if (value !== env.value) result[env.name] = value;
		}
		return result;
	}

	/** A variable as UNOS sets it: PUID and PGID the group's in unos-smb, otherwise the template's value. */
	function setByUnosValue(env: EnvVar, inGroup: boolean): string {
		return inGroup && isUserOrGroup(env.name) ? String(UNOS_SMB_IDS[env.name]) : env.value;
	}

	// It reads $t: the hint follows the language of the page.
	function setByUnosHint(template: AppTemplate, env: EnvVar, installed: boolean, inGroup: boolean): string {
		// The wiring between an app's containers, and every variable of a companion.
		if (!isUserOrGroup(env.name)) return $t('apps.detail.setByUnos');
		if (installed) {
			return $t(inGroup ? 'apps.detail.setByUnosIn' : 'apps.detail.setByUnosOutside', { name: template.name });
		}
		if (!template.can_run_in_unos_smb) return $t('apps.detail.setByUnos');
		return $t(env.name === 'PGID' ? 'apps.detail.setByUnosOptionGroup' : 'apps.detail.setByUnosOption', {
			id: UNOS_SMB_IDS[env.name],
			value: env.value
		});
	}

	function close() {
		onclose?.();
	}

	async function handleInstall() {
		if (!app || !canInstall) return;
		const template = app;

		installing = true;
		error = '';
		const request = ++installRequest;

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

			const job = await installApp(req);
			if (request === installRequest) pending = job;
		} catch (e) {
			if (request !== installRequest) return;
			error = describeApiError(e, $t('apps.detail.installFailed'));
			installing = false;
		}
	}

	/** The job of this dialog's install as it goes: a refusal of its checks shows here; once they
	 * have passed, or it ended, the dialog hands it to the App Store and closes. */
	function follow(job: AppInstallJob) {
		if (job.state === 'failed' || job.state === 'cancelled') {
			error = job.error || $t('apps.detail.installFailed');
			pending = null;
			installing = false;
		} else if (job.state === 'succeeded' || job.step !== 'checking') {
			const template = app;
			pending = null;
			installing = false;
			if (template) onstarted?.({ app: template, job });
			close();
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

<Modal {open} size="xl" title="" onclose={close}>
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
							class="flex items-center gap-1.5 text-sm text-info hover:text-status-info transition-colors"
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
		<AppHardwareHint {app} installed={isInstalled ? installedApp : null} class="mb-4" />

		<!-- Tabs: they wrap on a narrow screen (ADR-0302) -->
		<div class="flex flex-wrap border-b border-border -mx-6 px-6 mb-4">
			<Button variant="plain"
			 class="tab-underline {activeTab === 'overview' ? 'tab-underline-active' : ''}"
				onclick={() => (activeTab = 'overview')}
			>
				<Package class="w-4 h-4" />
				{$t('apps.detail.tabs.overview')}
			</Button>
			{#if app.ports?.length > 0}
				<Button variant="plain"
				 class="tab-underline {activeTab === 'ports' ? 'tab-underline-active' : ''}"
					onclick={() => (activeTab = 'ports')}
				>
					<Network class="w-4 h-4" />
					{$t('apps.detail.tabs.ports', { count: app.ports.length })}
				</Button>
			{/if}
			{#if volumeCount > 0}
				<Button variant="plain"
				 class="tab-underline {activeTab === 'volumes' ? 'tab-underline-active' : ''}"
					onclick={() => (activeTab = 'volumes')}
				>
					<HardDrive class="w-4 h-4" />
					{$t('apps.detail.tabs.volumes', { count: volumeCount })}
				</Button>
			{/if}
			{#if variableCount > 0}
				<Button variant="plain"
				 class="tab-underline {activeTab === 'environment' ? 'tab-underline-active' : ''}"
					onclick={() => (activeTab = 'environment')}
				>
					<Settings class="w-4 h-4" />
					{$t('apps.detail.tabs.environment', { count: variableCount })}
				</Button>
			{/if}
		</div>

		<!-- Tab Content -->
		<div class="min-h-[200px]">
			{#if activeTab === 'overview'}
				<div class="space-y-4">
					{#if several}
						<!-- Each container with the image it is created from (ADR-0381); the images break on a phone. -->
						<div class="card-nested">
							<h4 class="text-sm font-medium mb-1">{$t('apps.detail.containers')}</h4>
							<p class="text-xs text-muted mb-3">
								{$t('apps.detail.containersHint', { name: app.name, count: containers.length })}
							</p>
							<ul class="space-y-2">
								{#each containers as container (container.name)}
									<li class="text-sm min-w-0">
										<p class="mono break-all">{container.name}</p>
										<code class="mono text-xs text-muted break-all">{container.spec.image}</code>
									</li>
								{/each}
							</ul>
							{#if app.system_disk_bytes}
								<p class="text-xs text-muted mt-3">
									{$t('apps.detail.imagesSize', { name: app.name, size: formatSize(app.system_disk_bytes) })}
								</p>
							{/if}
						</div>
					{:else}
						<!-- Docker Image: on a phone it goes below its label and breaks (ADR-0302). -->
						<div class="card-nested">
							<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
								<span class="text-secondary text-sm">{$t('apps.detail.image')}</span>
								<code class="mono text-sm min-w-0 break-all">{app.image}</code>
							</div>
							{#if app.system_disk_bytes}
								<p class="text-xs text-muted mt-2">
									{$t('apps.detail.imagesSize', { name: app.name, size: formatSize(app.system_disk_bytes) })}
								</p>
							{/if}
						</div>
					{/if}

					{#if DISK_AWAKE.has(app.id)}
						<Alert variant="info">{$t('apps.detail.diskAwake', { name: app.name })}</Alert>
					{/if}

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
								{#each dataLocations as location (location.host)}
									<li class="text-sm">
										<p class="mono break-all">{location.host}</p>
										{#each location.mounts as mount}
											<p class="text-xs text-muted break-all">
												{#if several}
													{$t(mount.readonly ? 'apps.detail.mountedAtInReadOnly' : 'apps.detail.mountedAtIn', {
														path: mount.path,
														container: mount.container
													})}
												{:else}
													{$t(mount.readonly ? 'apps.detail.mountedAtReadOnly' : 'apps.detail.mountedAt', {
														path: mount.path
													})}
												{/if}
											</p>
										{/each}
										{#if location.secret}
											<p class="text-xs text-muted">{$t('apps.detail.generatedSecret')}</p>
										{/if}
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
							<HardDrive class="w-5 h-5 mx-auto mb-1 text-accent-memory" />
							<p class="text-2xl font-bold">{volumeCount}</p>
							<p class="text-xs text-secondary">{$t('apps.detail.volumes')}</p>
						</div>
						<div class="card-nested text-center">
							<Settings class="w-5 h-5 mx-auto mb-1 text-accent-temperature" />
							<p class="text-2xl font-bold">{variableCount}</p>
							<p class="text-xs text-secondary">{$t('apps.detail.variables')}</p>
						</div>
					</div>
				</div>
			{:else if activeTab === 'ports'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">
						{inHostNetwork
							? $t('apps.detail.portsHostNetwork', { name: app.name })
							: $t('apps.detail.portsHint')}
					</p>
					{#each app.ports as port}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<Input
									class="flex-1"
									type="number"
									label={$t('apps.detail.hostPort')}
									bind:value={portValues[port.container]}
									disabled={isInstalled || inHostNetwork}
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
				<!-- The folders the installed containers really use, not paths on a pool -->
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">{$t('apps.detail.installedVolumesHint')}</p>
					{#if installedApp && installedVolumes.length > 0}
						{#each installedVolumes as group (group.name)}
							<!-- An app of several containers groups the folders per container (ADR-0381). -->
							<div
								class="space-y-3"
								role={several ? 'group' : undefined}
								aria-labelledby={several ? `app-volumes-${group.name}` : undefined}
							>
								{#if several}
									<h4 id={`app-volumes-${group.name}`} class="text-sm font-medium mono break-all pt-2">
										{group.name}
									</h4>
								{/if}
								{#each group.volumes as volume}
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
										{#if isGeneratedSecret(app, volume)}
											<p class="text-xs text-muted mt-2">{$t('apps.detail.generatedSecret')}</p>
										{/if}
									</div>
								{/each}
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
					{#each containers as container (container.name)}
						{#if container.spec.volumes.length > 0}
							<!-- An app of several containers groups the folders per container; only the main
							     container's data folders can be moved, a password file never (ADR-0381). -->
							<div
								class="space-y-3"
								role={several ? 'group' : undefined}
								aria-labelledby={several ? `app-volumes-${container.name}` : undefined}
							>
								{#if several}
									<h4 id={`app-volumes-${container.name}`} class="text-sm font-medium mono break-all pt-2">
										{container.name}
									</h4>
								{/if}
								{#each container.spec.volumes as volume}
									<div class="card-nested">
										<div class="flex items-start gap-4">
											<div class="flex-1 min-w-0">
												{#if volume.on_pool && container.companion === null && !volume.generated_secret}
													<Input
														class="font-mono text-sm"
														label={$t('apps.detail.hostPath')}
														value={effectiveHostPath(volume, selectedPool, volumeOverrides)}
														oninput={(e) => setVolumePath(volume, inputValue(e))}
														disabled={isInstalled || selectedPool === null}
														autocomplete="off"
														error={invalidVolumes.includes(volume.container)
															? $t('apps.detail.insidePool')
															: ''}
													/>
												{:else}
													<p class="form-label">{$t('apps.detail.hostPath')}</p>
													<p class="mono text-sm break-all py-2">
														{volume.on_pool && selectedPool ? poolHostPath(volume, selectedPool.path) : volume.host}
													</p>
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
										{#if volume.generated_secret}
											<p class="text-xs text-muted mt-2">{$t('apps.detail.generatedSecret')}</p>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					{/each}
				</div>
			{:else if activeTab === 'environment'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">{$t('apps.detail.environmentHint')}</p>
					{#each containers as container (container.name)}
						{#if container.spec.environment.length > 0}
							<!-- An app of several containers groups the variables per container (ADR-0381). -->
							<div
								class="space-y-3"
								role={several ? 'group' : undefined}
								aria-labelledby={several ? `app-environment-${container.name}` : undefined}
							>
								{#if several}
									<h4 id={`app-environment-${container.name}`} class="text-sm font-medium mono break-all pt-2">
										{container.name}
									</h4>
								{/if}
								{#each container.spec.environment as env}
									<div class="card-nested">
										{#if container.companion !== null}
											<!-- A companion takes no override: UNOS sets all its variables (ADR-0381). -->
											<Input
												class="font-mono text-sm"
												label={env.name}
												value={env.value}
												disabled
												hint={$t('apps.detail.setByUnos')}
											/>
										{:else if isSetByUnos(env)}
											<!-- The server sets PUID and PGID, which the install option decides (ADR-0274), and
											     the wiring between the app's containers (ADR-0381). -->
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

		{#if pending}
			<Alert variant="info" class="mt-4">{$t('apps.detail.checking')}</Alert>
		{/if}

		{#if error}
			<Alert variant="error" title={$t('apps.detail.installFailed')} class="mt-4">{error}</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" onclick={close}>{$t('common.cancel')}</Button>
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
				onclick={handleInstall}
				icon={Download}
			>
				{$t('apps.detail.install', { name: app?.name ?? '' })}
			</Button>
		{/if}
	{/snippet}
</Modal>

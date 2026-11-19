<script lang="ts">
	// The details of an app of the App Store, with its install form: the pool for its data, the
	// group option, its ports, folders and variables (one tab each), and Install, which starts its
	// job and follows the job's checks (ADR-0384).
	import { untrack } from 'svelte';
	import { Download, Check } from 'lucide-svelte';
	import { Button, Modal, Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { appInstalls } from '$lib/stores/appInstalls';
	import { wsConnected } from '$lib/stores/stats';
	import { ApiError } from '$lib/api/client';
	import AppDetailHeader from './AppDetailHeader.svelte';
	import AppDetailTabs from './AppDetailTabs.svelte';
	import AppEnvironmentTab from './AppEnvironmentTab.svelte';
	import AppHardwareHint from './AppHardwareHint.svelte';
	import AppInstallOptions from './AppInstallOptions.svelte';
	import AppOverviewTab from './AppOverviewTab.svelte';
	import AppPortsTab from './AppPortsTab.svelte';
	import AppStorageWarning from './AppStorageWarning.svelte';
	import AppVolumesTab from './AppVolumesTab.svelte';
	import {
		changedEnv,
		changedPorts,
		hostPortOf,
		isInHostNetwork,
		isValidPort,
		locationsOf,
		volumesPerContainer,
		type DetailTab
	} from './detail';
	import { PoolChoice } from './pools.svelte';
	import {
		type AppTemplate,
		type AppInstallJob,
		type AppInstallRequest,
		type InstalledApp,
		type VolumeMapping,
		appContainers,
		getInstallJob,
		installApp,
		poolHostPath,
		isInsidePools,
		describeApiError,
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
	let activeTab = $state<DetailTab>('overview');

	// Form state, reset whenever an app is shown
	let portValues = $state<Record<number, string>>({});
	let volumeOverrides = $state<Record<string, string>>({});
	let envValues = $state<Record<string, string>>({});
	// Run the app in the group unos-smb, the only way to give it a share's folder (ADR-0274).
	let runInUnosSmb = $state(false);

	// Storage pools, reloaded every time the modal opens
	const pools = new PoolChoice();
	let wasOpen = false;

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

	const selectedPool = $derived(pools.selected);
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
			.filter(([, path]) => !isInsidePools(path, pools.pools))
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
			!pools.loading &&
			(!needsPool || selectedPool !== null) &&
			blockers.length === 0
	);

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
		// An installed app already has its folders; no pool is chosen for it.
		if (isOpen && !wasOpen) pools.opened(isInstalled);
		wasOpen = isOpen;
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
			const ports = changedPorts(template, portValues);
			if (Object.keys(ports).length > 0) req.port_overrides = ports;
			const volumes = Object.fromEntries(
				Object.entries(volumeOverrides).map(([container, path]) => [container, path.trim()])
			);
			if (Object.keys(volumes).length > 0) req.volume_overrides = volumes;
			const env = changedEnv(template, envValues);
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
</script>

<Modal {open} size="xl" title="" onclose={close}>
	{#if app}
		<AppDetailHeader {app} {isInstalled} />

		<AppInstallOptions {app} {isInstalled} {needsPool} {pools} bind:runInUnosSmb />
		{#if isInstalled && installedApp?.runs_in_unos_smb}
			<Alert variant="info" class="mb-4">
				{$t('apps.detail.runsInUnosSmb', { name: app.name, puid: UNOS_SMB_IDS.PUID, pgid: UNOS_SMB_IDS.PGID })}
			</Alert>
		{/if}
		{#if isInstalled && installedApp}
			<AppStorageWarning app={installedApp} class="mb-4" />
		{/if}
		<AppHardwareHint {app} installed={isInstalled ? installedApp : null} class="mb-4" />

		<AppDetailTabs
			active={activeTab}
			portCount={app.ports?.length ?? 0}
			{volumeCount}
			{variableCount}
			onselect={(tab) => (activeTab = tab)}
		/>

		<!-- Tab Content -->
		<div class="min-h-[200px]">
			{#if activeTab === 'overview'}
				<AppOverviewTab
					{app}
					{containers}
					{several}
					{isInstalled}
					{needsPool}
					{selectedPool}
					{dataLocations}
					{volumeCount}
					{variableCount}
				/>
			{:else if activeTab === 'ports'}
				<AppPortsTab {app} bind:portValues {inHostNetwork} {isInstalled} {invalidPorts} />
			{:else if activeTab === 'volumes'}
				<AppVolumesTab
					{app}
					{isInstalled}
					{installedApp}
					{installedVolumes}
					{containers}
					{several}
					{needsPool}
					{selectedPool}
					{volumeOverrides}
					{invalidVolumes}
					onpath={setVolumePath}
				/>
			{:else if activeTab === 'environment'}
				<AppEnvironmentTab
					{app}
					{containers}
					{several}
					{isInstalled}
					{inUnosSmb}
					bind:envValues
					{missingRequired}
				/>
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

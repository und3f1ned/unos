<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import {
		ExternalLink,
		Globe,
		Github,
		BookOpen,
		Network,
		HardDrive,
		Settings,
		Download,
		Check,
		AlertCircle,
		Package,
		Tag,
		Shield
	} from 'lucide-svelte';
	import { Button, Badge, Modal, Alert, Input } from '$lib/components/ui';
	import { type AppTemplate, installApp, type AppInstallRequest } from '$lib/api/apps';

	export let app: AppTemplate | null = null;
	export let open = false;
	export let isInstalled = false;

	const dispatch = createEventDispatcher();

	let installing = false;
	let error = '';
	let activeTab: 'overview' | 'ports' | 'volumes' | 'environment' = 'overview';

	// Configuration overrides
	let portOverrides: Record<number, number> = {};
	let volumeOverrides: Record<string, string> = {};
	let envOverrides: Record<string, string> = {};
	$: sourceBadge = app ? getSourceBadge(app.source) : { variant: "warning" as const, label: "Unknown" };


	$: if (app) {
		// Reset overrides when app changes
		portOverrides = {};
		volumeOverrides = {};
		envOverrides = {};
		error = '';
		activeTab = 'overview';
	}

	function close() {
		dispatch('close');
	}

	async function handleInstall() {
		if (!app) return;

		installing = true;
		error = '';

		try {
			const req: AppInstallRequest = {
				template_id: app.id,
				port_overrides: Object.keys(portOverrides).length > 0 ? portOverrides : undefined,
				volume_overrides: Object.keys(volumeOverrides).length > 0 ? volumeOverrides : undefined,
				env_overrides: Object.keys(envOverrides).length > 0 ? envOverrides : undefined
			};

			await installApp(req);
			dispatch('installed', { app });
			close();
		} catch (e: any) {
			error = e.message || 'Installation failed';
		} finally {
			installing = false;
		}
	}

	function getSourceBadge(
		source: string
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
			{#if app.volumes?.length > 0}
				<button
					class="flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors {activeTab ===
					'volumes'
						? 'text-info border-b-2 border-blue-500'
						: 'text-secondary hover:text-content'}"
					on:click={() => (activeTab = 'volumes')}
				>
					<HardDrive class="w-4 h-4" />
					Volumes ({app.volumes.length})
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
							<p class="text-2xl font-bold">{app.volumes?.length || 0}</p>
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
							<div class="flex items-center gap-4">
								<div class="flex-1">
									<label class="form-label">Host Port</label>
									<input
										type="number"
										value={portOverrides[port.container] ?? port.host}
										on:input={(e) => {
											const value = parseInt(e.currentTarget.value);
											if (value !== port.host) {
												portOverrides[port.container] = value;
											} else {
												delete portOverrides[port.container];
											}
											portOverrides = portOverrides;
										}}
										class="form-input"
									/>
								</div>
								<span class="text-secondary pt-6">→</span>
								<div class="flex-1">
									<label class="form-label">Container Port</label>
									<input
										type="number"
										value={port.container}
										disabled
										class="form-input bg-surface-overlay opacity-60"
									/>
								</div>
								<div class="pt-6">
									<Badge variant="info">{port.protocol.toUpperCase()}</Badge>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{:else if activeTab === 'volumes'}
				<div class="space-y-3">
					<p class="text-sm text-secondary mb-4">
						Configure volume paths. Data will be stored at these locations on the host.
					</p>
					{#each app.volumes as volume}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<div class="flex-1">
									<label class="form-label">Host Path</label>
									<input
										type="text"
										value={volumeOverrides[volume.container] ?? volume.host}
										on:input={(e) => {
											const value = e.currentTarget.value;
											if (value !== volume.host) {
												volumeOverrides[volume.container] = value;
											} else {
												delete volumeOverrides[volume.container];
											}
											volumeOverrides = volumeOverrides;
										}}
										class="form-input font-mono text-sm"
									/>
								</div>
								<span class="text-secondary pt-7">→</span>
								<div class="flex-1">
									<label class="form-label">Container Path</label>
									<input
										type="text"
										value={volume.container}
										disabled
										class="form-input font-mono text-sm bg-surface-overlay opacity-60"
									/>
								</div>
								<div class="pt-7">
									{#if volume.readonly}
										<Badge variant="warning">RO</Badge>
									{:else}
										<Badge variant="neutral">RW</Badge>
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
							<div class="flex items-center justify-between mb-2">
								<div class="flex items-center gap-2">
									<code class="mono font-medium">{env.name}</code>
									{#if env.required}
										<Badge variant="error">Required</Badge>
									{/if}
								</div>
							</div>
							{#if env.description}
								<p class="text-sm text-secondary mb-2">{env.description}</p>
							{/if}
							<input
								type="text"
								value={envOverrides[env.name] ?? env.value}
								placeholder={env.value || `Enter ${env.name}`}
								on:input={(e) => {
									const value = e.currentTarget.value;
									if (value !== env.value) {
										envOverrides[env.name] = value;
									} else {
										delete envOverrides[env.name];
									}
									envOverrides = envOverrides;
								}}
								class="form-input font-mono text-sm"
							/>
						</div>
					{/each}
				</div>
			{/if}
		</div>

		{#if error}
			<Alert variant="error" class="mt-4">{error}</Alert>
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
				on:click={handleInstall}
				icon={Download}
			>
				Install {app?.name}
			</Button>
		{/if}
	</svelte:fragment>
</Modal>

<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getContainers,
		startContainer,
		stopContainer,
		restartContainer,
		createContainer,
		removeContainer,
		type Container,
		type ContainerCreate
	} from '$lib/api/containers';
	import {
		Container as ContainerIcon,
		Play,
		Square,
		RotateCw,
		Trash2,
		Plus,
		Search,
		RefreshCw,
		LayoutGrid,
		List,
		Filter,
		ExternalLink,
		MoreVertical,
		CheckCircle2,
		XCircle,
		Clock,
		Box
	} from 'lucide-svelte';
	import { Button, Card, Badge, Input, Select, Modal, Alert, EmptyState } from '$lib/components/ui';
	import { ContainerDetailDrawer, ImageList } from '$lib/components/containers';
	import { t } from '$lib/i18n';

	const tabs = [
		{ id: 'containers', key: 'containers.tabs.containers' },
		{ id: 'images', key: 'containers.tabs.images' }
	] as const;
	type TabId = (typeof tabs)[number]['id'];

	let activeTab: TabId = 'containers';
	// The Images tab loads its list when it is first shown, and keeps it (and a
	// pull in progress) while the other tab is shown.
	let imagesShown = false;
	let imageList: ImageList | undefined;
	$: if (activeTab === 'images') imagesShown = true;

	let containers: Container[] = [];
	let loading = true;
	let error = '';
	let showCreateModal = false;
	let submitting = false;

	// Search & Filter
	let searchQuery = '';
	let stateFilter = '';
	let viewMode: 'table' | 'cards' = 'table';

	// Drawer state
	let selectedContainer: Container | null = null;
	let showDrawer = false;

	// Form state for create modal
	let formData: ContainerCreate = {
		name: '',
		image: '',
		ports: [],
		environment: {},
		restart_policy: 'unless-stopped'
	};
	let portMapping = { host: '', container: '', protocol: 'tcp' };
	let envVar = { key: '', value: '' };
	let portsList: Array<{ host: string; container: string; protocol: string }> = [];
	let envList: Array<{ key: string; value: string }> = [];

	// Computed stats
	$: runningCount = containers.filter((c) => c.state === 'running').length;
	$: stoppedCount = containers.filter((c) => c.state === 'exited').length;
	$: otherCount = containers.filter((c) => c.state !== 'running' && c.state !== 'exited').length;

	// Filtered containers
	$: filteredContainers = containers.filter((c) => {
		const matchesSearch =
			searchQuery === '' ||
			c.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
			c.image.toLowerCase().includes(searchQuery.toLowerCase());
		const matchesState = stateFilter === '' || c.state === stateFilter;
		return matchesSearch && matchesState;
	});

	onMount(async () => {
		await loadData();
	});

	async function loadData() {
		loading = true;
		error = '';
		try {
			containers = await getContainers();
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	function refresh() {
		if (activeTab === 'images') imageList?.refresh();
		else loadData();
	}

	/**
	 * Keyboard of the Containers / Images tabs: the arrow keys move to the
	 * previous or next tab (wrapping around), Home and End to the first or
	 * last; the tab reached is selected and focused.
	 */
	function tabKeydown(event: KeyboardEvent, from: TabId) {
		const index = tabs.findIndex((tab) => tab.id === from);
		let next: number;
		if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
		else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = tabs.length - 1;
		else return;
		event.preventDefault();
		activeTab = tabs[next].id;
		document.getElementById(`docker-tab-${activeTab}`)?.focus();
	}

	function openCreateModal() {
		formData = {
			name: '',
			image: '',
			ports: [],
			environment: {},
			restart_policy: 'unless-stopped'
		};
		portsList = [];
		envList = [];
		portMapping = { host: '', container: '', protocol: 'tcp' };
		envVar = { key: '', value: '' };
		error = '';
		showCreateModal = true;
	}

	function closeCreateModal() {
		showCreateModal = false;
	}

	function addPort() {
		if (portMapping.host && portMapping.container) {
			portsList = [...portsList, { ...portMapping }];
			portMapping = { host: '', container: '', protocol: 'tcp' };
		}
	}

	function removePort(index: number) {
		portsList = portsList.filter((_, i) => i !== index);
	}

	function addEnvVar() {
		if (envVar.key) {
			envList = [...envList, { ...envVar }];
			envVar = { key: '', value: '' };
		}
	}

	function removeEnvVar(index: number) {
		envList = envList.filter((_, i) => i !== index);
	}

	async function handleCreate() {
		if (!formData.name || !formData.image) {
			error = $t('containers.create.required');
			return;
		}

		submitting = true;
		error = '';

		try {
			const config: ContainerCreate = {
				name: formData.name,
				image: formData.image,
				restart_policy: formData.restart_policy,
				ports: portsList.map((p) => ({
					host_port: parseInt(p.host),
					container_port: parseInt(p.container),
					protocol: p.protocol
				})),
				environment: envList.reduce((acc, e) => ({ ...acc, [e.key]: e.value }), {})
			};

			await createContainer(config);
			closeCreateModal();
			await loadData();
		} catch (e: any) {
			error = e.message;
		} finally {
			submitting = false;
		}
	}

	async function handleQuickAction(
		e: Event,
		containerId: string,
		action: 'start' | 'stop' | 'restart' | 'remove'
	) {
		e.stopPropagation();

		if (action === 'remove') {
			const container = containers.find((c) => c.id === containerId);
			if (!confirm($t('containers.confirmRemove', { name: container?.name ?? containerId }))) return;
		}

		try {
			switch (action) {
				case 'start':
					await startContainer(containerId);
					break;
				case 'stop':
					await stopContainer(containerId);
					break;
				case 'restart':
					await restartContainer(containerId);
					break;
				case 'remove':
					await removeContainer(containerId, true);
					break;
			}
			await loadData();
		} catch (e: any) {
			error = e.message;
		}
	}

	function openDrawer(container: Container) {
		selectedContainer = container;
		showDrawer = true;
	}

	function closeDrawer() {
		showDrawer = false;
		selectedContainer = null;
	}

	async function handleDrawerAction() {
		await loadData();
		// Refresh selected container if it still exists
		if (selectedContainer) {
			const updated = containers.find((c) => c.id === selectedContainer?.id);
			if (updated) {
				selectedContainer = updated;
			}
		}
	}

	// A container's state as the server answers it (Docker's State.Status): keys of the catalog,
	// by value. ContainerDetailDrawer.svelte has the same map.
	const STATES: Record<string, string> = {
		created: 'containers.state.created',
		running: 'containers.state.running',
		paused: 'containers.state.paused',
		restarting: 'containers.state.restarting',
		removing: 'containers.state.removing',
		exited: 'containers.state.exited',
		dead: 'containers.state.dead'
	};

	/** A state in the language of the page; a state without a key as it comes. */
	$: stateLabel = (state: string): string =>
		Object.prototype.hasOwnProperty.call(STATES, state) ? $t(STATES[state]) : state;

	function getStateVariant(
		state: string
	): 'success' | 'error' | 'warning' | 'info' | 'neutral' | 'purple' | 'cyan' | 'orange' {
		switch (state) {
			case 'running':
				return 'success';
			case 'exited':
				return 'error';
			case 'paused':
				return 'warning';
			case 'restarting':
				return 'info';
			default:
				return 'neutral';
		}
	}

	function getWebUrl(container: Container): string | null {
		const webPort = container.ports.find((p) => p.host_port);
		if (webPort) {
			return `http://${window.location.hostname}:${webPort.host_port}`;
		}
		return null;
	}

	function formatPorts(container: Container): string {
		if (container.ports.length === 0) return '-';
		return container.ports
			.filter((p) => p.host_port)
			.map((p) => `${p.host_port}:${p.container_port}`)
			.join(', ');
	}
</script>

<div class="page-container">
	<!-- Page Header -->
	<div class="page-header">
		<div class="flex items-center gap-3">
			<div class="w-10 h-10 rounded-lg bg-surface-elevated flex items-center justify-center">
				<ContainerIcon class="w-5 h-5 icon-container" />
			</div>
			<div>
				<h1 class="page-title">{$t('containers.title')}</h1>
				<p class="text-sm text-secondary">{$t('containers.subtitle')}</p>
			</div>
		</div>
		<div class="flex items-center gap-2">
			<Button
				variant="secondary"
				size="icon"
				on:click={refresh}
				disabled={activeTab === 'containers' && loading}
			>
				<RefreshCw class="w-4 h-4 {activeTab === 'containers' && loading ? 'animate-spin' : ''}" />
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
			{#if activeTab === 'containers'}
				<Button variant="primary" icon={Plus} on:click={openCreateModal}
					>{$t('containers.create.button')}</Button
				>
			{/if}
		</div>
	</div>

	<div
		class="flex gap-1 p-1 bg-surface-elevated rounded-lg w-fit"
		role="tablist"
		aria-label={$t('containers.tabs.label')}
	>
		{#each tabs as tab (tab.id)}
			<button
				type="button"
				role="tab"
				id="docker-tab-{tab.id}"
				aria-selected={activeTab === tab.id}
				aria-controls="docker-panel-{tab.id}"
				tabindex={activeTab === tab.id ? 0 : -1}
				class="px-4 py-2 rounded-md text-sm font-medium transition-colors {activeTab === tab.id
					? 'bg-blue-600 text-white'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = tab.id)}
				on:keydown={(event) => tabKeydown(event, tab.id)}
			>
				{$t(tab.key)}
			</button>
		{/each}
	</div>

	<div
		role="tabpanel"
		id="docker-panel-containers"
		aria-labelledby="docker-tab-containers"
		hidden={activeTab !== 'containers'}
		class="space-y-6"
	>
		<!-- Stats Cards -->
		<div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
			<button
				class="card hover:bg-surface-overlay transition-colors text-left cursor-pointer {stateFilter ===
				'running'
					? 'ring-2 ring-green-500'
					: ''}"
				on:click={() => (stateFilter = stateFilter === 'running' ? '' : 'running')}
			>
				<div class="flex items-center justify-between">
					<div>
						<p class="text-secondary text-sm">{$t('containers.filter.running')}</p>
						<p class="text-2xl font-bold text-success">{runningCount}</p>
					</div>
					<div class="w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center">
						<CheckCircle2 class="w-5 h-5 text-success" />
					</div>
				</div>
			</button>

			<button
				class="card hover:bg-surface-overlay transition-colors text-left cursor-pointer {stateFilter ===
				'exited'
					? 'ring-2 ring-red-500'
					: ''}"
				on:click={() => (stateFilter = stateFilter === 'exited' ? '' : 'exited')}
			>
				<div class="flex items-center justify-between">
					<div>
						<p class="text-secondary text-sm">{$t('containers.filter.stopped')}</p>
						<p class="text-2xl font-bold text-error">{stoppedCount}</p>
					</div>
					<div class="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center">
						<XCircle class="w-5 h-5 text-error" />
					</div>
				</div>
			</button>

			<button
				class="card hover:bg-surface-overlay transition-colors text-left cursor-pointer {stateFilter ===
				''
					? 'ring-2 ring-blue-500'
					: ''}"
				on:click={() => (stateFilter = '')}
			>
				<div class="flex items-center justify-between">
					<div>
						<p class="text-secondary text-sm">{$t('containers.filter.total')}</p>
						<p class="text-2xl font-bold">{containers.length}</p>
					</div>
					<div class="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
						<Box class="w-5 h-5 text-info" />
					</div>
				</div>
			</button>
		</div>

		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}

		<!-- Search and View Toggle -->
		<Card>
			<div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-4">
				<div class="relative flex-1">
					<Search
						class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-content-muted pointer-events-none"
					/>
					<input
						type="text"
						placeholder={$t('containers.search')}
						bind:value={searchQuery}
						class="form-input pl-10"
					/>
				</div>
				<div class="flex items-center gap-2">
					<select bind:value={stateFilter} class="form-select w-auto">
						<option value="">{$t('containers.filter.all')}</option>
						<option value="running">{$t('containers.filter.running')}</option>
						<option value="exited">{$t('containers.filter.stopped')}</option>
						<option value="paused">{$t('containers.filter.paused')}</option>
						<option value="restarting">{$t('containers.filter.restarting')}</option>
					</select>
					<div class="flex border border-border rounded-lg overflow-hidden">
						<button
							class="p-2 {viewMode === 'table' ? 'bg-surface-overlay' : 'bg-transparent'}"
							on:click={() => (viewMode = 'table')}
							title={$t('containers.view.table')}
						>
							<List class="w-4 h-4" />
						</button>
						<button
							class="p-2 {viewMode === 'cards' ? 'bg-surface-overlay' : 'bg-transparent'}"
							on:click={() => (viewMode = 'cards')}
							title={$t('containers.view.cards')}
						>
							<LayoutGrid class="w-4 h-4" />
						</button>
					</div>
				</div>
			</div>
		</Card>

		<!-- Container List -->
		{#if loading}
			<p class="loading-text">{$t('containers.loading')}</p>
		{:else if filteredContainers.length === 0}
			<EmptyState
				icon={ContainerIcon}
				title={$t('containers.empty.title')}
				description={searchQuery || stateFilter
					? $t('containers.empty.filtered')
					: $t('containers.empty.none')}
			>
				{#if !searchQuery && !stateFilter}
					<Button variant="primary" icon={Plus} on:click={openCreateModal}
						>{$t('containers.create.button')}</Button
					>
				{:else}
					<Button
						variant="secondary"
						on:click={() => {
							searchQuery = '';
							stateFilter = '';
						}}>{$t('containers.empty.clear')}</Button
					>
				{/if}
			</EmptyState>
		{:else if viewMode === 'table'}
			<!-- Table View -->
			<Card>
				<div class="overflow-x-auto">
					<table class="table">
						<thead class="table-header">
							<tr>
								<th class="w-12"></th>
								<th>{$t('containers.column.name')}</th>
								<th>{$t('containers.column.image')}</th>
								<th>{$t('containers.column.state')}</th>
								<th>{$t('containers.column.ports')}</th>
								<th class="text-right">{$t('containers.column.actions')}</th>
							</tr>
						</thead>
						<tbody>
							{#each filteredContainers as container}
								<tr
									class="table-row hover:bg-surface-overlay cursor-pointer transition-colors"
									on:click={() => openDrawer(container)}
								>
									<td>
										<div
											class="w-8 h-8 rounded bg-surface-muted flex items-center justify-center"
										>
											<ContainerIcon class="w-4 h-4 icon-container" />
										</div>
									</td>
									<td>
										<div class="font-medium">{container.name}</div>
										<div class="text-xs text-muted mono-xs truncate max-w-48">
											{container.id.substring(0, 12)}
										</div>
									</td>
									<td>
										<div class="mono text-sm truncate max-w-48">{container.image}</div>
									</td>
									<td>
										<Badge variant={getStateVariant(container.state)}>{stateLabel(container.state)}</Badge>
									</td>
									<td>
										<div class="flex items-center gap-2">
											<span class="text-sm text-secondary mono-xs">{formatPorts(container)}</span>
											{#if getWebUrl(container)}
												<a
													href={getWebUrl(container)}
													target="_blank"
													rel="noopener"
													on:click|stopPropagation
													class="text-info hover:text-blue-300"
													title={$t('containers.openInBrowser')}
												>
													<ExternalLink class="w-3.5 h-3.5" />
												</a>
											{/if}
										</div>
									</td>
									<td>
										<div class="flex items-center justify-end gap-1">
											{#if container.state === 'running'}
												<button
													on:click={(e) => handleQuickAction(e, container.id, 'stop')}
													class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-warning transition-colors"
													title={$t('containers.action.stop')}
												>
													<Square class="w-4 h-4" />
												</button>
												<button
													on:click={(e) => handleQuickAction(e, container.id, 'restart')}
													class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-info transition-colors"
													title={$t('containers.action.restart')}
												>
													<RotateCw class="w-4 h-4" />
												</button>
											{:else}
												<button
													on:click={(e) => handleQuickAction(e, container.id, 'start')}
													class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-success transition-colors"
													title={$t('containers.action.start')}
												>
													<Play class="w-4 h-4" />
												</button>
											{/if}
											<button
												on:click={(e) => handleQuickAction(e, container.id, 'remove')}
												class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-error transition-colors"
												title={$t('containers.action.remove')}
											>
												<Trash2 class="w-4 h-4" />
											</button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card>
		{:else}
			<!-- Cards View -->
			<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
				{#each filteredContainers as container}
					<div
						class="card hover:bg-surface-overlay cursor-pointer transition-colors"
						on:click={() => openDrawer(container)}
						role="button"
						tabindex="0"
						on:keypress={(e) => e.key === 'Enter' && openDrawer(container)}
					>
						<div class="flex items-start justify-between mb-3">
							<div class="flex items-center gap-3 min-w-0">
								<div
									class="w-10 h-10 rounded-lg bg-surface-muted flex items-center justify-center flex-shrink-0"
								>
									<ContainerIcon class="w-5 h-5 icon-container" />
								</div>
								<div class="min-w-0">
									<h3 class="font-semibold truncate">{container.name}</h3>
									<p class="text-sm text-secondary truncate">{container.image}</p>
								</div>
							</div>
							<Badge variant={getStateVariant(container.state)}>{stateLabel(container.state)}</Badge>
						</div>

						{#if container.ports.length > 0}
							<div class="mb-3">
								<p class="text-xs text-muted mb-1">{$t('containers.column.ports')}</p>
								<div class="flex flex-wrap gap-1">
									{#each container.ports.filter((p) => p.host_port) as port}
										<span class="badge badge-info">
											{port.host_port}:{port.container_port}
										</span>
									{/each}
								</div>
							</div>
						{/if}

						<div class="flex items-center justify-between pt-3 border-t border-border">
							<span class="mono-xs text-muted">{container.id.substring(0, 12)}</span>
							<div class="flex items-center gap-1">
								{#if getWebUrl(container)}
									<a
										href={getWebUrl(container)}
										target="_blank"
										rel="noopener"
										on:click|stopPropagation
										class="p-1.5 rounded hover:bg-surface-muted text-info transition-colors"
										title={$t('containers.openInBrowser')}
									>
										<ExternalLink class="w-4 h-4" />
									</a>
								{/if}
								{#if container.state === 'running'}
									<button
										on:click={(e) => handleQuickAction(e, container.id, 'stop')}
										class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-warning transition-colors"
										title={$t('containers.action.stop')}
									>
										<Square class="w-4 h-4" />
									</button>
								{:else}
									<button
										on:click={(e) => handleQuickAction(e, container.id, 'start')}
										class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-success transition-colors"
										title={$t('containers.action.start')}
									>
										<Play class="w-4 h-4" />
									</button>
								{/if}
								<button
									on:click={(e) => handleQuickAction(e, container.id, 'remove')}
									class="p-1.5 rounded hover:bg-surface-muted text-secondary hover:text-error transition-colors"
									title={$t('containers.action.remove')}
								>
									<Trash2 class="w-4 h-4" />
								</button>
							</div>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<div
		role="tabpanel"
		id="docker-panel-images"
		aria-labelledby="docker-tab-images"
		hidden={activeTab !== 'images'}
	>
		{#if imagesShown}
			<ImageList bind:this={imageList} />
		{/if}
	</div>
</div>

<!-- Create Container Modal -->
<Modal
	bind:open={showCreateModal}
	title={$t('containers.create.title')}
	size="lg"
	on:close={closeCreateModal}
>
	<form on:submit|preventDefault={handleCreate} class="space-y-4">
		<Input
			label={$t('containers.create.name')}
			bind:value={formData.name}
			placeholder={$t('containers.create.namePlaceholder')}
			required
		/>

		<Input
			label={$t('containers.create.image')}
			bind:value={formData.image}
			placeholder={$t('containers.create.imagePlaceholder')}
			hint={$t('containers.create.imageHint')}
			required
		/>

		<Select
			label={$t('containers.create.restartPolicy')}
			bind:value={formData.restart_policy}
			options={[
				{ value: 'no', label: $t('containers.create.restartNo') },
				{ value: 'always', label: $t('containers.create.restartAlways') },
				{ value: 'unless-stopped', label: $t('containers.create.restartUnlessStopped') },
				{ value: 'on-failure', label: $t('containers.create.restartOnFailure') }
			]}
		/>

		<!-- Port Mappings -->
		<div>
			<label class="form-label">{$t('containers.create.ports')}</label>
			<div class="flex gap-2 mb-2">
				<input
					type="number"
					bind:value={portMapping.host}
					placeholder={$t('containers.create.hostPort')}
					class="form-input w-24"
				/>
				<span class="text-secondary self-center">:</span>
				<input
					type="number"
					bind:value={portMapping.container}
					placeholder={$t('containers.create.containerPort')}
					class="form-input w-24"
				/>
				<select bind:value={portMapping.protocol} class="form-select w-24">
					<option value="tcp">{$t('containers.protocol.tcp')}</option>
					<option value="udp">{$t('containers.protocol.udp')}</option>
				</select>
				<Button variant="secondary" size="sm" on:click={addPort} type="button"
					>{$t('containers.create.add')}</Button
				>
			</div>
			{#if portsList.length > 0}
				<div class="space-y-1">
					{#each portsList as port, i}
						<div
							class="flex items-center justify-between py-1.5 px-3 bg-surface-muted rounded text-sm"
						>
							<span class="mono">{port.host}:{port.container}/{port.protocol}</span>
							<button
								type="button"
								on:click={() => removePort(i)}
								class="text-error hover:text-red-300"
							>
								<Trash2 class="w-4 h-4" />
							</button>
						</div>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Environment Variables -->
		<div>
			<label class="form-label">{$t('containers.create.environment')}</label>
			<div class="flex gap-2 mb-2">
				<input
					type="text"
					bind:value={envVar.key}
					placeholder={$t('containers.create.envKey')}
					class="form-input flex-1"
				/>
				<span class="text-secondary self-center">=</span>
				<input
					type="text"
					bind:value={envVar.value}
					placeholder={$t('containers.create.envValue')}
					class="form-input flex-1"
				/>
				<Button variant="secondary" size="sm" on:click={addEnvVar} type="button"
					>{$t('containers.create.add')}</Button
				>
			</div>
			{#if envList.length > 0}
				<div class="space-y-1">
					{#each envList as env, i}
						<div
							class="flex items-center justify-between py-1.5 px-3 bg-surface-muted rounded text-sm"
						>
							<span class="mono">{env.key}={env.value}</span>
							<button
								type="button"
								on:click={() => removeEnvVar(i)}
								class="text-error hover:text-red-300"
							>
								<Trash2 class="w-4 h-4" />
							</button>
						</div>
					{/each}
				</div>
			{/if}
		</div>

		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" on:click={closeCreateModal}
			>{$t('common.cancel')}</Button
		>
		<Button
			variant="primary"
			class="flex-1"
			loading={submitting}
			on:click={handleCreate}
			icon={Plus}
		>
			{$t('containers.create.button')}
		</Button>
	</svelte:fragment>
</Modal>

<!-- Container Detail Drawer -->
<ContainerDetailDrawer
	bind:open={showDrawer}
	container={selectedContainer}
	on:close={closeDrawer}
	on:action={handleDrawerAction}
/>

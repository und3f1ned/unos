<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { getContainers, createContainer, type Container } from '$lib/api/containers';
	import { Plus, RefreshCw } from 'lucide-svelte';
	import { Button, Alert, Skeleton } from '$lib/components/ui';
	import {
		BulkStatus,
		BulkToolbar,
		ContainerCreateDialog,
		ContainerDetailDrawer,
		ContainerList,
		ContainerStats,
		ContainerTabs,
		ContainerToolbar,
		ImageList,
		type TabId
	} from '$lib/components/containers';
	import { containerAction } from '$lib/components/containers/actions';
	import { BulkRun, Selection, type BulkAction } from '$lib/components/containers/bulk.svelte';
	import { ContainerForm } from '$lib/components/containers/containerForm.svelte';
	import type { QuickAction } from '$lib/components/containers/labels';
	import { t } from '$lib/i18n';

	let activeTab = $state<TabId>('containers');
	// The Images tab loads its list when it is first shown, and keeps it (and a
	// pull in progress) while the other tab is shown: showTab marks it shown.
	let imagesShown = $state(false);
	let imageList = $state<ImageList>();

	let containers = $state<Container[]>([]);
	let loading = $state(true);
	// The first load has answered: until then the tab is its skeleton, and a reload keeps what is
	// shown (ADR-0430).
	let loaded = $state(false);
	let error = $state('');
	// Why the list could not be loaded (Docker did not answer: 503, ADR-0323). The tab then shows
	// this error alone: no counts, search or empty list, and no Create button. The actions' and the
	// Create dialog's errors are `error`.
	let loadError = $state('');
	let showCreateModal = $state(false);
	let submitting = $state(false);

	// Search & Filter
	let searchQuery = $state('');
	let stateFilter = $state('');
	let viewMode = $state<'table' | 'cards'>('table');

	// Drawer state
	let selectedContainer = $state<Container | null>(null);
	let showDrawer = $state(false);

	// The fields of the create dialog, emptied each time it opens.
	const form = new ContainerForm();

	// Several containers at once (ADR-0389): the selected containers. Only what the list shows can
	// be selected: a container that a search or a filter hides, or that the list no longer has,
	// leaves the selection. An action calls the endpoint of each container, one after another, and
	// lists each one's result.
	const selection = new Selection();
	const bulk = new BulkRun();
	// The page was left: a run ends after the container it is at.
	let left = false;
	onDestroy(() => (left = true));

	// Computed stats
	const runningCount = $derived(containers.filter((c) => c.state === 'running').length);
	const stoppedCount = $derived(containers.filter((c) => c.state === 'exited').length);

	// Filtered containers
	const filteredContainers = $derived(
		containers.filter((c) => {
			const matchesSearch =
				searchQuery === '' ||
				c.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
				c.image.toLowerCase().includes(searchQuery.toLowerCase());
			const matchesState = stateFilter === '' || c.state === stateFilter;
			return matchesSearch && matchesState;
		})
	);

	// What the list does not show leaves the selection: after a search, a filter or a load, before
	// the markup updates, as Svelte 4's statement did. Only the list shown makes it run again.
	$effect.pre(() => {
		const shown = filteredContainers;
		untrack(() => selection.keep(new Set(shown.map((c) => c.id))));
	});
	const allShownSelected = $derived(
		filteredContainers.length > 0 && filteredContainers.every((c) => selection.has(c.id))
	);

	/**
	 * Start, stop or restart every selected container through its own endpoint, in the order of the
	 * list (BulkRun, ADR-0389). What was not done stays selected. A 401, which ends the session, and
	 * leaving the page end the run after the container it is at.
	 */
	async function runBulk(action: BulkAction) {
		const targets = filteredContainers
			.filter((c) => selection.has(c.id))
			.map((c) => ({ id: c.id, name: c.name, running: c.state === 'running' }));
		if (targets.length === 0 || bulk.running) return;
		error = '';
		const items = await bulk.run(action, targets, () => left);
		if (items === null) return;
		// What was not done stays selected.
		selection.set(items.filter((item) => item.state === undefined).map((item) => item.id));
		await loadData();
	}

	onMount(async () => {
		await loadData();
	});

	async function loadData() {
		loading = true;
		error = '';
		try {
			containers = await getContainers();
			// The error stays until a load succeeds: a reload does not blank it (ADR-0430).
			loadError = '';
			const listed = new Set(containers.map((c) => c.id));
			selection.set([...selection.ids].filter((id) => listed.has(id)));
		} catch (e: any) {
			loadError = e.message;
		} finally {
			loading = false;
			loaded = true;
		}
	}

	function refresh() {
		if (activeTab === 'images') imageList?.refresh();
		else loadData();
	}

	function showTab(id: TabId) {
		activeTab = id;
		if (id === 'images') imagesShown = true;
	}

	function openCreateModal() {
		form.reset();
		error = '';
		showCreateModal = true;
	}

	function closeCreateModal() {
		showCreateModal = false;
	}

	async function handleCreate() {
		if (!form.formData.name || !form.formData.image) {
			error = $t('containers.create.required');
			return;
		}

		submitting = true;
		error = '';

		try {
			await createContainer(form.config());
			closeCreateModal();
			await loadData();
		} catch (e: any) {
			error = e.message;
		} finally {
			submitting = false;
		}
	}

	async function handleQuickAction(e: Event, containerId: string, action: QuickAction) {
		e.stopPropagation();
		if (bulk.running) return;

		if (action === 'remove') {
			const container = containers.find((c) => c.id === containerId);
			if (!confirm($t('containers.confirmRemove', { name: container?.name ?? containerId }))) return;
		}

		try {
			await containerAction(containerId, action);
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
</script>

<div class="page-container">
	<!-- Page Header -->
	<div class="page-header">
		<div class="min-w-0">
			<h1 class="page-title">{$t('containers.title')}</h1>
			<p class="page-subtitle">{$t('containers.subtitle')}</p>
		</div>
		<div class="page-actions">
			<Button
				variant="secondary"
				size="icon"
				onclick={refresh}
				disabled={activeTab === 'containers' && loading}
			>
				<RefreshCw class="w-4 h-4 {activeTab === 'containers' && loading ? 'animate-spin' : ''}" />
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
			{#if activeTab === 'containers' && !loadError}
				<Button variant="primary" icon={Plus} onclick={openCreateModal}
					>{$t('containers.create.button')}</Button
				>
			{/if}
		</div>
	</div>

	<ContainerTabs active={activeTab} onselect={showTab} />

	<div
		role="tabpanel"
		id="docker-panel-containers"
		aria-labelledby="docker-tab-containers"
		hidden={activeTab !== 'containers'}
		class="space-y-6"
	>
		{#if !loaded}
			<!-- The first load (ADR-0430): the counts, the toolbar and the list, swapped at once. -->
			<div class="space-y-6" aria-busy="true">
				<!-- The counts, each as tall as its card (ContainerStats, ADR-0431). -->
				<div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
					{#each [0, 1, 2] as stat (stat)}
						<Skeleton shape="block" class="h-[78px] w-full rounded-card" />
					{/each}
				</div>
				<Skeleton shape="block" class="h-9 w-full" />
				<div class="card"><Skeleton shape="row" columns={6} count={4} size="control" /></div>
			</div>
		{:else if loadError}
			<Alert variant="error">{loadError}</Alert>
		{:else}
		<!-- Stats Cards -->
		<ContainerStats
			running={runningCount}
			stopped={stoppedCount}
			total={containers.length}
		/>

		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}

		<BulkStatus progress={bulk.progress} result={bulk.result} ondismiss={() => (bulk.result = null)} />

		<ContainerToolbar
			bind:search={searchQuery}
			bind:filter={stateFilter}
			view={viewMode}
			onview={(view) => (viewMode = view)}
		/>

		{#if containers.length > 0}
			<BulkToolbar
				allSelected={allShownSelected}
				empty={filteredContainers.length === 0}
				selectedCount={selection.size}
				busy={bulk.running}
				ontoggleall={(on) => selection.set(on ? filteredContainers.map((c) => c.id) : [])}
				onbulk={runBulk}
			/>
		{/if}

		<!-- Container List -->
		<ContainerList
			containers={filteredContainers}
			filtered={!!(searchQuery || stateFilter)}
			view={viewMode}
			selected={selection.ids}
			busy={bulk.running}
			onopen={openDrawer}
			onselect={(id, on) => selection.toggle(id, on)}
			onaction={handleQuickAction}
			onclear={() => {
				searchQuery = '';
				stateFilter = '';
			}}
		/>
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
<ContainerCreateDialog
	open={showCreateModal}
	{form}
	{error}
	{submitting}
	onclose={closeCreateModal}
	oncreate={handleCreate}
/>

<!-- Container Detail Drawer -->
<ContainerDetailDrawer
	bind:open={showDrawer}
	container={selectedContainer}
	onclose={closeDrawer}
/>

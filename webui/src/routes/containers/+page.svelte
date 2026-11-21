<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { getContainers, createContainer, type Container } from '$lib/api/containers';
	import { Plus, RefreshCw } from 'lucide-svelte';
	import { Button, Alert, LoadState, Skeleton, confirm, toast } from '$lib/components/ui';
	import {
		BulkStatus,
		BulkToolbar,
		ContainerCreateDialog,
		ContainerDetailDrawer,
		ContainerList,
		ContainerTabs,
		ContainerToolbar,
		ImageList,
		type TabId
	} from '$lib/components/containers';
	import { containerAction } from '$lib/components/containers/actions';
	import { BULK_DONE, BulkRun, Selection, type BulkAction } from '$lib/components/containers/bulk.svelte';
	import { ContainerForm } from '$lib/components/containers/containerForm.svelte';
	import { countByGroup, groupOf, type QuickAction, type StateFilter } from '$lib/components/containers/labels';
	import { t } from '$lib/i18n';
	import { ApiError } from '$lib/api/client';

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
	// Why the list could not be loaded. The tab then shows this error alone: no counts, search or
	// empty list, and no Create button. The actions' and the Create dialog's errors are `error`.
	let loadError = $state('');
	// The load failed because Docker did not answer (503, ADR-0323), not for another reason.
	let dockerDown = $state(false);
	let showCreateModal = $state(false);
	let submitting = $state(false);

	// Search & Filter
	let searchQuery = $state('');
	let stateFilter = $state<StateFilter>('all');
	// The view the user chose; until then cards on a phone, where a table's state and buttons would
	// be off the screen, and the table on a wider screen (webui.ux-apps).
	let chosenView = $state<'table' | 'cards' | null>(null);
	// i18n-ignore: a CSS media query, the same in every language
	const narrow = new MediaQuery('(max-width: 639px)', false);
	const viewMode = $derived(chosenView ?? (narrow.current ? 'cards' : 'table'));

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

	// The containers that the search lets through, and their counts by state, which the filter's
	// segments show and which add up to All.
	const searched = $derived(
		containers.filter((c) => {
			const query = searchQuery.toLowerCase();
			return query === '' || c.name.toLowerCase().includes(query) || c.image.toLowerCase().includes(query);
		})
	);
	const counts = $derived(countByGroup(searched));
	const filteredContainers = $derived(
		stateFilter === 'all' ? searched : searched.filter((c) => groupOf(c.state) === stateFilter)
	);

	// What the list does not show leaves the selection: after a search, a filter or a load, before
	// the markup updates, as Svelte 4's statement did. Only the list shown makes it run again.
	$effect.pre(() => {
		const shown = filteredContainers;
		untrack(() => selection.keep(new Set(shown.map((c) => c.id))));
	});

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
		// A run that did everything says so in a toast; one that did not keeps its list of what
		// failed and why on the page (BulkStatus).
		const ok = items.filter((item) => item.state !== undefined).length;
		if (ok === items.length) {
			toast.success($t(BULK_DONE[action], { ok, n: items.length }));
			bulk.result = null;
		}
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
		} catch (e) {
			loadError = (e as Error).message;
			dockerDown = e instanceof ApiError && e.status === 503;
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
		// The dialog says under each field what is missing.
		if (!form.formData.name.trim() || !form.formData.image.trim()) return;

		submitting = true;
		error = '';

		try {
			await createContainer(form.config());
			closeCreateModal();
			await loadData();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			submitting = false;
		}
	}

	async function handleQuickAction(containerId: string, action: QuickAction) {
		if (bulk.running) return;

		if (action === 'remove') {
			const container = containers.find((c) => c.id === containerId);
			const yes = await confirm({
				title: $t('containers.confirmRemove', { name: container?.name ?? containerId }),
				message: $t('containers.removeMessage'),
				confirmLabel: $t('containers.removeAction')
			});
			if (!yes) return;
		}

		try {
			await containerAction(containerId, action);
			await loadData();
		} catch (e) {
			error = (e as Error).message;
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
		</div>
		<div class="page-actions">
			<Button
				variant="secondary"
				size="icon"
				icon={RefreshCw}
				onclick={refresh}
				loading={activeTab === 'containers' && loading}
				title={$t('common.refresh')}
			>
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
			{#if activeTab === 'containers' && !loadError}
				<Button variant="primary" icon={Plus} onclick={openCreateModal}>{$t('containers.create.button')}</Button>
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
		<!-- The first load's skeleton (ADR-0430), then a failed load alone, with what it means, the
		     server's reason and Retry: no counts, list or Create (webui.ux-apps). "Docker is not
		     answering" only for Docker's own 503 (ADR-0323). -->
		<LoadState
			loading={!loaded}
			error={loadError}
			errorTitle={$t(dockerDown ? 'containers.loadError.title' : 'containers.loadFailed.title')}
			errorDescription={$t(dockerDown ? 'containers.loadError.hint' : 'containers.loadFailed.hint')}
			onretry={loadData}
			retrying={loading}
			class="space-y-6"
		>
			{#snippet skeleton()}
				<!-- The toolbar (on a phone the filter has a row of its own) and the list, swapped at
				     once; the cards with their Select all shown. -->
				<div class="space-y-6">
					<div class="space-y-3">
						<Skeleton shape="block" class="h-9 w-full" />
						<Skeleton shape="block" class="h-9 w-full sm:hidden" />
					</div>
					{#if viewMode === 'cards'}
						<!-- A phone's cards (the view it starts in). -->
						<div class="space-y-3">
							<Skeleton shape="block" class="h-5 w-40" />
							{#each [0, 1, 2] as card (card)}
								<Skeleton shape="card" count={2} />
							{/each}
						</div>
					{:else}
						<div class="card"><Skeleton shape="row" columns={6} count={4} size="control" /></div>
					{/if}
				</div>
			{/snippet}
			{#if error}
				<Alert variant="error">{error}</Alert>
			{/if}

			<BulkStatus result={bulk.result} ondismiss={() => (bulk.result = null)} />

			<ContainerToolbar
				bind:search={searchQuery}
				bind:filter={stateFilter}
				{counts}
				view={viewMode}
				onview={(view) => (chosenView = view)}
			/>

			<!-- Container List -->
			<ContainerList
				containers={filteredContainers}
				filtered={!!searchQuery || stateFilter !== 'all'}
				view={viewMode}
				selected={selection.ids}
				busy={bulk.running}
				onopen={openDrawer}
				onselect={(id, on) => selection.toggle(id, on)}
				ontoggleall={(on) => selection.set(on ? filteredContainers.map((c) => c.id) : [])}
				onaction={handleQuickAction}
				onclear={() => {
					searchQuery = '';
					stateFilter = 'all';
				}}
			/>

			<!-- The selection's actions, after the list (ADR-0437). -->
			<BulkToolbar
				selectedCount={selection.size}
				progress={bulk.progress}
				busy={bulk.running}
				onclear={() => selection.set([])}
				onbulk={runBulk}
			/>
		</LoadState>
	</div>

	<div role="tabpanel" id="docker-panel-images" aria-labelledby="docker-tab-images" hidden={activeTab !== 'images'}>
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

<!-- A container's details (a Sheet) -->
<ContainerDetailDrawer bind:open={showDrawer} container={selectedContainer} onclose={closeDrawer} />

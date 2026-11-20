<script lang="ts">
	// The Catalog tab of the App Store: the categories with their counts, the search and the apps
	// they let through, each app once (no second, featured copy: ADR-0431). The page keeps the search and the category, so that
	// they stay while the Installed tab is shown.
	import {
		Activity,
		Code,
		Cpu,
		Database,
		Download as DownloadIcon,
		FileText,
		Film,
		Globe,
		Home,
		LayoutGrid,
		Lock,
		Package,
		Search
	} from 'lucide-svelte';
	import { Button, Card, EmptyState } from '$lib/components/ui';
	import type { AppTemplate, InstalledApp } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppCatalogCard from './AppCatalogCard.svelte';
	import AppCategory from './AppCategory.svelte';
	import { installedOf } from './installed';

	type Props = {
		catalog: AppTemplate[];
		installed: InstalledApp[];
		/** The catalog's ids of the apps whose install runs. */
		installing: ReadonlySet<string>;
		/** The text that an app's name or description contains. */
		search: string;
		/** The category shown; '' is every app. */
		category: string;
		onopen: (app: AppTemplate) => void;
	};

	let {
		catalog,
		installed,
		installing,
		search = $bindable(),
		category = $bindable(),
		onopen
	}: Props = $props();

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

	// Update category counts
	const categoriesWithCounts = $derived(
		categories.map((cat) => ({
			...cat,
			count:
				cat.value === ''
					? catalog.length
					: catalog.filter((app) => app.category === cat.value).length
		}))
	);

	// Filtered catalog
	const filteredCatalog = $derived(
		catalog.filter((app) => {
			const matchesSearch =
				search === '' ||
				app.name.toLowerCase().includes(search.toLowerCase()) ||
				app.description.toLowerCase().includes(search.toLowerCase());
			const matchesCategory = category === '' || app.category === category;
			return matchesSearch && matchesCategory;
		})
	);
</script>

<!-- Search and Categories -->
<div class="flex flex-col lg:flex-row gap-6">
	<!-- Category Sidebar -->
	<div class="lg:w-56 flex-shrink-0">
		<Card>
			<h3 class="font-semibold mb-4">{$t('apps.categories')}</h3>
			<nav class="space-y-1">
				{#each categoriesWithCounts as cat}
					{#if cat.count > 0 || cat.value === ''}
						<Button variant="plain"
						 class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-sm transition-colors
									{category === cat.value
								? 'bg-brand text-content-on-brand'
								: 'text-secondary hover:text-content hover:bg-surface-overlay'}"
							onclick={() => (category = cat.value)}
						>
							<span class="flex items-center gap-2.5">
								<cat.icon class="w-4 h-4" />
								<AppCategory category={cat.value} />
							</span>
							<span
								class="text-xs px-1.5 py-0.5 rounded {category === cat.value
									? 'bg-brand-hover text-content-on-brand'
									: 'bg-surface-overlay text-muted'}"
							>
								{cat.count}
							</span>
						</Button>
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
					bind:value={search}
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
					onclick={() => {
						search = '';
						category = '';
					}}>{$t('apps.clearFilters')}</Button
				>
			</EmptyState>
		{:else}
			<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
				{#each filteredCatalog as app}
					<AppCatalogCard
						{app}
						installedInfo={installedOf(installed, app.id)}
						installing={installing.has(app.id)}
						{onopen}
					/>
				{/each}
			</div>
		{/if}
	</div>
</div>

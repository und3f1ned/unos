<script lang="ts">
	// The Catalog tab of the App Store: the categories with their counts, the search and the apps
	// they let through, each app once (no second, featured copy: ADR-0431). The page keeps the
	// search and the category, so that they stay while the Installed tab is shown. The categories
	// are a list beside the apps from lg, selected as the sidebar's links are, and a menu above
	// them on a narrower screen, where twelve rows would push the apps off the first screen
	// (webui.ux-apps).
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
	import { Button, Card, EmptyState, Input, Select } from '$lib/components/ui';
	import type { AppTemplate, InstalledApp } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppCatalogCard from './AppCatalogCard.svelte';
	import AppCategory from './AppCategory.svelte';
	import { installedOf } from './installed';
	import { categoryLabel } from './labels';

	type Props = {
		catalog: AppTemplate[];
		/** Apps can be installed: Docker answered the installed apps. */
		canInstall: boolean;
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
		canInstall,
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
			count: cat.value === '' ? catalog.length : catalog.filter((app) => app.category === cat.value).length
		}))
	);

	// The menu of a narrow screen: each category with its count.
	const categoryOptions = $derived(
		categoriesWithCounts
			.filter((cat) => cat.count > 0 || cat.value === '')
			.map((cat) => ({
				value: cat.value,
				label: $t('apps.categoryCount', { name: categoryLabel(cat.value, $t), count: cat.count })
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

{#snippet categoryName(value: string)}<AppCategory category={value} />{/snippet}

<!-- Search and Categories -->
<div class="flex flex-col lg:flex-row gap-6">
	<!-- Category list, from lg -->
	<div class="hidden lg:block lg:w-56 flex-shrink-0">
		<Card padding="sm">
			<h3 class="eyebrow px-2.5 mb-2">{$t('apps.categories')}</h3>
			<nav class="space-y-0.5" aria-label={$t('apps.categories')}>
				{#each categoriesWithCounts as cat (cat.value)}
					{#if cat.count > 0 || cat.value === ''}
						<Button
							variant="plain"
							class="nav-item w-full justify-between {category === cat.value
								? 'nav-item-active'
								: 'text-content-secondary hover:text-content hover:bg-surface-overlay'}"
							pressed={category === cat.value}
							onclick={() => (category = cat.value)}
						>
							<span class="flex items-center gap-2.5 min-w-0">
								<cat.icon aria-hidden="true" />
								<span class="truncate">{@render categoryName(cat.value)}</span>
							</span>
							<span class="tabular text-xs text-content-muted">{cat.count}</span>
						</Button>
					{/if}
				{/each}
			</nav>
		</Card>
	</div>

	<!-- App Grid -->
	<div class="flex-1 min-w-0">
		<!-- Search, and the category menu below lg -->
		<div class="mb-5 flex flex-col sm:flex-row gap-3">
			<Input
				icon={Search}
				placeholder={$t('apps.search')}
				ariaLabel={$t('apps.search')}
				bind:value={search}
				class="flex-1"
			/>
			<div class="lg:hidden sm:w-60">
				<Select bind:value={category} options={categoryOptions} placeholder={$t('apps.categories')} />
			</div>
		</div>

		{#if filteredCatalog.length === 0}
			<EmptyState icon={Package} title={$t('apps.noneFound')} description={$t('apps.noneFoundHint')}>
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
				{#each filteredCatalog as app (app.id)}
					<AppCatalogCard
						{app}
						{canInstall}
						installedInfo={installedOf(installed, app.id)}
						installing={installing.has(app.id)}
						{onopen}
					/>
				{/each}
			</div>
		{/if}
	</div>
</div>

<script lang="ts">
	// The head of an app's details: its icon, name, description, category, version and source,
	// and the links to its website, code and documentation.
	import { BookOpen, Github, Globe, Shield, Tag } from 'lucide-svelte';
	import { Badge, type IconComponent } from '$lib/components/ui';
	import type { AppTemplate } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppCategory from './AppCategory.svelte';
	import { categoryIcon, sourceBadge } from './labels';

	type Props = {
		app: AppTemplate;
		isInstalled: boolean;
	};

	let { app, isInstalled }: Props = $props();

	const source = $derived(sourceBadge(app.source, true));
</script>

{#snippet link(href: string, className: string, Icon: IconComponent, key: string)}
	<a
		{href}
		target="_blank"
		rel="noopener"
		class={className}
	>
		<Icon class="w-4 h-4" />
		{$t(key)}
	</a>
{/snippet}

<!-- Custom Header -->
<div class="-mx-6 -mt-6 mb-6 p-6 bg-gradient-to-b from-surface-overlay to-transparent">
	<div class="flex items-start gap-4">
		<div
			class="w-14 h-14 rounded-card bg-surface-overlay flex items-center justify-center text-3xl flex-shrink-0"
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
				<Badge variant="neutral">{categoryIcon(app.category)} <AppCategory category={app.category} /></Badge>
				{#if app.version}
					<Badge variant="info">
						<Tag class="w-3 h-3 mr-1" />
						{app.version}
					</Badge>
				{/if}
				<Badge variant={source.variant}>
					<Shield class="w-3 h-3 mr-1" />
					{$t(source.key)}
				</Badge>
			</div>
		</div>
	</div>

	<!-- External Links -->
	{#if app.website || app.github || app.documentation}
		<div class="flex items-center gap-3 mt-4 pt-4 border-t border-border">
			{#if app.website}
				{@render link(
					app.website,
					'flex items-center gap-1.5 text-sm font-medium text-content-link hover:underline',
					Globe,
					'apps.detail.website'
				)}
			{/if}
			{#if app.github}
				{@render link(
					app.github,
					'flex items-center gap-1.5 text-sm text-secondary hover:text-content transition-colors',
					Github,
					'apps.detail.github'
				)}
			{/if}
			{#if app.documentation}
				{@render link(
					app.documentation,
					'flex items-center gap-1.5 text-sm text-secondary hover:text-content transition-colors',
					BookOpen,
					'apps.detail.docs'
				)}
			{/if}
		</div>
	{/if}
</div>

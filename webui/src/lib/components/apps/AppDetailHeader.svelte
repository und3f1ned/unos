<script lang="ts">
	// The head of an app's details under the dialog's title (the app's name and icon): its
	// description, its category, version and source as neutral badges, whether it is installed,
	// and the links to its website, code and documentation (webui.ux-apps).
	import { BadgeCheck, BookOpen, Check, Github, Globe, Tag } from 'lucide-svelte';
	import { Badge, type IconComponent } from '$lib/components/ui';
	import type { AppTemplate } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppCategory from './AppCategory.svelte';
	import { sourceBadge } from './labels';

	type Props = {
		app: AppTemplate;
		isInstalled: boolean;
	};

	let { app, isInstalled }: Props = $props();

	const source = $derived(sourceBadge(app.source, true));
</script>

{#snippet link(href: string, Icon: IconComponent, key: string)}
	<a {href} target="_blank" rel="noopener" class="link inline-flex items-center gap-1.5 text-sm">
		<Icon class="w-4 h-4" aria-hidden="true" />
		{$t(key)}
	</a>
{/snippet}

<div class="mb-5 space-y-3">
	<p class="text-content-secondary">{app.description}</p>
	<div class="flex flex-wrap items-center gap-1.5">
		{#if isInstalled}
			<Badge variant="success">
				<Check class="w-3 h-3" aria-hidden="true" />
				{$t('apps.detail.installed')}
			</Badge>
		{/if}
		<Badge variant="neutral"><AppCategory category={app.category} /></Badge>
		{#if app.version}
			<Badge variant="neutral">
				<Tag class="w-3 h-3" aria-hidden="true" />
				{app.version}
			</Badge>
		{/if}
		<Badge variant="neutral">
			{#if source.verified}<BadgeCheck class="w-3 h-3" aria-hidden="true" />{/if}
			{$t(source.key)}
		</Badge>
	</div>
	{#if app.website || app.github || app.documentation}
		<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
			{#if app.website}{@render link(app.website, Globe, 'apps.detail.website')}{/if}
			{#if app.github}{@render link(app.github, Github, 'apps.detail.github')}{/if}
			{#if app.documentation}{@render link(app.documentation, BookOpen, 'apps.detail.docs')}{/if}
		</div>
	{/if}
</div>

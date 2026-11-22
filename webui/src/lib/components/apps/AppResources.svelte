<script lang="ts">
	// The containers of an installed app with their use against their limits (ADR-0390): the
	// limits as Docker keeps them, the use as UNOS last read it from the kernel, about every 30
	// seconds; Limits opens the dialog that changes a container's. A table in all but its markup:
	// a row per container, the memory and the CPU in columns of their own and Limits always at
	// the row's end; on a phone the name and Limits share a line, the use goes under them. What
	// was not read yet is said once, under the rows (webui.ux-apps).
	import { SlidersHorizontal } from 'lucide-svelte';
	import { Button, ProgressBar } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatNumber, formatSize } from '$lib/format';
	import type { ContainerResources, InstalledApp } from '$lib/api/apps';

	type Props = {
		app: InstalledApp;
		class?: string;
		/** A container's Limits was pressed: the dialog that changes them opens for it. */
		onlimits?: (target: { container: string; resources: ContainerResources }) => void;
	};

	let { app, class: className = '', onlimits }: Props = $props();

	// A container that a fake or an older answer lists without them: no limits, no use read.
	const NONE: ContainerResources = {
		memory_limit: null,
		cpu_limit: null,
		memory_used: null,
		cpu_used: null,
		sampled_at: null
	};

	interface Row {
		name: string;
		running: boolean;
		resources: ContainerResources;
	}

	// The main container first, then the companions that Docker has.
	const rows: Row[] = $derived([
		...(app.status === 'missing'
			? []
			: [{ name: app.name, running: app.status === 'running', resources: app.resources ?? NONE }]),
		...app.companions.map((companion) => ({
			name: companion.container,
			running: companion.status === 'running',
			resources: companion.resources ?? NONE
		}))
	]);

	// The running containers whose use was not read yet: said once, under the rows.
	const unread = $derived(rows.filter((row) => row.running && row.resources.memory_used === null).length);

	function formatCpus(cpus: number): string {
		return formatNumber(cpus, $intlLocale, { digits: 2 });
	}

	/** The use is shown while the container runs and once it was read. */
	const read = (row: Row, used: number | null): used is number => row.running && used !== null;

	function memoryText(row: Row): string {
		const { memory_used: used, memory_limit: limit } = row.resources;
		if (read(row, used)) {
			return limit !== null
				? $t('apps.resources.memoryUse', {
						used: formatSize(used, $intlLocale, $t),
						limit: formatSize(limit, $intlLocale, $t)
					})
				: $t('apps.resources.memoryUseNoLimit', { used: formatSize(used, $intlLocale, $t) });
		}
		return limit !== null
			? $t('apps.resources.memoryLimit', { limit: formatSize(limit, $intlLocale, $t) })
			: $t('apps.resources.noLimit');
	}

	function cpuText(row: Row): string {
		const { cpu_used: used, cpu_limit: limit } = row.resources;
		if (read(row, used)) {
			return limit !== null
				? $t('apps.resources.cpuUse', { used: formatCpus(used), limit: formatCpus(limit) })
				: $t('apps.resources.cpuUseNoLimit', { used: formatCpus(used) });
		}
		return limit !== null ? $t('apps.resources.cpuLimit', { limit: formatCpus(limit) }) : $t('apps.resources.noLimit');
	}
</script>

{#snippet use(label: string, text: string, field: string, value: number | null, max: number | null, bar: string)}
	<div class="min-w-0">
		<span class="block text-xs text-content-secondary md:sr-only">{label}</span>
		<span class="block text-sm tabular" data-field={field}>{text}</span>
		{#if value !== null && max !== null}
			<ProgressBar size="sm" class="mt-1" {value} {max} label={bar} />
		{/if}
	</div>
{/snippet}

<section class="mt-4 pt-4 border-t border-border {className || ''}">
	<h4 class="text-sm font-medium mb-2">{$t('apps.resources.title')}</h4>
	<!-- The columns' heads, from md; on a phone each value carries its label. -->
	<div
		class="hidden md:grid md:grid-cols-[minmax(0,14rem)_1fr_1fr_auto] gap-x-6 pb-2 text-xs text-content-secondary"
		aria-hidden="true"
	>
		<span>{$t('apps.resources.container')}</span>
		<span>{$t('apps.resources.memory')}</span>
		<span>{$t('apps.resources.cpu')}</span>
		<span class="w-[5.5rem]"></span>
	</div>
	<ul class="divide-y divide-border-muted">
		{#each rows as row (row.name)}
			<li
				class="grid grid-cols-2 md:grid-cols-[minmax(0,14rem)_1fr_1fr_auto] items-center gap-x-6 gap-y-2 py-2.5"
				aria-label={row.name}
			>
				<span class="mono text-sm break-all">{row.name}</span>
				<div class="justify-self-end md:order-last">
					<Button
						variant="secondary"
						size="sm"
						icon={SlidersHorizontal}
						onclick={() => onlimits?.({ container: row.name, resources: row.resources })}
					>
						{$t('apps.resources.limits')}
					</Button>
				</div>
				{@render use(
					$t('apps.resources.memory'),
					memoryText(row),
					'memory',
					read(row, row.resources.memory_used) ? row.resources.memory_used : null,
					row.resources.memory_limit,
					$t('apps.resources.memoryBar', { name: row.name })
				)}
				{@render use(
					$t('apps.resources.cpu'),
					cpuText(row),
					'cpu',
					read(row, row.resources.cpu_used) ? row.resources.cpu_used : null,
					row.resources.cpu_limit,
					$t('apps.resources.cpuBar', { name: row.name })
				)}
			</li>
		{/each}
	</ul>
	<p class="mt-2 text-xs text-content-muted">
		{$t('apps.resources.hint')}
		{#if unread > 0}{$t('apps.resources.notRead', { n: unread })}{/if}
	</p>
</section>

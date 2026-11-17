<script lang="ts">
	// The containers of an installed app with their use against their limits (ADR-0388): the
	// limits as Docker keeps them, the use as UNOS last read it from the kernel, about every 30
	// seconds; Limits opens the dialog that changes a container's.
	import { createEventDispatcher } from 'svelte';
	import { SlidersHorizontal } from 'lucide-svelte';
	import { Button, ProgressBar } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import type { ContainerResources, InstalledApp } from '$lib/api/apps';

	export let app: InstalledApp;

	const dispatch = createEventDispatcher<{ limits: { container: string; resources: ContainerResources } }>();

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
	let rows: Row[];
	$: rows = [
		...(app.status === 'missing'
			? []
			: [{ name: app.name, running: app.status === 'running', resources: app.resources ?? NONE }]),
		...app.companions.map((companion) => ({
			name: companion.container,
			running: companion.status === 'running',
			resources: companion.resources ?? NONE
		}))
	];

	// The units of a size, by powers of 1024, as the details of an app write them.
	const SIZE_UNITS = ['apps.size.bytes', 'apps.size.kb', 'apps.size.mb', 'apps.size.gb', 'apps.size.tb'];

	$: formatSize = (bytes: number): string => {
		const power =
			bytes > 0 ? Math.max(0, Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), SIZE_UNITS.length - 1)) : 0;
		const size = new Intl.NumberFormat($intlLocale, { maximumFractionDigits: 1, useGrouping: false }).format(
			bytes / 1024 ** power
		);
		return $t(SIZE_UNITS[power], { size });
	};

	$: formatCpus = (cpus: number): string =>
		new Intl.NumberFormat($intlLocale, { maximumFractionDigits: 2 }).format(cpus);

	/** The use is shown while the container runs and once it was read. */
	const read = (row: Row, used: number | null): used is number => row.running && used !== null;

	$: memoryText = (row: Row): string => {
		const { memory_used: used, memory_limit: limit } = row.resources;
		if (read(row, used)) {
			return limit !== null
				? $t('apps.resources.memoryUse', { used: formatSize(used), limit: formatSize(limit) })
				: $t('apps.resources.memoryUseNoLimit', { used: formatSize(used) });
		}
		return limit !== null
			? $t('apps.resources.memoryLimit', { limit: formatSize(limit) })
			: $t('apps.resources.memoryNoLimit');
	};

	$: cpuText = (row: Row): string => {
		const { cpu_used: used, cpu_limit: limit } = row.resources;
		if (read(row, used)) {
			return limit !== null
				? $t('apps.resources.cpuUse', { used: formatCpus(used), limit: formatCpus(limit) })
				: $t('apps.resources.cpuUseNoLimit', { used: formatCpus(used) });
		}
		return limit !== null
			? $t('apps.resources.cpuLimit', { limit: formatCpus(limit) })
			: $t('apps.resources.cpuNoLimit');
	};
</script>

<div class="mt-4 pt-4 border-t border-border {$$props.class || ''}">
	<h4 class="text-sm font-medium">{$t('apps.resources.title')}</h4>
	<p class="text-xs text-muted mb-3">{$t('apps.resources.hint')}</p>
	<ul class="space-y-3">
		{#each rows as row (row.name)}
			<li class="flex flex-wrap items-center gap-x-4 gap-y-2" aria-label={row.name}>
				<span class="mono text-sm break-all w-full md:w-56 flex-shrink-0">{row.name}</span>
				<div class="flex-1 min-w-[10rem]">
					<p class="text-xs text-secondary">{memoryText(row)}</p>
					{#if read(row, row.resources.memory_used) && row.resources.memory_limit !== null}
						<ProgressBar
							size="sm"
							class="mt-1"
							value={row.resources.memory_used}
							max={row.resources.memory_limit}
							label={$t('apps.resources.memoryBar', { name: row.name })}
						/>
					{/if}
				</div>
				<div class="flex-1 min-w-[10rem]">
					<p class="text-xs text-secondary">{cpuText(row)}</p>
					{#if read(row, row.resources.cpu_used) && row.resources.cpu_limit !== null}
						<ProgressBar
							size="sm"
							class="mt-1"
							value={row.resources.cpu_used}
							max={row.resources.cpu_limit}
							label={$t('apps.resources.cpuBar', { name: row.name })}
						/>
					{/if}
				</div>
				{#if row.running && row.resources.memory_used === null}
					<p class="text-xs text-muted w-full">{$t('apps.resources.notRead')}</p>
				{/if}
				<Button
					variant="secondary"
					size="sm"
					icon={SlidersHorizontal}
					on:click={() => dispatch('limits', { container: row.name, resources: row.resources })}
				>
					{$t('apps.resources.limits')}
				</Button>
			</li>
		{/each}
	</ul>
</div>

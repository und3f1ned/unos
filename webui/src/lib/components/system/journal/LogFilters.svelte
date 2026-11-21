<script lang="ts">
	// The filters of System > Logs: a search in the messages, the level and the time range of the
	// journal, and how many entries to show, with what is active as badges. The tab reads the
	// entries again when one changes.
	import type { LogPriority } from '$lib/api/logs';
	import { Badge, Button, Card, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Search, X } from 'lucide-svelte';
	import { textOf } from './logs';

	type Props = {
		search: string;
		priority: string;
		since: string;
		limitStr: string;
		loading: boolean;
		hasActiveFilters: boolean;
		onsearch: () => void;
		onclearsearch: () => void;
		onfilterchange: () => void;
		onclear: () => void;
	};

	let {
		search = $bindable(),
		priority = $bindable(),
		since = $bindable(),
		limitStr = $bindable(),
		loading,
		hasActiveFilters,
		onsearch,
		onclearsearch,
		onfilterchange,
		onclear
	}: Props = $props();

	// The journal's levels, the most severe first: the filter lists a level and the more severe
	// ones.
	const PRIORITIES: Record<LogPriority, string> = {
		emerg: 'logs.priority.emerg',
		alert: 'logs.priority.alert',
		crit: 'logs.priority.crit',
		err: 'logs.priority.err',
		warning: 'logs.priority.warning',
		notice: 'logs.priority.notice',
		info: 'logs.priority.info',
		debug: 'logs.priority.debug'
	};
	// The time ranges of the filter by the since they send: journalctl's --since, which the server
	// takes as <N> minutes ago, <N> hours ago or <N> days ago; "" reads the whole journal.
	const TIME_RANGES: Record<string, string> = {
		'': 'logs.time.all',
		'5 minutes ago': 'logs.time.last5Minutes',
		'15 minutes ago': 'logs.time.last15Minutes',
		'1 hour ago': 'logs.time.lastHour',
		'6 hours ago': 'logs.time.last6Hours',
		'24 hours ago': 'logs.time.last24Hours',
		'7 days ago': 'logs.time.last7Days'
	};

	// Options for Select components, in the language of the page.
	let priorityOptions = $derived([
		{ value: '', label: $t('logs.filter.allPriorities') },
		...Object.entries(PRIORITIES).map(([value, key]) => ({ value, label: $t(key) }))
	]);

	let timeRangeOptions = $derived(Object.entries(TIME_RANGES).map(([value, key]) => ({ value, label: $t(key) })));

	const limitOptions = [
		{ value: '50', label: '50' },
		{ value: '100', label: '100' },
		{ value: '200', label: '200' },
		{ value: '500', label: '500' },
		{ value: '1000', label: '1000' }
	];
</script>

<Card>
	<div class="space-y-4">
		<!-- Search -->
		<div class="flex gap-2">
			<div class="relative flex-1">
				<Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-content-muted" aria-hidden="true" />
				<input
					type="text"
					bind:value={search}
					onkeydown={(e) => e.key === 'Enter' && onsearch()}
					placeholder={$t('logs.searchPlaceholder')}
					aria-label={$t('logs.searchPlaceholder')}
					class="form-input pl-10 pr-10 text-sm"
				/>
				{#if search}
					<Button
						variant="plain"
						class="absolute right-3 top-1/2 -translate-y-1/2 text-content-muted hover:text-content"
						title={$t('logs.clearSearch')}
						onclick={onclearsearch}
					>
						<X class="w-4 h-4" />
						<span class="sr-only">{$t('logs.clearSearch')}</span>
					</Button>
				{/if}
			</div>
			<Button variant="secondary" onclick={onsearch} disabled={loading}>
				{$t('logs.search')}
			</Button>
		</div>

		<!-- Filter Row -->
		<div class="flex flex-wrap items-end gap-4">
			<!-- Priority Filter -->
			<Select
				class="flex-1 min-w-[150px]"
				label={$t('logs.filter.priority')}
				bind:value={priority}
				options={priorityOptions}
				placeholder={$t('logs.filter.allPriorities')}
				onchange={onfilterchange}
			/>

			<!-- Time Range Filter -->
			<Select
				class="flex-1 min-w-[150px]"
				label={$t('logs.filter.timeRange')}
				bind:value={since}
				options={timeRangeOptions}
				placeholder={$t('logs.time.all')}
				onchange={onfilterchange}
			/>

			<!-- Limit -->
			<Select
				class="w-[120px]"
				label={$t('logs.filter.limit')}
				bind:value={limitStr}
				options={limitOptions}
				onchange={onfilterchange}
			/>

			<!-- Clear Filters -->
			{#if hasActiveFilters}
				<Button variant="ghost" size="sm" onclick={onclear}>
					<X class="w-4 h-4 mr-1" />
					{$t('logs.filter.clear')}
				</Button>
			{/if}
		</div>

		<!-- Active Filters Badges -->
		{#if hasActiveFilters}
			<div class="flex flex-wrap gap-2 pt-2 border-t border-border">
				<span class="text-sm text-content-secondary">{$t('logs.filter.active')}</span>
				{#if priority}
					<Badge variant="neutral">
						{$t('logs.filter.priorityBadge', { level: textOf($t, PRIORITIES, priority) })}
					</Badge>
				{/if}
				{#if since}
					<Badge variant="neutral">
						{$t('logs.filter.timeBadge', { range: textOf($t, TIME_RANGES, since) })}
					</Badge>
				{/if}
				{#if search}
					<Badge variant="neutral">
						{$t('logs.filter.searchBadge', { text: search })}
					</Badge>
				{/if}
			</div>
		{/if}
	</div>
</Card>

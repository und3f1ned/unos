<script lang="ts">
	import { CheckCircle, History, XCircle } from 'lucide-svelte';
	import { Badge, Button, EmptyState, Sheet, Alert, StatusDot, TechDetails } from '$lib/components/ui';
	import { formatDuration } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { apiTime, formatDateTime } from '$lib/time';
	import type { TaskHistory } from './history.svelte';

	// The runs of every task, or of one, newest first, in a side panel (a Sheet, ADR-0432), their
	// times on the NAS's clock.
	type Props = {
		history: TaskHistory;
		// The NAS's time zone; null while it is not known (the browser's then).
		zone: string | null;
	};

	let { history, zone }: Props = $props();

	const STATUS_VARIANTS: Record<string, 'success' | 'error' | 'warning'> = {
		success: 'success',
		failed: 'error',
		running: 'warning'
	};

	// The status of a run (TaskRun.status), by value.
	const RUN_STATUSES: Record<string, string> = {
		success: 'scheduler.runs.success',
		failed: 'scheduler.runs.failed',
		running: 'scheduler.runs.running'
	};

	// The functions below that give texts are reactive, so that the history follows a change of
	// the language.

	// A status without a label as it comes.
	let getStatusLabel = $derived((status: string): string =>
		Object.prototype.hasOwnProperty.call(RUN_STATUSES, status) ? $t(RUN_STATUSES[status]) : status
	);

	let runDuration = $derived((start: string, end?: string): string => {
		if (!end) return $t('scheduler.runs.inProgress');
		const ms = (apiTime(end)?.getTime() ?? NaN) - (apiTime(start)?.getTime() ?? NaN);
		if (Number.isNaN(ms)) return '';
		// Under a minute with one decimal of a second, in the language's digits (1.5s); under a
		// second in milliseconds.
		return formatDuration(ms / 1000, $intlLocale, $t, { smallest: 's', digits: 1 });
	});
</script>

<Sheet
	open={history.open}
	title={$t('scheduler.runs.title', {
		name: history.taskId ? history.taskName || $t('scheduler.runs.task') : $t('scheduler.runs.allTasks')
	})}
	size="lg"
	onclose={() => history.close()}
>
	{#if history.runs.length === 0}
		<EmptyState icon={History} compact title={$t('scheduler.runs.empty')} />
	{:else}
		<ul class="space-y-2">
			{#each history.runs as run (run.id)}
				<li class="p-3 bg-surface-overlay rounded-lg">
					<div class="flex flex-wrap items-center justify-between gap-2 mb-2">
						<div class="flex items-center gap-2 min-w-0">
							{#if run.status === 'success'}
								<CheckCircle class="w-4 h-4 shrink-0 text-status-success" aria-hidden="true" />
							{:else if run.status === 'failed'}
								<XCircle class="w-4 h-4 shrink-0 text-status-error" aria-hidden="true" />
							{:else}
								<!-- Running: the pulse of the design system's live state (StatusDot). -->
								<span class="w-4 h-4 shrink-0 grid place-items-center"><StatusDot status="warning" pulse /></span>
							{/if}
							<span class="font-medium text-sm break-words min-w-0">{run.task_name}</span>
							<Badge variant={STATUS_VARIANTS[run.status] ?? 'neutral'}>{getStatusLabel(run.status)}</Badge>
						</div>
						<span class="text-xs text-content-muted tabular">{runDuration(run.started_at, run.finished_at)}</span>
					</div>
					<div class="flex flex-wrap gap-x-3 text-xs text-content-secondary">
						<span class="tabular">{formatDateTime(run.started_at, $intlLocale, { zone })}</span>
						{#if typeof run.exit_code === 'number'}
							<span>{$t('scheduler.runs.exitStatus', { code: run.exit_code })}</span>
						{/if}
					</div>
					{#if run.error}
						<Alert variant="error" size="sm" class="mt-2"><p class="mono text-xs break-all">{run.error}</p></Alert>
					{/if}
					{#if run.output}
						<TechDetails text={run.output} summary={$t('scheduler.runs.output')} />
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
	{#snippet footer()}
		<Button variant="secondary" onclick={() => history.close()}>{$t('scheduler.runs.close')}</Button>
	{/snippet}
</Sheet>

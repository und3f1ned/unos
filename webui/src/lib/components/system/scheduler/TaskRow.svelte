<script lang="ts">
	import { untrack } from 'svelte';
	import { Edit3, History, Trash2, Zap } from 'lucide-svelte';
	import type { ScheduledTask, TaskType } from '$lib/api/scheduler';
	import { ActionMenu, Badge, Button, Switch } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatClock, formatDateTime } from '$lib/time';
	import { DAYS_OF_WEEK, TASK_TYPES, getTaskTypeIcon } from './tasks';

	// A task of the list (ADR-0432): its switch, type, name, target, schedule and next run on the
	// NAS's clock; Edit and Delete as icons, Run now and its history in its ⋯ menu.
	type Props = {
		task: ScheduledTask;
		// The NAS's time zone, whose clock the times are on; null while it is not known.
		zone: string | null;
		// Run Now was asked and the server has not answered yet.
		running: boolean;
		// The on/off was changed and the server has not answered yet: the switch shows the new state.
		toggling: boolean;
		ontoggle: (task: ScheduledTask) => void;
		onrun: (task: ScheduledTask) => void;
		onhistory: (task: ScheduledTask) => void;
		onedit: (task: ScheduledTask) => void;
		ondelete: (task: ScheduledTask) => void;
	};

	let { task, zone, running, toggling, ontoggle, onrun, onhistory, onedit, ondelete }: Props = $props();

	let TypeIcon = $derived(getTaskTypeIcon(task.task_type));

	// The switch's state: the task's, again whenever it changes (a load, or the page putting the stored
	// state back after a refused toggle).
	let on = $state(untrack(() => task.enabled));
	$effect.pre(() => {
		const enabled = task.enabled;
		untrack(() => (on = enabled));
	});

	// The functions below that give texts are reactive, so that the list follows a change of the
	// language.

	// A type without a label as it comes.
	let getTaskTypeLabel = $derived((type: TaskType): string => {
		const known = TASK_TYPES.find((option) => option.value === type);
		return known ? $t(known.key) : type;
	});

	// On the clock of the NAS's time zone. A weekly or monthly task without its day runs on the
	// server's default, Sunday or the 1st (get_cron_expression of scheduler/router.py).
	let formatSchedule = $derived((task: ScheduledTask): string => {
		if (task.frequency === 'custom' && task.cron_expression) {
			return task.cron_expression;
		}

		// design-ignore: the minute of every hour, written :05 in every language
		const minute = String(task.minute).padStart(2, '0');
		const time = formatClock(task.hour, task.minute, $intlLocale);
		const weekday = DAYS_OF_WEEK.find((d) => d.value === (task.day_of_week ?? 0));

		if (task.frequency === 'hourly') return $t('scheduler.schedule.hourly', { minute });
		if (task.frequency === 'daily') return $t('scheduler.schedule.daily', { time });
		if (task.frequency === 'weekly' && weekday) return $t(weekday.schedule, { time });
		if (task.frequency === 'monthly') return $t('scheduler.schedule.monthly', { day: task.day_of_month ?? 1, time });
		return task.frequency;
	});

	const nextRun = $derived(task.enabled && task.next_run ? formatDateTime(task.next_run, $intlLocale, { zone }) : '');
</script>

<!-- Below sm the actions wrap below the task, which keeps the row's width (ADR-0302). -->
<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-3" data-task={task.name}>
	<div class="flex min-w-0 flex-1 items-start gap-3 max-sm:basis-full">
		<!-- On or off: a switch, which is no action icon (ADR-0432); it takes no second change until
         the server has answered the first. -->
		<Switch
			bind:checked={on}
			disabled={toggling}
			label={$t('scheduler.task.enabledLabel', { name: task.name })}
			onchange={() => ontoggle(task)}
		/>

		<div class="min-w-0 flex-1">
			<div class="flex flex-wrap items-center gap-2">
				<TypeIcon class="h-4 w-4 shrink-0 text-content-secondary" aria-hidden="true" />
				<p class="min-w-0 truncate text-sm font-medium" title={task.name}>{task.name}</p>
				{#if !task.enabled}
					<Badge variant="neutral">{$t('scheduler.task.paused')}</Badge>
				{:else if task.last_status === 'failed'}
					<Badge variant="error" dot>{$t('scheduler.task.lastFailed')}</Badge>
				{/if}
			</div>
			<!-- Wraps on a phone: cut off, a long type such as the Russian one hid the schedule. -->
			<p class="break-words text-xs text-content-secondary">
				{getTaskTypeLabel(task.task_type)}
				{#if task.target_pool}
					{$t('scheduler.task.on', { target: task.target_pool })}
				{:else if task.target_disk}
					{$t('scheduler.task.on', { target: task.target_disk })}
				{/if}
				&bull; {formatSchedule(task)}
			</p>
			{#if nextRun}
				<p class="mt-0.5 text-xs text-content-muted tabular">
					<span>{$t('scheduler.task.nextRun')}</span>
					<span>{nextRun}</span>
				</p>
			{/if}
		</div>
	</div>

	<!-- Actions: one line, the two icons and the menu. -->
	<div class="table-actions ml-auto">
		<Button
			variant="ghost"
			size="icon-sm"
			icon={Edit3}
			onclick={() => onedit(task)}
			title={$t('scheduler.task.edit')}
			aria-label={$t('scheduler.task.editOne', { name: task.name })}
		></Button>
		<Button
			variant="ghost-danger"
			size="icon-sm"
			icon={Trash2}
			onclick={() => ondelete(task)}
			title={$t('scheduler.task.delete')}
			aria-label={$t('scheduler.task.deleteOne', { name: task.name })}
		></Button>
		<ActionMenu
			name={task.name}
			items={[
				{ label: $t('scheduler.task.runNow'), icon: Zap, disabled: running, onclick: () => onrun(task) },
				{ label: $t('scheduler.task.viewHistory'), icon: History, onclick: () => onhistory(task) }
			]}
		/>
	</div>
</div>

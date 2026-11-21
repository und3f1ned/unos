import { Archive, Database, HardDrive, Search, Terminal, UploadCloud } from 'lucide-svelte';
import type { ScheduleFrequency, ScheduledTask, TaskType } from '$lib/api/scheduler';
import type { IconComponent } from '$lib/components/ui';

// The task types, frequencies and days of the week, as the server names them, with the keys of
// their labels in the catalog.
export const TASK_TYPES: { value: TaskType; key: string; icon: IconComponent }[] = [
	{ value: 'snapshot', key: 'scheduler.type.snapshot', icon: Database },
	{ value: 'scrub', key: 'scheduler.type.scrub', icon: Search },
	{ value: 'smart_test', key: 'scheduler.type.smartTest', icon: HardDrive },
	{ value: 'backup', key: 'scheduler.type.backup', icon: Archive },
	{ value: 'offsite_copy', key: 'scheduler.type.offsiteCopy', icon: UploadCloud },
	{ value: 'custom', key: 'scheduler.type.custom', icon: Terminal }
];

export const FREQUENCIES: { value: ScheduleFrequency; key: string }[] = [
	{ value: 'hourly', key: 'scheduler.frequency.hourly' },
	{ value: 'daily', key: 'scheduler.frequency.daily' },
	{ value: 'weekly', key: 'scheduler.frequency.weekly' },
	{ value: 'monthly', key: 'scheduler.frequency.monthly' },
	{ value: 'custom', key: 'scheduler.frequency.custom' }
];

// As cron numbers them; `schedule` is the key of a weekly schedule on that day.
export const DAYS_OF_WEEK = [
	{ value: 0, key: 'scheduler.day.sunday', schedule: 'scheduler.schedule.sundays' },
	{ value: 1, key: 'scheduler.day.monday', schedule: 'scheduler.schedule.mondays' },
	{ value: 2, key: 'scheduler.day.tuesday', schedule: 'scheduler.schedule.tuesdays' },
	{ value: 3, key: 'scheduler.day.wednesday', schedule: 'scheduler.schedule.wednesdays' },
	{ value: 4, key: 'scheduler.day.thursday', schedule: 'scheduler.schedule.thursdays' },
	{ value: 5, key: 'scheduler.day.friday', schedule: 'scheduler.schedule.fridays' },
	{ value: 6, key: 'scheduler.day.saturday', schedule: 'scheduler.schedule.saturdays' }
];

// At least as long as the server asks (MIN_PASSPHRASE_LENGTH in scheduler/router.py).
export const MIN_PASSPHRASE_LENGTH = 8;

/** The form of a new task, before its pool and disk are chosen. */
export function getDefaultFormData(): Partial<ScheduledTask> {
	return {
		name: '',
		task_type: 'snapshot',
		enabled: true,
		frequency: 'daily',
		hour: 2,
		minute: 0,
		day_of_week: 0,
		day_of_month: 1,
		cron_expression: '0 2 * * *',
		target_pool: '',
		target_disk: '',
		command: '',
		keep_count: 7
	};
}

export function getTaskTypeIcon(type: TaskType): IconComponent {
	return TASK_TYPES.find((option) => option.value === type)?.icon || Terminal;
}

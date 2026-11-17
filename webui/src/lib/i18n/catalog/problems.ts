import type { Translation } from '../translate';

// The health banner above every page of an administrator: src/lib/components/ProblemsBanner.svelte.
// One text per kind of problem (supervisor/src/health/problems.py, ADR-0332), with {subject}: the
// pool's name, the disk's path, the task's name or <pool>/<volume>.

export const en = {
	'problems.title': {
		one: '{n} problem needs attention',
		other: '{n} problems need attention'
	},
	'problems.error': 'Error',
	'problems.warning': 'Warning',
	'problems.kind.pool_unmounted': 'Pool {subject} is not mounted',
	'problems.kind.pool_degraded': 'Pool {subject} is degraded: a disk is missing or has errors',
	'problems.kind.pool_read_only': 'Pool {subject} became read-only after an error',
	'problems.kind.pool_low_space': 'Pool {subject} is running out of space',
	'problems.kind.disk_failed': 'Disk {subject} is failing its SMART health check',
	'problems.kind.disk_test_failed': 'The last SMART self-test of disk {subject} failed',
	'problems.kind.task_failed': 'The last run of task {subject} failed',
	'problems.kind.task_missed': 'Task {subject} missed its scheduled run',
	// A volume that reached a level of its quota: {subject} is <pool>/<volume>.
	'problems.kind.volume_quota_80': 'Volume {subject} has reached 80% of its quota',
	'problems.kind.volume_quota_90': 'Volume {subject} has reached 90% of its quota',
	'problems.kind.volume_quota_95': 'Volume {subject} has reached 95% of its quota',
	// A kind that this web UI does not know yet.
	'problems.kind.other': 'A problem with {subject}'
};

export const ru: Translation<typeof en> = {
	'problems.title': {
		one: '{n} проблема требует внимания',
		few: '{n} проблемы требуют внимания',
		many: '{n} проблем требуют внимания'
	},
	'problems.error': 'Ошибка',
	'problems.warning': 'Предупреждение',
	'problems.kind.pool_unmounted': 'Пул {subject} не смонтирован',
	'problems.kind.pool_degraded': 'Пул {subject} деградирован: диска не хватает или на нём ошибки',
	'problems.kind.pool_read_only': 'Пул {subject} перешёл в режим только для чтения после ошибки',
	'problems.kind.pool_low_space': 'На пуле {subject} заканчивается место',
	'problems.kind.disk_failed': 'Диск {subject} не проходит проверку здоровья SMART',
	'problems.kind.disk_test_failed': 'Последний самотест SMART диска {subject} завершился ошибкой',
	'problems.kind.task_failed': 'Последний запуск задачи {subject} завершился ошибкой',
	'problems.kind.task_missed': 'Задача {subject} пропустила запуск по расписанию',
	'problems.kind.volume_quota_80': 'Том {subject} заполнил 80% своей квоты',
	'problems.kind.volume_quota_90': 'Том {subject} заполнил 90% своей квоты',
	'problems.kind.volume_quota_95': 'Том {subject} заполнил 95% своей квоты',
	'problems.kind.other': 'Проблема: {subject}'
};

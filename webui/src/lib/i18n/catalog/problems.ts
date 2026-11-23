import type { Translation } from '../translate';

// The health banner above every page of an administrator: src/lib/components/ProblemsBanner.svelte.
// One text per kind of problem (supervisor/src/health/problems.py, ADR-0332), with {subject}: the
// pool's name, the disk's path, the task's name, <pool>/<volume>, a container's name or the time of a
// shutdown on the host's clock.

export const en = {
	'problems.title': {
		one: '{n} problem needs attention',
		other: '{n} problems need attention'
	},
	'problems.error': 'Error',
	'problems.warning': 'Warning',
	'problems.showAll': { one: 'Show all {n}', other: 'Show all {n}' },
	'problems.showFewer': 'Show fewer',
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
	// A container of an app at its limits (ADR-0390): {subject} is the container's name.
	'problems.kind.app_memory': 'Container {subject} is near its memory limit',
	'problems.kind.app_cpu': 'Container {subject} is held at its CPU limit',
	'problems.kind.app_oom': 'Container {subject} ran out of memory: the kernel ended a process of it',
	// A shutdown of the power settings (ADR-0407): {subject} is its time on the host's clock, "23:00 (Europe/Berlin)".
	'problems.kind.shutdown_scheduled':
		'The host shuts down at {subject}, as the power settings schedule it. Running jobs postpone it.',
	'problems.kind.shutdown_postponed':
		'The scheduled shutdown of {subject} is postponed: System > Power names what it waits for.',
	'problems.kind.shutdown_idle': 'The host is idle and shuts down at about {subject} unless it is used before.',
	// The system disk when it is an eMMC (ADR-0471): {subject} is its device, /dev/mmcblk0.
	'problems.kind.emmc_worn': 'The eMMC {subject} that holds the system has used most of its life',
	'problems.kind.system_disk_errors': 'The system disk {subject} reports I/O or filesystem errors',
	'problems.kind.system_disk_failed': 'The system disk {subject} is failing',
	// The SD card standby (ADR-0471): {subject} is its card, /dev/mmcblk1, or for running_on_standby the
	// chooser's reason (marked, tries, emmc_unreadable, emmc_boot_failed).
	'problems.kind.standby_sync_failed': 'The last sync of the SD standby {subject} failed',
	'problems.kind.standby_copy_old': 'The SD standby {subject} had no complete sync for 3 days',
	'problems.kind.standby_card_missing': 'The SD standby is set up, but its card {subject} is missing',
	'problems.kind.standby_not_in_boot_path':
		"This boot did not pass the SD standby's chooser on {subject}: an eMMC failure would not switch to the card",
	'problems.kind.standby_has_changes': 'The SD standby {subject} ran and holds changes that the eMMC lacks',
	'problems.kind.running_on_standby': 'UNOS runs from the SD standby ({subject})',
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
	'problems.showAll': { one: 'Показать все {n}', few: 'Показать все {n}', many: 'Показать все {n}' },
	'problems.showFewer': 'Показать меньше',
	'problems.kind.pool_unmounted': 'Пул {subject} не смонтирован',
	'problems.kind.pool_degraded': 'Пул {subject} деградирован: диска не хватает или на нём ошибки',
	'problems.kind.pool_read_only': 'Пул {subject} перешёл в режим только для чтения после ошибки',
	'problems.kind.pool_low_space': 'В пуле {subject} заканчивается место',
	'problems.kind.disk_failed': 'Диск {subject} не проходит проверку здоровья SMART',
	'problems.kind.disk_test_failed': 'Последний самотест SMART диска {subject} завершился ошибкой',
	'problems.kind.task_failed': 'Последний запуск задачи {subject} завершился ошибкой',
	'problems.kind.task_missed': 'Задача {subject} пропустила запуск по расписанию',
	'problems.kind.volume_quota_80': 'Том {subject} заполнен на 80\u00a0% квоты',
	'problems.kind.volume_quota_90': 'Том {subject} заполнен на 90\u00a0% квоты',
	'problems.kind.volume_quota_95': 'Том {subject} заполнен на 95\u00a0% квоты',
	'problems.kind.app_memory': 'Контейнер {subject} близок к ограничению памяти',
	'problems.kind.app_cpu': 'Контейнер {subject} упирается в ограничение ЦП',
	'problems.kind.app_oom': 'Контейнеру {subject} не хватило памяти: ядро завершило его процесс',
	'problems.kind.shutdown_scheduled':
		'Хост выключится в {subject} по расписанию, заданному в настройках питания. Выполняющиеся задания откладывают выключение.',
	'problems.kind.shutdown_postponed':
		'Выключение по расписанию в {subject} отложено: в разделе «Система» → «Питание» указано, чего оно ждёт.',
	'problems.kind.shutdown_idle': 'Хост простаивает и выключится примерно в {subject}, если им не воспользуются.',
	'problems.kind.emmc_worn': 'eMMC {subject}, на которой находится система, израсходовала большую часть ресурса',
	'problems.kind.system_disk_errors': 'Системный диск {subject} сообщает об ошибках ввода-вывода или файловой системы',
	'problems.kind.system_disk_failed': 'Системный диск {subject} отказывает',
	'problems.kind.standby_sync_failed': 'Последняя синхронизация резерва на SD {subject} не удалась',
	'problems.kind.standby_copy_old': 'Резерв на SD {subject} не синхронизировался полностью 3 дня',
	'problems.kind.standby_card_missing': 'Резерв на SD настроен, но его карты {subject} нет',
	'problems.kind.standby_not_in_boot_path':
		'Эта загрузка прошла мимо выбора резерва на SD {subject}: при отказе eMMC переключения на карту не будет',
	'problems.kind.standby_has_changes': 'Резерв на SD {subject} работал и хранит изменения, которых нет на eMMC',
	'problems.kind.running_on_standby': 'UNOS работает с резерва на SD ({subject})',
	'problems.kind.other': 'Проблема: {subject}'
};

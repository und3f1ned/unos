import type { Translation } from '../translate';

// The dashboard: src/routes/+page.svelte and its widgets in src/lib/components/widgets/. What
// the server sends as text (a notification's title and message, a disk's risk factors) stays
// as it comes.

export const en = {
	'dashboard.osUptime': '{os} · Uptime {uptime}',
	'dashboard.noProblems': 'No problems',
	'dashboard.issues': { one: '{n} issue', other: '{n} issues' },
	'dashboard.notification': 'Notification',

	'dashboard.replace.loadFailed': 'Could not load the failure predictions',
	'dashboard.issue.poolDegraded': 'Pool {name} is degraded',
	'dashboard.issue.poolError': 'Pool {name} has errors',
	'dashboard.issue.poolUnmounted': 'Pool {name} is not mounted',
	'dashboard.issue.poolUnknown': 'The state of pool {name} could not be read',
	'dashboard.issue.poolReadOnly': 'Pool {name} is mounted read-only',
	'dashboard.issue.replaceSoon': 'Replace disk {device} soon (serial {serial})',
	'dashboard.issue.predictionsFailed': 'Could not check the disks for a predicted failure',
	'dashboard.issue.poolsFailed': 'Could not read the pools',

	'dashboard.user.signedInAs': 'Signed in as {name}',
	'dashboard.user.managed': 'Storage, shares, containers and apps are managed by an administrator.',
	'dashboard.user.account': 'You can change your password, two-factor authentication and review your sign-ins under',
	'dashboard.user.accountLink': 'Account',

	'dashboard.stats.cpu': 'CPU',
	'dashboard.stats.cores': { one: '{n} core', other: '{n} cores' },
	'dashboard.stats.memory': 'Memory',
	'dashboard.stats.network': 'Network',
	'dashboard.stats.diskIo': 'Disk I/O',
	'dashboard.stats.read': 'Read',
	'dashboard.stats.write': 'Write',
	'dashboard.stats.received': 'Received',
	'dashboard.stats.sent': 'Sent',
	'dashboard.stats.temperature': 'Temperature',
	'dashboard.stats.none': 'No reading',

	'dashboard.manage': 'Manage',

	// Arranging the widgets (ADR-0395).
	'dashboard.layout.customize': 'Customize',
	'dashboard.layout.done': 'Done',
	'dashboard.layout.hint':
		'Drag a widget by its handle, or use the arrows, to move it. Hide the widgets you do not need: they stay here to show again.',
	'dashboard.layout.drag': 'Drag to move',
	'dashboard.layout.moveUp': 'Move {name} up',
	'dashboard.layout.moveDown': 'Move {name} down',
	'dashboard.layout.hide': 'Hide {name}',
	'dashboard.layout.show': 'Show {name}',
	'dashboard.layout.hiddenTitle': 'Hidden widgets',
	'dashboard.layout.noneHidden': 'No hidden widgets',
	'dashboard.layout.allHidden': 'All widgets are hidden',
	'dashboard.layout.allHiddenHint': 'Customize shows them again.',
	'dashboard.layout.reset': 'Reset to default',
	'dashboard.layout.moved': '{name} moved to position {position} of {total}',
	'dashboard.layout.hidden': '{name} hidden',
	'dashboard.layout.shown': '{name} shown',
	'dashboard.layout.wasReset': 'Dashboard reset to the default layout',
	'dashboard.layout.saveFailed': 'Could not save your dashboard layout: {error}',
	'dashboard.layout.loadFailed': 'Could not load your dashboard layout; the default is shown',

	'dashboard.storage.title': 'Storage pools',
	'dashboard.storage.none': 'No pools yet: create one on the Storage page.',
	'dashboard.storage.total': 'Total storage',
	'dashboard.storage.usedOf': '{used} of {size}',
	'dashboard.storage.pools': { one: '{n} pool', other: '{n} pools' },
	'dashboard.storage.attention': { one: '{n} needs attention', other: '{n} need attention' },
	'dashboard.storage.missing': { one: '{n} disk missing', other: '{n} disks missing' },
	'dashboard.storage.errors': { one: '{n} disk error', other: '{n} disk errors' },
	'dashboard.storage.forcedReadOnly': 'Read-only after an error',
	'dashboard.storage.readOnly': 'Read-only',
	// A pool's health, as GET /storage/pools reports it; "unknown" is common.unknown.
	'dashboard.health.healthy': 'Healthy',
	'dashboard.health.degraded': 'Degraded',
	'dashboard.health.unmounted': 'Not mounted',
	'dashboard.health.error': 'Error',

	'dashboard.containers.title': 'Containers',
	'dashboard.containers.running': 'Running',
	'dashboard.containers.stopped': 'Stopped',
	'dashboard.containers.other': 'Other',
	'dashboard.containers.runningCount': '{n} running',
	'dashboard.containers.stoppedCount': '{n} stopped',
	'dashboard.containers.none': 'No containers yet',
	'dashboard.containers.open': 'Open',
	'dashboard.containers.shown': { one: '{shown} of {n} container shown', other: '{shown} of {n} containers shown' },
	// A container's state, as Docker names it (ContainerState of supervisor/src/containers/schemas.py).
	'dashboard.containers.state.running': 'Running',
	'dashboard.containers.state.exited': 'Exited',
	'dashboard.containers.state.stopped': 'Stopped',
	'dashboard.containers.state.paused': 'Paused',
	'dashboard.containers.state.restarting': 'Restarting',
	'dashboard.containers.state.created': 'Created',
	'dashboard.containers.state.dead': 'Dead',

	'dashboard.activity.title': 'Recent activity',
	'dashboard.activity.viewAll': 'View all',
	'dashboard.activity.none': 'No recent activity'
};

export const ru: Translation<typeof en> = {
	'dashboard.osUptime': '{os} · Время работы {uptime}',
	'dashboard.noProblems': 'Проблем нет',
	'dashboard.issues': { one: '{n} проблема', few: '{n} проблемы', many: '{n} проблем' },
	'dashboard.notification': 'Уведомление',

	'dashboard.replace.loadFailed': 'Не удалось загрузить прогнозы отказов',
	'dashboard.issue.poolDegraded': 'Пул {name} деградирован',
	'dashboard.issue.poolError': 'В пуле {name} ошибки',
	'dashboard.issue.poolUnmounted': 'Пул {name} не смонтирован',
	'dashboard.issue.poolUnknown': 'Не удалось прочитать состояние пула {name}',
	'dashboard.issue.poolReadOnly': 'Пул {name} смонтирован только для чтения',
	'dashboard.issue.replaceSoon': 'Замените диск {device} в ближайшее время (серийный номер {serial})',
	'dashboard.issue.predictionsFailed': 'Не удалось проверить прогноз отказа дисков',
	'dashboard.issue.poolsFailed': 'Не удалось прочитать пулы',

	'dashboard.user.signedInAs': 'Вы вошли как {name}',
	'dashboard.user.managed': 'Хранилищем, общими папками, контейнерами и приложениями управляет администратор.',
	'dashboard.user.account':
		'Сменить пароль, настроить двухфакторную аутентификацию и просмотреть историю входов можно на странице',
	'dashboard.user.accountLink': 'Учётная запись',

	'dashboard.stats.cpu': 'ЦП',
	'dashboard.stats.cores': { one: '{n} ядро', few: '{n} ядра', many: '{n} ядер' },
	'dashboard.stats.memory': 'Память',
	'dashboard.stats.network': 'Сеть',
	'dashboard.stats.diskIo': 'Диски',
	'dashboard.stats.read': 'Чтение',
	'dashboard.stats.write': 'Запись',
	'dashboard.stats.received': 'Принято',
	'dashboard.stats.sent': 'Отправлено',
	'dashboard.stats.temperature': 'Температура',
	'dashboard.stats.none': 'Нет данных',

	'dashboard.manage': 'Управление',

	// Раскладка виджетов (ADR-0395).
	'dashboard.layout.customize': 'Настроить',
	'dashboard.layout.done': 'Готово',
	'dashboard.layout.hint':
		'Перетащите виджет за ручку или переместите его стрелками. Ненужные виджеты можно скрыть: они останутся здесь, и их можно будет снова показать.',
	'dashboard.layout.drag': 'Перетащить',
	'dashboard.layout.moveUp': 'Переместить «{name}» выше',
	'dashboard.layout.moveDown': 'Переместить «{name}» ниже',
	'dashboard.layout.hide': 'Скрыть «{name}»',
	'dashboard.layout.show': 'Показать «{name}»',
	'dashboard.layout.hiddenTitle': 'Скрытые виджеты',
	'dashboard.layout.noneHidden': 'Скрытых виджетов нет',
	'dashboard.layout.allHidden': 'Все виджеты скрыты',
	'dashboard.layout.allHiddenHint': 'Кнопка «Настроить» снова их покажет.',
	'dashboard.layout.reset': 'Вернуть макет по умолчанию',
	'dashboard.layout.moved': 'Виджет «{name}» теперь на месте {position} из {total}',
	'dashboard.layout.hidden': 'Виджет «{name}» скрыт',
	'dashboard.layout.shown': 'Виджет «{name}» показан',
	'dashboard.layout.wasReset': 'Панель возвращена к макету по умолчанию',
	'dashboard.layout.saveFailed': 'Не удалось сохранить макет панели: {error}',
	'dashboard.layout.loadFailed': 'Не удалось загрузить макет панели; показан макет по умолчанию',

	'dashboard.storage.title': 'Пулы хранения',
	'dashboard.storage.none': 'Пулов пока нет: создайте пул на странице «Хранилище».',
	'dashboard.storage.total': 'Общий объём',
	'dashboard.storage.usedOf': '{used} из {size}',
	'dashboard.storage.pools': { one: '{n} пул', few: '{n} пула', many: '{n} пулов' },
	'dashboard.storage.attention': {
		one: '{n} требует внимания',
		few: '{n} требуют внимания',
		many: '{n} требуют внимания'
	},
	'dashboard.storage.missing': {
		one: 'Не хватает {n} диска',
		few: 'Не хватает {n} дисков',
		many: 'Не хватает {n} дисков'
	},
	'dashboard.storage.errors': { one: '{n} ошибка дисков', few: '{n} ошибки дисков', many: '{n} ошибок дисков' },
	'dashboard.storage.forcedReadOnly': 'Только для чтения после ошибки',
	'dashboard.storage.readOnly': 'Только для чтения',
	'dashboard.health.healthy': 'Исправен',
	'dashboard.health.degraded': 'Деградирован',
	'dashboard.health.unmounted': 'Не смонтирован',
	'dashboard.health.error': 'Ошибка',

	'dashboard.containers.title': 'Контейнеры',
	'dashboard.containers.running': 'Работают',
	'dashboard.containers.stopped': 'Остановлены',
	'dashboard.containers.other': 'Другие',
	'dashboard.containers.runningCount': 'Работают: {n}',
	'dashboard.containers.stoppedCount': 'Остановлены: {n}',
	'dashboard.containers.none': 'Контейнеров пока нет',
	'dashboard.containers.open': 'Открыть',
	'dashboard.containers.shown': {
		one: 'Показано {shown} из {n} контейнера',
		few: 'Показано {shown} из {n} контейнеров',
		many: 'Показано {shown} из {n} контейнеров'
	},
	'dashboard.containers.state.running': 'Работает',
	'dashboard.containers.state.exited': 'Остановлен',
	'dashboard.containers.state.stopped': 'Остановлен',
	'dashboard.containers.state.paused': 'Приостановлен',
	'dashboard.containers.state.restarting': 'Перезапускается',
	'dashboard.containers.state.created': 'Создан',
	'dashboard.containers.state.dead': 'Неисправен',

	'dashboard.activity.title': 'Последние события',
	'dashboard.activity.viewAll': 'Показать все',
	'dashboard.activity.none': 'Нет недавних событий'
};

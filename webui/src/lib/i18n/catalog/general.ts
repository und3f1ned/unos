import type { Translation } from '../translate';

// System > General: src/routes/system/tabs/GeneralTab.svelte, and src/routes/settings/+page.svelte,
// which opens it.

export const en = {
	'general.redirecting': 'Redirecting to System settings...',
	'general.title': 'General Settings',

	'general.info.title': 'System Information',
	'general.info.hostname': 'Hostname:',
	'general.info.os': 'Operating System:',
	'general.info.kernel': 'Kernel:',
	'general.info.architecture': 'Architecture:',
	'general.info.uptime': 'Uptime:',
	'general.info.loadFailed': 'Failed to load system info',
	// The uptime, as long as it is.
	'general.uptime.days': '{days}d {hours}h {minutes}m',
	'general.uptime.hours': '{hours}h {minutes}m',
	'general.uptime.minutes': '{minutes}m',

	'general.version.title': 'UNOS Version',
	'general.version.label': 'Version:',
	// The badge of a version that is not a release (ADR-0234).
	'general.version.dev': 'dev',
	'general.version.check': 'Check for Updates',
	'general.version.unavailable': 'Version info unavailable',

	'general.user.title': 'Current User',
	'general.user.username': 'Username:',
	'general.user.role': 'Role:',
	'general.user.admin': 'Administrator',
	'general.user.user': 'User',
	'general.user.manage': 'Manage Users',
	'general.user.signedOut': 'Not logged in'
};

export const ru: Translation<typeof en> = {
	'general.redirecting': 'Переход к настройкам системы...',
	'general.title': 'Общие настройки',

	'general.info.title': 'Сведения о системе',
	'general.info.hostname': 'Имя хоста:',
	'general.info.os': 'Операционная система:',
	'general.info.kernel': 'Ядро:',
	'general.info.architecture': 'Архитектура:',
	'general.info.uptime': 'Время работы:',
	'general.info.loadFailed': 'Не удалось загрузить сведения о системе',
	'general.uptime.days': '{days} д {hours} ч {minutes} мин',
	'general.uptime.hours': '{hours} ч {minutes} мин',
	'general.uptime.minutes': '{minutes} мин',

	'general.version.title': 'Версия UNOS',
	'general.version.label': 'Версия:',
	'general.version.dev': 'разработка',
	'general.version.check': 'Проверить обновления',
	'general.version.unavailable': 'Сведения о версии недоступны',

	'general.user.title': 'Текущий пользователь',
	'general.user.username': 'Имя пользователя:',
	'general.user.role': 'Роль:',
	'general.user.admin': 'Администратор',
	'general.user.user': 'Пользователь',
	'general.user.manage': 'Управление пользователями',
	'general.user.signedOut': 'Вход не выполнен'
};

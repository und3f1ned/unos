import type { Translation } from '../translate';

// System > General: src/routes/system/+page.svelte.

export const en = {
	'general.title': 'General',

	'general.info.title': 'System information',

	// The name and time zone of the host (ADR-0331).
	'general.host.title': 'Name and time zone',
	'general.host.hostname': 'Hostname',
	'general.host.saveHostname': 'Save hostname',
	'general.host.hostnameHint': 'Latin letters, digits and hyphens, at most 63; not starting or ending with a hyphen.',
	'general.host.hostnameSaved': 'The hostname is now {name}.',
	'general.host.zone': 'Time zone',
	'general.host.zoneHint': 'Scheduled tasks run at the times of this zone.',
	'general.host.unknownZone': 'Unknown',
	'general.host.noZones': 'The host does not list its time zones.',
	'general.host.saveZone': 'Save time zone',
	'general.host.zoneSaved': 'The time zone is now {zone}. Scheduled tasks follow it.',
	'general.host.saveFailed': 'The change was not saved.',
	'general.host.unchanged': 'No changes to save',
	'general.host.loadFailed': 'The hostname and the time zone could not be read.',
	'general.info.os': 'Operating system',
	'general.info.kernel': 'Kernel',
	'general.info.architecture': 'Architecture',
	'general.info.uptime': 'Uptime',
	'general.info.loadFailed': 'The system information could not be read.'
};

export const ru: Translation<typeof en> = {
	'general.title': 'Общие',

	'general.info.title': 'Сведения о системе',

	'general.host.title': 'Имя и часовой пояс',
	'general.host.hostname': 'Имя хоста',
	'general.host.saveHostname': 'Сохранить имя хоста',
	'general.host.hostnameHint':
		'Латинские буквы, цифры и дефисы, не больше 63; дефис не может быть первым или последним.',
	'general.host.hostnameSaved': 'Имя хоста изменено на {name}.',
	'general.host.zone': 'Часовой пояс',
	'general.host.zoneHint': 'Задачи планировщика выполняются по времени этого пояса.',
	'general.host.unknownZone': 'Неизвестен',
	'general.host.noZones': 'Хост не сообщает список часовых поясов.',
	'general.host.saveZone': 'Сохранить часовой пояс',
	'general.host.zoneSaved': 'Часовой пояс изменён на {zone}. Задачи планировщика теперь выполняются по нему.',
	'general.host.saveFailed': 'Изменение не сохранено.',
	'general.host.unchanged': 'Нет изменений для сохранения',
	'general.host.loadFailed': 'Не удалось прочитать имя хоста и часовой пояс.',
	'general.info.os': 'Операционная система',
	'general.info.kernel': 'Ядро',
	'general.info.architecture': 'Архитектура',
	'general.info.uptime': 'Время работы',
	'general.info.loadFailed': 'Не удалось прочитать сведения о системе.'
};

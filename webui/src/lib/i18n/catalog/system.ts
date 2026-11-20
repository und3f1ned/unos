import type { Translation } from '../translate';

// The frame of the System page and its tabs: src/routes/system/+page.svelte. A regular user sees
// it as the Account page, with the Security tab alone.

export const en = {
	'system.heading.admin': 'System',
	'system.heading.user': 'Account',
	'system.group.settings': 'Settings',
	'system.group.monitoring': 'Monitoring',
	'system.group.maintenance': 'Maintenance',
	'system.tabs.general': 'General',
	'system.tabs.updates': 'Updates',
	'system.tabs.security': 'Security',
	'system.tabs.monitor': 'Resource Monitor',
	'system.tabs.logs': 'System Logs',
	'system.tabs.health': 'Disk Health',
	'system.tabs.backup': 'Backup & Restore',
	'system.tabs.scheduler': 'Scheduler',
	'system.tabs.notifications': 'Notifications',
	'system.tabs.network': 'Network',
	'system.tabs.power': 'Power Management'
};

export const ru: Translation<typeof en> = {
	'system.heading.admin': 'Система',
	'system.heading.user': 'Учётная запись',
	'system.group.settings': 'Настройки',
	'system.group.monitoring': 'Мониторинг',
	'system.group.maintenance': 'Обслуживание',
	'system.tabs.general': 'Общие',
	'system.tabs.updates': 'Обновления',
	'system.tabs.security': 'Безопасность',
	'system.tabs.monitor': 'Монитор ресурсов',
	'system.tabs.logs': 'Системные журналы',
	'system.tabs.health': 'Состояние дисков',
	'system.tabs.backup': 'Резервные копии',
	'system.tabs.scheduler': 'Планировщик',
	'system.tabs.notifications': 'Уведомления',
	'system.tabs.network': 'Сеть',
	'system.tabs.power': 'Питание'
};

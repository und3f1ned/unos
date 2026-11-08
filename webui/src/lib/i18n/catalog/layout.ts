import type { Translation } from '../translate';

// The frame of every signed-in page: src/routes/+layout.svelte. The page names of the
// navigation are the names the pages go by.

export const en = {
	'layout.nav.dashboard': 'Dashboard',
	'layout.nav.storage': 'Storage',
	'layout.nav.snapshots': 'Snapshots',
	'layout.nav.shares': 'Shares',
	'layout.nav.files': 'Files',
	'layout.nav.containers': 'Containers',
	'layout.nav.apps': 'App Store',
	'layout.nav.users': 'Users',
	'layout.nav.audit': 'Audit Log',
	'layout.nav.system': 'System',
	'layout.nav.account': 'Account',
	'layout.role.admin': 'Administrator',
	'layout.role.user': 'User',
	'layout.logout': 'Logout',
	'layout.accountError': 'Could not load your account',
	'layout.accountFailed': 'Failed to load your account'
};

export const ru: Translation<typeof en> = {
	'layout.nav.dashboard': 'Обзор',
	'layout.nav.storage': 'Хранилище',
	'layout.nav.snapshots': 'Снимки',
	'layout.nav.shares': 'Общие папки',
	'layout.nav.files': 'Файлы',
	'layout.nav.containers': 'Контейнеры',
	'layout.nav.apps': 'Каталог приложений',
	'layout.nav.users': 'Пользователи',
	'layout.nav.audit': 'Журнал аудита',
	'layout.nav.system': 'Система',
	'layout.nav.account': 'Учётная запись',
	'layout.role.admin': 'Администратор',
	'layout.role.user': 'Пользователь',
	'layout.logout': 'Выйти',
	'layout.accountError': 'Не удалось загрузить вашу учётную запись',
	'layout.accountFailed': 'Не удалось загрузить учётную запись'
};

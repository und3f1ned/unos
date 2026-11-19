import type { Translation } from '../translate';

// The command palette: src/lib/components/palette/CommandPalette.svelte (ADR-0393). Its pages take
// the names of the navigation and of System's tabs from the layout and system catalogs; the
// keywords are the other words a page or an action is known by, which the search also finds in
// both languages. An action that asks first ends with an ellipsis.

export const en = {
	'palette.title': 'Command palette',
	'palette.open': 'Open the command palette',
	'palette.field': 'Search pages and actions',
	'palette.placeholder': 'Type the name of a page or an action',
	'palette.list': 'Pages and actions',
	'palette.none': 'Nothing matches.',
	'palette.count': { one: '{n} choice', other: '{n} choices' },
	'palette.hint': '↑ and ↓ choose, Enter opens, Esc closes',
	'palette.kind.page': 'Page',
	'palette.kind.action': 'Action',

	'palette.action.addUser': 'Add a user…',
	'palette.action.reboot': 'Reboot the host…',
	'palette.action.shutdown': 'Shut down the host…',
	'palette.action.suspend': 'Suspend the host…',
	'palette.action.toRussian': 'Switch the language to Russian (Русский)',
	'palette.action.toEnglish': 'Switch the language to English',

	'palette.keywords.storage': 'pools disks volumes scrub balance',
	'palette.keywords.shares': 'SMB NFS exports',
	'palette.keywords.containers': 'Docker images',
	'palette.keywords.apps': 'applications install',
	'palette.keywords.users': 'accounts groups',
	'palette.keywords.general': 'host name time zone',
	'palette.keywords.updates': 'version upgrade',
	'palette.keywords.security': 'account password two-factor 2FA firewall certificates language',
	'palette.keywords.account': 'security password two-factor 2FA language',
	'palette.keywords.monitor': 'CPU memory processes',
	'palette.keywords.health': 'SMART disks',
	'palette.keywords.scheduler': 'tasks cron',
	'palette.keywords.notifications': 'e-mail Telegram alerts',
	'palette.keywords.network': 'interfaces IP addresses',
	'palette.keywords.remote': 'WireGuard VPN tunnel peers app names',
	'palette.keywords.power': 'reboot shutdown suspend Wake-on-LAN',
	'palette.keywords.addUser': 'new account',
	'palette.keywords.reboot': 'restart',
	'palette.keywords.shutdown': 'power off',
	'palette.keywords.suspend': 'sleep'
};

export const ru: Translation<typeof en> = {
	'palette.title': 'Палитра команд',
	'palette.open': 'Открыть палитру команд',
	'palette.field': 'Поиск страниц и действий',
	'palette.placeholder': 'Введите название страницы или действия',
	'palette.list': 'Страницы и действия',
	'palette.none': 'Ничего не найдено.',
	'palette.count': { one: '{n} вариант', few: '{n} варианта', many: '{n} вариантов' },
	'palette.hint': '↑ и ↓ выбирают, Enter открывает, Esc закрывает',
	'palette.kind.page': 'Страница',
	'palette.kind.action': 'Действие',

	'palette.action.addUser': 'Добавить пользователя…',
	'palette.action.reboot': 'Перезагрузить хост…',
	'palette.action.shutdown': 'Выключить хост…',
	'palette.action.suspend': 'Перевести хост в спящий режим…',
	'palette.action.toRussian': 'Переключить язык на русский',
	'palette.action.toEnglish': 'Переключить язык на английский (English)',

	'palette.keywords.storage': 'пулы диски тома проверка балансировка',
	'palette.keywords.shares': 'SMB NFS экспорт',
	'palette.keywords.containers': 'Docker образы',
	'palette.keywords.apps': 'приложения установка',
	'palette.keywords.users': 'учётные записи группы',
	'palette.keywords.general': 'имя хоста часовой пояс',
	'palette.keywords.updates': 'версия',
	'palette.keywords.security': 'учётная запись пароль двухфакторная 2FA брандмауэр сертификаты язык',
	'palette.keywords.account': 'безопасность пароль двухфакторная 2FA язык',
	'palette.keywords.monitor': 'процессор память процессы',
	'palette.keywords.health': 'SMART диски',
	'palette.keywords.scheduler': 'задачи cron',
	'palette.keywords.notifications': 'почта Telegram оповещения',
	'palette.keywords.network': 'интерфейсы IP адреса',
	'palette.keywords.remote': 'WireGuard VPN туннель узлы имена приложений',
	'palette.keywords.power': 'перезагрузка выключение спящий режим Wake-on-LAN',
	'palette.keywords.addUser': 'новая учётная запись',
	'palette.keywords.reboot': 'перезапуск',
	'palette.keywords.shutdown': 'отключение',
	'palette.keywords.suspend': 'сон'
};

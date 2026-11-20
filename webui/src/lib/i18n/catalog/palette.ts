import type { Translation } from '../translate';

// The command palette: src/lib/components/palette/CommandPalette.svelte (ADR-0393). Its pages take
// the names of the navigation and of System's tabs from the layout and system catalogs; the
// keywords are the other words a page or an action is known by, which the search also finds in
// both languages. An action that asks first ends with an ellipsis.

export const en = {
	'palette.title': 'Command palette',
	'palette.open': 'Open the command palette',
	'palette.trigger': 'Search',
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

	'palette.keywords.storage': 'pools disks volumes scrub balance',
	'palette.keywords.shares': 'SMB NFS exports',
	'palette.keywords.containers': 'Docker images',
	'palette.keywords.apps': 'applications install',
	'palette.keywords.users': 'accounts groups',
	'palette.keywords.general': 'general host name time zone',
	'palette.keywords.updates': 'version upgrade',
	'palette.keywords.security': 'firewall certificates HTTPS two-factor requirement',
	'palette.keywords.account': 'sign-in security password two-factor 2FA recovery codes security keys API tokens login',
	'palette.keywords.sharing': 'SMB network file access',
	'palette.keywords.appearance': 'theme dark light language English Russian Русский',
	'palette.keywords.monitor': 'CPU memory processes',
	'palette.keywords.health': 'SMART disks',
	'palette.keywords.scheduler': 'tasks cron',
	'palette.keywords.notifications': 'e-mail Telegram alerts',
	'palette.keywords.network': 'interfaces IP addresses routes DNS app names',
	'palette.keywords.remote': 'WireGuard VPN tunnel peers',
	'palette.keywords.power': 'reboot shutdown suspend Wake-on-LAN',
	'palette.keywords.addUser': 'new account',
	'palette.keywords.reboot': 'restart',
	'palette.keywords.shutdown': 'power off',
	'palette.keywords.suspend': 'sleep'
};

export const ru: Translation<typeof en> = {
	'palette.title': 'Палитра команд',
	'palette.open': 'Открыть палитру команд',
	'palette.trigger': 'Поиск',
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

	'palette.keywords.storage': 'пулы диски тома проверка балансировка',
	'palette.keywords.shares': 'SMB NFS экспорт',
	'palette.keywords.containers': 'Docker образы',
	'palette.keywords.apps': 'приложения установка',
	'palette.keywords.users': 'учётные записи группы',
	'palette.keywords.general': 'общие имя хоста часовой пояс',
	'palette.keywords.updates': 'версия',
	'palette.keywords.security': 'брандмауэр сертификаты HTTPS требование двухфакторной',
	'palette.keywords.account': 'вход безопасность пароль двухфакторная 2FA коды восстановления ключи безопасности API токены',
	'palette.keywords.sharing': 'SMB сетевой доступ к файлам',
	'palette.keywords.appearance': 'тема тёмная светлая язык английский русский English Русский',
	'palette.keywords.monitor': 'процессор память процессы',
	'palette.keywords.health': 'SMART диски',
	'palette.keywords.scheduler': 'задачи cron',
	'palette.keywords.notifications': 'почта Telegram оповещения',
	'palette.keywords.network': 'интерфейсы IP адреса маршруты DNS имена приложений',
	'palette.keywords.remote': 'WireGuard VPN туннель узлы',
	'palette.keywords.power': 'перезагрузка выключение спящий режим Wake-on-LAN',
	'palette.keywords.addUser': 'новая учётная запись',
	'palette.keywords.reboot': 'перезапуск',
	'palette.keywords.shutdown': 'отключение',
	'palette.keywords.suspend': 'сон'
};

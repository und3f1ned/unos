import type { Translation } from '../translate';

// System > Network: src/routes/system/tabs/NetworkTab.svelte (ADR-0244, ADR-0246). The values that
// ip and ss print in the tab's tables (link types, route and socket protocols, socket states) stay
// as they come and have no key here (ADR-0295).

export const en = {
	'network.title': 'Network',
	'network.live': 'Live',
	'network.loadError': 'Some network information could not be loaded',
	// The parts of the page: the views, and what could not be loaded.
	'network.interfaces': 'Interfaces',
	'network.dns': 'DNS',
	'network.gateway': 'Gateway',
	'network.routes': 'Routes',
	'network.connections': 'Connections',
	'network.more': '+{n} more',

	// Disables that come back on by themselves unless they are kept (ADR-0244).
	'network.pending.list': 'Disables waiting to be kept',
	'network.pending.title': '{name} is disabled',
	'network.pending.countdown': 'Unless you keep this change, {name} comes back on by itself in {seconds} s.',
	'network.pending.keep': 'Keep {name} disabled',
	'network.pending.expired': 'The time to keep this change has run out: {name} is coming back on.',
	'network.pending.kept': '{name} stays disabled.',
	'network.pending.late':
		'UNOS answered after the time to keep the change had run out: {name} comes back on by itself.',
	'network.pending.lost': {
		one: 'The connection to UNOS was lost. If it went through {name}, {name} comes back on by itself within {n} second: reload the page then.',
		other: 'The connection to UNOS was lost. If it went through {name}, {name} comes back on by itself within {n} seconds: reload the page then.'
	},
	'network.pending.keepLost':
		'The connection to UNOS was lost before it answered: unless the change was kept, {name} comes back on by itself.',
	// What the page finds when the time to keep a disable has run out (ADR-0246).
	'network.revert.unchecked':
		'Whether {name} came back on by itself could not be checked: refresh to check again.',
	'network.revert.gone': '{name} did not come back on by itself: the host no longer lists it.',
	'network.revert.back': '{name} came back on by itself.',
	'network.revert.notYet': '{name} did not come back on by itself yet. Enable it, or refresh to check again.',
	'network.revert.stillOff':
		'{name} is still disabled: either the change was kept, or {name} could not come back on by itself. Enable it if it should be on.',

	'network.summary.gateway': 'Default Gateway',
	'network.summary.notSet': 'Not set',
	'network.summary.via': 'via {name}',
	'network.summary.dns': 'DNS Servers',
	'network.summary.notConfigured': 'Not configured',
	'network.summary.connections': 'Active Connections',
	'network.summary.sockets': 'TCP/UDP sockets',

	'network.interface.label': 'Interface {name}',
	'network.interface.disabled': 'Disabled',
	// The operational states that the kernel reports (operstate).
	'network.state.up': 'UP',
	'network.state.down': 'DOWN',
	'network.state.lowerLayerDown': 'LOWERLAYERDOWN',
	'network.state.dormant': 'DORMANT',
	'network.state.testing': 'TESTING',
	'network.state.notPresent': 'NOTPRESENT',
	'network.state.unknown': 'UNKNOWN',
	'network.interface.noIp': 'No IP',
	'network.interface.mac': 'MAC Address',
	'network.interface.noMac': 'N/A',
	'network.interface.mtu': 'MTU',
	'network.interface.type': 'Type',
	'network.interface.speed': 'Speed',
	'network.interface.addresses': 'IP Addresses',
	'network.interface.noAddresses': 'None',
	'network.interface.traffic': 'Traffic Statistics (Live)',
	'network.interface.rxBytes': 'RX Bytes',
	'network.interface.txBytes': 'TX Bytes',
	'network.interface.rxRate': 'RX Rate',
	'network.interface.txRate': 'TX Rate',
	'network.interface.disable': 'Disable',
	'network.interface.enable': 'Enable',
	'network.interface.none': 'No network interfaces found',
	'network.speed.gbps': '{speed} Gb/s',
	'network.speed.mbps': '{speed} Mb/s',
	// The live traffic: a count of bytes in a unit of powers of 1024, and the same per second.
	'network.bytes.b': 'B',
	'network.bytes.kb': 'KB',
	'network.bytes.mb': 'MB',
	'network.bytes.gb': 'GB',
	'network.bytes.tb': 'TB',
	'network.bytes.size': '{value} {unit}',
	'network.bytes.perSecond': '{size}/s',

	'network.routes.destination': 'Destination',
	'network.routes.interface': 'Interface',
	'network.protocol': 'Protocol',
	// The destination of a default route, which ip names "default".
	'network.routes.default': 'default',
	'network.routes.none': 'No routes found',
	'network.connections.count': { one: '{n} active connection', other: '{n} active connections' },
	'network.connections.refresh': 'Refresh connections',
	'network.connections.state': 'State',
	'network.connections.local': 'Local',
	'network.connections.remote': 'Remote',
	'network.connections.none': 'No active connections',

	'network.dnsConfig.title': 'DNS Configuration',
	'network.dnsConfig.servers': 'Name Servers',
	'network.dnsConfig.domains': 'Search Domains',

	// Disable asks first: the connection to UNOS may go through the interface (ADR-0244).
	'network.disable.title': 'Disable {name}',
	'network.disable.confirm': 'Disable {name}',
	// Around the interface's name, in bold.
	'network.disable.dropBefore': 'Disabling',
	'network.disable.dropAfter': 'may drop your connection to UNOS, if you reach UNOS through it.',
	'network.disable.revert': {
		one: 'Unless you keep the change within {n} second, {name} comes back on by itself, with its addresses and routes.',
		other: 'Unless you keep the change within {n} seconds, {name} comes back on by itself, with its addresses and routes.'
	}
};

export const ru: Translation<typeof en> = {
	'network.title': 'Сеть',
	'network.live': 'В реальном времени',
	'network.loadError': 'Не удалось загрузить часть сведений о сети',
	'network.interfaces': 'Интерфейсы',
	'network.dns': 'DNS',
	'network.gateway': 'Шлюз',
	'network.routes': 'Маршруты',
	'network.connections': 'Подключения',
	'network.more': 'ещё {n}',

	'network.pending.list': 'Выключения, ожидающие подтверждения',
	'network.pending.title': '{name} выключен',
	'network.pending.countdown': 'Если вы не сохраните это изменение, {name} сам включится снова через {seconds} с.',
	'network.pending.keep': 'Оставить {name} выключенным',
	'network.pending.expired': 'Время на сохранение изменения истекло: {name} включается снова.',
	'network.pending.kept': '{name} остаётся выключенным.',
	'network.pending.late':
		'UNOS ответил, когда время на сохранение изменения уже истекло: {name} сам включится снова.',
	'network.pending.lost': {
		one: 'Связь с UNOS потеряна. Если она шла через {name}, {name} сам включится снова в течение {n} секунды: тогда перезагрузите страницу.',
		few: 'Связь с UNOS потеряна. Если она шла через {name}, {name} сам включится снова в течение {n} секунд: тогда перезагрузите страницу.',
		many: 'Связь с UNOS потеряна. Если она шла через {name}, {name} сам включится снова в течение {n} секунд: тогда перезагрузите страницу.'
	},
	'network.pending.keepLost':
		'Связь с UNOS потеряна до ответа: если изменение не сохранилось, {name} сам включится снова.',
	'network.revert.unchecked':
		'Не удалось проверить, включился ли {name} снова сам: обновите данные, чтобы проверить ещё раз.',
	'network.revert.gone': '{name} не включился снова сам: хост больше не показывает этот интерфейс.',
	'network.revert.back': '{name} снова включился сам.',
	'network.revert.notYet':
		'{name} пока не включился снова сам. Включите его или обновите данные, чтобы проверить ещё раз.',
	'network.revert.stillOff':
		'{name} всё ещё выключен: либо изменение сохранили, либо {name} не смог включиться снова сам. Включите его, если он должен работать.',

	'network.summary.gateway': 'Шлюз по умолчанию',
	'network.summary.notSet': 'Не задан',
	'network.summary.via': 'через {name}',
	'network.summary.dns': 'DNS-серверы',
	'network.summary.notConfigured': 'Не настроены',
	'network.summary.connections': 'Активные подключения',
	'network.summary.sockets': 'Сокеты TCP/UDP',

	'network.interface.label': 'Интерфейс {name}',
	'network.interface.disabled': 'Выключен',
	'network.state.up': 'Работает',
	'network.state.down': 'Нет связи',
	'network.state.lowerLayerDown': 'Нет связи',
	'network.state.dormant': 'Ожидание',
	'network.state.testing': 'Проверка',
	'network.state.notPresent': 'Отсутствует',
	'network.state.unknown': 'Неизвестно',
	'network.interface.noIp': 'Нет IP-адреса',
	'network.interface.mac': 'MAC-адрес',
	'network.interface.noMac': 'Нет',
	'network.interface.mtu': 'MTU',
	'network.interface.type': 'Тип',
	'network.interface.speed': 'Скорость',
	'network.interface.addresses': 'IP-адреса',
	'network.interface.noAddresses': 'Нет',
	'network.interface.traffic': 'Статистика трафика (в реальном времени)',
	'network.interface.rxBytes': 'Принято',
	'network.interface.txBytes': 'Передано',
	'network.interface.rxRate': 'Скорость приёма',
	'network.interface.txRate': 'Скорость передачи',
	'network.interface.disable': 'Выключить',
	'network.interface.enable': 'Включить',
	'network.interface.none': 'Сетевые интерфейсы не найдены',
	'network.speed.gbps': '{speed} Гбит/с',
	'network.speed.mbps': '{speed} Мбит/с',
	'network.bytes.b': 'Б',
	'network.bytes.kb': 'КБ',
	'network.bytes.mb': 'МБ',
	'network.bytes.gb': 'ГБ',
	'network.bytes.tb': 'ТБ',
	'network.bytes.size': '{value} {unit}',
	'network.bytes.perSecond': '{size}/с',

	'network.routes.destination': 'Назначение',
	'network.routes.interface': 'Интерфейс',
	'network.protocol': 'Протокол',
	'network.routes.default': 'по умолчанию',
	'network.routes.none': 'Маршруты не найдены',
	'network.connections.count': {
		one: '{n} активное подключение',
		few: '{n} активных подключения',
		many: '{n} активных подключений'
	},
	'network.connections.refresh': 'Обновить подключения',
	'network.connections.state': 'Состояние',
	'network.connections.local': 'Локальный адрес',
	'network.connections.remote': 'Удалённый адрес',
	'network.connections.none': 'Активных подключений нет',

	'network.dnsConfig.title': 'Настройки DNS',
	'network.dnsConfig.servers': 'Серверы имён',
	'network.dnsConfig.domains': 'Домены поиска',

	'network.disable.title': 'Выключение {name}',
	'network.disable.confirm': 'Выключить {name}',
	'network.disable.dropBefore': 'Выключение',
	'network.disable.dropAfter': 'может прервать ваше подключение к UNOS, если вы подключаетесь к UNOS через него.',
	'network.disable.revert': {
		one: 'Если вы не сохраните изменение в течение {n} секунды, {name} сам включится снова, со своими адресами и маршрутами.',
		few: 'Если вы не сохраните изменение в течение {n} секунд, {name} сам включится снова, со своими адресами и маршрутами.',
		many: 'Если вы не сохраните изменение в течение {n} секунд, {name} сам включится снова, со своими адресами и маршрутами.'
	}
};

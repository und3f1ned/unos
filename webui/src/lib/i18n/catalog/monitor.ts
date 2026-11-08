import type { Translation } from '../translate';

// System > Resource Monitor: src/routes/system/tabs/MonitorTab.svelte. The rates and sizes come
// from formatBytes and formatBytesPerSecond of $lib/stores/stats.

export const en = {
	'monitor.title': 'Resource Monitor',
	'monitor.live': 'Live',
	'monitor.disconnected': 'Disconnected',
	'monitor.waiting': 'Waiting for WebSocket data...',
	'monitor.cpu': 'CPU',
	'monitor.memory': 'Memory',
	'monitor.network': 'Network',
	'monitor.diskIo': 'Disk I/O',
	'monitor.read': 'R:',
	'monitor.write': 'W:',
	'monitor.cores': 'CPU Cores',
	'monitor.processes': 'Top Processes',
	'monitor.process.name': 'Name',
	'monitor.process.cpu': 'CPU',
	'monitor.process.memory': 'Memory',
	'monitor.process.user': 'User',
	'monitor.megabytes': '{value} MB'
};

export const ru: Translation<typeof en> = {
	'monitor.title': 'Монитор ресурсов',
	'monitor.live': 'Онлайн',
	'monitor.disconnected': 'Нет связи',
	'monitor.waiting': 'Ожидание данных WebSocket...',
	'monitor.cpu': 'Процессор',
	'monitor.memory': 'Память',
	'monitor.network': 'Сеть',
	'monitor.diskIo': 'Дисковый ввод-вывод',
	'monitor.read': 'Чтение:',
	'monitor.write': 'Запись:',
	'monitor.cores': 'Ядра процессора',
	'monitor.processes': 'Самые активные процессы',
	'monitor.process.name': 'Имя',
	'monitor.process.cpu': 'ЦП',
	'monitor.process.memory': 'Память',
	'monitor.process.user': 'Пользователь',
	'monitor.megabytes': '{value} МБ'
};

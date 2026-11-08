import type { Translation } from '../translate';

// System > Resource Monitor: src/routes/system/tabs/MonitorTab.svelte. It writes its sizes, rates
// and percentages itself, the numbers in the format of the page: in English as formatBytes and
// formatBytesPerSecond of $lib/stores/stats write them.

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
	'monitor.percent': '{value}%',
	// A size and a rate per second, one key for each power of 1024.
	'monitor.size.b': '{value} B',
	'monitor.size.kb': '{value} KB',
	'monitor.size.mb': '{value} MB',
	'monitor.size.gb': '{value} GB',
	'monitor.size.tb': '{value} TB',
	'monitor.rate.b': '{value} B/s',
	'monitor.rate.kb': '{value} KB/s',
	'monitor.rate.mb': '{value} MB/s',
	'monitor.rate.gb': '{value} GB/s'
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
	// A no-break space between the number and the sign, as Intl.NumberFormat writes a Russian percentage.
	'monitor.percent': '{value}\u00a0%',
	'monitor.size.b': '{value} Б',
	'monitor.size.kb': '{value} КБ',
	'monitor.size.mb': '{value} МБ',
	'monitor.size.gb': '{value} ГБ',
	'monitor.size.tb': '{value} ТБ',
	'monitor.rate.b': '{value} Б/с',
	'monitor.rate.kb': '{value} КБ/с',
	'monitor.rate.mb': '{value} МБ/с',
	'monitor.rate.gb': '{value} ГБ/с'
};

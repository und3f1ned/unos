import type { Translation } from '../translate';

// System > Resource Monitor: src/routes/system/tabs/MonitorTab.svelte. It writes its sizes, rates
// and percentages itself, the numbers in the format of the page and the units of this catalog.

export const en = {
	'monitor.title': 'Resource Monitor',
	'monitor.waiting': 'Waiting for WebSocket data...',
	'monitor.cpu': 'CPU',
	'monitor.memory': 'Memory',
	'monitor.network': 'Network',
	'monitor.diskIo': 'Disk I/O',
	'monitor.read': 'R:',
	'monitor.write': 'W:',
	// The time range buttons and the span of each, which names the charts: "CPU over the last hour".
	'monitor.range': 'Time range',
	'monitor.range.5m': '5 min',
	'monitor.range.15m': '15 min',
	'monitor.range.1h': '1 hour',
	'monitor.range.5m.span': 'the last 5 minutes',
	'monitor.range.15m.span': 'the last 15 minutes',
	'monitor.range.1h.span': 'the last hour',
	'monitor.chart': '{metric} over {span}',
	// The highest value of the range shown.
	'monitor.peak': 'Peak {value}',
	'monitor.peak.network': 'Peak ↓ {recv} ↑ {sent}',
	'monitor.peak.disk': 'Peak R: {read} W: {write}',
	'monitor.historyError': 'The history could not be loaded',
	'monitor.cores': 'CPU Cores',
	'monitor.processes': 'Top Processes',
	'monitor.processes.hint':
		'The ten processes of the host that use the most CPU, those of the apps included, read every 10 seconds. CPU is the share of one core.',
	'monitor.process.name': 'Name',
	'monitor.process.pid': 'PID',
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
	'monitor.waiting': 'Ожидание данных WebSocket...',
	'monitor.cpu': 'Процессор',
	'monitor.memory': 'Память',
	'monitor.network': 'Сеть',
	'monitor.diskIo': 'Дисковый ввод-вывод',
	'monitor.read': 'Чтение:',
	'monitor.write': 'Запись:',
	'monitor.range': 'Период',
	'monitor.range.5m': '5 мин',
	'monitor.range.15m': '15 мин',
	'monitor.range.1h': '1 час',
	'monitor.range.5m.span': 'последние 5 минут',
	'monitor.range.15m.span': 'последние 15 минут',
	'monitor.range.1h.span': 'последний час',
	'monitor.chart': '{metric} за {span}',
	'monitor.peak': 'Пик {value}',
	'monitor.peak.network': 'Пик ↓ {recv} ↑ {sent}',
	'monitor.peak.disk': 'Пик: чтение {read}, запись {write}',
	'monitor.historyError': 'Не удалось загрузить историю',
	'monitor.cores': 'Ядра процессора',
	'monitor.processes': 'Самые активные процессы',
	'monitor.processes.hint':
		'Десять процессов хоста, которые больше всего нагружают процессор, включая процессы приложений; список обновляется каждые 10 секунд. ЦП — доля одного ядра.',
	'monitor.process.name': 'Имя',
	'monitor.process.pid': 'PID',
	'monitor.process.cpu': 'ЦП',
	'monitor.process.memory': 'Память',
	'monitor.process.user': 'Пользователь',
	// A no-break space between the number and the sign, as Intl.NumberFormat writes a Russian percentage.
	'monitor.percent': '{value} %',
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

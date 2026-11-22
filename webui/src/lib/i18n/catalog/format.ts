import type { Translation } from '../translate';

// The words of the numbers that src/lib/format.ts writes, for every page (ADR-0459): the units of a
// size and a rate, of a duration, and an age. The number in each is already in the page's format.

export const en = {
	'format.size.b': '{size} B',
	'format.size.kb': '{size} KB',
	'format.size.mb': '{size} MB',
	'format.size.gb': '{size} GB',
	'format.size.tb': '{size} TB',
	'format.rate': '{size}/s',
	'format.duration.d': '{value}d',
	'format.duration.h': '{value}h',
	'format.duration.min': '{value}m',
	'format.duration.s': '{value}s',
	'format.duration.ms': '{value}ms',
	'format.ago.now': 'Just now',
	'format.ago.minutes': '{value}m ago',
	'format.ago.hours': '{value}h ago'
};

export const ru: Translation<typeof en> = {
	'format.size.b': '{size} Б',
	'format.size.kb': '{size} КБ',
	'format.size.mb': '{size} МБ',
	'format.size.gb': '{size} ГБ',
	'format.size.tb': '{size} ТБ',
	'format.rate': '{size}/с',
	'format.duration.d': '{value}\u00a0дн.',
	'format.duration.h': '{value}\u00a0ч',
	'format.duration.min': '{value}\u00a0мин',
	'format.duration.s': '{value}\u00a0с',
	'format.duration.ms': '{value}\u00a0мс',
	'format.ago.now': 'Только что',
	'format.ago.minutes': '{value}\u00a0мин назад',
	'format.ago.hours': '{value}\u00a0ч назад'
};

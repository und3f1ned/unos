// The one home of the numbers that the web UI writes (webui.locale-formats): a number, a size, a rate, a
// percentage, a temperature, a duration and an age, in the format of the page's language
// (`$intlLocale` of $lib/i18n: "1.5 GB" and "90.3%" in English, a decimal comma, a no-break space
// before % and the Russian units in Russian), with the words of the catalog `format`
// (src/lib/i18n/catalog/format.ts). Dates and times are src/lib/time.ts's. scripts/design-check.mjs
// fails for the usual tools of a number or a date written by hand elsewhere in src/ (toFixed,
// toLocaleString, Intl's number and date formats, a date's getters, a percentage or a unit put
// together).
//
// A count, a port, an id or a year is no quantity: it is written as it is, the same in both
// languages.

import type { Translate } from '$lib/i18n';
import { apiTime, formatDate } from '$lib/time';

// One formatter per language and options: a list of thousands of rows asks for the same few.
const numberFormats = new Map<string, Intl.NumberFormat>();
function numberFormat(locale: string, options: Intl.NumberFormatOptions): Intl.NumberFormat {
	const key = `${locale} ${JSON.stringify(options)}`;
	let format = numberFormats.get(key);
	if (!format) {
		format = new Intl.NumberFormat(locale, options);
		numberFormats.set(key, format);
	}
	return format;
}

/**
 * How a number is written: at most `digits` decimals, all of them when `fixed` (1.0 GB), and with the
 * language's grouping of thousands when `grouping` (8,192 MB in English), which a size or a
 * percentage, short anyway, does without.
 */
export type Precision = { digits?: number; fixed?: boolean; grouping?: boolean };

/** One decimal, always written: the Storage page's sizes and percentages, the dashboard's percentages. */
export const ONE_DECIMAL: Precision = { digits: 1, fixed: true };

/** The options of Intl for `precision`; a value that rounds to zero is written without a minus. */
function decimals({ digits = 0, fixed = false, grouping = false }: Precision): Intl.NumberFormatOptions {
	return {
		maximumFractionDigits: digits,
		minimumFractionDigits: fixed ? digits : 0,
		useGrouping: grouping,
		signDisplay: 'negative'
	};
}

/** `value` in the digits of `locale`: 1.5 in English, 1,5 in Russian. */
export function formatNumber(value: number, locale: string, precision: Precision = {}): string {
	return numberFormat(locale, decimals(precision)).format(value);
}

/** `percent` (0 to 100) as a percentage of `locale`: 90.3% in English, 90,3 % in Russian. */
export function formatPercent(percent: number, locale: string, precision: Precision = {}): string {
	return numberFormat(locale, { ...decimals(precision), style: 'percent' }).format(percent / 100);
}

/** A temperature in degrees Celsius as `locale` writes it: 45°C in English, 45 °C in Russian. */
export function formatTemperature(celsius: number, locale: string): string {
	return numberFormat(locale, { style: 'unit', unit: 'celsius', maximumFractionDigits: 0 }).format(celsius);
}

/**
 * `bytes` scaled to the largest power of 1024 that keeps it at 1 or more (power 0 below 1 byte),
 * at most `units - 1`: 1536 with 5 units is { value: 1.5, power: 1 }.
 */
function scaleBytes(bytes: number, units: number): { value: number; power: number } {
	const power = bytes >= 1 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units - 1) : 0;
	return { value: bytes / 1024 ** power, power };
}

// The units of a size, by powers of 1024, by the names a text of the server gives them ("GB" of
// "4.0 GB", _format_capacity of supervisor/src/health/router.py): B to TB in English, their Russian
// ones in Russian.
const SIZE_UNITS: Record<string, string> = {
	B: 'format.size.b',
	KB: 'format.size.kb',
	MB: 'format.size.mb',
	GB: 'format.size.gb',
	TB: 'format.size.tb'
};
const SIZE_KEYS = Object.values(SIZE_UNITS);

/** A unit of a size that a text of the server names ("GB" of "4.0 GB"): its key; null for another. */
export function sizeUnitKey(unit: string): string | null {
	return Object.prototype.hasOwnProperty.call(SIZE_UNITS, unit) ? SIZE_UNITS[unit] : null;
}

/** `bytes` in the largest unit that keeps it at 1 or more, at most TB, with one decimal at most by default: 1.5 GB. */
export function formatSize(bytes: number, locale: string, t: Translate, precision: Precision = { digits: 1 }): string {
	const { value, power } = scaleBytes(bytes, SIZE_KEYS.length);
	return t(SIZE_KEYS[power], { size: formatNumber(value, locale, precision) });
}

/** Bytes per second as formatSize writes the size, per second: 1.5 MB/s. */
export function formatRate(bytesPerSecond: number, locale: string, t: Translate, precision?: Precision): string {
	return t('format.rate', { size: formatSize(bytesPerSecond, locale, t, precision) });
}

// The units of a duration, from the largest: days, hours, minutes and seconds.
const DURATION_UNITS: readonly [string, number][] = [
	['format.duration.d', 86400],
	['format.duration.h', 3600],
	['format.duration.min', 60],
	['format.duration.s', 1]
];

/**
 * The options of formatDuration: how many units it writes from the largest it needs (`parts`), its
 * smallest unit (`smallest`), and the decimals of a duration written in that unit alone (`digits`:
 * 1.5s).
 */
export type DurationOptions = { parts?: number; smallest?: 'min' | 's'; digits?: number };

/**
 * `seconds` in days, hours, minutes and seconds, the largest unit it reaches first: 3d 4h, 1m 5s,
 * with the Russian abbreviations in Russian. A duration shorter than the unit after the smallest is
 * written in the smallest alone, 0 of it or with `digits` its decimals; with seconds as the
 * smallest, one shorter than a second in milliseconds (11ms).
 */
export function formatDuration(seconds: number, locale: string, t: Translate, options: DurationOptions = {}): string {
	const { parts = 2, smallest = 'min', digits = 0 } = options;
	const ms = Math.max(0, Math.round(seconds * 1000));
	if (smallest === 's' && ms < 1000) return t('format.duration.ms', { value: formatNumber(ms, locale) });
	// Rounded first to what is written, so that 59.96 s with one decimal is 1m 0s, not 60s.
	const shown = smallest === 's' && digits ? Math.round(Math.max(0, seconds) * 10 ** digits) / 10 ** digits : seconds;
	const units = DURATION_UNITS.slice(0, smallest === 's' ? 4 : 3);
	const start = units.findIndex(([, size]) => shown >= size);
	if (start < 0 || start === units.length - 1) {
		const [key, size] = units[units.length - 1];
		const value = Math.max(0, shown / size);
		return t(key, { value: formatNumber(digits ? value : Math.floor(value), locale, { digits }) });
	}
	let rest = Math.floor(shown);
	return units
		.slice(start, start + parts)
		.map(([key, size]) => {
			const value = Math.floor(rest / size);
			rest -= value * size;
			return t(key, { value });
		})
		.join(' ');
}

/** The seconds since a time of the API until now; null for a text that is not a time with its offset. */
export function secondsSince(value: string | null | undefined): number | null {
	const moment = apiTime(value);
	return moment ? Math.max(0, (Date.now() - moment.getTime()) / 1000) : null;
}

/**
 * How long ago a time of the API was, within a day: Just now, 5m ago, 3h ago; an older one as its
 * date (formatDate of $lib/time), another value as it comes.
 */
export function formatAgo(value: string, locale: string, t: Translate): string {
	const seconds = secondsSince(value);
	if (seconds === null) return value;
	if (seconds < 60) return t('format.ago.now');
	if (seconds < 3600) return t('format.ago.minutes', { value: Math.floor(seconds / 60) });
	if (seconds < 86400) return t('format.ago.hours', { value: Math.floor(seconds / 3600) });
	return formatDate(value, locale);
}

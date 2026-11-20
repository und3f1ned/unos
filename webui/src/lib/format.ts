// Numbers as the pages write them. The units' words come from each area's catalog (ADR-0287); these
// functions only scale and write the number, so that every page writes it the same way.

/**
 * `bytes` scaled to the largest power of 1024 that keeps it at 1 or more (power 0 below 1 byte),
 * at most `units - 1`: 1536 with 5 units is { value: 1.5, power: 1 }.
 */
export function scaleBytes(bytes: number, units: number): { value: number; power: number } {
	const power = bytes >= 1 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units - 1) : 0;
	return { value: bytes / 1024 ** power, power };
}

/** `value` in the digits of `locale`, without grouping, with at most `digits` decimals: 1,5 in Russian. */
export function formatNumber(value: number, locale: string, digits: number): string {
	return new Intl.NumberFormat(locale, { maximumFractionDigits: digits, useGrouping: false }).format(value);
}

/** `value` rounded to `digits` decimals and written as JavaScript writes numbers, whatever the language: 1.5. */
export function roundedNumber(value: number, digits: number): string {
	return String(parseFloat(value.toFixed(digits)));
}

/**
 * A date and time to the minute, the year in full: what the data pages show of a time of the API
 * ("11/7/2026, 3:00 AM", "07.11.2026, 03:00"); seconds only in logs (ADR-0432).
 */
export const DATE_TIME_MINUTES: Intl.DateTimeFormatOptions = {
	year: 'numeric',
	month: 'numeric',
	day: 'numeric',
	hour: 'numeric',
	minute: '2-digit'
};

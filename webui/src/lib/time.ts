/**
 * The times of the API (ADR-0401): every one is ISO 8601 with its offset, in UTC ("...Z" or
 * "...+00:00"; a scheduled task's next run in the host's zone). The pages show them in the
 * browser's time zone, in the date format of the page's language (`$intlLocale`).
 *
 * A text without an offset is not read as a time: a browser would take it for its own local time.
 * The functions show such a value as it comes.
 */

// An ISO 8601 date and time with its offset: Z, +HH:MM or +HHMM.
const WITH_OFFSET = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/i;

/** The instant of a time of the API; null for a text that is not a time with its offset. */
export function apiTime(value: string | null | undefined): Date | null {
	if (!value || !WITH_OFFSET.test(value)) return null;
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? null : date;
}

/** A time of the API as a date and time in the browser's time zone; another value as it comes. */
export function formatDateTime(
	value: string | null | undefined,
	locale: string,
	options?: Intl.DateTimeFormatOptions
): string {
	const date = apiTime(value);
	return date ? date.toLocaleString(locale, options) : (value ?? '');
}

/** A time of the API as a date in the browser's time zone; another value as it comes. */
export function formatDate(value: string | null | undefined, locale: string): string {
	const date = apiTime(value);
	return date ? date.toLocaleDateString(locale) : (value ?? '');
}

/** A time of the API as a clock time in the browser's time zone; another value as it comes. */
export function formatTimeOfDay(
	value: string | null | undefined,
	locale: string,
	options?: Intl.DateTimeFormatOptions
): string {
	const date = apiTime(value);
	return date ? date.toLocaleTimeString(locale, options) : (value ?? '');
}

/** The day of a time of the API in the browser's time zone, as YYYY-MM-DD; null for another value. */
export function localDay(value: string | null | undefined): string | null {
	const date = apiTime(value);
	if (!date) return null;
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

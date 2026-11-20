/**
 * The times of the API (ADR-0401): every one is ISO 8601 with its offset, in UTC ("...Z" or
 * "...+00:00"; a scheduled task's next run in the host's zone). The pages show them in the
 * browser's time zone, in the date format of the page's language (`$intlLocale`); a time of what
 * the NAS runs by its own clock (a schedule, its runs) in the NAS's time zone, which the store
 * `nasTimeZone` of $lib/stores/nasTime knows: `formatDateTime(value, $intlLocale, { zone: $nasTimeZone })`
 * (ADR-0432).
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

/** Whether `zone` is a time zone that this browser knows (an IANA name such as Europe/Berlin). */
export function knownZone(zone: string | null | undefined): zone is string {
	if (!zone) return false;
	try {
		new Intl.DateTimeFormat('en', { timeZone: zone });
		return true;
	} catch {
		return false;
	}
}

/** The options of a date with `zone` as its time zone: the browser's when the zone is not known. */
function inZone(options: DateOptions | undefined): Intl.DateTimeFormatOptions | undefined {
	if (!options) return undefined;
	const { zone, ...rest } = options;
	return knownZone(zone) ? { ...rest, timeZone: zone } : rest;
}

/**
 * The options of formatDateTime and formatTimeOfDay: those of Intl, and `zone`, the time zone to
 * show the time in (the NAS's, `$nasTimeZone`); without one, or with one this browser does not
 * know ("Unknown"), the browser's.
 */
export type DateOptions = Intl.DateTimeFormatOptions & { zone?: string | null };

/** A time of the API as a date and time in the browser's time zone, or `zone`; another value as it comes. */
export function formatDateTime(value: string | null | undefined, locale: string, options?: DateOptions): string {
	const date = apiTime(value);
	return date ? date.toLocaleString(locale, inZone(options)) : (value ?? '');
}

/** A time of the API as a date in the browser's time zone; another value as it comes. */
export function formatDate(value: string | null | undefined, locale: string): string {
	const date = apiTime(value);
	return date ? date.toLocaleDateString(locale) : (value ?? '');
}

/** A time of the API as a clock time in the browser's time zone, or `zone`; another value as it comes. */
export function formatTimeOfDay(value: string | null | undefined, locale: string, options?: DateOptions): string {
	const date = apiTime(value);
	return date ? date.toLocaleTimeString(locale, inZone(options)) : (value ?? '');
}

/** The day of a time of the API in the browser's time zone, as YYYY-MM-DD; null for another value. */
export function localDay(value: string | null | undefined): string | null {
	const date = apiTime(value);
	if (!date) return null;
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

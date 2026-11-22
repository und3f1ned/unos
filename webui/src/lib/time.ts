/**
 * The dates and times that the web UI writes (ADR-0401, webui.locale-formats), beside the numbers of
 * src/lib/format.ts. Every time of the API is ISO 8601 with its offset, in UTC ("...Z" or
 * "...+00:00"; a scheduled task's next run in the host's zone). The pages show them in the
 * browser's time zone in one format of the page's language (`$intlLocale`): a date and time to the
 * minute, "11/7/2026, 5:03 AM" in an en-US browser, "07/11/2026, 05:03" in en-GB, "07.11.2026, 05:03"
 * in Russian, with the seconds only where they tell entries apart (the lines of a log, a trash
 * checkbox's label); a date alone and a time of day alone are the parts of it. A time of what the
 * NAS runs by its own clock (a schedule, its runs) is shown in the NAS's time zone, which the store
 * `nasTimeZone` of $lib/stores/nasTime knows:
 * `formatDateTime(value, $intlLocale, { zone: $nasTimeZone })` (ADR-0432).
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

// One formatter per language and options, and one answer per zone: a list of thousands of rows asks
// for the same few.
const dateFormats = new Map<string, Intl.DateTimeFormat>();
const zones = new Map<string, boolean>();

function dateFormat(locale: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
	const key = `${locale} ${JSON.stringify(options)}`;
	let format = dateFormats.get(key);
	if (!format) {
		format = new Intl.DateTimeFormat(locale, options);
		dateFormats.set(key, format);
	}
	return format;
}

/** Whether `zone` is a time zone that this browser knows (an IANA name such as Europe/Berlin). */
export function knownZone(zone: string | null | undefined): zone is string {
	if (!zone) return false;
	let known = zones.get(zone);
	if (known === undefined) {
		try {
			new Intl.DateTimeFormat('en', { timeZone: zone });
			known = true;
		} catch {
			known = false;
		}
		zones.set(zone, known);
	}
	return known;
}

/**
 * The options of a date or time: `zone`, the time zone to show it in (the NAS's, `$nasTimeZone`;
 * without one, or with one this browser does not know, "Unknown", the browser's), and `seconds`,
 * which only the lines of a log show (the Audit Log, System > Logs, the sign-in history) and a trash
 * checkbox's label, which tells two items of one name apart (ADR-0434).
 */
export type DateOptions = { zone?: string | null; seconds?: boolean };

const DATE: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'numeric', day: 'numeric' };
// The hour and the minute as `numeric`: each language's own clock (5:03 AM, 05:03); a `2-digit`
// hour would write 05:03 AM in English, and a `2-digit` minute alone drops the hour's zero in Russian.
const TIME: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: 'numeric' };

/** The options of Intl for `parts` with the zone and the seconds of `options`. */
function intlOptions(parts: Intl.DateTimeFormatOptions, options: DateOptions = {}): Intl.DateTimeFormatOptions {
	return {
		...parts,
		...(options.seconds && parts.hour ? { second: 'numeric' } : {}),
		...(knownZone(options.zone) ? { timeZone: options.zone } : {})
	};
}

/** A time of the API as a date and time in the browser's time zone, or `zone`; another value as it comes. */
export function formatDateTime(value: string | null | undefined, locale: string, options?: DateOptions): string {
	const date = apiTime(value);
	return date ? dateFormat(locale, intlOptions({ ...DATE, ...TIME }, options)).format(date) : (value ?? '');
}

/** A time of the API as a date in the browser's time zone, or `zone`; another value as it comes. */
export function formatDate(value: string | null | undefined, locale: string, options?: DateOptions): string {
	const date = apiTime(value);
	return date ? dateFormat(locale, intlOptions(DATE, options)).format(date) : (value ?? '');
}

/** A time of the API as a clock time in the browser's time zone, or `zone`; another value as it comes. */
export function formatTimeOfDay(value: string | null | undefined, locale: string, options?: DateOptions): string {
	const date = apiTime(value);
	return date ? dateFormat(locale, intlOptions(TIME, options)).format(date) : (value ?? '');
}

/**
 * A time of day without a date, such as a schedule's 02:00, as the clock of `locale` writes it:
 * 2:00 AM in an en-US browser, 02:00 in Russian.
 */
export function formatClock(hour: number, minute: number, locale: string): string {
	return dateFormat(locale, { ...TIME, timeZone: 'UTC' }).format(new Date(Date.UTC(2000, 0, 1, hour, minute)));
}

/** A clock time as an `<input type="time">` holds it, HH:MM, whatever the language. */
export function timeFieldValue(hour: number, minute: number): string {
	return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

/** The day of a time of the API, or of an instant, in the browser's time zone, as YYYY-MM-DD; null for another value. */
export function localDay(value: string | Date | null | undefined): string | null {
	const date = value instanceof Date ? value : apiTime(value);
	if (!date || Number.isNaN(date.getTime())) return null;
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** The day `day` (YYYY-MM-DD) as a heading of `locale` in full: Saturday, November 7, 2026; else `day`. */
export function formatDayHeading(day: string, locale: string): string {
	const [year, month, date] = day.split('-').map(Number);
	const moment = new Date(year, month - 1, date);
	if (Number.isNaN(moment.getTime())) return day;
	return dateFormat(locale, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }).format(moment);
}

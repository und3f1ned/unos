// Sizes as the widgets of the dashboard write them (ADR-0297): the number of src/lib/format.ts and
// the unit of the dashboard's catalog.
import { roundedNumber, scaleBytes } from '$lib/format';
import type { Params } from '$lib/i18n';

/** The page's $t: a key's text in the language of the page. */
type Translate = (key: string, params?: Params) => string;

// The units of a size, from bytes up: keys of the catalog.
const UNITS = ['dashboard.unit.b', 'dashboard.unit.kb', 'dashboard.unit.mb', 'dashboard.unit.gb', 'dashboard.unit.tb'];

/** `bytes` with one decimal at most and its unit: 1.5 GB; at most in TB. */
export function formatBytes(bytes: number, t: Translate): string {
	const { value, power } = scaleBytes(bytes, UNITS.length);
	return `${roundedNumber(value, 1)} ${t(UNITS[power])}`;
}

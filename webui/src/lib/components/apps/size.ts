import { formatNumber, scaleBytes } from '$lib/format';
import type { Translate } from '$lib/i18n';

/** The page's $t: a key's text in the language of the page. */
export type { Translate } from '$lib/i18n';

// The units of a size, by powers of 1024, as formatBytes of $lib/stores/stats writes them.
const SIZE_UNITS = ['apps.size.bytes', 'apps.size.kb', 'apps.size.mb', 'apps.size.gb', 'apps.size.tb'];

/**
 * A size in the language of the page (`locale`, `t`): the number in its format with at most
 * `digits` decimals, and the unit of the catalog. The details of an app write two decimals, the
 * job cards, the resources and the update dialog one.
 */
export function formatSize(bytes: number, locale: string, t: Translate, digits: number): string {
	const { value, power } = scaleBytes(bytes, SIZE_UNITS.length);
	return t(SIZE_UNITS[power], { size: formatNumber(value, locale, digits) });
}

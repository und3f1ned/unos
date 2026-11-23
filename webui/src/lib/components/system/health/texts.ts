import type { DiskPrediction, SmartInfo } from '$lib/api/health';
import { formatNumber, sizeUnitKey } from '$lib/format';
import type { Translate } from '$lib/i18n';

// What System > Health shows of a disk's health, in the language of the page (ADR-0287). The
// functions that give texts take the page's $t (and $intlLocale), so that the markup that calls
// them follows a change of the language.

/** $t of $lib/i18n. */
export type { Translate } from '$lib/i18n';

export const HEALTH_STATUS: Record<string, string> = {
	PASSED: 'health.status.passed',
	FAILED: 'health.status.failed',
	UNKNOWN: 'health.status.unknown',
	// The disk sleeps: smartctl left it alone, so no SMART data was read (ADR-0378).
	STANDBY: 'health.status.standby',
	// An SD card or eMMC, which has no SMART (storage.free-mmc-as-data-disk).
	NO_SMART: 'health.status.noSmart'
};

// The recommendation of a failure prediction as the server writes it, one sentence per status
// (_predict_failure of supervisor/src/health/router.py): keys of the catalog.
export const RECOMMENDATION: Record<string, string> = {
	'Disk showing signs of failure. Replace within 30 days.': 'health.recommendation.replaceSoon',
	'Disk health degrading. Monitor daily and prepare replacement.': 'health.recommendation.monitorClosely',
	'Minor issues detected. Continue monitoring.': 'health.recommendation.watch',
	'No concerning trends detected.': 'health.recommendation.healthy'
};

// By smartctl's serial, which the SMART monitor records too: a disk's path
// can change (ADR-0235).
export function predictionOf(disk: SmartInfo, predictions: DiskPrediction[]): DiskPrediction | undefined {
	return predictions.find((prediction) => prediction.serial === disk.serial);
}

export function healthClass(status: string): string {
	if (status === 'PASSED') return 'text-success';
	if (status === 'FAILED') return 'text-error';
	if (status === 'STANDBY' || status === 'NO_SMART') return 'text-content-secondary';
	return 'text-warning';
}

/**
 * The one badge of a disk in the list: a prediction to replace it soon comes first, then
 * smartctl's overall health in plain words. Keys of the catalog and the badge's look.
 */
export function diskState(
	disk: SmartInfo,
	prediction: DiskPrediction | undefined
): { key: string; variant: 'success' | 'warning' | 'error' | 'neutral' } {
	if (prediction) return { key: 'health.replaceSoon', variant: 'error' };
	if (disk.health_status === 'FAILED') return { key: 'health.state.failing', variant: 'error' };
	if (disk.health_status === 'PASSED') return { key: 'health.state.healthy', variant: 'success' };
	if (disk.health_status === 'STANDBY') return { key: 'health.state.asleep', variant: 'neutral' };
	if (disk.health_status === 'NO_SMART') return { key: 'health.state.noSmart', variant: 'neutral' };
	return { key: 'health.state.unknown', variant: 'warning' };
}

/** The text of `value` by its key in `keys`, in the language of the page; else `value`. */
export function textOf(t: Translate, keys: Record<string, string>, value: string): string {
	return Object.prototype.hasOwnProperty.call(keys, value) ? t(keys[value]) : value;
}

// The server's word for what neither smartctl nor lsblk reported (_disk_health of
// supervisor/src/health/router.py).
export function orUnknown(t: Translate, value: string): string {
	return value === 'Unknown' ? t('common.unknown') : value;
}

/**
 * A disk's capacity as the server writes it, "3.64 TB" or "931.51 GB" (_format_capacity of
 * supervisor/src/health/router.py), with its number in the format of the page, its decimals kept,
 * and the unit of the catalog `format`; another text as it comes.
 */
export function capacityText(t: Translate, locale: string, capacity: string): string {
	const [number, unit, ...rest] = capacity.split(' ');
	const key = sizeUnitKey(unit);
	if (rest.length > 0 || !key || !/^\d+(\.\d+)?$/.test(number)) return orUnknown(t, capacity);
	const digits = number.split('.')[1]?.length ?? 0;
	return t(key, { size: formatNumber(Number(number), locale, { digits, fixed: true }) });
}

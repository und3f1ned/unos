import type { DiskPrediction, SmartInfo, SystemDiskHealth } from '$lib/api/health';
import { formatNumber, formatPercent, sizeUnitKey } from '$lib/format';
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
	STANDBY: 'health.status.standby'
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
	if (status === 'STANDBY') return 'text-content-secondary';
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

// The values of an eMMC's life-time estimates (JEDEC eMMC 5.0): 1 to 10 each a tenth of its life used,
// 11 past its rated life; 0 is not defined and higher values are reserved (ADR-0471).
const LIFE_EXCEEDED = 11;

/** A life-time estimate as the share of the life used: "0% to 10% used", "Past its rated life". */
export function lifeTimeText(t: Translate, locale: string, value: number | null): string {
	if (value === null || value < 1 || value > LIFE_EXCEEDED) return t('health.systemDisk.notReported');
	if (value === LIFE_EXCEEDED) return t('health.systemDisk.lifeExceeded');
	return t('health.systemDisk.lifeUsed', {
		from: formatPercent((value - 1) * 10, locale),
		to: formatPercent(value * 10, locale)
	});
}

/** The pre-EOL state (1 normal, 2 warning, 3 urgent) as a key of the catalog. */
export function preEolKey(value: number | null): string {
	if (value === 1) return 'health.systemDisk.preEol.normal';
	if (value === 2) return 'health.systemDisk.preEol.warning';
	if (value === 3) return 'health.systemDisk.preEol.urgent';
	return 'health.systemDisk.notReported';
}

/**
 * The badge of the system disk: its worst problem (a failure, then nearly worn out, then errors, then
 * wearing), else healthy. Keys of the catalog and the badge's look.
 */
export function systemDiskState(health: SystemDiskHealth): { key: string; variant: 'success' | 'warning' | 'error' } {
	const has = (kind: string, severity?: string) =>
		health.problems.some((problem) => problem.kind === kind && (!severity || problem.severity === severity));
	if (has('system_disk_failed')) return { key: 'health.systemDisk.state.failing', variant: 'error' };
	if (has('emmc_worn', 'error')) return { key: 'health.systemDisk.state.nearlyWornOut', variant: 'error' };
	if (has('system_disk_errors')) return { key: 'health.systemDisk.state.errors', variant: 'warning' };
	if (has('emmc_worn')) return { key: 'health.systemDisk.state.wearing', variant: 'warning' };
	return { key: 'health.state.healthy', variant: 'success' };
}

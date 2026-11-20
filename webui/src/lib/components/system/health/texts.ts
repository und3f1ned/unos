import type { DiskPrediction, SmartInfo } from "$lib/api/health";
import type { Translate } from "$lib/i18n";

// What System > Health shows of a disk's health, in the language of the page (ADR-0287). The
// functions that give texts take the page's $t (and $intlLocale), so that the markup that calls
// them follows a change of the language.

/** $t of $lib/i18n. */
export type { Translate } from '$lib/i18n';

export const HEALTH_STATUS: Record<string, string> = {
  PASSED: "health.status.passed",
  FAILED: "health.status.failed",
  UNKNOWN: "health.status.unknown",
  // The disk sleeps: smartctl left it alone, so no SMART data was read (ADR-0378).
  STANDBY: "health.status.standby"
};

// A disk's capacity as the server writes it, "3.64 TB" or "931.51 GB" (_format_capacity of
// supervisor/src/health/router.py), by the unit: keys of the catalog.
const CAPACITY_UNITS: Record<string, string> = {
  GB: "health.size.gb",
  TB: "health.size.tb"
};

// By smartctl's serial, which the SMART monitor records too: a disk's path
// can change (ADR-0235).
export function predictionOf(disk: SmartInfo, predictions: DiskPrediction[]): DiskPrediction | undefined {
  return predictions.find((prediction) => prediction.serial === disk.serial);
}

export function healthClass(status: string): string {
  if (status === "PASSED") return "text-success";
  if (status === "FAILED") return "text-error";
  if (status === "STANDBY") return "text-content-secondary";
  return "text-warning";
}

/**
 * The one badge of a disk in the list: a prediction to replace it soon comes first, then
 * smartctl's overall health in plain words. Keys of the catalog and the badge's look.
 */
export function diskState(
  disk: SmartInfo,
  prediction: DiskPrediction | undefined
): { key: string; variant: "success" | "warning" | "error" | "neutral" } {
  if (prediction) return { key: "health.replaceSoon", variant: "error" };
  if (disk.health_status === "FAILED") return { key: "health.state.failing", variant: "error" };
  if (disk.health_status === "PASSED") return { key: "health.state.healthy", variant: "success" };
  if (disk.health_status === "STANDBY") return { key: "health.state.asleep", variant: "neutral" };
  return { key: "health.state.unknown", variant: "warning" };
}

/** The text of `value` by its key in `keys`, in the language of the page; else `value`. */
export function textOf(t: Translate, keys: Record<string, string>, value: string): string {
  return Object.prototype.hasOwnProperty.call(keys, value) ? t(keys[value]) : value;
}

// The server's word for what neither smartctl nor lsblk reported (_disk_health of
// supervisor/src/health/router.py).
export function orUnknown(t: Translate, value: string): string {
  return value === "Unknown" ? t("common.unknown") : value;
}

/** A capacity with its number in the format of the page and the unit of the catalog; another
 *  text as it comes. */
export function capacityText(t: Translate, locale: string, capacity: string): string {
  const [number, unit, ...rest] = capacity.split(" ");
  const known = Object.prototype.hasOwnProperty.call(CAPACITY_UNITS, unit);
  if (rest.length > 0 || !known || !/^\d+(\.\d+)?$/.test(number)) return orUnknown(t, capacity);
  const decimals = number.split(".")[1]?.length ?? 0;
  const size = Number(number).toLocaleString(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    useGrouping: false
  });
  return t(CAPACITY_UNITS[unit], { size });
}

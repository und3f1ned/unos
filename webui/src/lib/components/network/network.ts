// Helpers of System > Network: its texts come from src/lib/i18n/catalog/network.ts and virtual.ts.
import type { Route } from "$lib/api/network";
import type { Params } from "$lib/i18n";

/** $t of $lib/i18n: the text of a key in the language of the page. */
export type Translate = (key: string, params?: Params) => string;

/** The message of an error that an API call threw. */
export function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The text of `value`'s key in `keys`, in the language of the page; a value without one as it comes. */
export function labelOf(t: Translate, keys: Record<string, string>, value: string | null): string {
  return value !== null && Object.prototype.hasOwnProperty.call(keys, value) ? t(keys[value]) : (value ?? "");
}

/** `record` without `key`, as a new object: the page assigns it, Svelte sees the change. */
export function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const copy = { ...record };
  delete copy[key];
  return copy;
}

/** A default route: ip names its destination "default". */
export function isDefault(route: Route): boolean {
  return !route.dst || route.dst === "default";
}

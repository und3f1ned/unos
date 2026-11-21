// The rules of the off-device copy's form that the server also applies (ADR-0337, ADR-0422).
import type { OffsiteSettingsAnswer } from '$lib/api/offsite';

// At least as long as the server asks (MIN_PASSWORD_LENGTH of supervisor/src/offsite/service.py).
export const MIN_PASSWORD_LENGTH = 12;

/** A folder's path as the server stores it: one leading slash, no empty or "." parts. */
export function normalized(value: string): string {
	return (
		'/' +
		value
			.split('/')
			.filter((part) => part !== '' && part !== '.')
			.join('/')
	);
}

/** The pool of a folder below the pools' base, or null. */
export function poolOf(base: string | undefined, source: string): string | null {
	if (!base || !source.startsWith(base + '/')) return null;
	return source.slice(base.length + 1).split('/')[0] || null;
}

/** The pools a restore of files may go into: the managed ones and those of the folders copied. */
export function restorePoolsOf(answer: OffsiteSettingsAnswer | null, sources: string[]): string[] {
	return [
		...new Set([
			...(answer?.pools ?? []).map((pool) => pool.name),
			...sources.map((source) => poolOf(answer?.pools_base, source)).filter((name): name is string => !!name)
		])
	].sort();
}

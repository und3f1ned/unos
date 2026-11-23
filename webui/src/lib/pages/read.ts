// The parts of a page reader's snapshot ($lib/pages/load): a read's value or why it failed, and a
// slow part that the page shows without waiting for.
import { messageOf } from '$lib/errors';

/** A read's value, or the message of its failure (the server's, ADR-0189). */
export type Settled<T> = { ok: true; value: T } | { ok: false; error: string };

/** The answer of `request`, or why it failed: one read's failure leaves the others. */
export async function settle<T>(request: Promise<T>): Promise<Settled<T>> {
	try {
		return { ok: true, value: await request };
	} catch (e) {
		return { ok: false, error: messageOf(e) };
	}
}

/** The value of a read, or `fallback` when it failed. */
export function valueOr<T>(read: Settled<T>, fallback: T): T {
	return read.ok ? read.value : fallback;
}

/** The message of a failed read; '' when it succeeded. */
export function errorOf(read: Settled<unknown>): string {
	return read.ok ? '' : read.error;
}

/**
 * A part that is not the page's point and takes longer than the rest (the dashboard's containers):
 * the load does not wait for it, and the page shows its place waiting until it comes. `value` once
 * it has, so that a cached snapshot shows it at once.
 */
export type Later<T> = { readonly promise: Promise<T>; value?: T };

/** `request` as a Later part; it never fails: `fallback` takes the place of a failure. */
export function later<T>(request: Promise<T>, fallback: T): Later<T> {
	const promise = request.catch(() => fallback);
	const part: { promise: Promise<T>; value?: T } = { promise };
	void promise.then((value) => (part.value = value));
	return part;
}

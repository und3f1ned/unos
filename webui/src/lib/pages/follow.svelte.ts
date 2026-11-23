// A page component's side of its load ($lib/pages/load): it shows the snapshot its load gave, at
// once, before it is first drawn, and the fresh one when that answers; a load that runs again (a
// Refresh, another account) gives it a new one.
//
// - A snapshot that a change of its areas made old since its read started is not shown: a fresh
//   one is the page's own business then (the page reads again after its own change), and without
//   one the load runs again.
// - A snapshot read longer ago than PRELOAD_MAX_AGE_MS without a fresh one (a preload that the
//   click did not follow at once) is shown, and the load runs again for a fresh one.
// - A page that has shown nothing when its fresh read failed or came for nobody runs its load again,
//   so that no page stays on its skeleton.
import { untrack } from 'svelte';
import { invalidate } from '$app/navigation';
import { upToDate } from './cache';
import { PRELOAD_MAX_AGE_MS, isOld, pageDependency, type PageRead } from './load';

/**
 * Call during the component's initialisation, after the state that `apply` fills: `apply` takes
 * each snapshot the page is to show. `read` returns the page's data (`() => data`).
 */
export function followPage<T>(read: () => PageRead<T>, apply: (snapshot: T) => void): void {
	let followed: PageRead<T> | null = null;
	function take(data: PageRead<T>) {
		if (data === followed) return;
		followed = data;
		const again = () => void invalidate(pageDependency(data.key));
		let shown = false;
		if (data.shown !== null) {
			if (!isOld(data)) {
				apply(data.shown);
				shown = true;
				if (!data.fresh && Date.now() - data.readAt > PRELOAD_MAX_AGE_MS) again();
			} else if (!data.fresh) {
				again();
			}
		}
		void data.fresh?.then((next) => {
			if (followed !== data) return;
			if (next && upToDate(data.areas, next.mark)) apply(next.value);
			else if (!shown) again();
		});
	}
	take(untrack(read));
	$effect.pre(() => {
		const data = read();
		untrack(() => take(data));
	});
}

import { writable } from 'svelte/store';
import { getScheduleTimezone } from '$lib/api/scheduler';

// The NAS's time zone (ADR-0432), for the times of what the NAS runs by its own clock: a schedule,
// a task's runs. A page shows them on the NAS's clock and names the zone once (formatDateTime of
// $lib/time with `zone: $nasTimeZone`). Read from GET /scheduler/timezone, the zone whose clock
// the schedules follow (the host's, else UTC; ADR-0266), each time a page starts to show it (the
// first subscriber after none), so that a zone changed elsewhere (another browser, the host's
// shell) shows on the next visit; the zone known before stays meanwhile. System > General tells it
// of a change it saved (setNasTimeZone). Null until it is known: the times are the browser's then.

const zone = writable<string | null>(null);
let watchers = 0;

function ask() {
	getScheduleTimezone()
		.then(({ timezone }) => zone.set(timezone || null))
		.catch(() => {
			// Not known: the zone known before, or the browser's clock.
		});
}

export const nasTimeZone = {
	subscribe(run: (value: string | null) => void) {
		if (watchers++ === 0) ask();
		const unsubscribe = zone.subscribe(run);
		return () => {
			watchers -= 1;
			unsubscribe();
		};
	}
};

/** The NAS's time zone changed (System > General saved a new one). */
export function setNasTimeZone(value: string): void {
	zone.set(value);
}

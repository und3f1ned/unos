import { messageOf } from '$lib/errors';
// An action on several containers at once (ADR-0389): the endpoint of each container, one after
// another, and each one's result.
import { ApiError } from '$lib/api/client';
import { restartContainer, startContainer, stopContainer, type Container } from '$lib/api/containers';

export type BulkAction = 'start' | 'stop' | 'restart';
// A container gets a state (done), a reason (failed) or `skipped` (not running, so not
// restarted: as on its own row, Restart is for a running container).
export type BulkItem = { id: string; name: string; state?: string; reason?: string; skipped?: boolean };
export type BulkProgress = { action: BulkAction; name: string; i: number; n: number };
export type BulkResult = { action: BulkAction; items: BulkItem[] };
/** A container to act on, in the order of the list. */
export type BulkTarget = { id: string; name: string; running: boolean };

const BULK_CALLS: Record<BulkAction, (id: string) => Promise<Container>> = {
	start: startContainer,
	stop: stopContainer,
	restart: restartContainer
};
export const BULK_PROGRESS: Record<BulkAction, string> = {
	start: 'containers.bulk.progress.start',
	stop: 'containers.bulk.progress.stop',
	restart: 'containers.bulk.progress.restart'
};
export const BULK_DONE: Record<BulkAction, string> = {
	start: 'containers.bulk.done.start',
	stop: 'containers.bulk.done.stop',
	restart: 'containers.bulk.done.restart'
};

/**
 * The ids of the containers selected for an action on several. Only what the list shows can be
 * selected: the page keeps those it shows, after a search, a filter or a load. A Set is not made
 * reactive by $state: every change assigns a new one.
 */
export class Selection {
	ids = $state<ReadonlySet<string>>(new Set());

	get size(): number {
		return this.ids.size;
	}

	has(id: string): boolean {
		return this.ids.has(id);
	}

	toggle(id: string, on: boolean) {
		const next = new Set(this.ids);
		if (on) next.add(id);
		else next.delete(id);
		this.ids = next;
	}

	/** Select exactly `ids`. */
	set(ids: Iterable<string>) {
		this.ids = new Set(ids);
	}

	/** Keep only the selected ids among `shown`. */
	keep(shown: ReadonlySet<string>) {
		const kept = [...this.ids].filter((id) => shown.has(id));
		if (kept.length !== this.ids.size) this.ids = new Set(kept);
	}
}

/** The run of an action on several containers, the container it is at, and the last result. */
export class BulkRun {
	running = $state(false);
	progress = $state<BulkProgress | null>(null);
	result = $state<BulkResult | null>(null);

	/**
	 * Start, stop or restart `targets` through each one's own endpoint, one after another: the
	 * server's checks and audit entries apply to each, and a failure does not stop the others.
	 * Restart, as on a container's row, is for the running ones: the others are listed as not
	 * restarted. A 401, which ends the session, and `left()` (the page was left) end the run after
	 * the container it is at: no result then (null).
	 */
	async run(action: BulkAction, targets: BulkTarget[], left: () => boolean): Promise<BulkItem[] | null> {
		this.running = true;
		this.result = null;
		const items: BulkItem[] = [];
		try {
			for (const [index, { running, ...target }] of targets.entries()) {
				if (left()) return null;
				if (action === 'restart' && !running) {
					items.push({ ...target, skipped: true });
					continue;
				}
				this.progress = { action, name: target.name, i: index + 1, n: targets.length };
				try {
					const info = await BULK_CALLS[action](target.id);
					items.push({ ...target, state: info.state });
				} catch (e) {
					// The session ended: the sign-in page is on its way.
					if (e instanceof ApiError && e.status === 401) return null;
					items.push({ ...target, reason: messageOf(e) });
				}
			}
		} finally {
			this.progress = null;
			this.running = false;
		}
		this.result = { action, items };
		return items;
	}
}

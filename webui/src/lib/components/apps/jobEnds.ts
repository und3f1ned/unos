import type { AppInstallJob } from '$lib/api/apps';

/**
 * The installs that ended, as the events socket said them (ADR-0384): when another one ends, the
 * installed apps are read again. Those already in the store when the page is made count as seen,
 * since the page's first load reads the apps they made; after a reload the store is empty, and
 * the socket's first message reads them once more. The installs seen running: one that the store
 * no longer has (a socket that signed in again to a supervisor that restarted, and forgot it) may
 * have left containers, so the installed apps are read again too.
 */
export class JobEnds {
	#ended: Set<string>;
	#running: Set<string>;

	constructor(jobs: AppInstallJob[]) {
		this.#ended = new Set(jobs.filter((job) => job.state !== 'running').map((job) => job.id));
		this.#running = new Set(jobs.filter((job) => job.state === 'running').map((job) => job.id));
	}

	/** Note the store's jobs: true when one ended or was forgotten since the last ones. */
	note(jobs: AppInstallJob[]): boolean {
		let changed = false;
		for (const job of jobs) {
			if (job.state === 'running') {
				this.#running.add(job.id);
			} else if (!this.#ended.has(job.id)) {
				this.#ended.add(job.id);
				this.#running.delete(job.id);
				changed = true;
			}
		}
		for (const id of [...this.#running]) {
			if (!jobs.some((job) => job.id === id)) {
				this.#running.delete(id);
				changed = true;
			}
		}
		return changed;
	}
}

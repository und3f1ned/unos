// The jobs on the pools of the Storage page: each mounted pool's scrub (ADR-0313), balance
// (ADR-0379) and disk removal (ADR-0411), as the page last read them or /ws/v1/events sent them,
// and the scrub and balance actions of the pools' cards.
import * as storage from '$lib/api/storage';
import type { BalanceStatus, DiskRemovalStatus, Pool, ScrubStatus } from '$lib/api/storage';
import type { BalanceUpdate } from '$lib/stores/balances';
import type { DiskRemovalUpdate } from '$lib/stores/diskRemovals';
import type { ScrubUpdate } from '$lib/stores/scrubs';
import { balanceRuns, messageOf, removalRuns, type BalanceAction } from './pools';

/** The last status of a kind of job on each pool, and why it could not be read. */
export class JobStatuses<Status> {
	statuses: Record<string, Status> = $state({});
	// Shown instead of the last status (ADR-0313).
	errors: Record<string, string> = $state({});

	/** Show a pool's job as btrfs reports it. */
	show(pool: string, status: Status) {
		delete this.errors[pool];
		this.statuses[pool] = status;
	}

	/**
	 * The status could not be read (e.g. btrfs did not answer): say why instead of the last one.
	 * The last one still decides the buttons, so that a job that was running can be cancelled.
	 */
	fail(pool: string, e: unknown) {
		this.errors[pool] = messageOf(e);
	}
}

const BALANCE_ACTIONS: Record<BalanceAction, (pool: string) => Promise<BalanceStatus>> = {
	pause: storage.pauseBalance,
	resume: storage.resumeBalance,
	cancel: storage.cancelBalance
};

export class PoolJobs {
	readonly scrub = new JobStatuses<ScrubStatus>();
	readonly balance = new JobStatuses<BalanceStatus>();
	readonly removal = new JobStatuses<DiskRemovalStatus>();
	// The pools whose scrub is starting, and those whose balance's pause, resume or cancel is on
	// its way.
	startingScrub: Record<string, boolean> = $state({});
	balanceBusy: Record<string, boolean> = $state({});
	// The page's error: a scrub or a balance that did not start or stop.
	error = $state('');

	readonly #pools: () => Pool[];
	readonly #onRemovalEnded: () => void;

	/**
	 * `pools`: the pools the page shows. `onRemovalEnded`: a disk removal that ran has ended, and
	 * the pool's disks have changed.
	 */
	constructor(pools: () => Pool[], onRemovalEnded: () => void) {
		this.#pools = pools;
		this.#onRemovalEnded = onRemovalEnded;
	}

	/**
	 * The scrubs, the balances and the disk removals of the mounted pools, one after the other.
	 * Scrubbing needs a mounted filesystem. An answer is shown only while `latest` holds: a newer
	 * load of the lists shows its own.
	 */
	async load(latest: () => boolean) {
		await this.#loadEach(
			latest,
			storage.getScrubStatus,
			(pool, status) => this.scrub.show(pool, status),
			(pool, e) => this.scrub.fail(pool, e)
		);
		await this.#loadEach(
			latest,
			storage.getBalanceStatus,
			(pool, status) => this.balance.show(pool, status),
			(pool, e) => this.balance.fail(pool, e)
		);
		await this.#loadEach(
			latest,
			storage.getDiskRemoval,
			(pool, status) => this.showRemoval(pool, status),
			(pool, e) => this.removal.fail(pool, e)
		);
	}

	async #loadEach<Status>(
		latest: () => boolean,
		read: (pool: string) => Promise<Status>,
		show: (pool: string, status: Status) => void,
		fail: (pool: string, e: unknown) => void
	) {
		for (const pool of this.#pools().filter((p) => p.mounted)) {
			if (!latest()) return;
			try {
				const status = await read(pool.name);
				if (latest()) show(pool.name, status);
			} catch (e) {
				if (latest()) fail(pool.name, e);
			}
		}
	}

	/** Show a pool's disk removal. Once one that ran has ended, the pool's disks have changed. */
	showRemoval(pool: string, status: DiskRemovalStatus) {
		const ended = removalRuns(this.removal.statuses[pool]) && !removalRuns(status);
		this.removal.show(pool, status);
		if (ended) this.#onRemovalEnded();
	}

	// A scrub that runs is followed over /ws/v1/events, which sends the status of each one whenever
	// it changed, until the scrub ended or its status could not be read (ADR-0367): the page asks
	// for the status only when it loads its lists and when the socket connected again (below).
	followScrubs(updates: ScrubUpdate[]) {
		for (const update of updates) {
			if ('error' in update) this.scrub.fail(update.pool, update.error);
			else this.scrub.show(update.pool, update);
		}
	}

	// A balance that runs is followed over /ws/v1/events as a scrub is (ADR-0379).
	followBalances(updates: BalanceUpdate[]) {
		for (const update of updates) {
			// A status has an error field of its own (a failed balance's).
			if ('status' in update) this.balance.show(update.pool, update);
			else this.balance.fail(update.pool, update.error);
		}
	}

	// A removal that runs is followed over /ws/v1/events as a balance is (ADR-0411).
	followRemovals(updates: DiskRemovalUpdate[]) {
		for (const update of updates) {
			if ('status' in update) this.showRemoval(update.pool, update);
			else this.removal.fail(update.pool, update.error);
		}
	}

	// The events socket dropped and connected again, maybe to a supervisor that restarted and
	// follows none of the jobs it followed (ADR-0367): the page asks once for each job it shows
	// running or could not read, and that read makes the server follow it again.
	#socketWasUp = false;
	#socketDropped = false;

	followSocket(connected: boolean) {
		if (!connected) {
			this.#socketDropped = this.#socketWasUp;
			return;
		}
		this.#socketWasUp = true;
		if (!this.#socketDropped) return;
		this.#socketDropped = false;
		this.#readAgain(
			(pool) => this.scrub.statuses[pool.name]?.status === 'running' || pool.name in this.scrub.errors,
			storage.getScrubStatus,
			(pool, status) => this.scrub.show(pool, status),
			(pool, e) => this.scrub.fail(pool, e)
		);
		this.#readAgain(
			(pool) => balanceRuns(this.balance.statuses[pool.name]) || pool.name in this.balance.errors,
			storage.getBalanceStatus,
			(pool, status) => this.balance.show(pool, status),
			(pool, e) => this.balance.fail(pool, e)
		);
		this.#readAgain(
			(pool) => removalRuns(this.removal.statuses[pool.name]) || pool.name in this.removal.errors,
			storage.getDiskRemoval,
			(pool, status) => this.showRemoval(pool, status),
			(pool, e) => this.removal.fail(pool, e)
		);
	}

	/** Read again the job of each mounted pool that `shown` names, chosen before the first answer. */
	async #readAgain<Status>(
		shown: (pool: Pool) => boolean,
		read: (pool: string) => Promise<Status>,
		show: (pool: string, status: Status) => void,
		fail: (pool: string, e: unknown) => void
	) {
		for (const pool of this.#pools().filter((pool) => pool.mounted && shown(pool))) {
			try {
				show(pool.name, await read(pool.name));
			} catch (e) {
				fail(pool.name, e);
			}
		}
	}

	async startScrub(pool: string) {
		this.startingScrub[pool] = true;
		this.error = '';
		try {
			this.scrub.show(pool, await storage.startScrub(pool));
		} catch (e) {
			this.error = messageOf(e);
		} finally {
			this.startingScrub[pool] = false;
		}
	}

	async cancelScrub(pool: string) {
		try {
			// btrfs can still report the scrub as running for a moment: then the events socket
			// sends it once it stopped (ADR-0367).
			this.scrub.show(pool, await storage.cancelScrub(pool));
		} catch (e) {
			this.error = messageOf(e);
		}
	}

	async balanceAction(pool: string, action: BalanceAction) {
		this.balanceBusy[pool] = true;
		this.error = '';
		try {
			// A pause or a cancel can still wait for the chunk being moved: then the events socket
			// sends the balance once it stopped.
			this.balance.show(pool, await BALANCE_ACTIONS[action](pool));
		} catch (e) {
			this.error = messageOf(e);
		} finally {
			this.balanceBusy[pool] = false;
		}
	}
}

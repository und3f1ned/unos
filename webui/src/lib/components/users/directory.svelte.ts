import { getUsers, type User } from '$lib/api/auth';
import { ApiError } from '$lib/api/client';
import { messageOf } from './errors';
import { Outcome } from './outcome.svelte';

/**
 * The users of the Users page, as the last load found them, and the outcome of the last change.
 * The page creates one; its cards and dialogs show it and change it.
 */
export class UserDirectory {
	users = $state.raw<User[]>([]);
	loading = $state(true);
	error = $state('');
	// Outcome of the last change: shown until dismissed.
	outcome = new Outcome();

	#loadFailed: () => string;

	/** `loadFailed`: the page's words for a load that failed without a reason. */
	constructor(loadFailed: () => string) {
		this.#loadFailed = loadFailed;
	}

	async load(showSpinner = true) {
		if (showSpinner) this.loading = true;
		try {
			this.users = await getUsers();
			this.error = '';
		} catch (e) {
			this.error = messageOf(e) || this.#loadFailed();
		} finally {
			this.loading = false;
		}
	}

	/**
	 * After a refusal that can mean the list is out of date (409, e.g. "User N
	 * is now 'x'; reload the page", or 404), load the users again and return
	 * `target` as stored now. null: its id names someone else now, or nobody,
	 * so nothing a dialog prepared for `target` applies any more. Other
	 * errors: `target` itself.
	 */
	async currentAfterRefusal(e: unknown, target: User): Promise<User | null> {
		if (!(e instanceof ApiError && (e.status === 409 || e.status === 404))) return target;
		await this.load(false);
		const now = this.users.find((u) => u.id === target.id);
		return now !== undefined && now.username === target.username ? now : null;
	}
}

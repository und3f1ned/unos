import { getUsers, type User } from '$lib/api/auth';
import { getGroups, type Group } from '$lib/api/groups';
import { pickableUsers } from '$lib/components/shares/access';
import { messageOf } from './errors';
import { Outcome } from './outcome.svelte';

/**
 * The groups of the Users page (ADR-0385) and the users loaded with them, so that a dialog never
 * sends a member list older than the group; and the outcome of the last change. The groups card
 * creates one; its dialog changes it.
 */
export class GroupDirectory {
	groups = $state.raw<Group[]>([]);
	known = $state.raw<User[]>([]);
	loading = $state(true);
	loadError = $state('');
	outcome = new Outcome();
	// The users that a group can list.
	listedUsers = $derived(pickableUsers(this.known));

	#request = 0;

	/** Loads the groups and the users; of loads that overlap, the last one asked counts. */
	async load() {
		const mine = ++this.#request;
		try {
			const [loaded, loadedUsers] = await Promise.all([getGroups(), getUsers()]);
			if (mine !== this.#request) return;
			this.groups = loaded;
			this.known = loadedUsers;
			this.loadError = '';
		} catch (e) {
			if (mine === this.#request) this.loadError = messageOf(e);
		} finally {
			if (mine === this.#request) this.loading = false;
		}
	}
}

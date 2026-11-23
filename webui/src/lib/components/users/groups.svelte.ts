import { getUsers, type User } from '$lib/api/auth';
import { getGroups, type Group } from '$lib/api/groups';
import { pickableUsers } from '$lib/components/shares/access';
import type { Settled } from '$lib/pages/read';
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
	// A group's dialog is open: the users it lists stay as the user saw them (a member renamed
	// meanwhile would drop out of what it sends), and what came meanwhile is read once it closes.
	#dialogOpen = false;
	#readOnClose = false;

	/** A group's dialog opened or closed. */
	dialog(open: boolean) {
		this.#dialogOpen = open;
		if (!open && this.#readOnClose) {
			this.#readOnClose = false;
			void this.load();
		}
	}

	/** The page's users changed: the groups and their users are read again, once an open dialog closes. */
	followUsers() {
		if (this.#dialogOpen) this.#readOnClose = true;
		else void this.load();
	}

	/**
	 * The groups and the users as the page's load read them ($lib/pages/users), as a load that has
	 * answered. Not once a load of its own was asked: it read later; nor while a dialog is open.
	 */
	apply(groups: Settled<Group[]>, users: Settled<User[]>) {
		if (this.#request > 0) return;
		if (this.#dialogOpen) {
			this.#readOnClose = true;
			return;
		}
		if (groups.ok && users.ok) {
			this.groups = groups.value;
			this.known = users.value;
			this.loadError = '';
		} else {
			this.loadError = groups.ok ? (users.ok ? '' : users.error) : groups.error;
		}
		this.loading = false;
	}

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

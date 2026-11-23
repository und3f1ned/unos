// The Users page's reads (routes/users/+page.ts): the users and the groups (ADR-0385) at once, so
// that the page is drawn once; the groups card lists the users read with the groups, so that a
// dialog never sends a member list older than the group.
import { getUsers, type User } from '$lib/api/auth';
import { getGroups, type Group } from '$lib/api/groups';
import type { PageReader } from './load';
import { settle, type Settled } from './read';

export type UsersSnapshot = {
	users: Settled<User[]>;
	groups: Settled<Group[]>;
};

async function readUsers(): Promise<UsersSnapshot> {
	const [users, groups] = await Promise.all([settle(getUsers()), settle(getGroups())]);
	return { users, groups };
}

export const usersReader: PageReader<UsersSnapshot> = {
	key: 'users',
	// A user's SMB state and a group's shares change with the shares (their access, a restore).
	areas: ['auth', 'groups', 'shares'],
	adminOnly: true,
	read: readUsers
};

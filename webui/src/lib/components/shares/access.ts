import type { User } from '$lib/api/auth';
import type { Group } from '$lib/api/groups';
import type {
	ShareAccess,
	ShareAccessEntry,
	ShareAccessLevel,
	ShareGroupAccess,
	ShareGroupAccessEntry
} from '$lib/api/shares';

export type AccessChoice = 'none' | ShareAccessLevel;

/**
 * Key of a user in the access picker. User ids are reused after a deletion,
 * so a choice belongs to an id and the name it had: a reloaded list where the
 * id names someone else starts that person at "No access".
 */
export function accessKey(userId: number, username: string): string {
	return `${userId}:${username}`;
}

/** The users the picker lists: administrators cannot use SMB. Sorted by name. */
export function pickableUsers(users: User[]): User[] {
	return users
		.filter((user) => !user.is_admin)
		.sort((a, b) => a.username.localeCompare(b.username));
}

/** Users whose name SMB cannot use are listed, but cannot be given access. */
export function canBeGivenAccess(user: User): boolean {
	return !user.is_admin && user.smb?.state !== 'unavailable';
}

/** The picker's choices for a share's access list. */
export function levelsFromAccess(access: ShareAccess[]): Record<string, AccessChoice> {
	const levels: Record<string, AccessChoice> = {};
	for (const entry of access) levels[accessKey(entry.user_id, entry.username)] = entry.level;
	return levels;
}

/** The access list the picker shows for `users`, sorted by user id. */
export function accessEntries(
	users: User[],
	levels: Record<string, AccessChoice>
): ShareAccessEntry[] {
	const entries: ShareAccessEntry[] = [];
	for (const user of users) {
		if (!canBeGivenAccess(user)) continue;
		const level = levels[accessKey(user.id, user.username)];
		if (level === 'read' || level === 'write') {
			entries.push({ user_id: user.id, username: user.username, level });
		}
	}
	return entries.sort((a, b) => a.user_id - b.user_id);
}

export function sameAccess(a: ShareAccessEntry[], b: ShareAccessEntry[]): boolean {
	return (
		a.length === b.length &&
		a.every(
			(entry, i) =>
				entry.user_id === b[i].user_id &&
				entry.username === b[i].username &&
				entry.level === b[i].level
		)
	);
}

// ---- Groups ----

/**
 * Key of a group in the access picker: its id and the name it had, as for users: a reloaded list
 * where the id names another group starts that one at "No access".
 */
export function groupKey(id: number, name: string): string {
	return `${id}:${name}`;
}

/** The picker's choices for a share's group access list. */
export function groupLevelsFromAccess(access: ShareGroupAccess[]): Record<string, AccessChoice> {
	const levels: Record<string, AccessChoice> = {};
	for (const entry of access) levels[groupKey(entry.group_id, entry.group)] = entry.level;
	return levels;
}

/** The group access list the picker shows for `groups`, sorted by group id. */
export function groupAccessEntries(
	groups: Group[],
	levels: Record<string, AccessChoice>
): ShareGroupAccessEntry[] {
	const entries: ShareGroupAccessEntry[] = [];
	for (const group of groups) {
		const level = levels[groupKey(group.id, group.name)];
		if (level === 'read' || level === 'write') entries.push({ group_id: group.id, group: group.name, level });
	}
	return entries.sort((a, b) => a.group_id - b.group_id);
}

export function sameGroupAccess(a: ShareGroupAccessEntry[], b: ShareGroupAccessEntry[]): boolean {
	return (
		a.length === b.length &&
		a.every(
			(entry, i) =>
				entry.group_id === b[i].group_id && entry.group === b[i].group && entry.level === b[i].level
		)
	);
}

/**
 * The level that smb.conf gives `username` on a share: the higher of their own level and the levels
 * of the groups through which they get access now. null when they have none in effect.
 */
export function effectiveLevel(
	username: string,
	access: ShareAccess[],
	groupAccess: ShareGroupAccess[]
): ShareAccessLevel | null {
	const levels = [
		...access.filter((entry) => entry.username === username && entry.effective).map((entry) => entry.level),
		...groupAccess
			.filter((entry) => entry.members.some((member) => member.username === username && member.effective))
			.map((entry) => entry.level)
	];
	if (levels.includes('write')) return 'write';
	return levels.includes('read') ? 'read' : null;
}

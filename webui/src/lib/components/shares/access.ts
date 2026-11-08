import type { User } from '$lib/api/auth';
import type { ShareAccess, ShareAccessEntry, ShareAccessLevel } from '$lib/api/shares';

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

import type { SharePath } from '$lib/api/shares';
import { POOL_ROOT_MESSAGE, appsFolderMessage } from './texts';

/**
 * The components of a folder typed below a pool or volume. Empty and "."
 * components are dropped, as the server normalizes paths ("a//b/" and
 * "./a/b" are "a/b"); anything else is left for the server to check.
 */
export function folderParts(folder: string): string[] {
	return folder.split('/').filter((part) => part !== '' && part !== '.');
}

/** The share path: the location, then the folder below it, if any. */
export function joinSharePath(base: string, parts: string[]): string {
	return parts.length > 0 ? `${base}/${parts.join('/')}` : base;
}

/**
 * Split a stored share path into the deepest offered location that contains
 * it and the folder below that location. No location: base and folder are
 * empty (e.g. the pool is not mounted, or the path is not on a pool).
 */
export function splitSharePath(path: string, locations: SharePath[]): { base: string; folder: string } {
	let best: SharePath | null = null;
	for (const location of locations) {
		if (path === location.path || path.startsWith(`${location.path}/`)) {
			if (best === null || location.path.length > best.path.length) best = location;
		}
	}
	if (best === null) return { base: '', folder: '' };
	return { base: best.path, folder: path.slice(best.path.length + 1) };
}

/** The last component of a location path: the pool's name for a pool. */
function lastComponent(path: string): string {
	const parts = path.split('/').filter(Boolean);
	return parts[parts.length - 1] ?? path;
}

/**
 * Why the folder cannot be shared at this location, as far as the browser
 * can tell, or ''. The server checks every rule again.
 */
export function folderProblem(location: SharePath | undefined, parts: string[]): string {
	if (!location || location.type !== 'pool') return '';
	if (parts.length === 0) return POOL_ROOT_MESSAGE;
	if (parts[0] === 'apps') return appsFolderMessage(lastComponent(location.path));
	return '';
}

// The paths of the Files page.

/** A folder that the breadcrumbs open: its name and its path. */
export type Crumb = { name: string; path: string };

/** From the pools directory, named `rootName`, down to `path`: nothing above it can be opened. */
export function getBreadcrumbs(path: string | null, root: string | null, rootName: string): Crumb[] {
	if (!path || !root) return [];
	const crumbs = [{ name: rootName, path: root }];
	const prefix = root.endsWith('/') ? root : `${root}/`;
	if (!path.startsWith(prefix)) return crumbs;
	let current = root;
	for (const part of path.slice(prefix.length).split('/').filter(Boolean)) {
		current = `${current.replace(/\/$/, '')}/${part}`;
		crumbs.push({ name: part, path: current });
	}
	return crumbs;
}

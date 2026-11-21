// The selection of the Files page and of its trash: the paths of the chosen items.

/** `selected` with `path` added, or taken out when it was in: a new set, since only a new value updates a page. */
export function toggled(selected: ReadonlySet<string>, path: string): Set<string> {
	const next = new Set(selected);
	if (next.has(path)) {
		next.delete(path);
	} else {
		next.add(path);
	}
	return next;
}

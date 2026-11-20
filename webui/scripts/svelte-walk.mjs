// A walk over the syntax tree that svelte/compiler's parse() returns, for the checks of this
// folder: Svelte 5's compiler no longer exports the walk of Svelte 4 (estree-walker's). It
// visits every object with a `type` under the node, in the order of its keys, and calls
// enter(node, parent) and leave(node, parent) as estree-walker did: `this.skip()` in enter keeps
// the walk out of the node's children, and its leave is not called.

const SKIPPED_KEYS = new Set(['parent', 'metadata', 'loc', 'name_loc']);
const isNode = (value) => value !== null && typeof value === 'object' && typeof value.type === 'string';

export function walk(root, { enter, leave } = {}) {
	const visit = (node, parent) => {
		let skipped = false;
		const context = { skip: () => { skipped = true; } };
		if (enter) enter.call(context, node, parent);
		if (skipped) return;
		for (const [key, value] of Object.entries(node)) {
			if (SKIPPED_KEYS.has(key)) continue;
			if (Array.isArray(value)) {
				for (const child of value) if (isNode(child)) visit(child, node);
			} else if (isNode(value)) {
				visit(value, node);
			}
		}
		if (leave) leave.call(context, node, parent);
	};
	if (isNode(root)) visit(root, null);
}

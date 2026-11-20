// Where a floating part of a control goes (ADR-0432): a tooltip above its control, a menu below its
// button, each moved to stay in the window. Both are `position: fixed`, so that a card or a table
// frame that clips its content (overflow) does not cut them.

const MARGIN = 8;
const GAP = 6;

/** Place `tip` above `anchor`, centred on it, or below it when the window has no room above. */
export function placeTip(tip: HTMLElement, anchor: DOMRect): void {
	const width = tip.offsetWidth;
	const height = tip.offsetHeight;
	let top = anchor.top - height - GAP;
	if (top < MARGIN) top = anchor.bottom + GAP;
	const left = clamp(anchor.left + anchor.width / 2 - width / 2, MARGIN, window.innerWidth - width - MARGIN);
	tip.style.top = `${Math.round(top)}px`;
	tip.style.left = `${Math.round(left)}px`;
}

/**
 * Place `menu` below `anchor`, its start or end edge on the anchor's, or above it when the window
 * has no room below; a menu taller than the room scrolls.
 */
export function placeMenu(menu: HTMLElement, anchor: DOMRect, align: 'start' | 'end'): void {
	const below = window.innerHeight - anchor.bottom - GAP - MARGIN;
	const above = anchor.top - GAP - MARGIN;
	menu.style.maxHeight = '';
	const height = menu.offsetHeight;
	const width = menu.offsetWidth;
	let top: number;
	if (height <= below || below >= above) {
		top = anchor.bottom + GAP;
		if (height > below) menu.style.maxHeight = `${Math.max(below, 0)}px`;
	} else {
		const room = Math.min(height, above);
		if (height > above) menu.style.maxHeight = `${above}px`;
		top = anchor.top - GAP - room;
	}
	const start = align === 'end' ? anchor.right - width : anchor.left;
	const left = clamp(start, MARGIN, window.innerWidth - width - MARGIN);
	menu.style.top = `${Math.round(top)}px`;
	menu.style.left = `${Math.round(left)}px`;
}

function clamp(value: number, low: number, high: number): number {
	return Math.max(low, Math.min(value, Math.max(low, high)));
}

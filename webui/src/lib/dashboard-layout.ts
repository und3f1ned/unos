// The dashboard's widgets and the layout a user gives them (ADR-0395). The server keeps a list of
// {id, hidden} and knows no widgets: what this module does not know is ignored, and a widget the
// list lacks comes last, so that a widget added or removed later needs no migration.

import type { LayoutEntry } from '$lib/api/dashboard';

export interface WidgetDef {
	id: string;
	/** The key of the widget's name in the catalog. */
	nameKey: string;
	/** Only administrators see it (ADR-0030). */
	adminOnly: boolean;
	/** A small card, five to a row on a wide screen, or a wide one, three to a row there. */
	size: 'small' | 'wide';
}

/**
 * The grid of the widgets and the columns a widget of each size takes in it (ADR-0395, ADR-0484),
 * for the page and its skeleton alike. The columns follow the width of the grid, not of the window:
 * the grid's wrapper is a container (WIDGET_CONTAINER) and the grid's classes are container queries.
 * Below 480 px (a phone) the grid has 2 columns: small cards two to a row, wide ones the full width;
 * from 480 px (a tablet) 6: small cards three to a row, wide ones the full width; from 640 px wide
 * ones two to a row; from 900 px (a window of 1196 px beside the sidebar) 15: five small cards (3
 * each) or three wide ones (5 each) to a row.
 */
export const WIDGET_CONTAINER = '@container';
export const WIDGET_GRID = 'grid grid-cols-2 @min-[480px]:grid-cols-6 @min-[900px]:grid-cols-15 gap-4';
export const WIDGET_SPAN: Record<WidgetDef['size'], string> = {
	small: 'col-span-1 @min-[480px]:col-span-2 @min-[900px]:col-span-3',
	wide: 'col-span-2 @min-[480px]:col-span-6 @min-[640px]:col-span-3 @min-[900px]:col-span-5'
};

/** The widgets in their default order. */
export const WIDGETS: WidgetDef[] = [
	{ id: 'cpu', nameKey: 'dashboard.stats.cpu', adminOnly: false, size: 'small' },
	{ id: 'memory', nameKey: 'dashboard.stats.memory', adminOnly: false, size: 'small' },
	{ id: 'network', nameKey: 'dashboard.stats.network', adminOnly: false, size: 'small' },
	{ id: 'disk-io', nameKey: 'dashboard.stats.diskIo', adminOnly: false, size: 'small' },
	{ id: 'temperature', nameKey: 'dashboard.stats.temperature', adminOnly: false, size: 'small' },
	{ id: 'storage', nameKey: 'dashboard.storage.title', adminOnly: true, size: 'wide' },
	{ id: 'containers', nameKey: 'dashboard.containers.title', adminOnly: true, size: 'wide' },
	{ id: 'recent-activity', nameKey: 'dashboard.activity.title', adminOnly: true, size: 'wide' }
];

export function widgetDef(id: string): WidgetDef | undefined {
	return WIDGETS.find((widget) => widget.id === id);
}

/** The default layout of a user: every widget they may see, in order, none hidden. */
export function defaultLayout(isAdmin: boolean): LayoutEntry[] {
	return WIDGETS.filter((w) => isAdmin || !w.adminOnly).map((w) => ({ id: w.id, hidden: false }));
}

/**
 * What the page shows for a stored layout: the stored widgets that exist and that the user may
 * see, in the stored order (an id twice counts once), then the others in their default place.
 */
export function resolveLayout(stored: LayoutEntry[], isAdmin: boolean): LayoutEntry[] {
	const allowed = new Set(defaultLayout(isAdmin).map((entry) => entry.id));
	const seen = new Set<string>();
	const result: LayoutEntry[] = [];
	for (const entry of stored) {
		if (!allowed.has(entry.id) || seen.has(entry.id)) continue;
		seen.add(entry.id);
		result.push({ id: entry.id, hidden: entry.hidden === true });
	}
	for (const entry of defaultLayout(isAdmin)) if (!seen.has(entry.id)) result.push(entry);
	return result;
}

export function isDefaultLayout(layout: LayoutEntry[], isAdmin: boolean): boolean {
	const base = defaultLayout(isAdmin);
	return layout.length === base.length && layout.every((e, i) => e.id === base[i].id && !e.hidden);
}

/** `layout` with the widget `id` taken to the place of the widget `target`. */
export function moveTo(layout: LayoutEntry[], id: string, target: string): LayoutEntry[] {
	const from = layout.findIndex((e) => e.id === id);
	const to = layout.findIndex((e) => e.id === target);
	if (from < 0 || to < 0 || from === to) return layout;
	const next = [...layout];
	const [moved] = next.splice(from, 1);
	next.splice(to, 0, moved);
	return next;
}

/**
 * `layout` with the widget `id` one place earlier (`direction` -1) or later (1) among the widgets
 * that `isShown` accepts: a hidden one in between is passed over, so that a move always shows.
 */
export function step(
	layout: LayoutEntry[],
	id: string,
	direction: -1 | 1,
	isShown: (id: string) => boolean
): LayoutEntry[] {
	const from = layout.findIndex((e) => e.id === id);
	for (let i = from + direction; i >= 0 && i < layout.length; i += direction) {
		if (isShown(layout[i].id)) return moveTo(layout, id, layout[i].id);
	}
	return layout;
}

export function setHidden(layout: LayoutEntry[], id: string, hidden: boolean): LayoutEntry[] {
	return layout.map((e) => (e.id === id ? { ...e, hidden } : e));
}

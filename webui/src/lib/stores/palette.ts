import { writable } from 'svelte/store';

/**
 * The dialogs of pages that the command palette opens instead of acting itself (ADR-0388): each
 * is the page's own, which asks the user and acts only when the user confirms it there.
 */
export type PaletteDialog = 'reboot' | 'shutdown' | 'suspend' | 'add-user';

/** The palette's request that a page open one of its dialogs: the dialog, and where it is. */
export interface PaletteRequest {
	dialog: PaletteDialog;
	/** The path and hash of the page that opens it, such as /system#power. */
	at: string;
}

/**
 * The request of the palette that no page has taken yet. The page of `at` takes it (sets null)
 * and opens its dialog; the palette drops it when the browser goes elsewhere first.
 */
export const paletteRequest = writable<PaletteRequest | null>(null);

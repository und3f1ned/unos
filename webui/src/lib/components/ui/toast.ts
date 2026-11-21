import { writable } from 'svelte/store';

// Messages after an action (ADR-0432): a toast says what an action did, or why it failed, for a
// few seconds in the corner of the page, in a live region that screen readers read out, with an
// optional action ("Undo", "Open"). An outcome the user must read before going on stays on the
// page, an Alert by the object it concerns. ToastRegion of the root layout shows them.

export type ToastTone = 'success' | 'error' | 'warning' | 'info';

export type Toast = {
	id: number;
	tone: ToastTone;
	message: string;
	title?: string;
	action?: { label: string; run: () => void };
	/** How long it stays, in milliseconds; the time stands still while it is hovered or focused. */
	duration: number;
};

type ToastOptions = { title?: string; action?: Toast['action']; duration?: number };

/** At most this many toasts at once: a new one pushes the oldest out. */
const MAX_SHOWN = 3;
// Success and information go after 5 s; an error or a warning stays twice as long.
const DURATION: Record<ToastTone, number> = { success: 5000, info: 5000, warning: 10000, error: 10000 };

let next = 0;
const shown = writable<Toast[]>([]);

/** The toasts shown, oldest first. */
export const toasts = { subscribe: shown.subscribe };

/** Show `message`; its id, for dismissToast. */
export function showToast(tone: ToastTone, message: string, options: ToastOptions = {}): number {
	const id = ++next;
	const toast: Toast = {
		id,
		tone,
		message,
		title: options.title,
		action: options.action,
		duration: options.duration ?? DURATION[tone]
	};
	shown.update((list) => [...list, toast].slice(-MAX_SHOWN));
	return id;
}

/** Drop the toasts that carry an action: their action belongs to the page that is going. */
export function dropActions(): void {
	shown.update((list) => list.filter((toast) => !toast.action));
}

export function dismissToast(id: number): void {
	shown.update((list) => list.filter((toast) => toast.id !== id));
}

/** toast.success('Backup deleted'), toast.error(message, { title }). */
export const toast = {
	success: (message: string, options?: ToastOptions) => showToast('success', message, options),
	error: (message: string, options?: ToastOptions) => showToast('error', message, options),
	warning: (message: string, options?: ToastOptions) => showToast('warning', message, options),
	info: (message: string, options?: ToastOptions) => showToast('info', message, options)
};

import { get, writable } from 'svelte/store';

// confirm() asks a question in the ConfirmDialog of the root layout (ConfirmHost) and answers
// true once the action is confirmed, false when it is cancelled (ADR-0432). It replaces the
// browser's window.confirm, which the design check refuses: a dialog of the page's look and
// language, with the action's name on its button, that the keyboard and screen readers work like
// every other dialog.
//
//   if (!(await confirm({ title: $t('backup.deleteTitle', { name }), confirmLabel: $t('backup.delete') }))) return;

export type ConfirmOptions = {
	/** The question: "Delete backup x?" */
	title: string;
	message?: string;
	/** What will happen, each a short sentence. */
	consequences?: string[];
	/** A risk to weigh before the action, shown as a warning (an Alert). */
	warning?: string;
	/** The action's button: a verb and its object ("Delete backup"). */
	confirmLabel: string;
	cancelLabel?: string;
	tone?: 'danger' | 'warning';
	/** A text, such as the object's name, that must be typed first. */
	typeToConfirm?: string;
};

export type ConfirmRequest = ConfirmOptions & { id: number; answer: (yes: boolean) => void };

let next = 0;
const queue = writable<ConfirmRequest[]>([]);

/** The questions waiting, the one shown first. */
export const confirmQueue = { subscribe: queue.subscribe };

/**
 * Answer every pending question no: the page that asked is going (a navigation, a sign-out), and
 * its action must not run on the next page. ConfirmHost calls it before each navigation.
 */
export function cancelAll(): void {
	for (const request of get(queue)) request.answer(false);
}

/** Ask; true once confirmed, false when cancelled. Questions asked meanwhile wait their turn. */
export function confirm(options: ConfirmOptions): Promise<boolean> {
	return new Promise((resolve) => {
		const id = ++next;
		const answer = (yes: boolean) => {
			queue.update((list) => list.filter((request) => request.id !== id));
			resolve(yes);
		};
		queue.update((list) => [...list, { ...options, id, answer }]);
	});
}

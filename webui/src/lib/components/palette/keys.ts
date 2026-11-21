// The keys of the command palette (ADR-0393): its shortcut, which a text field may keep, and
// whether a dialog of the page is open, over which the shortcut is the browser's.

// Ctrl+K or Cmd+K, without Shift or Alt, whatever the keyboard layout: the key K where the
// layout has no Latin letters (Russian), else the letter K. Not a key of an input method.
export function isShortcut(event: KeyboardEvent): boolean {
	if (event.isComposing || event.altKey || event.shiftKey) return false;
	if (event.ctrlKey === event.metaKey) return false;
	// A keydown of the browser's autofill has no key.
	const key = typeof event.key === 'string' ? event.key.toLowerCase() : '';
	return key === 'k' || (!/^[a-z]$/.test(key) && event.code === 'KeyK');
}

// i18n-ignore: types of input that hold no text
const NOT_TEXT = new Set([
	'button',
	'checkbox',
	'color',
	'file',
	'hidden',
	'image',
	'radio',
	'range',
	'reset',
	'submit'
]);

// On macOS Ctrl+K in a text field deletes to the end of its line: the field keeps it there.
export function keptByTheField(event: KeyboardEvent): boolean {
	if (!event.ctrlKey || !/Mac|iPhone|iPad|iPod/.test(navigator.platform)) return false;
	const target = event.target;
	return (
		target instanceof HTMLElement &&
		(target.isContentEditable ||
			target instanceof HTMLTextAreaElement ||
			(target instanceof HTMLInputElement && !NOT_TEXT.has(target.type)))
	);
}

/** A dialog of the page is open: a Modal, or one written by hand with its markers. */
export function dialogOpen(): boolean {
	return document.querySelector('[aria-modal="true"], .modal-overlay') !== null;
}

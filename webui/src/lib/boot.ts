// The boot screen of src/app.html (ADR-0430). It covers the page from the first frame, drawn by
// app.css alone, until the root layout knows the account (or that the page needs none: sign-in,
// first run) and the page has drawn its skeleton or its data. Then it fades out (app.css: 160 ms)
// and is removed, so that nothing of it stays in the page.

/** The fade of .boot in app.css, and a frame more. */
const FADE_MS = 180;

/** Fade the boot screen out and remove it; nothing once it has gone. */
export function finishBoot(): void {
	if (typeof document === 'undefined') return;
	const boot = document.getElementById('boot');
	if (!boot || boot.hasAttribute('data-done')) return;
	boot.setAttribute('data-done', '');
	setTimeout(() => boot.remove(), FADE_MS);
}

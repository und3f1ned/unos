import { derived, readable, writable } from 'svelte/store';
import { browser } from '$app/environment';
import { currentSession } from '$lib/api/client';
import { getTheme, saveTheme, type ThemeChoice } from '$lib/api/theme';

// The theme of the web UI (ADR-0402): Dark, Light or System, which follows the device's
// prefers-color-scheme as it changes. The server keeps each user's choice; this browser keeps the
// last one it showed (no secret: it names no account), which static/theme-init.js reads before
// the page is drawn and the sign-in and first-run pages follow. The theme is the attribute
// data-theme of <html>, which selects the values of the colour tokens in src/app.css.

export type { ThemeChoice };

/** The choices of the Account page, in its order. */
export const THEME_CHOICES: readonly ThemeChoice[] = ['dark', 'light', 'system'];

/** Where this browser keeps the last choice; static/theme-init.js reads the same key. */
export const THEME_KEY = 'unos-theme';

export function isThemeChoice(value: unknown): value is ThemeChoice {
	return value === 'dark' || value === 'light' || value === 'system';
}

function cachedChoice(): ThemeChoice {
	try {
		const saved = localStorage.getItem(THEME_KEY);
		return isThemeChoice(saved) ? saved : 'system';
	} catch {
		// No storage (outside a browser, or blocked): System.
		return 'system';
	}
}

const choice = writable<ThemeChoice>(browser ? cachedChoice() : 'system');

/** The theme chosen: the signed-in user's, else the last one shown in this browser, else System. */
export const themeChoice = { subscribe: choice.subscribe };

function show(value: ThemeChoice): void {
	choice.set(value);
	try {
		localStorage.setItem(THEME_KEY, value);
	} catch {
		// Not kept: the theme still applies to this page.
	}
}

/** Whether the device prefers a light theme, followed as it changes. */
const prefersLight = readable(false, (set) => {
	if (!browser || typeof window.matchMedia !== 'function') return;
	// i18n-ignore: a CSS media query, the same in every language
	const query = window.matchMedia('(prefers-color-scheme: light)');
	const update = () => set(query.matches);
	update();
	query.addEventListener('change', update);
	return () => query.removeEventListener('change', update);
});

/** The theme that the page shows. */
export const appliedTheme = derived([choice, prefersLight], ([$choice, $prefersLight]) =>
	$choice === 'system' ? ($prefersLight ? 'light' : 'dark') : $choice
);

/**
 * Show the theme on <html>, and take a choice made in another tab of this browser. The root layout
 * calls it once; it returns the function that stops it.
 */
export function applyTheme(): () => void {
	const root = document.documentElement;
	const stop = appliedTheme.subscribe((theme) => {
		root.setAttribute('data-theme', theme);
		// static/theme-init.js set it before the style sheet came; it must follow the theme.
		root.style.colorScheme = theme;
	});
	const fromAnotherTab = (event: StorageEvent) => {
		if (event.key !== THEME_KEY || !isThemeChoice(event.newValue)) return;
		// Newer than an answer of the server that this tab still waits for.
		choices += 1;
		saved = event.newValue;
		choice.set(event.newValue);
	};
	window.addEventListener('storage', fromAnotherTab);
	return () => {
		stop();
		window.removeEventListener('storage', fromAnotherTab);
	};
}

// The session whose user's theme was taken from the server, and a count of the choices made here
// and in the other tabs: an answer that comes after a choice is older than it.
let loadedFor: number | null = null;
let choices = 0;

/** Whether the signed-in user's theme could not be read: the page shows this browser's. */
export const themeLoadFailed = writable(false);

/**
 * Take the signed-in user's theme from the server, once per sign-in and page load: the root layout
 * calls it when the account is known, the Account page when it opens. A failure leaves the theme as
 * it is, says so in themeLoadFailed, and the next call asks again.
 */
export async function loadAccountTheme(): Promise<void> {
	const session = currentSession();
	if (loadedFor === session) return;
	loadedFor = session;
	const before = choices;
	try {
		const theme = await getTheme();
		if (!isThemeChoice(theme)) throw new Error('not a theme');
		if (currentSession() !== session) return;
		themeLoadFailed.set(false);
		if (choices !== before) return;
		saved = theme;
		show(theme);
	} catch {
		if (loadedFor === session) {
			loadedFor = null;
			themeLoadFailed.set(true);
		}
	}
}

// What the server keeps, as far as this page knows; the choice being sent, and the last one asked
// for: a choice made while another is sent waits for it, and only the last one goes.
let saved: ThemeChoice = browser ? cachedChoice() : 'system';
let sending: Promise<void> | null = null;
let wanted: ThemeChoice = saved;

/**
 * Choose a theme on the Account page: shown at once and kept for the user on the server. Choices
 * made while one is sent wait for it, and the last of them is sent. When the server does not keep
 * it, the theme it keeps comes back and the error is thrown.
 */
export function chooseTheme(next: ThemeChoice): Promise<void> {
	choices += 1;
	wanted = next;
	show(next);
	if (sending) return sending;
	sending = (async () => {
		try {
			while (wanted !== saved) {
				const value = wanted;
				await saveTheme(value);
				saved = value;
			}
		} catch (error) {
			wanted = saved;
			show(saved);
			throw error;
		} finally {
			sending = null;
		}
	})();
	return sending;
}

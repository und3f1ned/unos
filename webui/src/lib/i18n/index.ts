import { derived, writable } from 'svelte/store';
import {
	dateLocale,
	isLocale,
	translate,
	type Catalogs,
	type Locale,
	type Messages,
	type Params
} from './translate';

// The web UI in English or Russian (ADR-0284). Texts come from the catalogs, one file per area
// of the web UI: catalog/<area>.ts exports `en` and `ru` with keys that start with "<area>.".
// Pages use $t('area.key') and $t('area.key', { n: 3 }).

export { isLocale, type Locale, type Params } from './translate';

/** The languages the Account page offers, each named in its own language. */
export const LANGUAGES: readonly { code: Locale; name: string }[] = [
	{ code: 'en', name: 'English' },
	{ code: 'ru', name: 'Русский' }
];

/** Where this browser keeps the language chosen on the Account page. */
export const LOCALE_KEY = 'unos-locale';

// Every file of catalog/ is collected here, so that a new area needs no edit of a shared file.
const modules = import.meta.glob<Record<Locale, Messages>>('./catalog/*.ts', { eager: true });
const catalogs: Catalogs = { en: {}, ru: {} };
for (const catalog of Object.values(modules)) {
	Object.assign(catalogs.en, catalog.en);
	Object.assign(catalogs.ru, catalog.ru);
}

function savedLocale(): Locale | null {
	try {
		const saved = localStorage.getItem(LOCALE_KEY);
		return isLocale(saved) ? saved : null;
	} catch {
		// No storage (outside a browser, or blocked): nothing was chosen here.
		return null;
	}
}

/** The language chosen in this browser, else Russian for a browser set to Russian, else English. */
function initialLocale(): Locale {
	const saved = savedLocale();
	if (saved) return saved;
	const language = typeof navigator === 'undefined' ? '' : (navigator.language ?? '');
	return language.toLowerCase().split('-')[0] === 'ru' ? 'ru' : 'en';
}

const current = writable<Locale>(initialLocale());

/** The language of the web UI. set() applies it at once and keeps it in this browser. */
export const locale = {
	subscribe: current.subscribe,
	set(value: Locale) {
		current.set(value);
		try {
			localStorage.setItem(LOCALE_KEY, value);
		} catch {
			// Not kept: the choice still applies to this page.
		}
	}
};

/** $t(key, params): the text of key in the current language (translate() of ./translate). */
export const t = derived(
	current,
	($locale) =>
		(key: string, params?: Params): string =>
			translate(catalogs, $locale, key, params)
);

/**
 * The locale for dates and numbers: the browser's own when it is of the current language (so an
 * English page in a British browser shows British dates), else the current language with the day
 * first and a 24-hour clock (dateLocale of ./translate).
 */
export const intlLocale = derived(current, ($locale): string =>
	dateLocale($locale, typeof navigator === 'undefined' ? '' : (navigator.language ?? ''))
);

// <html lang> follows the language: screen readers, hyphenation and the browser's translation
// offer read it.
if (typeof document !== 'undefined') {
	current.subscribe(($locale) => {
		document.documentElement.lang = $locale;
	});
}
